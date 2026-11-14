"""The v3 held-out runner on a development device (T3-C4, T3-C5, T3-C8, T3-R1, T3-R3).

FakeMarrakesh d=5 stands in for a confirmatory square-lattice d=5 case, so the R3-C5
best-found set, the family placements' shots, the sensitivity runs (R3-C10), the grading
variants (R3-C15), the Qiskit seed sweep (R3-C14), the detection rate (R3-C16) and the
directional setting (R3-C13) are exercised, with small shots and small top and random sets.
Revision 6 adds the failure records and the chunk-by-chunk re-decode check.
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
from noise_aware_compilation.placement import _rounded, enumerate_placements

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
    "family": {"expected_failures": 60, "max_shots_per_seed": 16_000},
    "other": {"expected_failures": 30, "max_shots_per_seed": 8_000},
    "family_placement_roles": ["confirmatory", "synthetic"],
}
REFERENCES = ["naive", "lowest-summed-error", "qiskit-default-l2", "mapomatic"]
ISOLATION = {
    "repetition": ["idle-aware-summed-error", "v1"],
    "surface": ["idle-aware-summed-error"],
}
VARIANTS = {
    "applies_to": "test",
    "variants": {
        "idle-z-x2": {"kinds": ["repetition", "surface"]},
        "lognormal-0.1": {"kinds": ["repetition", "surface"], "delta": 0.1, "draws": 1},
        "directional": {"kinds": ["repetition"], "bases": ["Z"]},
    },
}


class SerialPool:
    """A pool that runs every task in this process, so module patches reach the tasks."""

    def map(self, fn: Any, items: Any, chunksize: int = 1) -> Any:
        return [fn(item) for item in items]


def _config(tmp_path: Path) -> dict[str, Any]:
    freeze = tmp_path / "freeze.json"
    freeze.write_text(
        json.dumps({"surface_scaled_score": {"k": 10, "step": 0.05}, "fast_search": FAST_SEARCH})
    )
    return {
        "spec_revision": run_v3.SPEC_REVISION,
        "family": "v3-headline",
        "settings": {
            "readout": "reset = readout",
            "zero": "reset 0",
            "directional": "P(1|0)",
        },
        "settings_order": ["readout", "zero", "directional"],
        "setting_scope": {
            "directional": {
                "devices": ["marrakesh"],
                "circuits": ["D5"],
                "simulated_set": "choices",
            }
        },
        "headline_setting": "readout",
        "headline_references": REFERENCES,
        "isolation_references": ISOLATION,
        "devices": {
            "marrakesh": {
                "lattice": "square",
                "role": "confirmatory",
                "synthetic_calibration": False,
                "circuits": ["D5"],
            }
        },
        "circuits": {"D5": {"kind": "repetition", "distance": 5, "rounds": 3, "basis": "Z"}},
        "base_seeds": [11, 12, 13],
        "pilot_seed": 10,
        "sample_seed": 14,
        "seed_transpiler": 15,
        "shots_rule": RULE,
        "best_found": {
            "top": 3,
            "random": 5,
            "top_by": ["decoder_graph", "v1", "summed_error", "mapomatic"],
            "every_placement_cases": [],
        },
        "qiskit_seed_sweep": {"level": 2, "seeds": [16, 17]},
        "grading_variants": VARIANTS,
        "nove_checks_per_case": 3,
        "surface_scaled_score": {"k": 10, "step": 0.05},
        "surface_k_sensitivity": [2, 5],
        "sensitivity": {"rounds_equal_distance": {"circuits": ["D5", "D7"]}},
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
    assert ids == [
        "v3-readout-marrakesh-D5",
        "v3-zero-marrakesh-D5",
        "v3-directional-marrakesh-D5",
    ]
    config["setting_scope"]["directional"]["circuits"] = ["A"]
    assert [c["case_id"] for c in run_v3.cases(config)][-1] == "v3-zero-marrakesh-D5"


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
        ("idle-aware-summed-error", "idle_aware_summed_error", float),
        ("detection-rate", "detection_rate", float),
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
    sweep = {i for i in run["methods"]["qiskit-default-l2"]["seed_sweep_indices"] if i is not None}
    top = run["simulated_set"]["top"]
    assert chosen <= simulated and sweep <= simulated
    assert set(top) == {"decoder_graph", "v1", "summed_error", "mapomatic"}
    for name, indices in top.items():
        assert set(indices) <= simulated
        if name != "mapomatic":
            assert len(indices) == 3
    assert len(run["simulated_set"]["random_sample"]) == 5
    assert set(run["simulated_set"]["random_sample"]) <= simulated
    union = chosen.union(sweep, *map(set, top.values()), run["simulated_set"]["random_sample"])
    assert simulated == union


def test_r3_c14_seed_sweep_is_recorded(runs: tuple[dict[str, Any], dict[str, Any], Path]) -> None:
    run, _second, _out = runs
    level2 = run["methods"]["qiskit-default-l2"]
    sweep = level2["detail"]["seed_sweep_runs"]
    assert [r["seed_transpiler"] for r in sweep] == [16, 17]
    assert len(level2["seed_sweep_indices"]) == 2
    assert run["methods"]["qiskit-default-l1"]["detail"]["seed_sweep_runs"] == []


def test_t3_c5_shots_follow_the_pilot(runs: tuple[dict[str, Any], dict[str, Any], Path]) -> None:
    run, _second, _out = runs
    names = ["decoder-graph", *REFERENCES, *ISOLATION["repetition"]]
    family = {run["methods"][m]["index"] for m in names}
    family.discard(None)
    assert set(run["family_placements"]) == family
    for row in run["simulated"]:
        pilot = row["pilot"]
        assert pilot["shots"] == RULE["pilot_shots"]
        assert row["family_placement"] == (row["index"] in family)
        tier = run_v3.stage_rule(RULE, row["family_placement"])
        n = run_v3.main_shots(pilot["logical_failures"], pilot["shots"], tier, 3)
        assert row["shots_per_seed"] == n
        assert [s["shots"] for s in row["per_seed"]] == [n, n, n]
        assert [s["base_seed"] for s in row["per_seed"]] == [11, 12, 13]


def test_stage_rule_tiers() -> None:
    family = run_v3.stage_rule(RULE, True)
    other = run_v3.stage_rule(RULE, False)
    assert family == {
        "min_shots_per_seed": 2_000,
        "max_shots_per_seed": 16_000,
        "expected_failures": 60,
    }
    assert other["max_shots_per_seed"] == 8_000 and other["expected_failures"] == 30


def test_r3_c10_sensitivity_runs(runs: tuple[dict[str, Any], dict[str, Any], Path]) -> None:
    run, _second, _out = runs
    sens = run["sensitivity"]
    family = sorted(run["family_placements"])
    main = {row["index"]: row for row in run["simulated"]}
    redecoded = sens["correlated_matching"]["rows"]
    assert [r["index"] for r in redecoded] == family
    for r in redecoded:
        assert r["reproduced"] is True
        assert [s["shots"] for s in r["per_seed"]] == [
            s["shots"] for s in main[r["index"]]["per_seed"]
        ]
        for s, m in zip(r["per_seed"], main[r["index"]]["per_seed"], strict=True):
            assert s["chunks_reproduced"] == len(m["chunk_failures"])
            assert sum(m["chunk_failures"]) == m["logical_failures"]
    longer = sens["rounds_equal_distance"]
    assert longer["rounds"] == 5
    assert [r["index"] for r in longer["rows"]] == family
    for r in longer["rows"]:
        assert r["family_placement"] is True
        tier = run_v3.stage_rule(RULE, True)
        n = run_v3.main_shots(r["pilot"]["logical_failures"], r["pilot"]["shots"], tier, 3)
        assert r["shots_per_seed"] == n
    variants = sens["grading_variants"]["variants"]
    assert set(variants) == {"idle-z-x2", "lognormal-0.1-draw0", "directional"}
    other = run_v3.stage_rule(RULE, False)
    for rows in variants.values():
        assert [r["index"] for r in rows["rows"]] == family
        for r in rows["rows"]:
            pilot = main[r["index"]]["pilot"]
            n = run_v3.main_shots(pilot["logical_failures"], pilot["shots"], other, 3)
            assert [s["shots"] for s in r["per_seed"]] == [n, n, n]


def test_t3_c8_redecode_compares_every_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With 500-shot chunks the re-decode checks each chunk, so a main stage whose chunk
    counts are moved between chunks (same total per seed) is refused."""

    monkeypatch.setattr(run_v3, "CHUNK_SHOTS", 500)
    config = _config(tmp_path)
    key = ("repetition", 3, 3, "Z")
    placements = enumerate_placements(
        run_v3._circuit(key), run_v3._models("marrakesh", "readout")[0].device
    )
    items = [(key, placements[0], "a", True), (key, placements[1], "b", True)]
    pool = SerialPool()
    rows, _seconds = run_v3.simulate(pool, "marrakesh", "readout", "t", items, config)  # type: ignore[arg-type]
    for row in rows:
        for seed in row["per_seed"]:
            assert len(seed["chunk_failures"]) == seed["shots"] // 500 > 1
    indexed = [{"index": k, **row} for k, row in enumerate(rows)]
    short = [item[:3] for item in items]
    out = run_v3.redecode(pool, "marrakesh", "readout", "t", short, indexed, config)  # type: ignore[arg-type]
    assert all(r["reproduced"] for r in out)
    seed = indexed[0]["per_seed"][0]
    chunks = seed["chunk_failures"]
    moved = next(i for i in range(len(chunks) - 1) if chunks[i] != chunks[i + 1])
    chunks[moved], chunks[moved + 1] = chunks[moved + 1], chunks[moved]
    with pytest.raises(RuntimeError, match="chunks"):
        run_v3.redecode(pool, "marrakesh", "readout", "t", short, indexed, config)  # type: ignore[arg-type]


