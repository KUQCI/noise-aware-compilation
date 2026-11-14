"""v2 Qiskit layout baseline: T2-B2, T2-B3, T2-B4 (skipped where Qiskit is not installed)."""

from __future__ import annotations

import itertools
import math
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from conftest import CIRCUITS, circuit_for

from noise_aware_compilation.devices import StoredDevice
from noise_aware_compilation.placement import enumerate_placements

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    ("device", "label", "distance", "basis", "expected"),
    [c for c in CIRCUITS if c[1] != "C"],
    ids=[f"{d}-{c}" for d, c, *_ in CIRCUITS if c != "C"],
)
def test_t2_b2_qiskit_placements_in_candidate_set(
    device: str,
    label: str,
    distance: int,
    basis: str,
    expected: int,
    devices: dict[str, StoredDevice],
) -> None:
    pytest.importorskip("qiskit")
    from nac_bench.qiskit_layout import qiskit_layout

    stored = devices[device]
    circuit = circuit_for(distance, basis)
    choice = qiskit_layout(circuit, stored.device, stored.noise)
    assert choice.failure is None
    assert choice.mapping in enumerate_placements(circuit, stored.device)
    again = qiskit_layout(circuit, stored.device, stored.noise)
    assert again == choice  # deterministic (seed -1, no time limit)


def test_t2_b2_outside_candidates_is_a_failure(devices: dict[str, StoredDevice]) -> None:
    pytest.importorskip("qiskit")
    from nac_bench.qiskit_layout import qiskit_layout

    stored = devices["jakarta"]
    circuit = circuit_for(3, "Z")
    candidates = enumerate_placements(circuit, stored.device)
    chosen = qiskit_layout(circuit, stored.device, stored.noise).mapping
    restricted = [m for m in candidates if m != chosen]
    result = qiskit_layout(circuit, stored.device, stored.noise, restricted)
    assert result.failure == "outside candidate set" and result.mapping == chosen


def test_t2_b3_qiskit_target_carries_the_same_noise(devices: dict[str, StoredDevice]) -> None:
    pytest.importorskip("qiskit")
    from nac_bench.qiskit_layout import build_target, qiskit_layout

    stored = devices["hanoi"]
    circuit = circuit_for(5, "Z")
    target = build_target(stored.device, stored.noise)
    mapping = qiskit_layout(circuit, stored.device, stored.noise).mapping
    assert mapping is not None
    snap = stored.snapshot
    for q in mapping:
        params = snap.qubit_parameters[q]
        assert target["measure"][(q,)].error == params["readout_flip_probability"]
        assert target["reset"][(q,)].error == params["reset_flip_probability"]
        h = next(
            p for k, p in snap.operation_parameters.items() if k.gate == "H" and k.targets == (q,)
        )
        assert target["h"][(q,)].error == math.fsum(v for k, v in h.items() if k.startswith("p_"))
    for a, b in itertools.pairwise(mapping):
        for pair in ((a, b), (b, a)):
            cx = next(
                p
                for k, p in snap.operation_parameters.items()
                if k.gate == "CX" and k.targets == pair
            )
            total = math.fsum(v for k, v in cx.items() if k.startswith("p_"))
            assert target["cx"][pair].error == total
            assert total == pytest.approx(1.25 * cx["source_gate_error"], rel=1e-12)


def test_t2_b4_package_and_cli_without_qiskit(tmp_path: Path) -> None:
    """With Qiskit unimportable: the package's v2 modules import and both CLI commands run.

    The rest of the suite runs without Qiskit too: the Qiskit tests skip through
    ``pytest.importorskip`` and nothing else imports it (checked by
    ``scripts/check_no_qiskit.sh`` and ``scripts/check_readme_example.sh``).
    """

    script = textwrap.dedent(
        """
        import sys, importlib.abc
        class Block(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path, target=None):
                if name.split('.')[0] in ('qiskit', 'qiskit_ibm_runtime'):
                    raise ImportError(name + ' is blocked')
                return None
        sys.meta_path.insert(0, Block())
        import noise_aware_compilation.drift, noise_aware_compilation.baselines
        import noise_aware_compilation.decoder_graph, noise_aware_compilation.api
        from noise_aware_compilation.cli import main
        assert main(['example', '--out', 'ex']) == 0
        assert main(['convert', '--conf', 'ex/conf_jakarta.json',
                     '--props', 'ex/props_jakarta.json', '--out', 'dev']) == 0
        assert main(['place', '--circuit', 'ex/repetition_d3_bitflip.stim', '--device', 'dev',
                     '--scorer', 'decoder-graph', '--out', 'p']) == 0
        assert not any(m.split('.')[0] == 'qiskit' for m in sys.modules)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
