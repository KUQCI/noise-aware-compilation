"""The held-out analysis on synthetic run files (T3-F2, section 7's claim rules, revision 6)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from nac_bench import analysis_v3

REFERENCES = ["naive", "lowest-summed-error", "qiskit-default-l2", "mapomatic"]
HEAVY_HEX = {"devices": ["boston", "pittsburgh"], "repetition_cases": 8, "t": 5}
#: synthetic run files carry no provenance and cover only some cases
LOOSE = analysis_v3.Checks(complete=False, provenance=False)
SQUARE = {"devices": ["berlin"], "repetition_cases": 4, "t": 3}


def _config(devices: dict[str, Any], layouts: dict[str, Any]) -> dict[str, Any]:
    return {
        "_config_sha256": "synthetic",
        "spec_revision": analysis_v3.SPEC_REVISION,
        "family": "v3-headline",
        "isolation_family": "v3-decoder-graph-isolation",
        "headline_setting": "readout",
        "settings_order": ["readout", "zero"],
        "headline_references": REFERENCES,
        "isolation_references": {
            "repetition": ["idle-aware-summed-error", "v1"],
            "surface": ["idle-aware-summed-error"],
        },
        "alpha": 0.05,
        "claim_rules": {"layouts": layouts},
        "devices": devices,
        "circuits": {
            "A": {"kind": "repetition", "rounds": 3},
            "B": {"kind": "repetition", "rounds": 3},
            "D5": {"kind": "repetition", "rounds": 3},
            "D7": {"kind": "repetition", "rounds": 3},
            "S5": {"kind": "surface", "rounds": 5},
        },
    }


def _device(lattice: str, circuits: list[str], role: str = "confirmatory") -> dict[str, Any]:
    return {"lattice": lattice, "role": role, "circuits": circuits}


def _run(
    case_id: str,
    device: str,
    lattice: str,
    label: str,
    rates: list[float],
    picks: dict[str, Any],
    *,
    role: str = "confirmatory",
    pilot: list[float] | None = None,
    sensitivity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    kind = "surface" if label == "S5" else "repetition"
    shots = 1_000_000
    pilot = pilot or rates
    simulated = [
        {
            "index": i,
            "scores": {"decoder_graph": float(i), "scaled": float(i)},
            "pilot": {"shots": 100_000, "logical_failures": round(pilot[i] * 100_000)},
            "per_seed": [
                {"base_seed": b, "logical_failures": round(rate * shots), "shots": shots}
                for b in (1, 2, 3)
            ],
        }
        for i, rate in enumerate(rates)
    ]
    methods: dict[str, Any] = {}
    for name, index in picks.items():
        if isinstance(index, str):  # a failure message
            methods[name] = {"index": None, "detail": {"failure": index}}
        else:
            methods[name] = {"index": index, "detail": {}}
    headline = "scaled" if kind == "surface" else "decoder-graph"
    family = sorted(
        {v for k, v in picks.items() if k in [headline, *REFERENCES] and isinstance(v, int)}
    )
    return {
        "config_file_sha256": "synthetic",
        "no_valid_placement": False,
        "case": {
            "case_id": case_id,
            "device": device,
            "lattice": lattice,
            "role": role,
            "synthetic_calibration": role == "synthetic",
            "setting": case_id.split("-")[1],
            "kind": kind,
            "circuit": {"label": label, "rounds": 5 if kind == "surface" else 3},
        },
        "simulated": simulated,
        "simulated_set": {"every_placement": True, "random_sample": []},
        "methods": methods,
        "family_placements": family,
        "sensitivity": sensitivity,
    }


def _write(tmp_path: Path, runs: list[dict[str, Any]]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    for r in runs:
        (tmp_path / f"{r['case']['case_id']}.json").write_text(json.dumps(r))
    return tmp_path


def _heavy_hex_runs(**extra: Any) -> list[dict[str, Any]]:
    runs = []
    for device in ("boston", "pittsburgh"):
        for label in ("A", "B", "D5", "D7"):
            # placement 0: the choice at 1%; 1: naive at 2%; 2: summed error at 1.001%
            picks: dict[str, int | str] = {
                "decoder-graph": 0,
                "naive": 1,
                "lowest-summed-error": 2,
                "qiskit-default-l2": 1,
                "mapomatic": 0,
                "idle-aware-summed-error": 1,
                "v1": 2,
            }
            if device == "pittsburgh" and label == "D7":
                picks["mapomatic"] = "transpiled circuit is not SWAP-free: two SWAPs"
            runs.append(
                _run(
                    f"v3-readout-{device}-{label}",
                    device,
                    "heavy-hex",
                    label,
                    [0.010, 0.020, 0.01001],
                    picks,
                    **extra,
                )
            )
    return runs


HEAVY_HEX_DEVICES = {
    "boston": _device("heavy-hex", ["A", "B", "D5", "D7"]),
    "pittsburgh": _device("heavy-hex", ["A", "B", "D5", "D7"]),
}


def test_family_holm_and_claims(tmp_path: Path) -> None:
    config = _config(HEAVY_HEX_DEVICES, {"heavy-hex": HEAVY_HEX})
    result = analysis_v3.analyse(config, _write(tmp_path, _heavy_hex_runs()), LOOSE)
    fam = result["families"]["v3-headline"]
    assert fam["planned_size"] == 32
    assert fam["size"] == 31  # pittsburgh D7's mapomatic left: not SWAP-free
    assert fam["left_the_family"] == [
        {
            "case_id": "v3-readout-pittsburgh-D7",
            "method": "decoder-graph",
            "reference": "mapomatic",
            "reason": "transpiled circuit is not SWAP-free: two SWAPs",
        }
    ]
    by = {(m["case_id"], m["reference"]): m for m in fam["members"]}
    assert by[("v3-readout-boston-A", "naive")]["after_holm"] == "better"
    assert by[("v3-readout-boston-A", "mapomatic")]["same_placement"]
    assert by[("v3-readout-boston-A", "mapomatic")]["after_holm"] == "tie"
    assert by[("v3-readout-boston-A", "lowest-summed-error")]["after_holm"] == "tie"
    claims = {c["reference"]: c["claim"] for c in result["claims"] if c["scope"] == "layout"}
    where = "on FakeBoston and FakePittsburgh (heavy-hex), under this noise model"
    both = "FakeBoston: better in 4, worse in 0 of 4; FakePittsburgh: better in 4, worse in 0 of 4"
    assert claims["naive"] == f"beats naive {where} ({both})"
    assert claims["qiskit-default-l2"] == f"beats qiskit-default-l2 {where} ({both})"
    assert claims["lowest-summed-error"].startswith(
        "better after Holm in 0 of 8 cases (fewer than t = 5); no layout claim against "
        f"lowest-summed-error {where} (FakeBoston: better in 0"
    )
    assert claims["mapomatic"].startswith(
        "better after Holm in 0 of 7 cases (fewer than t = 5); "
        f"no layout claim against mapomatic {where}"
    )
    per = next(c for c in result["claims"] if c.get("reference") == "naive")["per_device"]
    assert per == {
        "FakeBoston": {"better": 4, "worse": 0, "cases": 4},
        "FakePittsburgh": {"better": 4, "worse": 0, "cases": 4},
    }
    # the zero-setting family has no run files here, so every member is missing
    zero = result["families"]["v3-headline at the zero setting (exploratory)"]
    assert zero["size"] == 0 and len(zero["left_the_family"]) == 32


def test_isolation_family(tmp_path: Path) -> None:
    """The decoder graph against the idle-aware summed error and the v1 score (B3)."""

    config = _config(HEAVY_HEX_DEVICES, {"heavy-hex": HEAVY_HEX})
    result = analysis_v3.analyse(config, _write(tmp_path, _heavy_hex_runs()), LOOSE)
    iso = result["families"]["v3-decoder-graph-isolation"]
    assert iso["planned_size"] == 16 and iso["size"] == 16
    claims = {c["reference"]: c["claim"] for c in result["isolation_claims"]}
    assert claims["idle-aware-summed-error"].startswith(
        "the decoder graph helps beyond idle-aware-summed-error on"
    )
    assert claims["v1"].startswith("better after Holm in 0 of 8 cases (fewer than t = 5)")


def test_after_holm_uses_rejection_and_sign() -> None:
    assert analysis_v3.after_holm(True, -0.001) == "better"
    assert analysis_v3.after_holm(True, 0.001) == "worse"
    assert analysis_v3.after_holm(False, -0.5) == "tie"
    assert analysis_v3.after_holm(True, 0.0) == "tie"


def test_mixed_and_loss_wording() -> None:
    where = "on FakeBerlin (square lattice), under this noise model"
    assert analysis_v3.layout_claim("naive", ["a", "b"], ["c"], 3, where) == (
        f"mixed against naive {where}: better in 2 cases, worse in 1 (c)"
    )
    assert analysis_v3.layout_claim("naive", [], ["c", "d"], 3, where) == (
        f"loses to naive {where} (c, d)"
    )
    assert analysis_v3.layout_claim("naive", ["a", "b"], [], 3, where, 4) == (
        "better after Holm in 2 of 4 cases (fewer than t = 3); "
        f"no layout claim against naive {where}"
    )
    assert analysis_v3.layout_claim("naive", ["a", "b", "c"], [], 3, where).startswith("beats")


def test_claim_rules_must_match_the_devices(tmp_path: Path) -> None:
    for wrong in (
        {**HEAVY_HEX, "t": 4},
        {**HEAVY_HEX, "devices": ["kingston", "aachen"]},
        {**HEAVY_HEX, "repetition_cases": 6},
    ):
        config = _config(HEAVY_HEX_DEVICES, {"heavy-hex": wrong})
        with pytest.raises(ValueError):
            analysis_v3.check_claim_rules(config)


def test_seen_and_synthetic_devices_stay_out_of_the_family(tmp_path: Path) -> None:
    devices = {
        **HEAVY_HEX_DEVICES,
        "kingston": _device("heavy-hex", ["A"], role="seen"),
        "nighthawk": _device("square", ["A"], role="synthetic"),
    }
    picks = {"decoder-graph": 0, "naive": 1, "lowest-summed-error": 1}
    picks |= {"qiskit-default-l2": 1, "mapomatic": 1}
    runs = _heavy_hex_runs()
    runs.append(
        _run(
            "v3-readout-kingston-A", "kingston", "heavy-hex", "A", [0.01, 0.02], picks, role="seen"
        )
    )
    runs.append(
        _run(
            "v3-readout-nighthawk-A",
            "nighthawk",
            "square",
            "A",
            [0.01, 0.02],
            picks,
            role="synthetic",
        )
    )
    config = _config(devices, {"heavy-hex": HEAVY_HEX})
    result = analysis_v3.analyse(config, _write(tmp_path, runs), LOOSE)
    fam = result["families"]["v3-headline"]
    assert fam["planned_size"] == 32
    assert not {m["device"] for m in fam["members"]} & {"kingston", "nighthawk"}
    seen = [c for c in result["comparisons"] if c["device"] == "kingston"]
    assert seen and all(c["role"] == "seen" for c in seen)


def test_loss_is_named_and_surface_claims(tmp_path: Path) -> None:
    devices = {"berlin": _device("square", ["A", "S5"])}
    layouts = {"square": {"devices": ["berlin"], "repetition_cases": 1, "t": 1}}
    rep = {"decoder-graph": 0, "naive": 1, "lowest-summed-error": 1}
    rep |= {"qiskit-default-l2": 1, "mapomatic": 1}
    surf = {"scaled": 1, "naive": 0, "lowest-summed-error": 1, "qiskit-default-l2": 0}
    surf |= {"mapomatic": 2}
    runs = [
        # the choice (0) is worse than every reference (1)
        _run("v3-readout-berlin-A", "berlin", "square", "A", [0.03, 0.01], rep),
        _run("v3-readout-berlin-S5", "berlin", "square", "S5", [0.05, 0.03, 0.03], surf),
    ]
    config = _config(devices, layouts)
    config["claim_rules"]["device_notes"] = {"berlin": "aggregates seen"}
    result = analysis_v3.analyse(config, _write(tmp_path, runs), LOOSE)
    layout = {c["reference"]: c for c in result["claims"] if c["scope"] == "layout"}
    where = "on FakeBerlin (square lattice), under this noise model"
    assert layout["naive"]["claim"] == f"loses to naive {where} (v3-readout-berlin-A)"
    assert {c["note"] for c in result["claims"]} == {"aggregates seen"}  # revision 6, B6
    assert layout["naive"]["worse_after_holm"] == ["v3-readout-berlin-A"]
    surface = {c["reference"]: c["claim"] for c in result["claims"] if c["scope"] != "layout"}
    assert surface["naive"] == f"beats naive {where}"
    assert surface["lowest-summed-error"] == (
        f"no difference shown against lowest-summed-error {where}"
    )
    assert surface["mapomatic"] == f"no difference shown against mapomatic {where}"
    regret = {s["case_id"]: s for s in result["secondary"]}
    assert regret["v3-readout-berlin-A"]["regret"]["decoder-graph"]["verdict"] == "worse"


def test_pilot_best_selects_by_pilot_and_is_graded_on_main_counts(tmp_path: Path) -> None:
    """Pilot counts select (T3-C5); the comparison uses main-stage counts only."""

    devices = {"berlin": _device("square", ["A"])}
    layouts = {"square": {"devices": ["berlin"], "repetition_cases": 1, "t": 1}}
    picks = {"decoder-graph": 0, "naive": 1, "lowest-summed-error": 1}
    picks |= {"qiskit-default-l2": 1, "mapomatic": 1}
    # the pilot ranks placement 2 best, but its main-stage rate is the worst
    run = _run(
        "v3-readout-berlin-A",
        "berlin",
        "square",
        "A",
        [0.010, 0.020, 0.030],
        picks,
        pilot=[0.02, 0.03, 0.001],
    )
    result = analysis_v3.analyse(_config(devices, layouts), _write(tmp_path, [run]), LOOSE)
    rows = [
        c
        for c in result["comparisons"]
        if c["method"] == "pilot-best" and c["reference"] == "decoder-graph"
    ]
    assert len(rows) == 1
    assert rows[0]["chosen"]["estimate"] == pytest.approx(0.030)
    assert rows[0]["verdict"] == "worse"


def test_context_percentile_mean_and_per_round(tmp_path: Path) -> None:
    devices = {"berlin": _device("square", ["A"])}
    layouts = {"square": {"devices": ["berlin"], "repetition_cases": 1, "t": 1}}
    picks = {"decoder-graph": 0, "naive": 3, "lowest-summed-error": 1}
    picks |= {"qiskit-default-l2": 1, "mapomatic": 1}
    rates = [0.010, 0.020, 0.030, 0.040]
    run = _run("v3-readout-berlin-A", "berlin", "square", "A", rates, picks)
    result = analysis_v3.analyse(_config(devices, layouts), _write(tmp_path, [run]), LOOSE)
    sec = result["secondary"][0]
    assert sec["all_placement_mean_p_l"] == pytest.approx(0.025)
    assert sec["choices"]["naive"]["percentile_among_simulated"] == pytest.approx(75.0)
    assert sec["choices"]["decoder-graph"]["percentile_among_simulated"] == pytest.approx(0.0)
    assert sec["choices"]["decoder-graph"]["p_l_per_round"] == pytest.approx(
        1 - (1 - 0.01) ** (1 / 3)
    )


def test_sensitivity_runs_recompute_the_family(tmp_path: Path) -> None:
    """R3-C10: correlated matching makes the choice and naive equal on boston-A."""

    runs = _heavy_hex_runs()
    for run in runs:
        rows = [
            {
                "index": i,
                "per_seed": [
                    {"base_seed": b, "logical_failures": 10_000, "shots": 1_000_000}
                    for b in (1, 2, 3)
                ],
            }
            for i in run["family_placements"]
        ]
        same = run["case"]["case_id"] == "v3-readout-boston-A"
        run["sensitivity"] = {
            "correlated_matching": {"rows": rows if same else []},
            "rounds_equal_distance": None,
        }
    config = _config(HEAVY_HEX_DEVICES, {"heavy-hex": HEAVY_HEX})
    result = analysis_v3.analyse(config, _write(tmp_path, runs), LOOSE)
    corr = result["sensitivity"]["correlated_matching"]
    changed = {(c["case_id"], c["reference"]): c for c in corr["changed"]}
    assert changed[("v3-readout-boston-A", "naive")]["sensitivity"] == "tie"
    assert changed[("v3-readout-boston-A", "naive")]["headline"] == "better"
    assert result["sensitivity"]["rounds_equal_distance"]["changed"] == []


def test_config_revision_is_checked(tmp_path: Path) -> None:
    config = _config(HEAVY_HEX_DEVICES, {"heavy-hex": HEAVY_HEX})
    config["spec_revision"] = 5
    with pytest.raises(ValueError, match="revision"):
        analysis_v3.analyse(config, tmp_path)


# --- revision 6 ------------------------------------------------------------------------------


def test_a_missing_or_failed_case_stops_the_analysis(tmp_path: Path) -> None:
    """A2: a planned case with no run file, a failed run and an unfinished one all stop it;
    a failed Nove check is R3-C4's pre-registered outcome and takes its case out."""

    config = _config(HEAVY_HEX_DEVICES, {"heavy-hex": HEAVY_HEX})
    config["settings_order"] = ["readout"]
    complete = analysis_v3.Checks(complete=True, provenance=False)
    runs = _heavy_hex_runs()
    analysis_v3.analyse(config, _write(tmp_path / "all", runs), complete)
    with pytest.raises(RuntimeError, match="no run file for v3-readout-boston-B"):
        analysis_v3.analyse(
            config,
            _write(tmp_path / "gap", [r for r in runs if "boston-B" not in r["case"]["case_id"]]),
            complete,
        )
    failed = [dict(r) for r in runs]
    failed[0] = {**failed[0], "failed": True, "stage": "simulation", "error": "OOM"}
    with pytest.raises(RuntimeError, match="failed in simulation"):
        analysis_v3.analyse(config, _write(tmp_path / "failed", failed), complete)
    pending = [dict(r) for r in runs]
    pending[1] = {**pending[1], "sensitivity": {"pending": True}}
    with pytest.raises(RuntimeError, match="sensitivity runs finished"):
        analysis_v3.analyse(config, _write(tmp_path / "pending", pending), complete)
    nove = [dict(r) for r in runs]
    nove[0] = {**nove[0], "failed": True, "stage": "nove_checks", "error": "differs"}
    result = analysis_v3.analyse(config, _write(tmp_path / "nove", nove), complete)
    fam = result["families"]["v3-headline"]
    assert fam["size"] == 27
    reasons = {m["reason"] for m in fam["left_the_family"] if m["case_id"] == "v3-readout-boston-A"}
    assert reasons == {"the Nove check failed (R3-C4): differs"}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_a8_provenance_traces_every_run_to_f4(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "configs_v3").mkdir(parents=True)
    _git(root, "init", "-q")
    for name in ("a", "b"):
        (root / name).write_text(name)
        _git(root, "add", name)
        _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", name)
    f4, later = _git(root, "rev-parse", "HEAD~1"), _git(root, "rev-parse", "HEAD")
    freeze = {"f4_commit": f4, "nac_bench_source_sha256": "src", "lock_file_sha256": "lock"}
    (root / "configs_v3" / "freeze_f4.json").write_text(json.dumps(freeze))
    machine = {"label": "patty", "cpu_model": "x", "logical_cores": 96}
    prov = {
        "package_git_commit": later,
        "package_git_dirty": False,
        "nac_bench_source_sha256": "src",
        "lock_file_sha256": "lock",
        "machine": machine,
    }
    runs_dir = tmp_path / "runs"
    raws = {
        f"{d}.json": {"case": {"device": d}, "provenance": dict(prov)} for d in ("boston", "berlin")
    }
    record = analysis_v3.check_provenance(raws, runs_dir, root)
    assert record["f4_commit"] == f4 and record["runs_checked"] == 2
    bad: dict[str, dict[str, Any]] = {
        "dirty": {"package_git_dirty": True},
        "descend": {"package_git_commit": "0" * 40},
        "runner source": {"nac_bench_source_sha256": "other"},
        "lock file": {"lock_file_sha256": "other"},
    }
    for message, change in bad.items():
        broken: dict[str, dict[str, Any]] = {
            **raws,
            "x.json": {"case": {"device": "x"}, "provenance": {**prov, **change}},
        }
        with pytest.raises(RuntimeError, match=message):
            analysis_v3.check_provenance(broken, runs_dir, root)
    two = {
        **raws,
        "b2.json": {
            "case": {"device": "boston"},
            "provenance": {**prov, "machine": {**machine, "label": "other"}},
        },
    }
    with pytest.raises(RuntimeError, match="X19"):
        analysis_v3.check_provenance(two, runs_dir, root)
    scores = tmp_path / "scores"
    scores.mkdir()
    (scores / "s.npz").write_bytes(b"scores")
    scored = {
        "s.json": {
            "case": {"device": "boston"},
            "provenance": prov,
            "score_file": {"path": "results/scores/s.npz", "sha256": "wrong"},
        }
    }
    with pytest.raises(RuntimeError, match="score file"):
        analysis_v3.check_provenance(scored, runs_dir, root)