def test_failed_case_writes_a_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Revision 6: a case that raises leaves a failure record naming its stage, and a re-run
    moves it aside instead of overwriting it."""

    config = _config(tmp_path)
    config["devices"]["marrakesh"]["circuits"] = ["A"]
    config["circuits"]["A"] = {"kind": "repetition", "distance": 3, "rounds": 3, "basis": "Z"}
    case = next(c for c in run_v3.cases(config) if c["case_id"] == "v3-zero-marrakesh-A")

    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise ValueError("planted")

    monkeypatch.setattr(run_v3, "qiskit_layout", boom)
    for attempt in range(2):
        with ProcessPoolExecutor(max_workers=1) as pool, pytest.raises(run_v3.CaseFailed):
            run_v3.run_case(case, config, pool, workers=1, out_dir=tmp_path, machine_label="t")
        record = json.loads((tmp_path / "runs" / "v3-zero-marrakesh-A.json").read_text())
        assert record["failed"] is True and record["stage"] == "qiskit_vf2"
        assert "planted" in record["error"]
        assert {"package_git_commit", "lock_file_sha256", "machine"} <= set(record["provenance"])
        assert len(list((tmp_path / "runs" / "failed").glob("*.json"))) == attempt
    assert not run_v3._existing_result(tmp_path / "runs" / "v3-zero-marrakesh-A.json")


def test_r3_c5_guard_raises_before_the_main_stage() -> None:
    """Revision 6: the every-placement guard projects the main stage from the pilot stage and
    stops before it when the projection is over the limit."""

    config = {"shots_rule": RULE, "pilot_seed": 10, "base_seeds": [11, 12, 13]}
    from noise_aware_compilation.circuits import repetition_code
    from noise_aware_compilation.devices import load_device

    key = ("repetition", 3, 3, "Z")
    device = load_device("marrakesh").device
    placements = enumerate_placements(repetition_code(3, 3, "Z", "CZ"), device)[:2]
    items = [(key, m, ",".join(map(str, m)), False) for m in placements]
    with pytest.raises(run_v3.OverLimit):
        run_v3.simulate(
            SerialPool(),  # type: ignore[arg-type]
            "marrakesh",
            "readout",
            "t",
            items,
            config,
            limit_seconds=0,
        )
    rows, _seconds = run_v3.simulate(
        SerialPool(),  # type: ignore[arg-type]
        "marrakesh",
        "readout",
        "t",
        items,
        config,
        limit_seconds=1e9,
    )
    assert len(rows) == 2


def test_r3_c5_guard_falls_back_to_the_best_found_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Revision 6: a named every-placement case whose main stage projects over the limit is
    simulated on the best-found union instead, and the run file says so."""

    config = _config(tmp_path)
    config["devices"]["marrakesh"]["role"] = "seen"  # no family shots or sensitivity runs
    config["best_found"]["every_placement_cases"] = ["marrakesh-D5"]
    config["best_found"]["every_placement_limit_hours"] = 1.0
    real = run_v3.simulate
    limits: list[float | None] = []

    def guarded(*args: Any, limit_seconds: float | None = None, **kwargs: Any) -> Any:
        limits.append(limit_seconds)
        if limit_seconds is not None:
            raise run_v3.OverLimit(7200.0, limit_seconds)
        return real(*args, **kwargs)

    monkeypatch.setattr(run_v3, "simulate", guarded)
    case = next(c for c in run_v3.cases(config) if c["case_id"] == "v3-readout-marrakesh-D5")
    with ProcessPoolExecutor(max_workers=2) as pool:
        path = run_v3.run_case(case, config, pool, workers=2, out_dir=tmp_path, machine_label="t")
    run = json.loads(path.read_text(encoding="utf-8"))
    assert limits == [3600.0, None]
    chosen = run["simulated_set"]
    assert chosen["every_placement"] is False and chosen["rule"].startswith("R3-C5 best-found")
    assert chosen["every_placement_fallback"]["projected_main_wall_hours"] == 2.0
    assert len(chosen["random_sample"]) == 5 and chosen["top"] is not None
    simulated = {row["index"] for row in run["simulated"]}
    assert set(chosen["random_sample"]) <= simulated < set(range(run["placements"]))


