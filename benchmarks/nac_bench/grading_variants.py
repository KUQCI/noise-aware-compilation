"""Grading-only model variants for the family placements (SPEC_V3.md revision 6, R3-C15).

Every family comparison grades a placement under the model that chose it (K18). These
variants keep each family placement fixed, simulate it under a changed noise model, and
decode with the selecting model's weights, as a real decoder that only has the published
calibration would (the design of R3-D5 and ``nac_bench.regrade_v2_drift``). They are
exploratory; no claim uses them. Each one changes one thing the converted model fixes:

* ``cz-duration-min`` / ``cz-duration-max``: every CZ takes the device's shortest or longest
  published CZ length instead of the mean (K2: Nove sets one duration per gate name), which
  changes the idle noise of every qubit waiting on a CZ layer.
* ``idle-z-x2``: each qubit's idle Z rate doubled (the converted rate uses the published
  T2, which IBM measures with an echo; without one dephasing is faster).
* ``lognormal-<delta>``: independent log-normal factors exp(delta z) on every gate, idle,
  readout and reset channel (``drift.drift_snapshot``), at delta = 0.1 and 0.25 with four
  seeded draws each, then reset = readout as at the headline setting. R3-D5 did this on the
  CX development devices only; here it reaches the CZ held-out family.
* ``directional``: readout and reset at each qubit's P(1|0) (``nac_bench.directional``), on
  the Z-basis repetition codes only, where every outcome is 0 when nothing goes wrong.
* ``cz-without-1q``: each CZ's published error less its two qubits' published ``sx`` errors
  (K19). IBM's two-qubit benchmark includes a layer of single-qubit Cliffords in the
  two-qubit error, and the circuits charge their H gates again, so this is the other end of
  that double count. The ``sx`` error is read back from each qubit's H channel (v1's rule:
  1.5 times the ``sx`` error over three terms), the CZ error from its channel (1.25 times the
  error over 15 terms), and a CZ error that would go below zero is set to zero.
"""

from __future__ import annotations

import math
from typing import Any

import nove

from nac_bench.directional import directional_noise
from noise_aware_compilation.conditions import reset_equals_readout
from noise_aware_compilation.conversion import noise_model_from_snapshot
from noise_aware_compilation.devices import StoredDevice
from noise_aware_compilation.drift import drift_snapshot
from noise_aware_compilation.evaluation import derive_seed

VARIANT_SEED_TAG = "variant-v3-lognormal"

Model = tuple[nove.DeviceModel, nove.HardwareNoiseModel]


def variant_names(spec: dict[str, Any], kind: str, basis: str | None) -> list[str]:
    """Every variant (log-normal draws named ``lognormal-<delta>-draw<k>``) for one case."""

    names: list[str] = []
    for name, info in spec["variants"].items():
        if kind not in info["kinds"]:
            continue
        if basis is not None and "bases" in info and basis not in info["bases"]:
            continue
        if "delta" in info:
            names += [f"{name}-draw{k}" for k in range(int(info["draws"]))]
        else:
            names.append(name)
    return names


def _durations(device: nove.DeviceModel, which: str) -> nove.DeviceModel:
    meta = device.metadata or {}
    key = f"cz_duration_{which}"
    if key not in meta:
        raise ValueError(f"the device records no {key}")
    data = device.to_dict()
    durations = dict(data["gate_durations"])  # type: ignore[call-overload]
    durations["CZ"] = float(meta[key])
    data["gate_durations"] = durations
    return nove.DeviceModel.from_dict(data)


def _idle_z(noise: nove.HardwareNoiseModel, factor: float) -> nove.HardwareNoiseModel:
    rates = {q: type(r)(x=r.x, y=r.y, z=r.z * factor) for q, r in dict(noise.idle_rates).items()}
    return nove.HardwareNoiseModel(
        gate_channels=dict(noise.gate_channels),
        gate_defaults=dict(noise.gate_defaults),
        idle_rates=rates,
        default_idle_rate=noise.default_idle_rate,
        readout_flip_probabilities=dict(noise.readout_flip_probabilities),
        default_readout_flip_probability=noise.default_readout_flip_probability,
        reset_flip_probabilities=dict(noise.reset_flip_probabilities),
        default_reset_flip_probability=noise.default_reset_flip_probability,
        calibration_hash=noise.calibration_hash,
    )


def _cz_without_1q(noise: nove.HardwareNoiseModel) -> nove.HardwareNoiseModel:
    sx = {
        int(key.targets[0]): math.fsum(channel.probabilities) / 1.5
        for key, channel in dict(noise.gate_channels).items()
        if key.gate == "H"
    }
    channels = dict(noise.gate_channels)
    for key, channel in dict(noise.gate_channels).items():
        if key.gate != "CZ":
            continue
        a, b = (int(q) for q in key.targets)
        if a not in sx or b not in sx:
            raise ValueError(f"CZ {key.targets}: a qubit has no H channel")
        error = math.fsum(channel.probabilities) / 1.25
        factor = max(error - sx[a] - sx[b], 0.0) / error if error > 0 else 0.0
        channels[key] = type(channel)(
            probabilities=tuple(p * factor for p in channel.probabilities)
        )
    return nove.HardwareNoiseModel(
        gate_channels=channels,
        gate_defaults=dict(noise.gate_defaults),
        idle_rates=dict(noise.idle_rates),
        default_idle_rate=noise.default_idle_rate,
        readout_flip_probabilities=dict(noise.readout_flip_probabilities),
        default_readout_flip_probability=noise.default_readout_flip_probability,
        reset_flip_probabilities=dict(noise.reset_flip_probabilities),
        default_reset_flip_probability=noise.default_reset_flip_probability,
        calibration_hash=noise.calibration_hash,
    )


def variant_model(
    stored: StoredDevice,
    device: str,
    base: nove.HardwareNoiseModel,
    name: str,
    spec: dict[str, Any],
    sample_seed: int,
) -> Model:
    """The (device, noise) a variant simulates under; ``base`` is the selecting model."""

    if name == "cz-duration-min":
        return _durations(stored.device, "min"), base
    if name == "cz-duration-max":
        return _durations(stored.device, "max"), base
    if name == "idle-z-x2":
        return stored.device, _idle_z(base, 2.0)
    if name == "cz-without-1q":
        return stored.device, _cz_without_1q(base)
    if name == "directional":
        return stored.device, directional_noise(stored, device)
    family, _, draw = name.rpartition("-draw")
    info = spec["variants"].get(family)
    if info is None or "delta" not in info:
        raise ValueError(f"unknown grading variant {name!r}")
    delta, k = float(info["delta"]), int(draw)
    seed = derive_seed(VARIANT_SEED_TAG, sample_seed, device, delta, k)
    seen = drift_snapshot(stored.snapshot, delta, seed, draw=k).snapshot
    return stored.device, reset_equals_readout(noise_model_from_snapshot(seen), stored.device)


__all__ = ["VARIANT_SEED_TAG", "variant_model", "variant_names"]
