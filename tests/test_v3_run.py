"""The v3 held-out runner on a development device (T3-C4, T3-C5, T3-R1, T3-R3).

FakeMarrakesh d=5 stands in for a square-lattice d=5 case, so the R3-C5 best-found set is
exercised, with small shots and small top and random sets.
"""

from __future__ import annotations

import hashlib
import io
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pytest

pytest.importorskip("qiskit")
pytest.importorskip("mapomatic")

from nac_bench import run_v3
from nac_bench.baselines_v3 import mapomatic_version
from nac_bench.qiskit_layout import qiskit_version
from noise_aware_compilation.placement import _rounded

FAST_SEARCH = {
    "prefilter": 10,
    "starts": 10,
    "spread": 0,
    "random": 20,
    "seed": 20261108,
    "max_shift": 2,
    "max_evaluations": 20_000,
}
RULE = {
    "pilot_shots": 1_000,
    "min_shots_per_seed": 2_000,
    "max_shots_per_seed": 8_000,
    "expected_failures": 30,
}


def _config(tmp_path: Path) -> dict[str, Any]:
    freeze = tmp_path / "freeze.json"
    freeze.write_text(
        json.dumps({"scaled_score": {"k": 10, "step": 0.05}, "fast_search": FAST_SEARCH})
    )
    return {
        "family": "v3-headline",
        "settings": {"readout": "reset = readout", "zero": "reset 0"},
        "settings_order": ["readout", "zero"],
        "headline_setting": "readout",
        "headline_references": ["naive", "lowest-summed-error", "qiskit-default-l1", "mapomatic"],
        "devices": {
            "marrakesh": {"lattice": "square", "synthetic_calibration": False, "circuits": ["D5"]}
        },
        "circuits": {"D5": {"kind": "repetition", "distance": 5, "rounds": 3, "basis": "Z"}},
        "base_seeds": [11, 12, 13],
        "pilot_seed": 10,
        "sample_seed": 14,
        "seed_transpiler": 15,
        "shots_rule": RULE,
        "best_found": {"top": 3, "random": 5},
        "nove_checks_per_case": 3,
        "scaled_score": {"k": 10, "step": 0.05},
        "fast_search": FAST_SEARCH,
        "score_freeze_file": str(freeze),
        "qiskit_version": qiskit_version(),
        "mapomatic_version": mapomatic_version(),
        "_config_sha256": "test",
    }


def _run(tmp_path: Path, name: str) -> tuple[dict[str, Any], Path]:
    config = _config(tmp_path)
    case = next(c for c in run_v3.cases(config) if c["case_id"] == "v3-readout-marrakesh-D5")
    out = tmp_path / name
    with ProcessPoolExecutor(max_workers=2) as pool:
        path = run_v3.run_case(case, config, pool, workers=2, out_dir=out, machine_label="test")
    return json.loads(path.read_text(encoding="utf-8")), out


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict[str, Any], dict[str, Any], Path]:
    tmp = tmp_path_factory.mktemp("run_v3")
    first, out = _run(tmp, "first")
    second, _ = _run(tmp, "second")
    return first, second, out