def test_random_sample_percentile_and_rho(tmp_path: Path) -> None:
    """B8: where not every placement is simulated, the uniform sample gives the percentile."""

    devices = {"berlin": _device("square", ["D7"])}
    layouts = {"square": {"devices": ["berlin"], "repetition_cases": 1, "t": 1}}
    picks = {"decoder-graph": 0, "naive": 1, "lowest-summed-error": 2}
    picks |= {"qiskit-default-l2": 2, "mapomatic": 2}
    rates = [0.010, 0.030, 0.012, 0.020, 0.040, 0.025]
    run = _run("v3-readout-berlin-D7", "berlin", "square", "D7", rates, picks)
    run["simulated_set"] = {"every_placement": False, "random_sample": [3, 4, 5]}
    result = analysis_v3.analyse(_config(devices, layouts), _write(tmp_path, [run]), LOOSE)
    sec = result["secondary"][0]
    assert sec["choices"]["naive"]["percentile_among_random_sample"] == pytest.approx(200 / 3)
    assert sec["choices"]["decoder-graph"]["percentile_among_random_sample"] == 0.0
    assert sec["random_sample"]["size"] == 3
    assert "decoder_graph" in sec["random_sample"]["spearman_rho_with_p_l"]
    pilot = [c for c in result["comparisons"] if c["method"] == "pilot-best"]
    assert pilot and all(c["note"].startswith("pilot-best chose within") for c in pilot)