def test_r3_c13_directional_grades_both_settings_choices(
    runs: tuple[dict[str, Any], dict[str, Any], Path],
) -> None:
    first, _second, out = runs
    config = _config(out.parent)
    case = next(c for c in run_v3.cases(config) if c["setting"] == "directional")
    with ProcessPoolExecutor(max_workers=2) as pool:
        path = run_v3.run_case(case, config, pool, workers=2, out_dir=out, machine_label="t")
    run = json.loads(path.read_text(encoding="utf-8"))
    assert run["setting"] == "directional" and run["sensitivity"] is None
    headline = run["simulated_set"]["headline_setting_choices"]
    assert headline["file"] == "v3-readout-marrakesh-D5.json"
    assert headline["choices"]["decoder-graph"] == first["methods"]["decoder-graph"]["index"]
    simulated = {row["index"] for row in run["simulated"]}
    chosen = {e["index"] for e in run["methods"].values() if e["index"] is not None}
    assert set(headline["choices"].values()) | chosen <= simulated
    assert run["simulated_set"]["every_placement"] is False
    assert run["simulated_set"]["random_sample"] == []
    # the headline setting's family placements, isolation references' choices included
    names = ["decoder-graph", *REFERENCES, *ISOLATION["repetition"]]
    family = {headline["choices"][n] for n in names if n in headline["choices"]}
    assert family <= set(run["family_placements"])


