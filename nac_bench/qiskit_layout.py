"""Qiskit layout baseline (SPEC_V2.md R-B5 to R-B8, T2-B2, T2-B3).

The placement Qiskit's ``VF2Layout`` chooses for an op-for-op Qiskit translation
of the circuit, on a ``Target`` built from the same Nove noise model the
package's method receives:

* each Target instruction's error is the total error probability of its Nove
  channel: the sum of the Pauli probabilities for ``cx`` (per direction) and
  ``h``, the readout flip for ``measure`` and the reset flip for ``reset``;
* durations come from the Nove device model, and T1 and T2 (qubit properties)
  are recovered from the noise model's idle rates;
* ``VF2Layout(seed=-1, strict_direction=True, call_limit=None,
  max_trials=None, time_limit=None)``: deterministic and unlimited.

A run that finds no layout, or one outside the candidate set, is a failure of
the baseline for that case; no substitute is used (R-B7).
"""

from __future__ import annotations

import importlib.metadata
import math
from collections.abc import Sequence
from dataclasses import dataclass

import nove
import stim
from qiskit import QuantumCircuit
from qiskit.circuit.library import CXGate, HGate, Measure, Reset
from qiskit.providers.backend import QubitProperties
from qiskit.transpiler import InstructionProperties, PassManager, Target
from qiskit.transpiler.passes import VF2Layout

from noise_aware_compilation.placement import (
    Placement,
    _iter_flat,
    device_qubits,
    enumerate_placements,
)

NS = 1e-9
VF2_SETTINGS = {
    "seed": -1,
    "strict_direction": True,
    "call_limit": None,
    "max_trials": None,
    "time_limit": None,
}
METHOD = (
    "Qiskit VF2Layout on an op-for-op Qiskit circuit and a Target whose instruction errors are "
    "the Nove channels' total error probabilities; settings " + repr(VF2_SETTINGS)
)


def qiskit_version() -> str:
    return importlib.metadata.version("qiskit")


@dataclass(frozen=True)
class QiskitChoice:
    """VF2Layout's choice for one circuit and noise model."""

    mapping: Placement | None
    stop_reason: str
    failure: str | None  # None when the choice is a valid candidate


def qiskit_circuit(circuit: nove.Circuit) -> QuantumCircuit:
    """Op-for-op translation: R -> reset, H -> h, CX -> cx (same direction), M -> measure."""

    num_measurements = int(circuit.num_measurements)
    qc = QuantumCircuit(circuit.num_qubits, num_measurements)
    clbit = 0
    for instruction in _iter_flat(circuit.to_stim()):
        name = instruction.name
        qubits = [t.value for t in instruction.targets_copy() if t.is_qubit_target]
        if name in ("R", "RZ"):
            for q in qubits:
                qc.reset(q)
        elif name == "H":
            for q in qubits:
                qc.h(q)
        elif name in ("CX", "CNOT"):
            for a, b in zip(qubits[::2], qubits[1::2], strict=True):
                qc.cx(a, b)
        elif name in ("M", "MZ"):
            for q in qubits:
                qc.measure(q, clbit)
                clbit += 1
        elif stim.gate_data(name).is_unitary or stim.gate_data(name).is_noisy_gate:
            raise ValueError(f"operation {name} has no translation for the Qiskit baseline")
    return qc


def channel_total(noise: nove.HardwareNoiseModel, gate: str, qubits: tuple[int, ...]) -> float:
    channel = noise.gate_channel_for(nove.OperationKey(gate, qubits))
    return 0.0 if channel is None else math.fsum(channel.probabilities)


def build_target(device: nove.DeviceModel, noise: nove.HardwareNoiseModel) -> Target:
    """A Qiskit Target carrying the same noise data as ``noise`` (R-B5, T2-B3)."""

    physical = device_qubits(device)
    if physical != tuple(range(len(physical))):
        raise ValueError("device qubits must be numbered 0..n-1")
    properties = []
    for q in physical:
        rate = noise.idle_rate_for(q)
        if rate is None or rate.x <= 0:
            properties.append(QubitProperties())
            continue
        t1_ns = 1.0 / (4.0 * rate.x)
        t2_ns = 1.0 / (2.0 * (rate.z + rate.x))
        properties.append(QubitProperties(t1=t1_ns * NS, t2=t2_ns * NS))
    target = Target(num_qubits=len(physical), qubit_properties=properties)
    cx_duration = float(device.duration_for("CX") or 0.0) * NS
    h_duration = float(device.duration_for("H") or 0.0) * NS
    measure_duration = float(device.measurement_duration or 0.0) * NS
    reset_duration = float(device.reset_duration or 0.0) * NS
    pairs = sorted(
        {(a, b) for a, b in device.connectivity} | {(b, a) for a, b in device.connectivity}
    )
    target.add_instruction(
        CXGate(),
        {
            pair: InstructionProperties(
                error=channel_total(noise, "CX", pair), duration=cx_duration
            )
            for pair in pairs
        },
    )
    target.add_instruction(
        HGate(),
        {
            (q,): InstructionProperties(error=channel_total(noise, "H", (q,)), duration=h_duration)
            for q in physical
        },
    )
    target.add_instruction(
        Measure(),
        {
            (q,): InstructionProperties(
                error=noise.readout_probability_for(q) or 0.0,
                duration=measure_duration,
            )
            for q in physical
        },
    )
    target.add_instruction(
        Reset(),
        {
            (q,): InstructionProperties(
                error=noise.reset_probability_for(q) or 0.0,
                duration=reset_duration,
            )
            for q in physical
        },
    )
    return target


def qiskit_layout(
    circuit: nove.Circuit,
    device: nove.DeviceModel,
    noise: nove.HardwareNoiseModel,
    candidates: Sequence[Placement] | None = None,
) -> QiskitChoice:
    """Run VF2Layout and check its choice against the candidate set (R-B6, R-B7)."""

    qc = qiskit_circuit(circuit)
    manager = PassManager([VF2Layout(target=build_target(device, noise), **VF2_SETTINGS)])
    manager.run(qc)
    reason = str(manager.property_set["VF2Layout_stop_reason"])
    layout = manager.property_set["layout"]
    if layout is None:
        return QiskitChoice(mapping=None, stop_reason=reason, failure="no layout returned")
    mapping = tuple(int(layout[qc.qubits[i]]) for i in range(qc.num_qubits))
    allowed = set(candidates if candidates is not None else enumerate_placements(circuit, device))
    if mapping not in allowed:
        return QiskitChoice(mapping=mapping, stop_reason=reason, failure="outside candidate set")
    return QiskitChoice(mapping=mapping, stop_reason=reason, failure=None)


__all__ = [
    "METHOD",
    "VF2_SETTINGS",
    "QiskitChoice",
    "build_target",
    "channel_total",
    "qiskit_circuit",
    "qiskit_layout",
    "qiskit_version",
]