def test_cases_cover_settings_and_names(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ids = [c["case_id"] for c in run_v3.cases(config)]
    assert ids == ["v3-readout-marrakesh-D5", "v3-zero-marrakesh-D5"]


def test_main_shots_rule() -> None:
    rule = {
        "min_shots_per_seed": 200_000,
        "max_shots_per_seed": 25_600_000,
        "expected_failures": 400,
    }
    assert run_v3.main_shots(0, 100_000, rule, 3) == 25_600_000
    assert run_v3.main_shots(1_000, 100_000, rule, 3) == 200_000  # p = 0.01: 6,000 >= 400
    # p = 1e-4: 3 x n x 1e-4 >= 400 needs n >= 1.33M, so 1.6M
    assert run_v3.main_shots(10, 100_000, rule, 3) == 1_600_000
    assert run_v3.main_shots(1, 1_000_000, rule, 3) == 25_600_000  # the cap


def test_t3_c4_choices_have_the_best_score(
    runs: tuple[dict[str, Any], dict[str, Any], Path],
) -> None:
    run, _second, out = runs
    data = (out / "scores" / "v3-readout-marrakesh-D5.npz").read_bytes()
    assert hashlib.sha256(data).hexdigest() == run["score_file"]["sha256"]
    arrays = np.load(io.BytesIO(data))
    placements = [tuple(int(q) for q in row) for row in arrays["placements"]]
    assert placements == sorted(placements) and len(placements) == run["placements"]
    for method, name, key in (
        ("decoder-graph", "decoder_graph", _rounded),
        ("v1", "v1", _rounded),
        ("lowest-summed-error", "summed_error", float),
    ):
        values = arrays[name]
        best = min(range(len(values)), key=lambda i: (key(float(values[i])), placements[i]))
        assert run["methods"][method]["index"] == best
        assert run["methods"][method]["ranks"][name] == 1
    for method, entry in run["methods"].items():
        if entry["index"] is not None:
            assert tuple(entry["mapping"]) == placements[entry["index"]], method
    assert run["methods"]["naive"]["index"] == 0
    assert run["nove_checks"]["placements_checked"] == 3


def test_r3_c5_simulated_set(runs: tuple[dict[str, Any], dict[str, Any], Path]) -> None:
    run, _second, _out = runs
    simulated = {row["index"] for row in run["simulated"]}
    chosen = {e["index"] for e in run["methods"].values() if e["index"] is not None}
    top = run["simulated_set"]["top"]
    assert chosen <= simulated
    for indices in top.values():
        assert len(indices) == 3 and set(indices) <= simulated
    assert len(run["simulated_set"]["random_sample"]) == 5
    assert set(run["simulated_set"]["random_sample"]) <= simulated
    union = chosen.union(*map(set, top.values()), run["simulated_set"]["random_sample"])
    assert simulated == union


def test_t3_c5_shots_follow_the_pilot(runs: tuple[dict[str, Any], dict[str, Any], Path]) -> None:
    run, _second, _out = runs
    for row in run["simulated"]:
        pilot = row["pilot"]
        assert pilot["shots"] == RULE["pilot_shots"]
        n = run_v3.main_shots(pilot["logical_failures"], pilot["shots"], RULE, 3)
        assert row["shots_per_seed"] == n
        assert [s["shots"] for s in row["per_seed"]] == [n, n, n]
        assert [s["base_seed"] for s in row["per_seed"]] == [11, 12, 13]


def test_t3_r1_rerun_is_identical(runs: tuple[dict[str, Any], dict[str, Any], Path]) -> None:
    first, second, _out = runs
    for key in ("methods", "simulated", "simulated_set", "score_summary"):
        assert _untimed(first[key]) == _untimed(second[key]), key
    assert first["score_file"]["sha256"] == second["score_file"]["sha256"]


def _untimed(value: Any) -> Any:
    """``value`` without its timings (keys ending in ``seconds``)."""

    if isinstance(value, dict):
        return {k: _untimed(v) for k, v in value.items() if not k.endswith("seconds")}
    if isinstance(value, list):
        return [_untimed(v) for v in value]
    return value


def test_t3_r3_provenance(runs: tuple[dict[str, Any], dict[str, Any], Path]) -> None:
    run, _second, _out = runs
    prov = run["provenance"]
    for field in (
        "nove_version",
        "stim_version",
        "package_git_commit",
        "package_source_sha256",
        "lock_file_sha256",
        "machine",
        "reset_setting",
        "stage_seconds",
    ):
        assert field in prov, field
    assert prov["reset_setting"] == "readout"
    assert set(prov["stage_seconds"]) >= {"scoring", "pilot", "main", "fast_search"}


def test_frozen_parameters_must_match(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config["fast_search"] = {**FAST_SEARCH, "starts": 30}
    with pytest.raises(RuntimeError, match="F3 freeze"):
        run_v3._check_frozen(config)


def test_no_valid_placement_is_recorded(tmp_path: Path) -> None:
    """FakeMiami has no d=5 surface-code embedding (P2-11): the case says so and leaves."""

    config = _config(tmp_path)
    config["devices"] = {
        "miami": {"lattice": "square", "synthetic_calibration": False, "circuits": ["S5"]}
    }
    config["circuits"] = {"S5": {"kind": "surface", "distance": 5, "rounds": 5}}
    case = run_v3.cases(config)[0]
    with ProcessPoolExecutor(max_workers=1) as pool:
        path = run_v3.run_case(case, config, pool, workers=1, out_dir=tmp_path, machine_label="t")
    run = json.loads(path.read_text(encoding="utf-8"))
    assert run["no_valid_placement"] is True
    assert run["case"]["case_id"] == "v3-readout-miami-S5"
    assert {"package_git_commit", "lock_file_sha256", "machine"} <= set(run["provenance"])
