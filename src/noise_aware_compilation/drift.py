"""Synthetic calibration drift: seen noise models for v2 Part A (SPEC_V2.md R-S2 to R-S4).

A *seen* model at drift strength ``delta`` multiplies every noise channel of a
calibration snapshot by an independent factor ``exp(delta * z)`` with ``z``
standard normal. A channel is each directed CX's Pauli channel, each qubit's H
channel, each qubit's idle rates (X, Y and Z scaled together), each qubit's
readout flip and each qubit's reset flip; all probabilities of one channel get
the same factor. Caps keep every channel valid: a Pauli channel whose total
would exceed 1 is scaled down proportionally to total 1, and a flip probability
is capped at 0.5. The ``source_*`` audit fields and the provenance are kept as
they are, so at ``delta = 0`` the seen snapshot is bit-identical to the true one
(every factor is exactly ``exp(0) = 1``) and has the same hash (R-S6).

Draw ``k`` of a device uses the same normal deviates at every drift strength
(common random numbers): the seed depends on the device and ``k`` only, so the
drift strengths differ in scale, not in which channels move which way.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

import nove
import numpy as np

from noise_aware_compilation.conversion import ONE_QUBIT_LABELS, TWO_QUBIT_LABELS
from noise_aware_compilation.evaluation import derive_seed

IDLE_LABELS = ("idle_x_rate", "idle_y_rate", "idle_z_rate")
READOUT = "readout_flip_probability"
RESET = "reset_flip_probability"
FLIP_CAP = 0.5
DRIFT_SEED_RULE = "sha256('drift-v2|<device>|<draw>')[:8] & (2**63-1)"


@dataclass(frozen=True)
class Channel:
    """One drifted unit: an operation's Pauli channel or one qubit quantity."""

    kind: str  # "gate", "idle", "readout" or "reset"
    operation: nove.OperationKey | None
    qubit: int | None

    @property
    def label(self) -> str:
        if self.operation is not None:
            return f"{self.operation.gate}{tuple(self.operation.targets)}"
        return f"{self.kind}[{self.qubit}]"


@dataclass(frozen=True)
class DriftDraw:
    """A seen snapshot and how it was made."""

    delta: float
    draw: int
    seed: int
    snapshot: nove.CalibrationSnapshot
    factors: tuple[float, ...]
    capped: tuple[str, ...]


def draw_seed(device_name: str, draw: int) -> int:
    """Seed of draw ``draw`` for a device; the same at every drift strength."""

    return derive_seed("drift-v2", device_name, draw)


def channels(snapshot: nove.CalibrationSnapshot) -> list[Channel]:
    """Every drifted channel of a snapshot, in a fixed order."""

    out: list[Channel] = []
    for key in sorted(snapshot.operation_parameters):
        params = snapshot.operation_parameters[key]
        labels = ONE_QUBIT_LABELS if len(key.targets) == 1 else TWO_QUBIT_LABELS
        if all(label in params for label in labels):
            out.append(Channel("gate", key, None))
    for qubit in sorted(snapshot.qubit_parameters):
        params = snapshot.qubit_parameters[qubit]
        if all(label in params for label in IDLE_LABELS):
            out.append(Channel("idle", None, qubit))
        if READOUT in params:
            out.append(Channel("readout", None, qubit))
        if RESET in params:
            out.append(Channel("reset", None, qubit))
    return out


def _scaled_pauli(
    params: Mapping[str, float], labels: tuple[str, ...], factor: float
) -> tuple[dict[str, float], bool]:
    values = [float(params[label]) * factor for label in labels]
    total = math.fsum(values)
    capped = total > 1.0
    if capped:
        values = [v / total for v in values]
    out = dict(params)
    out.update(zip(labels, values, strict=True))
    return out, capped


def drift_snapshot(
    snapshot: nove.CalibrationSnapshot, delta: float, seed: int, *, draw: int = 0
) -> DriftDraw:
    """Return the seen snapshot at drift strength ``delta`` for one seeded draw (R-S2)."""

    if delta < 0 or not math.isfinite(delta):
        raise ValueError("drift strength must be finite and non-negative")
    units = channels(snapshot)
    z = np.random.Generator(np.random.PCG64(seed)).standard_normal(len(units))
    factors = tuple(math.exp(delta * float(v)) for v in z)
    operations = {k: dict(v) for k, v in snapshot.operation_parameters.items()}
    qubits = {q: dict(v) for q, v in snapshot.qubit_parameters.items()}
    capped: list[str] = []
    for unit, factor in zip(units, factors, strict=True):
        if unit.kind == "gate":
            assert unit.operation is not None
            labels = ONE_QUBIT_LABELS if len(unit.operation.targets) == 1 else TWO_QUBIT_LABELS
            operations[unit.operation], was_capped = _scaled_pauli(
                operations[unit.operation], labels, factor
            )
        else:
            assert unit.qubit is not None
            params = qubits[unit.qubit]
            if unit.kind == "idle":
                for label in IDLE_LABELS:
                    params[label] = float(params[label]) * factor
                was_capped = False
            else:
                label = READOUT if unit.kind == "readout" else RESET
                value = float(params[label]) * factor
                was_capped = value > FLIP_CAP
                params[label] = min(value, FLIP_CAP)
        if was_capped:
            capped.append(unit.label)
    seen = nove.CalibrationSnapshot(
        device_id=snapshot.device_id,
        captured_at=snapshot.captured_at,
        time_unit=snapshot.time_unit,
        operation_parameters=operations,
        qubit_parameters=qubits,
        provenance=dict(snapshot.provenance),
        uncertainties=snapshot.uncertainties,
    )
    return DriftDraw(
        delta=delta, draw=draw, seed=seed, snapshot=seen, factors=factors, capped=tuple(capped)
    )


def channel_ratios(true: nove.CalibrationSnapshot, seen: nove.CalibrationSnapshot) -> list[float]:
    """seen / true for each channel with a non-zero true value (one value per channel)."""

    ratios: list[float] = []
    for unit in channels(true):
        if unit.operation is not None:
            labels = ONE_QUBIT_LABELS if len(unit.operation.targets) == 1 else TWO_QUBIT_LABELS
            before = true.operation_parameters[unit.operation]
            after = seen.operation_parameters[unit.operation]
        else:
            assert unit.qubit is not None
            labels = {
                "idle": IDLE_LABELS,
                "readout": (READOUT,),
                "reset": (RESET,),
            }[unit.kind]
            before = true.qubit_parameters[unit.qubit]
            after = seen.qubit_parameters[unit.qubit]
        total_before = math.fsum(float(before[label]) for label in labels)
        if total_before > 0:
            ratios.append(math.fsum(float(after[label]) for label in labels) / total_before)
    return ratios


__all__ = [
    "DRIFT_SEED_RULE",
    "FLIP_CAP",
    "Channel",
    "DriftDraw",
    "channel_ratios",
    "channels",
    "draw_seed",
    "drift_snapshot",
]