def test_seed_sweep_choices_are_compared(tmp_path: Path) -> None:
    """B11: each distinct level 2 choice of the sweep is compared with the headline score."""

    devices = {"berlin": _device("square", ["A"])}
    layouts = {"square": {"devices": ["berlin"], "repetition_cases": 1, "t": 1}}
    picks = {"decoder-graph": 0, "naive": 1, "lowest-summed-error": 1}
    picks |= {"qiskit-default-l2": 1, "mapomatic": 1}
    run = _run("v3-readout-berlin-A", "berlin", "square", "A", [0.01, 0.02, 0.03], picks)
    run["methods"]["qiskit-default-l2"]["seed_sweep_indices"] = [1, 2, None, 2, 1]
    result = analysis_v3.analyse(_config(devices, layouts), _write(tmp_path, [run]), LOOSE)
    sweep = result["secondary"][0]["qiskit_l2_seed_sweep"]
    assert sweep == {
        "seeds": 5,
        "same_as_main": 2,
        "no_swap_free_choice": 1,
        "distinct_other_choices": 1,
    }
    rows = [c for c in result["comparisons"] if c["method"] == "qiskit-default-l2-seed0"]
    assert len(rows) == 1 and rows[0]["reference"] == "decoder-graph"
    assert rows[0]["verdict"] == "worse"


