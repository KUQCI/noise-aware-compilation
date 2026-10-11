"""Python API for converting a device and placing a circuit (v2 R-I1 to R-I6).

The two operations here are exactly what the ``nac convert`` and ``nac place``
commands run, so the command line and the API give identical results for
identical inputs (R-I5). Neither needs Qiskit, a decoder or any simulation
(R-I6): conversion reads the backend files as JSON, and placement scores
candidates from the detector error model only.

Example::

    from noise_aware_compilation.api import convert_device, place_circuit

    stored = convert_device("conf_jakarta.json", "props_jakarta.json", "my_device")
    placed = place_circuit("circuit.stim", "placed", device="my_device")
    placed.mapping  # circuit qubit -> physical qubit
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import nove
import stim

from noise_aware_compilation import __version__
from noise_aware_compilation.conversion import convert_fake_backend
from noise_aware_compilation.devices import (
    StoredDevice,
    available_devices,
    device_file_names,
    load_device,
    load_device_dir,
    write_device_dir,
)
from noise_aware_compilation.placement import PlacementResult, place, relabel_circuit
from noise_aware_compilation.scoring import DEFAULT_SCORER, get_scorer

MAPPING_SCHEMA = "noise-aware-compilation.mapping/1"
MAPPING_FILE = "mapping.json"
PLACED_CIRCUIT_FILE = "placed_circuit.stim"

_CONF_FIELDS = ("backend_name", "n_qubits", "coupling_map")
_PROPS_FIELDS = ("backend_name", "last_update_date", "gates", "qubits")
_QUBIT_FIELDS = ("T1", "T2", "readout_error", "readout_length")
_GATE_FIELDS: dict[str, tuple[str, ...]] = {
    "cx": ("gate_error", "gate_length"),
    "sx": ("gate_error", "gate_length"),
    "reset": ("gate_length",),
}


class InputError(ValueError):
    """Invalid user input: the message is one line naming the problem (R-I7)."""


@dataclass(frozen=True)
class ConvertOutput:
    """What :func:`convert_device` wrote."""

    name: str
    directory: Path
    files: tuple[Path, ...]
    snapshot_sha256: str
    device_sha256: str
    notes: tuple[str, ...]


@dataclass(frozen=True)
class PlaceOutput:
    """What :func:`place_circuit` chose and wrote."""

    result: PlacementResult
    mapping_file: Path
    circuit_file: Path
    record: dict[str, Any]

    @property
    def mapping(self) -> dict[int, int]:
        return self.result.mapping_dict


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path, what: str) -> Any:
    if not path.is_file():
        raise InputError(f"{what} file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputError(f"{what} file {path} is not valid JSON ({exc})") from None


def _parameter_names(entries: Any) -> set[str]:
    if not isinstance(entries, list):
        return set()
    return {str(e.get("name")) for e in entries if isinstance(e, Mapping)}


def check_fake_backend_files(conf_path: str | Path, props_path: str | Path) -> None:
    """Raise :class:`InputError` naming the first missing or incomplete calibration field.

    Checks everything :func:`~noise_aware_compilation.conversion.convert_fake_backend`
    reads, so a converter error never reaches the user as a traceback.
    """

    conf_file, props_file = Path(conf_path), Path(props_path)
    conf = _load_json(conf_file, "configuration")
    props = _load_json(props_file, "properties")
    if not isinstance(conf, dict) or not isinstance(props, dict):
        raise InputError("configuration and properties files must each hold a JSON object")
    for field in _CONF_FIELDS:
        if field not in conf:
            raise InputError(f"configuration file {conf_file.name} has no {field!r} field")
    for field in _PROPS_FIELDS:
        if field not in props:
            raise InputError(f"properties file {props_file.name} has no {field!r} field")
    if conf["backend_name"] != props["backend_name"]:
        raise InputError(
            f"configuration ({conf['backend_name']}) and properties ({props['backend_name']}) "
            "describe different backends"
        )
    n = conf["n_qubits"]
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise InputError(f"configuration field 'n_qubits' must be a positive integer, got {n!r}")
    qubits = props["qubits"]
    listed = len(qubits) if isinstance(qubits, list) else 0
    if not isinstance(qubits, list) or listed != n:
        raise InputError(
            f"properties file lists calibration for {listed} qubits but the configuration has {n}"
        )
    for q, entries in enumerate(qubits):
        names = _parameter_names(entries)
        for field in _QUBIT_FIELDS:
            if field not in names:
                raise InputError(f"properties file has no {field!r} for qubit {q}")
    gates: dict[tuple[str, tuple[int, ...]], set[str]] = {}
    for gate in props["gates"] if isinstance(props["gates"], list) else []:
        if not isinstance(gate, Mapping) or "gate" not in gate or "qubits" not in gate:
            raise InputError("properties file has a gate entry without 'gate' or 'qubits'")
        key = (str(gate["gate"]), tuple(int(x) for x in gate["qubits"]))
        gates[key] = _parameter_names(gate.get("parameters"))
    for q in range(n):
        for name in ("sx", "reset"):
            if (name, (q,)) not in gates:
                raise InputError(f"properties file has no {name!r} calibration for qubit {q}")
            for field in _GATE_FIELDS[name]:
                if field not in gates[(name, (q,))]:
                    raise InputError(f"properties file has no {field!r} for {name} on qubit {q}")
    coupling = conf["coupling_map"]
    if not isinstance(coupling, list) or not coupling:
        raise InputError("configuration field 'coupling_map' is empty")
    for pair in coupling:
        a, b = (int(x) for x in pair)
        for directed in ((a, b), (b, a)):
            if ("cx", directed) not in gates:
                raise InputError(f"properties file has no 'cx' calibration for qubits {directed}")
            for field in _GATE_FIELDS["cx"]:
                if field not in gates[("cx", directed)]:
                    raise InputError(f"properties file has no {field!r} for cx on {directed}")


def default_device_name(conf_path: str | Path) -> str:
    """The stored name ``convert`` uses when none is given: the file's backend name."""

    conf = _load_json(Path(conf_path), "configuration")
    return str(conf["backend_name"]) if isinstance(conf, dict) else "device"


