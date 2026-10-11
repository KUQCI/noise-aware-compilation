"""v2 Part D: T2-I1 to T2-I6 and R-I6 (command line and Python API)."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import nove
import pytest

from noise_aware_compilation.api import (
    EXAMPLE_FILES,
    MAPPING_FILE,
    PLACED_CIRCUIT_FILE,
    convert_device,
    place_circuit,
    write_example,
)
from noise_aware_compilation.cli import build_parser
from noise_aware_compilation.devices import load_device, load_device_dir
from noise_aware_compilation.placement import is_valid_placement
from noise_aware_compilation.scoring import DEFAULT_SCORER

ROOT = Path(__file__).resolve().parent.parent
CIRCUIT, CONF, PROPS = EXAMPLE_FILES


def nac(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "noise_aware_compilation", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def example(tmp_path: Path) -> Path:
    write_example(tmp_path)
    return tmp_path


def _read_mapping(directory: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((directory / MAPPING_FILE).read_text())
    return data


def test_t2_i1_end_to_end_cli(tmp_path: Path) -> None:
    for args in (
        ("example", "--out", "ex"),
        ("convert", "--conf", f"ex/{CONF}", "--props", f"ex/{PROPS}", "--out", "dev"),
        ("place", "--circuit", f"ex/{CIRCUIT}", "--device", "dev", "--out", "placed"),
    ):
        result = nac(*args, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
    stored = load_device_dir(tmp_path / "dev")
    record = _read_mapping(tmp_path / "placed")
    mapping = [record["mapping"][str(c)] for c in range(len(record["mapping"]))]
    circuit = nove.Circuit.from_stim_text((tmp_path / "ex" / CIRCUIT).read_text())
    assert is_valid_placement(circuit, stored.device, mapping)
    assert record["candidates"] and record["scorer"] == DEFAULT_SCORER
    for key in ("score", "package_version", "inputs", "calibration_snapshot_sha256"):
        assert key in record
    placed = nove.Circuit.from_stim_text((tmp_path / "placed" / PLACED_CIRCUIT_FILE).read_text())
    experiment = nove.Experiment(
        placed, noise=nove.NoiseModel(hardware=stored.noise), device=stored.device
    )
    experiment.resolved_circuit()  # Nove accepts the placed circuit with this device and noise


def test_t2_i2_file_pair_and_stored_device_agree(example: Path) -> None:
    convert_device(example / CONF, example / PROPS, example / "dev")
    from_pair = place_circuit(
        example / CIRCUIT, example / "a", conf=example / CONF, props=example / PROPS
    )
    from_stored = place_circuit(example / CIRCUIT, example / "b", device=example / "dev")
    assert from_pair.result.mapping == from_stored.result.mapping
    assert from_pair.result.candidates == from_stored.result.candidates
    assert from_pair.record["device_sha256"] == from_stored.record["device_sha256"]
    assert (example / "a" / PLACED_CIRCUIT_FILE).read_text() == (
        example / "b" / PLACED_CIRCUIT_FILE
    ).read_text()


@pytest.mark.parametrize("device_kind", ["file-pair", "stored", "jakarta", "hanoi"])
def test_t2_i3_cli_and_api_agree(example: Path, device_kind: str) -> None:
    if device_kind == "file-pair":
        api_kwargs: dict[str, Any] = {"conf": example / CONF, "props": example / PROPS}
        cli_args = ["--conf", CONF, "--props", PROPS]
    elif device_kind == "stored":
        convert_device(example / CONF, example / PROPS, example / "dev")
        api_kwargs = {"device": example / "dev"}
        cli_args = ["--device", "dev"]
    else:
        api_kwargs = {"device": device_kind}
        cli_args = ["--device", device_kind]
    api = place_circuit(example / CIRCUIT, example / "api", **api_kwargs)
    cli = nac("place", "--circuit", CIRCUIT, *cli_args, "--out", "cli", cwd=example)
    assert cli.returncode == 0, cli.stderr
    # identical files, not just identical mappings (there are no timestamps)
    assert (example / "cli" / MAPPING_FILE).read_bytes() == api.mapping_file.read_bytes()
    assert (example / "cli" / PLACED_CIRCUIT_FILE).read_bytes() == api.circuit_file.read_bytes()


def test_t2_i3_convert_cli_and_api_agree(example: Path) -> None:
    api = convert_device(example / CONF, example / PROPS, example / "api")
    cli = nac("convert", "--conf", CONF, "--props", PROPS, "--out", "cli", cwd=example)
    assert cli.returncode == 0, cli.stderr
    for path in api.files:
        assert (example / "cli" / path.name).read_bytes() == path.read_bytes()
    stored = load_device("jakarta")
    assert api.snapshot_sha256 == stored.snapshot.sha256
    assert api.device_sha256 == stored.device.sha256


def _edit_props(example: Path, edit: Any) -> None:
    props = json.loads((example / PROPS).read_text())
    edit(props)
    (example / PROPS).write_text(json.dumps(props))


def _drop_qubit_field(name: str, qubit: int) -> Any:
    def edit(props: dict[str, Any]) -> None:
        props["qubits"][qubit] = [e for e in props["qubits"][qubit] if e["name"] != name]

    return edit


def _drop_gate(gate: str, qubits: list[int]) -> Any:
    def edit(props: dict[str, Any]) -> None:
        props["gates"] = [
            g for g in props["gates"] if not (g["gate"] == gate and g["qubits"] == qubits)
        ]

    return edit


def _drop_gate_parameter(gate: str, qubits: list[int], name: str) -> Any:
    def edit(props: dict[str, Any]) -> None:
        for g in props["gates"]:
            if g["gate"] == gate and g["qubits"] == qubits:
                g["parameters"] = [p for p in g["parameters"] if p["name"] != name]

    return edit


STAR = "CX 0 1 0 2 0 3 0 4\nM 0 1 2 3 4\n"  # qubit 0 needs 4 neighbours: no placement exists

INVALID = [
    ("circuit-missing", None, None, "circuit file not found"),
    ("circuit-garbage", "H 0 0 nonsense_target\n", None, "not a valid Stim circuit"),
    ("circuit-empty", "", None, "acts on no qubits"),
    ("props-missing-T1", None, _drop_qubit_field("T1", 3), "'T1' for qubit 3"),
    ("props-missing-readout", None, _drop_qubit_field("readout_error", 0), "'readout_error'"),
    ("props-missing-cx", None, _drop_gate("cx", [1, 2]), "'cx' calibration for qubits (1, 2)"),
    ("props-missing-sx", None, _drop_gate("sx", [4]), "'sx' calibration for qubit 4"),
    (
        "props-incomplete-cx",
        None,
        _drop_gate_parameter("cx", [0, 1], "gate_error"),
        "'gate_error' for cx on (0, 1)",
    ),
    ("props-not-json", None, "not json", "not valid JSON"),
    ("no-valid-placement", STAR, None, "no SWAP-free placement exists"),
]


@pytest.mark.parametrize(
    ("case", "circuit_text", "props_edit", "expected"), INVALID, ids=[c[0] for c in INVALID]
)
def test_t2_i4_invalid_input(
    example: Path, case: str, circuit_text: str | None, props_edit: Any, expected: str
) -> None:
    circuit = example / CIRCUIT
    if case == "circuit-missing":
        circuit.unlink()
    elif circuit_text is not None:
        circuit.write_text(circuit_text)
    if isinstance(props_edit, str):
        (example / PROPS).write_text(props_edit)
    elif props_edit is not None:
        _edit_props(example, props_edit)
    runs = [
        nac(
            "place",
            "--circuit",
            CIRCUIT,
            "--conf",
            CONF,
            "--props",
            PROPS,
            "--out",
            "o",
            cwd=example,
        )
    ]
    if case.startswith("props"):
        runs.append(nac("convert", "--conf", CONF, "--props", PROPS, "--out", "d", cwd=example))
    for result in runs:
        assert result.returncode != 0
        assert "Traceback" not in result.stderr
        lines = result.stderr.strip().splitlines()
        assert len(lines) == 1, result.stderr
        assert lines[0].startswith("nac: error: ") and expected in lines[0], lines[0]
    debug = nac(
        "--debug",
        "place",
        "--circuit",
        CIRCUIT,
        "--conf",
        CONF,
        "--props",
        PROPS,
        "--out",
        "o",
        cwd=example,
    )
    assert debug.returncode != 0 and "Traceback" in debug.stderr


def _options(parser: argparse.ArgumentParser) -> list[str]:
    return [
        opt
        for action in parser._actions
        for opt in action.option_strings
        if not isinstance(action, argparse._SubParsersAction)
    ]


def test_t2_i6_help_lists_every_command_and_option(tmp_path: Path) -> None:
    parser = build_parser()
    top = nac("--help", cwd=tmp_path)
    assert top.returncode == 0
    for opt in _options(parser):
        assert opt in top.stdout, opt
    (subparsers,) = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    assert set(subparsers.choices) == {"convert", "place", "example"}
    for name, sub in subparsers.choices.items():
        assert name in top.stdout
        text = nac(name, "--help", cwd=tmp_path).stdout
        for opt in _options(sub):
            assert opt in text, (name, opt)
        for action in sub._actions:
            for choice in action.choices or ():
                assert str(choice) in text, (name, choice)


def test_r_i6_commands_need_no_qiskit_decoder_or_simulation(example: Path) -> None:
    """Both commands run with Qiskit and PyMatching unimportable and every sampler raising."""

    script = textwrap.dedent(
        """
        import sys, importlib.abc
        class Block(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path, target=None):
                if name.split('.')[0] in ('qiskit', 'qiskit_ibm_runtime', 'pymatching'):
                    raise ImportError(name + ' is blocked in this test')
                return None
        sys.meta_path.insert(0, Block())
        import stim
        def refuse(*args, **kwargs):
            raise AssertionError('sampling is not allowed')
        for name in ('compile_sampler', 'compile_detector_sampler', 'compile_m2d_converter'):
            setattr(stim.Circuit, name, refuse)
        from noise_aware_compilation.cli import main
        assert main(['convert', '--conf', sys.argv[1], '--props', sys.argv[2], '--out', 'd']) == 0
        assert main(['place', '--circuit', sys.argv[3], '--device', 'd', '--out', 'p']) == 0
        assert main(['place', '--circuit', sys.argv[3], '--device', 'hanoi', '--out', 'h']) == 0
        assert not any(m.split('.')[0] in ('qiskit', 'pymatching') for m in sys.modules)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script, CONF, PROPS, CIRCUIT],
        cwd=example,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def _readme_example() -> str:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    match = re.search(
        r"<!-- worked-example:start -->\s*```bash\n(.*?)```\s*<!-- worked-example:end -->",
        readme,
        re.DOTALL,
    )
    assert match, "README has no worked example block"
    return match.group(1)


def test_t2_i5_readme_example_runs(tmp_path: Path) -> None:
    """The README's worked example, minus the install line, runs as written.

    The install itself is checked in a fresh virtual environment by
    ``scripts/check_readme_example.sh``.
    """

    lines = [
        line
        for line in _readme_example().splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "python -m pip", "pip "))
    ]
    nac_exe = shutil.which("nac") or str(Path(sys.executable).parent / "nac")
    env = {**os.environ, "PATH": f"{Path(nac_exe).parent}{os.pathsep}{os.environ['PATH']}"}
    for line in lines:
        result = subprocess.run(
            line, shell=True, cwd=tmp_path, env=env, capture_output=True, text=True
        )
        assert result.returncode == 0, (line, result.stderr)
    assert (tmp_path / "placed" / MAPPING_FILE).is_file()