def test_grading_variants_recompute_both_families(tmp_path: Path) -> None:
    """B13 and B1: a variant that makes boston-A's choice as bad as naive changes both
    families' rows there, and the isolation family is recomputed too."""

    runs = _heavy_hex_runs()
    for run in runs:
        rows = [
            {
                "index": i,
                "per_seed": [
                    {"base_seed": b, "logical_failures": 20_000, "shots": 1_000_000}
                    for b in (1, 2, 3)
                ],
            }
            for i in run["family_placements"]
        ]
        same = run["case"]["case_id"] == "v3-readout-boston-A"
        run["family_placements"] = sorted(set(run["family_placements"]) | {1, 2})
        run["sensitivity"] = {
            "correlated_matching": {"rows": []},
            "rounds_equal_distance": None,
            "grading_variants": {
                "variants": {"idle-z-x2": {"rows": rows if same else []}},
            },
        }
    config = _config(HEAVY_HEX_DEVICES, {"heavy-hex": HEAVY_HEX})
    result = analysis_v3.analyse(config, _write(tmp_path, runs), LOOSE)
    variant = result["sensitivity"]["grading_variants/idle-z-x2"]
    changed = {(c["case_id"], c["reference"]) for c in variant["changed"]}
    assert ("v3-readout-boston-A", "naive") in changed
    iso = {(c["case_id"], c["reference"]) for c in variant["isolation"]["changed"]}
    assert ("v3-readout-boston-A", "idle-aware-summed-error") in iso
    assert result["sensitivity"]["correlated_matching"]["isolation"]["changed"] == []