def test_t3_r1_rerun_is_identical(runs: tuple[dict[str, Any], dict[str, Any], Path]) -> None:
    first, second, _out = runs
    for key in ("methods", "simulated", "simulated_set", "score_summary", "sensitivity"):
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
    assert set(prov["stage_seconds"]) >= {"scoring", "pilot", "main", "fast_search", "sensitivity"}
    assert run["spec_revision"] == run_v3.SPEC_REVISION


def test_frozen_parameters_must_match(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config["fast_search"] = {**FAST_SEARCH, "starts": 30}
    with pytest.raises(RuntimeError, match="F3 freeze"):
        run_v3._check_frozen(config)


def test_no_valid_placement_is_recorded(tmp_path: Path) -> None:
    """FakeMiami has no d=5 surface-code embedding (P2-11): the case says so and leaves."""

    config = _config(tmp_path)
    config["devices"] = {
        "miami": {
            "lattice": "square",
            "role": "confirmatory",
            "synthetic_calibration": False,
            "circuits": ["S5"],
        }
    }
    config["circuits"] = {"S5": {"kind": "surface", "distance": 5, "rounds": 5}}
    case = run_v3.cases(config)[0]
    with ProcessPoolExecutor(max_workers=1) as pool:
        path = run_v3.run_case(case, config, pool, workers=1, out_dir=tmp_path, machine_label="t")
    run = json.loads(path.read_text(encoding="utf-8"))
    assert run["no_valid_placement"] is True
    assert run["case"]["case_id"] == "v3-readout-miami-S5"
    prov = run["provenance"]
    assert {"package_git_commit", "lock_file_sha256", "machine", "nac_bench_source_sha256"} <= set(
        prov
    )
    assert "enumeration" in prov["stage_seconds"] and prov["reset_setting"] == "readout"


def test_config_must_name_the_revision(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"spec_revision": 4}))
    with pytest.raises(ValueError, match="revision"):
        run_v3.load_config(path)


