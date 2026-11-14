"""Direction-aware readout (SPEC_V3.md revision 6, R3-C13; exploratory).

Nove's readout error is symmetric (K3): the converter stores each qubit's published
``readout_error``, the mean of its two directions. In a Z-basis repetition code every
measurement reads 0 when nothing has gone wrong, so the direction that matters is P(1|0),
the published ``prob_meas1_prep0``; on FakeFez it is about half of ``readout_error``.

The *directional* setting gives each qubit's readout flip probability, and its reset flip
probability (reset = readout, as at the headline setting), the value of P(1|0) from the
device's raw props file in ``data/devices`` (its hash is checked against ``SHA256SUMS``).
The Qiskit Target that Qiskit's default and mapomatic read carries the same values. Nothing
else changes, so the setting asks one question: does a direction-aware readout model change
which placement each method chooses, or how the choices compare?

A held-out device's props file exists only after conversion (F4), so the setting reads it
only then.
"""

from __future__ import annotations

import functools
import hashlib
import json
import math
from pathlib import Path

import nove

from noise_aware_compilation.conditions import reset_equals_readout
from noise_aware_compilation.devices import StoredDevice

ROOT = Path(__file__).resolve().parent.parent
PROPS_DIR = ROOT / "data" / "devices"
FIELD = "prob_meas1_prep0"
FLIP_CAP = 0.5
DESCRIPTION = (
    "each qubit's readout and reset flip probability equal its published P(1|0) "
    "(prob_meas1_prep0), the direction a Z-basis repetition code meets when nothing has "
    "gone wrong; every other value as converted (exploratory, revision 6)"
)


def _checked_props(device: str, props_dir: Path) -> bytes:
    name = f"props_{device}.json"
    data = (props_dir / name).read_bytes()
    sums = {}
    for line in (props_dir / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, _, file = line.partition("  ")
        sums[file.strip()] = digest.strip()
    if sums.get(name) != hashlib.sha256(data).hexdigest():
        raise RuntimeError(f"{name} does not match data/devices/SHA256SUMS")
    return data


@functools.cache
def prob_meas1_prep0(device: str, props_dir: Path = PROPS_DIR) -> dict[int, float]:
    """Each qubit's published P(1|0), capped at 0.5, from the device's raw props file."""

    props = json.loads(_checked_props(device, props_dir))
    out: dict[int, float] = {}
    for q, entries in enumerate(props["qubits"]):
        for entry in entries:
            if entry["name"] == FIELD:
                value = float(entry["value"])
                if not math.isfinite(value) or value < 0:
                    raise ValueError(f"{device} qubit {q}: {FIELD} = {value}")
                out[q] = min(value, FLIP_CAP)
    if not out:
        raise ValueError(f"{device}: no {FIELD} in its props file")
    return out


def _flips(stored: StoredDevice, device: str) -> dict[int, float]:
    p10 = prob_meas1_prep0(device)
    flips = {}
    for q in (int(q) for q in stored.snapshot.qubit_parameters):
        if q not in p10:
            raise ValueError(f"{device} qubit {q} has no {FIELD}")
        flips[q] = p10[q]
    return flips


def directional_noise(stored: StoredDevice, device: str) -> nove.HardwareNoiseModel:
    """The stored noise model with readout and reset flips at each qubit's P(1|0)."""

    flips = _flips(stored, device)
    noise = stored.noise
    readout = {q: flips[q] for q in noise.readout_flip_probabilities}
    base = nove.HardwareNoiseModel(
        gate_channels=dict(noise.gate_channels),
        gate_defaults=dict(noise.gate_defaults),
        idle_rates=dict(noise.idle_rates),
        default_idle_rate=noise.default_idle_rate,
        readout_flip_probabilities=readout,
        default_readout_flip_probability=noise.default_readout_flip_probability,
        reset_flip_probabilities=dict(noise.reset_flip_probabilities),
        default_reset_flip_probability=noise.default_reset_flip_probability,
        calibration_hash=noise.calibration_hash,
    )
    return reset_equals_readout(base, stored.device)


def directional_snapshot(stored: StoredDevice, device: str) -> nove.CalibrationSnapshot:
    """The stored snapshot with each qubit's readout values at its P(1|0), for the Target."""

    flips = _flips(stored, device)
    snapshot = stored.snapshot
    qubits = {}
    for q, params in snapshot.qubit_parameters.items():
        values = dict(params)
        values["readout_flip_probability"] = flips[int(q)]
        values["source_readout_error"] = flips[int(q)]
        qubits[q] = values
    return nove.CalibrationSnapshot(
        device_id=snapshot.device_id,
        captured_at=snapshot.captured_at,
        time_unit=snapshot.time_unit,
        operation_parameters={k: dict(v) for k, v in snapshot.operation_parameters.items()},
        qubit_parameters=qubits,
        provenance=dict(snapshot.provenance),
        uncertainties=snapshot.uncertainties,
    )


__all__ = [
    "DESCRIPTION",
    "FIELD",
    "directional_noise",
    "directional_snapshot",
    "prob_meas1_prep0",
]