def test_surface_headroom_and_suppression(tmp_path: Path) -> None:
    """R3-R6 (revision 6): a surface "no difference shown" says when naive was as good as the
    best placement found, and Lambda compares the best per-round error at d=3 and d=5."""

    devices = {"berlin": _device("square", ["S3", "S5"])}
    layouts = {"square": {"devices": ["berlin"], "repetition_cases": 0, "t": 1}}
    config = _config(devices, layouts)
    config["circuits"]["S3"] = {"kind": "surface", "rounds": 3, "family": False}
    surf = {"scaled": 0, "naive": 1, "lowest-summed-error": 1, "qiskit-default-l2": 1}
    surf |= {"mapomatic": 1}
    s5 = _run("v3-readout-berlin-S5", "berlin", "square", "S5", [0.0700, 0.0701], surf)
    s3 = _run("v3-readout-berlin-S3", "berlin", "square", "S3", [0.0420, 0.0430], surf)
    s5["case"]["circuit"]["distance"], s3["case"]["circuit"]["distance"] = 5, 3
    s3["case"]["circuit"]["label"], s3["case"]["kind"] = "S3", "surface"
    s3["case"]["circuit"]["rounds"] = 3
    result = analysis_v3.analyse(config, _write(tmp_path, [s5, s3]), LOOSE)
    fam = result["families"]["v3-headline"]
    assert {m["case_id"] for m in fam["members"]} == {"v3-readout-berlin-S5"}
    claim = next(
        c for c in result["claims"] if c["scope"] != "layout" and c["reference"] == "naive"
    )["claim"]
    assert claim.endswith("so the device left no room to show one (R3-R6)")
    (sup,) = result["surface_suppression"]
    assert sup["device"] == "berlin" and set(sup["best_per_round"]) == {"3", "5"}
    assert sup["lambda_3_5"] == pytest.approx(
        (1 - (1 - 0.042) ** (1 / 3)) / (1 - (1 - 0.070) ** (1 / 5)), rel=1e-6
    )


