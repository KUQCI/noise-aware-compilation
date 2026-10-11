"""T-I4 (backend agreement), T-I5 (decoder builds), T-I8 (no Qiskit at runtime), T-R1."""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import nove
import numpy as np
import pytest
import tomllib
from conftest import CIRCUITS, circuit_for

from noise_aware_compilation.benchmark import expand_cases, load_config, run_case
from noise_aware_compilation.devices import StoredDevice
from noise_aware_compilation.evaluation import PlacementEvaluator
from noise_aware_compilation.placement import enumerate_placements, fixed_placement, relabel_circuit
from noise_aware_compilation.stats import Z99

ROOT = Path(__file__).resolve().parent.parent


def test_t_i4_stim_and_python_backends_agree(devices: dict[str, StoredDevice]) -> None:
    stored = devices["jakarta"]
    circuit = circuit_for(3, "Z")
    mapping = fixed_placement(circuit, stored.device)
    ev = PlacementEvaluator(circuit, stored.device, stored.noise, mapping)
    shots = 20_000
    rates = []
    for backend in ("stim", "python"):
        result = ev.experiment.run(shots=shots, backend=backend, seed=99)
        pred = ev.decoder.decode_batch(result.detection_events)
        fails = int(np.any(pred != result.observables, axis=1).sum())
        rates.append(fails / shots)
    p1, p2 = rates
    se = math.sqrt(p1 * (1 - p1) / shots + p2 * (1 - p2) / shots)
    assert abs(p1 - p2) <= Z99 * se, rates


@pytest.mark.parametrize(("device", "label", "distance", "basis", "expected"), CIRCUITS)
def test_t_i5_decoder_builds_for_every_placement(
    device: str,
    label: str,
    distance: int,
    basis: str,
    expected: int,
    devices: dict[str, StoredDevice],
) -> None:
    stored = devices[device]
    circuit = circuit_for(distance, basis)
    for mapping in enumerate_placements(circuit, stored.device):
        ev = PlacementEvaluator(circuit, stored.device, stored.noise, mapping)
        assert ev.decoder.decoder_model_mismatch  # approximate model recorded on the decoder
        assert ev.dem.num_detectors == circuit.num_detectors


def test_t_i8_runtime_dependencies_exclude_qiskit() -> None:
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text())
    runtime = " ".join(meta["project"]["dependencies"]).lower()
    assert "qiskit" not in runtime
    src = "\n".join(p.read_text() for p in (ROOT / "src").rglob("*.py"))
    assert not re.search(r"^\s*(import|from)\s+qiskit", src, re.MULTILINE)


def test_t_i8_imports_and_runs_with_qiskit_blocked(tmp_path: Path) -> None:
    script = textwrap.dedent(
        f"""
        import sys, importlib.abc
        class Block(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path, target=None):
                if name.split('.')[0].startswith('qiskit'):
                    raise ImportError('qiskit is blocked in this test')
                return None
        sys.meta_path.insert(0, Block())
        from noise_aware_compilation.benchmark import main
        rc = main([{str(ROOT / "configs" / "benchmark.json")!r}, '--only', 'jakarta-A1-N1',
                   '--out', {str(tmp_path)!r}, '--workers', '1', '--allow-dirty'])
        assert rc == 0
        assert not any(m.startswith('qiskit') for m in sys.modules)
        """
    )
    subprocess.run([sys.executable, "-c", script], check=True, cwd=ROOT)
    data = json.loads((tmp_path / "jakarta-A1-N1.json").read_text())
    assert len(data["placements"]) == 8


@pytest.mark.results
def test_t_r1_same_seed_bit_identical(tmp_path: Path) -> None:
    committed = ROOT / "results" / "runs" / "jakarta-A3-N2.json"
    if not committed.exists():
        pytest.skip("results not generated yet")
    config = load_config(ROOT / "configs" / "benchmark.json")
    spec = next(c for c in expand_cases(config) if c.case_id == "jakarta-A3-N2")
    path = run_case(spec, config, workers=2, out_dir=tmp_path)
    new = json.loads(path.read_text())
    old = json.loads(committed.read_text())
    for a, b in zip(old["placements"], new["placements"], strict=True):
        assert a["mapping"] == b["mapping"]
        assert a["per_seed"] == b["per_seed"]


def test_evaluator_zero_noise_has_no_failures(devices: dict[str, StoredDevice]) -> None:
    from noise_aware_compilation.conditions import zero_noise

    stored = devices["hanoi"]
    circuit = circuit_for(5, "Z")
    mapping = fixed_placement(circuit, stored.device)
    ev = PlacementEvaluator(circuit, stored.device, zero_noise(stored.noise), mapping)
    counts = ev.run_chunk(5000, 3)
    assert counts.logical_failures == 0 and counts.detector_fires == 0
    assert relabel_circuit(circuit, mapping).num_qubits == max(mapping) + 1
    assert isinstance(ev.experiment, nove.Experiment)
