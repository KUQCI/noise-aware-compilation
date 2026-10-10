"""Noise conditions N0-N3 and the T-L1 sensitivity variants.

Every function takes and returns Nove types and works for any ``DeviceModel``
and ``HardwareNoiseModel`` (R-D5); nothing here knows which device it is.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import nove

from noise_aware_compilation.placement import device_qubits

PLANT_FACTOR = 5.0


def directed_pairs(device: nove.DeviceModel) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    for a, b in device.connectivity:
        pairs.extend([(a, b), (b, a)])
    return sorted(pairs)


def noise_sha256(noise: nove.HardwareNoiseModel) -> str:
    text = json.dumps(noise.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def _elementwise_median(channels: Iterable[nove.PauliChannel]) -> nove.PauliChannel:
    vectors = [c.probabilities for c in channels]
    return nove.PauliChannel(tuple(statistics.median(column) for column in zip(*vectors, strict=True)))


def _gate_names(noise: nove.HardwareNoiseModel) -> list[str]:
    return sorted({key.gate for key in noise.gate_channels} | set(noise.gate_defaults))


def _channels_for(
    noise: nove.HardwareNoiseModel, device: nove.DeviceModel, gate: str
) -> list[nove.PauliChannel]:
    explicit = [c for k, c in noise.gate_channels.items() if k.gate == gate]
    if explicit:
        return explicit
    default = noise.gate_defaults.get(gate)
    return [] if default is None else [default]


def zero_noise(noise: nove.HardwareNoiseModel) -> nove.HardwareNoiseModel:
    """N0: the same processes with every probability and rate set to zero."""

    defaults = {}
    for gate in _gate_names(noise):
        arity = 15 if any(
            len(k.targets) == 2 for k in noise.gate_channels if k.gate == gate
        ) or (gate in noise.gate_defaults and noise.gate_defaults[gate].arity == 2) else 3
        defaults[gate] = nove.PauliChannel((0.0,) * arity)
    return nove.HardwareNoiseModel(
        gate_defaults=defaults,
        default_idle_rate=nove.PauliRate(0.0, 0.0, 0.0),
        default_readout_flip_probability=0.0,
        default_reset_flip_probability=0.0,
    )


def uniform_median(
    noise: nove.HardwareNoiseModel, device: nove.DeviceModel
) -> nove.HardwareNoiseModel:
    """N1: every parameter set to its median over the device's qubits or directed pairs."""

    qubits = device_qubits(device)
    defaults = {
        gate: _elementwise_median(_channels_for(noise, device, gate))
        for gate in _gate_names(noise)
    }
    rates = [noise.idle_rate_for(q) for q in qubits]
    known_rates = [r for r in rates if r is not None]
    readouts = [p for p in (noise.readout_probability_for(q) for q in qubits) if p is not None]
    resets = [p for p in (noise.reset_probability_for(q) for q in qubits) if p is not None]
    return nove.HardwareNoiseModel(
        gate_defaults=defaults,
        default_idle_rate=nove.PauliRate(
            statistics.median(r.x for r in known_rates),
            statistics.median(r.y for r in known_rates),
            statistics.median(r.z for r in known_rates),
        )
        if known_rates
        else None,
        default_readout_flip_probability=statistics.median(readouts) if readouts else None,
        default_reset_flip_probability=statistics.median(resets) if resets else None,
    )


@dataclass(frozen=True)
class PlantedFault:
    kind: str  # "qubit" or "connection"
    qubit: int | None = None
    connection: tuple[int, int] | None = None

    def contained_in(self, mapping: Sequence[int], edges: Iterable[tuple[int, int]]) -> bool:
        """Whether a placement uses the faulty qubit, or runs a gate on the faulty connection."""

        if self.kind == "qubit":
            return self.qubit in mapping
        assert self.connection is not None
        target = {self.connection[0], self.connection[1]}
        return any({mapping[a], mapping[b]} == target for a, b in edges)

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "qubit": self.qubit,
            "connection": None if self.connection is None else list(self.connection),
            "factor": PLANT_FACTOR,
        }


def plant_fault(
    uniform: nove.HardwareNoiseModel, device: nove.DeviceModel, fault: PlantedFault
) -> nove.HardwareNoiseModel:
    """N3: a uniform model with one qubit's readout x5 or one connection's CX x5."""

    readout = dict(uniform.readout_flip_probabilities)
    channels = dict(uniform.gate_channels)
    if fault.kind == "qubit":
        assert fault.qubit is not None
        base = uniform.readout_probability_for(fault.qubit)
        if base is None:
            raise ValueError("no readout probability to scale")
        readout[fault.qubit] = min(1.0, PLANT_FACTOR * base)
    elif fault.kind == "connection":
        assert fault.connection is not None
        a, b = fault.connection
        if not device.is_connected(a, b):
            raise ValueError(f"({a}, {b}) is not a device connection")
        for pair in ((a, b), (b, a)):
            key = nove.OperationKey("CX", pair)
            base_channel = uniform.gate_channel_for(key)
            if base_channel is None:
                raise ValueError("no CX channel to scale")
            channels[key] = nove.PauliChannel(
                tuple(PLANT_FACTOR * p for p in base_channel.probabilities)
            )
    else:
        raise ValueError(f"unknown fault kind {fault.kind!r}")
    return nove.HardwareNoiseModel(
        gate_channels=channels,
        gate_defaults=dict(uniform.gate_defaults),
        idle_rates=dict(uniform.idle_rates),
        default_idle_rate=uniform.default_idle_rate,
        readout_flip_probabilities=readout,
        default_readout_flip_probability=uniform.default_readout_flip_probability,
        reset_flip_probabilities=dict(uniform.reset_flip_probabilities),
        default_reset_flip_probability=uniform.default_reset_flip_probability,
    )


def with_cx_duration(device: nove.DeviceModel, duration: float) -> nove.DeviceModel:
    """T-L1: the same device with the CX duration replaced."""

    data = device.to_dict()
    durations = dict(data["gate_durations"])  # type: ignore[call-overload]
    durations["CX"] = float(duration)
    data["gate_durations"] = durations
    return nove.DeviceModel.from_dict(data)


def reset_equals_readout(
    noise: nove.HardwareNoiseModel, device: nove.DeviceModel
) -> nove.HardwareNoiseModel:
    """T-L1: reset flip probability set to each qubit's readout flip probability."""

    reset = {}
    for q in device_qubits(device):
        p = noise.readout_probability_for(q)
        if p is not None:
            reset[q] = p
    return nove.HardwareNoiseModel(
        gate_channels=dict(noise.gate_channels),
        gate_defaults=dict(noise.gate_defaults),
        idle_rates=dict(noise.idle_rates),
        default_idle_rate=noise.default_idle_rate,
        readout_flip_probabilities=dict(noise.readout_flip_probabilities),
        default_readout_flip_probability=noise.default_readout_flip_probability,
        reset_flip_probabilities=reset,
        default_reset_flip_probability=noise.default_reset_flip_probability,
        calibration_hash=noise.calibration_hash,
    )


__all__ = [
    "PLANT_FACTOR",
    "PlantedFault",
    "directed_pairs",
    "noise_sha256",
    "plant_fault",
    "reset_equals_readout",
    "uniform_median",
    "with_cx_duration",
    "zero_noise",
]