def test_directional_grades_the_headline_setting_choices(tmp_path: Path) -> None:
    """B9: the directional run's family of the headline setting's choices, graded under it."""

    config = _config(HEAVY_HEX_DEVICES, {"heavy-hex": HEAVY_HEX})
    config["settings_order"] = ["readout", "directional"]
    config["setting_scope"] = {
        "directional": {
            "devices": ["boston", "pittsburgh"],
            "circuits": ["A", "D5", "D7"],
            "simulated_set": "choices",
        }
    }
    runs = _heavy_hex_runs()
    for run in list(runs):
        label = run["case"]["circuit"]["label"]
        if label == "B":
            continue
        d = json.loads(json.dumps(run))
        d["case"]["case_id"] = d["case"]["case_id"].replace("readout", "directional")
        d["case"]["setting"] = "directional"
        d["simulated_set"]["headline_setting_choices"] = {
            "choices": {k: v["index"] for k, v in run["methods"].items() if v["index"] is not None}
        }
        runs.append(d)
    result = analysis_v3.analyse(
        config, _write(tmp_path, runs), analysis_v3.Checks(complete=True, provenance=False)
    )
    name = (
        "v3-headline with the headline setting's choices graded at the directional setting "
        "(exploratory)"
    )
    graded = result["families"][name]
    assert graded["planned_size"] == 24 and graded["size"] == 23
    assert {m["method"] for m in graded["members"]} == {"decoder-graph@headline-setting"}
