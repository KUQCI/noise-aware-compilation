"""Converter from qiskit-ibm-runtime fake-backend files to Nove types (R-D3).

This is the only module that knows the fake-backend file format. It reads the
``conf_*.json`` / ``props_*.json`` files as plain JSON; Qiskit is never
imported (R-D6, X3). The rules applied are the SPEC.md "Noise conversion
specification", identically for every device. Two departures forced by the
data are documented in DECISIONS.md (D3, D4) and are flagged in every
converted snapshot's provenance:

* a directed CX pair with ``1.25 * gate_error > 1`` cannot be a probability
  distribution; its 15 terms are capped at ``1/15`` each (total 1);
* a qubit with ``T2 > 2 * T1`` gives a negative Z rate under the rule; the Z
  rate is clamped at 0 because Nove rejects negative rates.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import nove

SOURCE_PACKAGE = "qiskit-ibm-runtime"
SOURCE_PACKAGE_VERSION = "0.50.0"

#: Stim order of the 15 two-qubit Pauli terms, as Nove's calibration labels.
TWO_QUBIT_LABELS: tuple[str, ...] = tuple(
    f"p_{a}{b}" for a in "ixyz" for b in "ixyz" if (a, b) != ("i", "i")
)
ONE_QUBIT_LABELS: tuple[str, ...] = ("p_x", "p_y", "p_z")

CX_FACTOR = 1.25
H_FACTOR = 1.5
_US_TO_NS = 1000.0


@dataclass(frozen=True)
class ConvertedDevice:
    """A converted device: Nove snapshot and device model, plus conversion notes."""

    snapshot: nove.CalibrationSnapshot
    device: nove.DeviceModel
    notes: tuple[str, ...]


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _in_ns(value: float, unit: str) -> float:
    if unit == "ns":
        return value
    if unit == "us":
        return value * _US_TO_NS
    if unit == "ms":
        return value * 1e6
    if unit == "s":
        return value * 1e9
    raise ValueError(f"unsupported time unit {unit!r}")


def _qubit_fields(entries: Sequence[Mapping[str, Any]]) -> dict[str, tuple[float, str]]:
    return {str(e["name"]): (float(e["value"]), str(e.get("unit", ""))) for e in entries}


def _gate_fields(gate: Mapping[str, Any]) -> dict[str, tuple[float, str]]:
    return {str(p["name"]): (float(p["value"]), str(p.get("unit", ""))) for p in gate["parameters"]}


def cx_probabilities(gate_error: float) -> tuple[tuple[float, ...], bool]:
    """Return the 15 CX Pauli probabilities ``1.25 e / 15`` and whether a cap applied."""

    each = CX_FACTOR * gate_error / 15.0
    capped = each * 15.0 > 1.0
    if capped:
        each = 1.0 / 15.0
    return (each,) * 15, capped


def h_probabilities(sx_error: float) -> tuple[float, float, float]:
    """Return ``p_x = p_y = p_z = 1.5 e / 3`` for the H channel."""

    each = H_FACTOR * sx_error / 3.0
    return (each, each, each)


def idle_rates(t1_ns: float, t2_ns: float) -> tuple[float, float, float, bool]:
    """Return X, Y, Z idle rates per ns and whether the Z rate was clamped at 0."""

    x = 1.0 / (4.0 * t1_ns)
    z = 1.0 / (2.0 * t2_ns) - 1.0 / (4.0 * t1_ns)
    clamped = z < 0
    return x, x, max(z, 0.0), clamped


def convert_fake_backend(conf_path: str | Path, props_path: str | Path) -> ConvertedDevice:
    """Convert a fake-backend ``conf``/``props`` pair to a snapshot and device model."""

    conf_file = Path(conf_path)
    props_file = Path(props_path)
    conf = json.loads(conf_file.read_text(encoding="utf-8"))
    props = json.loads(props_file.read_text(encoding="utf-8"))
    if conf["backend_name"] != props["backend_name"]:
        raise ValueError("conf and props describe different backends")

    num_qubits = int(conf["n_qubits"])
    coupling = [(int(a), int(b)) for a, b in conf["coupling_map"]]
    notes: list[str] = []

    operations: dict[nove.OperationKey, dict[str, float]] = {}
    cx_lengths: list[float] = []
    sx_lengths: list[float] = []
    reset_lengths: list[float] = []
    seen_cx: set[tuple[int, int]] = set()
    for gate in props["gates"]:
        name = gate["gate"]
        qubits = tuple(int(q) for q in gate["qubits"])
        fields = _gate_fields(gate)
        if name == "cx":
            error = fields["gate_error"][0]
            length = _in_ns(*fields["gate_length"])
            probabilities, capped = cx_probabilities(error)
            if capped:
                notes.append(
                    f"CX{qubits}: gate_error={error} gives 1.25*e > 1; "
                    "15 terms capped at 1/15 each (DECISIONS.md D3)"
                )
            parameters = dict(zip(TWO_QUBIT_LABELS, probabilities, strict=True))
            parameters["source_gate_error"] = error
            parameters["source_gate_length_ns"] = length
            operations[nove.OperationKey("CX", qubits)] = parameters
            cx_lengths.append(length)
            seen_cx.add((qubits[0], qubits[1]))
        elif name == "sx":
            error = fields["gate_error"][0]
            length = _in_ns(*fields["gate_length"])
            parameters = dict(zip(ONE_QUBIT_LABELS, h_probabilities(error), strict=True))
            parameters["source_sx_gate_error"] = error
            parameters["source_sx_gate_length_ns"] = length
            operations[nove.OperationKey("H", qubits)] = parameters
            sx_lengths.append(length)
        elif name == "reset":
            reset_lengths.append(_in_ns(*fields["gate_length"]))

    missing = sorted(set(coupling) - seen_cx)
    if missing:
        raise ValueError(f"coupling-map pairs without cx calibration: {missing}")

    qubit_parameters: dict[int, dict[str, float]] = {}
    readout_lengths: list[float] = []
    for qubit, entries in enumerate(props["qubits"]):
        fields = _qubit_fields(entries)
        t1 = _in_ns(*fields["T1"])
        t2 = _in_ns(*fields["T2"])
        x, y, z, clamped = idle_rates(t1, t2)
        if clamped:
            notes.append(
                f"qubit {qubit}: T2={t2:.1f} ns > 2*T1={2 * t1:.1f} ns gives a negative "
                "Z rate; clamped at 0 (DECISIONS.md D4)"
            )
        readout_lengths.append(_in_ns(*fields["readout_length"]))
        qubit_parameters[qubit] = {
            "idle_x_rate": x,
            "idle_y_rate": y,
            "idle_z_rate": z,
            "readout_flip_probability": fields["readout_error"][0],
            "reset_flip_probability": 0.0,
            "source_t1_ns": t1,
            "source_t2_ns": t2,
            "source_readout_error": fields["readout_error"][0],
        }
    if len(qubit_parameters) != num_qubits:
        raise ValueError("props qubit count differs from conf n_qubits")

    provenance = {
        "source_package": SOURCE_PACKAGE,
        "source_package_version": SOURCE_PACKAGE_VERSION,
        "backend_name": conf["backend_name"],
        "last_update_date": props["last_update_date"],
        "conf_file": conf_file.name,
        "conf_sha256": _sha256_file(conf_file),
        "props_file": props_file.name,
        "props_sha256": _sha256_file(props_file),
        "conversion_notes": list(notes),
    }
    snapshot = nove.CalibrationSnapshot(
        device_id=str(conf["backend_name"]),
        captured_at=str(props["last_update_date"]),
        time_unit="ns",
        operation_parameters=operations,
        qubit_parameters=qubit_parameters,
        provenance=provenance,
    )
    device = nove.DeviceModel(
        device_id=str(conf["backend_name"]),
        time_unit="ns",
        connectivity=coupling,
        gate_durations={
            "CX": statistics.fmean(cx_lengths),
            "H": statistics.fmean(sx_lengths),
        },
        measurement_duration=statistics.fmean(readout_lengths),
        reset_duration=statistics.fmean(reset_lengths),
        metadata={
            "num_qubits": num_qubits,
            "cx_duration_min": min(cx_lengths),
            "cx_duration_max": max(cx_lengths),
            "calibration_sha256": snapshot.sha256,
            "provenance": provenance,
        },
    )
    return ConvertedDevice(snapshot=snapshot, device=device, notes=tuple(notes))


def noise_model_from_snapshot(snapshot: nove.CalibrationSnapshot) -> nove.HardwareNoiseModel:
    """Map a converted snapshot to Nove's executable hardware noise model."""

    return nove.HardwareNoiseModel.from_calibration(snapshot)


__all__ = [
    "ONE_QUBIT_LABELS",
    "SOURCE_PACKAGE",
    "SOURCE_PACKAGE_VERSION",
    "TWO_QUBIT_LABELS",
    "ConvertedDevice",
    "convert_fake_backend",
    "cx_probabilities",
    "h_probabilities",
    "idle_rates",
    "noise_model_from_snapshot",
]