def test_sensitivity_covers_confirmatory_headline_cases_only(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config["devices"]["nighthawk"] = {
        "lattice": "square",
        "role": "synthetic",
        "synthetic_calibration": True,
        "circuits": ["D5"],
    }
    config["devices"]["kingston"] = {
        "lattice": "heavy-hex",
        "role": "seen",
        "synthetic_calibration": False,
        "circuits": ["D5"],
    }
    applies = {
        c["case_id"]: (
            run_v3.sensitivity_applies(c, config),
            run_v3.family_placements_apply(c, config),
        )
        for c in run_v3.cases(config)
    }
    assert applies["v3-readout-marrakesh-D5"] == (True, True)
    assert applies["v3-zero-marrakesh-D5"] == (False, True)
    assert applies["v3-readout-nighthawk-D5"] == (False, True)
    assert applies["v3-readout-kingston-D5"] == (False, False)


@pytest.fixture(scope="module")
def surface_run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """A d=3 surface code on FakeMiami, which has SWAP-free d=3 embeddings (R3-S3)."""

    tmp = tmp_path_factory.mktemp("run_v3_surface")
    config = _config(tmp)
    config["devices"] = {
        "miami": {
            "lattice": "square",
            "role": "confirmatory",
            "synthetic_calibration": False,
            "circuits": ["S3"],
        }
    }
    config["circuits"] = {"S3": {"kind": "surface", "distance": 3, "rounds": 3}}
    case = run_v3.cases(config)[0]
    with ProcessPoolExecutor(max_workers=2) as pool:
        path = run_v3.run_case(case, config, pool, workers=2, out_dir=tmp, machine_label="t")
    run: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return run


def test_surface_choices_and_sensitivity(surface_run: dict[str, Any]) -> None:
    run = surface_run
    assert run["no_valid_placement"] is False
    rows = run["simulated"]
    assert [r["index"] for r in rows] == list(range(run["placements"]))
    default = [r["index"] for r in rows if r["schedule"] == run["default_schedule"]]

    def best(name: str, among: list[int], key: Any) -> int:
        return min(
            among,
            key=lambda i: (
                key(rows[i]["scores"][name]),
                rows[i]["schedule"] != run["default_schedule"],
                rows[i]["mapping"],
            ),
        )

    every = list(range(len(rows)))
    assert run["methods"]["scaled"]["index"] == best("scaled", every, _rounded)
    assert run["methods"]["scaled-k2"]["index"] == best("scaled_k2", every, _rounded)
    assert run["methods"]["scaled-k5"]["index"] == best("scaled_k5", every, _rounded)
    assert run["methods"]["idle-aware-summed-error"]["index"] == best(
        "idle_aware_summed_error", every, float
    )
    assert run["methods"]["lowest-summed-error"]["index"] == best("summed_error", default, float)
    assert run["scaled_parameters"] == {"k": 10, "step": 0.05, "sensitivity_k": [2, 5]}
    names = ["scaled", *REFERENCES, *ISOLATION["surface"]]
    family = {run["methods"][m]["index"] for m in names} - {None}
    assert set(run["family_placements"]) == family
    assert run["methods"]["detection-rate"]["index"] == best("detection_rate", every, float)
    assert run["methods"]["scaled-default-schedule"]["index"] == best("scaled", default, _rounded)
    scaled_schedule = run["methods"]["scaled"]["schedule"]
    for name in ("naive", "lowest-summed-error", "qiskit-default-l2", "mapomatic"):
        twin = run["methods"].get(f"{name}@scaled-schedule")
        if run["methods"][name]["index"] is None:
            continue
        assert twin is not None and twin["schedule"] == scaled_schedule
        assert twin["mapping"] == run["methods"][name]["mapping"]
    assert [r["index"] for r in run["sensitivity"]["correlated_matching"]["rows"]] == sorted(family)
    assert run["sensitivity"]["rounds_equal_distance"] is None
    assert set(run["sensitivity"]["grading_variants"]["variants"]) == {
        "idle-z-x2",
        "lognormal-0.1-draw0",
    }