def convert_device(
    conf_path: str | Path,
    props_path: str | Path,
    out_dir: str | Path,
    name: str | None = None,
) -> ConvertOutput:
    """Convert an IBM fake-backend file pair and store it in ``out_dir`` (R-I1).

    Writes ``<name>_snapshot.json`` (Nove ``CalibrationSnapshot``),
    ``<name>_device.json`` (Nove ``DeviceModel``) and ``hashes.json`` with
    both SHA-256 hashes. ``name`` defaults to the backend name in the files.
    """

    check_fake_backend_files(conf_path, props_path)
    stored_name = name if name is not None else default_device_name(conf_path)
    if not stored_name or any(c in stored_name for c in "/\\"):
        raise InputError(f"device name {stored_name!r} must be non-empty with no path separators")
    converted = convert_fake_backend(conf_path, props_path)
    directory = Path(out_dir)
    hashes_file = write_device_dir(converted, directory, stored_name)
    snapshot_file, device_file = device_file_names(stored_name)
    return ConvertOutput(
        name=stored_name,
        directory=directory,
        files=(directory / snapshot_file, directory / device_file, hashes_file),
        snapshot_sha256=converted.snapshot.sha256,
        device_sha256=converted.device.sha256,
        notes=converted.notes,
    )


def read_circuit(path: str | Path) -> nove.Circuit:
    """Read a Stim text file as a Nove circuit; raise :class:`InputError` if unreadable."""

    file = Path(path)
    if not file.is_file():
        raise InputError(f"circuit file not found: {file}")
    try:
        text = file.read_text(encoding="utf-8")
        circuit = nove.Circuit.from_stim(stim.Circuit(text))
    except Exception as exc:  # Stim and Nove raise several types for bad circuits
        first = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        raise InputError(f"circuit file {file.name} is not a valid Stim circuit: {first}") from None
    if circuit.num_qubits == 0:
        raise InputError(f"circuit file {file.name} acts on no qubits")
    return circuit


def _resolve_device(
    device: str | Path | None, conf: str | Path | None, props: str | Path | None
) -> tuple[StoredDevice, dict[str, Any]]:
    if device is not None and (conf is not None or props is not None):
        raise InputError(
            "give either a stored device or an IBM file pair (conf and props), not both"
        )
    if device is None:
        if conf is None or props is None:
            raise InputError("give a stored device, or both the conf and props files of a device")
        check_fake_backend_files(conf, props)
        converted = convert_fake_backend(conf, props)
        stored = StoredDevice(
            name=default_device_name(conf), snapshot=converted.snapshot, device=converted.device
        )
        conf_file, props_file = Path(conf), Path(props)
        inputs = {
            "kind": "ibm-file-pair",
            "files": {conf_file.name: _sha256(conf_file), props_file.name: _sha256(props_file)},
        }
        return stored, inputs
    path = Path(device)
    if path.is_dir():
        try:
            stored = load_device_dir(path)
        except (OSError, ValueError, KeyError) as exc:
            raise InputError(f"stored device {path} could not be loaded: {exc}") from None
        snapshot_file, device_file = device_file_names(stored.name)
        files = {n: _sha256(path / n) for n in ("hashes.json", snapshot_file, device_file)}
        return stored, {"kind": "stored-device", "files": files}
    if str(device) in available_devices():
        stored = load_device(str(device))
        return stored, {"kind": "shipped-device", "name": stored.name}
    raise InputError(
        f"device {str(device)!r} is neither a stored device directory nor a shipped device "
        f"({', '.join(available_devices())})"
    )


def place_circuit(
    circuit_path: str | Path,
    out_dir: str | Path,
    *,
    device: str | Path | None = None,
    conf: str | Path | None = None,
    props: str | Path | None = None,
    scorer: str = DEFAULT_SCORER,
) -> PlaceOutput:
    """Place the circuit in ``circuit_path`` on a device and write the results (R-I2, R-I3).

    The device is either ``device`` (a directory written by :func:`convert_device`,
    or the name of a device shipped with the package) or the IBM file pair
    ``conf`` and ``props``. ``scorer`` names the selection score.

    Writes ``mapping.json`` (the chosen mapping, its score, the scorer, every
    candidate with its score, input file hashes and the package version) and
    ``placed_circuit.stim`` (the relabelled circuit) into ``out_dir``.
    """

    try:
        chosen_scorer = get_scorer(scorer)
    except KeyError as exc:
        raise InputError(str(exc.args[0])) from None
    circuit = read_circuit(circuit_path)
    stored, device_inputs = _resolve_device(device, conf, props)
    result = place(circuit, stored.device, stored.noise, chosen_scorer)
    placed = relabel_circuit(circuit, result.mapping)
    placed_text = placed.to_stim_text()

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    circuit_file = out / PLACED_CIRCUIT_FILE
    circuit_file.write_text(placed_text, encoding="utf-8")
    source = Path(circuit_path)
    record: dict[str, Any] = {
        "schema": MAPPING_SCHEMA,
        "package_version": __version__,
        "scorer": result.scorer,
        "score_method": result.score_method,
        "tie_break_rule": result.tie_break_rule,
        "mapping": {str(c): q for c, q in enumerate(result.mapping)},
        "score": result.score,
        "candidates": [{"mapping": list(c.mapping), "score": c.score} for c in result.candidates],
        "num_candidates": len(result.candidates),
        "device_id": stored.device.device_id,
        "calibration_snapshot_sha256": stored.snapshot.sha256,
        "device_sha256": stored.device.sha256,
        "inputs": {
            "circuit": {"file": source.name, "sha256": _sha256(source)},
            "device": device_inputs,
        },
        "placed_circuit": {
            "file": PLACED_CIRCUIT_FILE,
            "sha256": hashlib.sha256(placed_text.encode("utf-8")).hexdigest(),
        },
    }
    mapping_file = out / MAPPING_FILE
    mapping_file.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return PlaceOutput(
        result=result, mapping_file=mapping_file, circuit_file=circuit_file, record=record
    )


EXAMPLE_FILES = ("repetition_d3_bitflip.stim", "conf_jakarta.json", "props_jakarta.json")


def write_example(out_dir: str | Path) -> tuple[Path, ...]:
    """Copy the shipped example (a circuit and an IBM file pair) into ``out_dir`` (R-I9).

    The circuit is a distance-3 bit-flip repetition code, 3 rounds; the device
    files are FakeJakartaV2's from ``qiskit-ibm-runtime`` 0.50.0.
    """

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name in EXAMPLE_FILES:
        data = resources.files("noise_aware_compilation.examples").joinpath(name).read_bytes()
        (out / name).write_bytes(data)
        written.append(out / name)
    return tuple(written)


__all__ = [
    "EXAMPLE_FILES",
    "MAPPING_FILE",
    "MAPPING_SCHEMA",
    "PLACED_CIRCUIT_FILE",
    "ConvertOutput",
    "InputError",
    "PlaceOutput",
    "check_fake_backend_files",
    "convert_device",
    "default_device_name",
    "place_circuit",
    "read_circuit",
    "write_example",
]
