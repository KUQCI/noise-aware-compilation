"""The held-out analysis on synthetic run files (T3-F2, section 7's claim rules, revision 5)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nac_bench import analysis_v3

REFERENCES = ["naive", "lowest-summed-error", "qiskit-default-l2", "mapomatic"]
HEAVY_HEX = {"devices": ["boston", "pittsburgh"], "repetition_cases": 8, "t": 5}
SQUARE = {"devices": ["berlin"], "repetition_cases": 4, "t": 3}


def _config(devices: dict[str, Any], layouts: dict[str, Any]) -> dict[str, Any]:
    return {
        "_config_sha256": "synthetic",
        "spec_revision": 5,
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
    result = analysis_v3.analyse(config, _write(tmp_path, _heavy_hex_runs()))
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
    assert claims["naive"] == f"beats naive {where}"
    assert claims["qiskit-default-l2"] == f"beats qiskit-default-l2 {where}"
    assert (
        claims["lowest-summed-error"] == f"no difference shown against lowest-summed-error {where}"
    )
    assert claims["mapomatic"] == f"no difference shown against mapomatic {where}"
    # the zero-setting family has no run files here, so every member is missing
    zero = result["families"]["v3-headline at reset zero (exploratory)"]
    assert zero["size"] == 0 and len(zero["left_the_family"]) == 32


def test_isolation_family(tmp_path: Path) -> None:
    """The decoder graph against the idle-aware summed error and the v1 score (B3)."""

    config = _config(HEAVY_HEX_DEVICES, {"heavy-hex": HEAVY_HEX})
    result = analysis_v3.analyse(config, _write(tmp_path, _heavy_hex_runs()))
    iso = result["families"]["v3-decoder-graph-isolation"]
    assert iso["planned_size"] == 16 and iso["size"] == 16
    claims = {c["reference"]: c["claim"] for c in result["isolation_claims"]}
    assert claims["idle-aware-summed-error"].startswith("beats idle-aware-summed-error on")
    assert claims["v1"].startswith("no difference shown against v1 on")


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
    assert analysis_v3.layout_claim("naive", ["a", "b"], [], 3, where).startswith("no difference")
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
    result = analysis_v3.analyse(config, _write(tmp_path, runs))
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
    result = analysis_v3.analyse(_config(devices, layouts), _write(tmp_path, runs))
    layout = {c["reference"]: c for c in result["claims"] if c["scope"] == "layout"}
    where = "on FakeBerlin (square lattice), under this noise model"
    assert layout["naive"]["claim"] == f"loses to naive {where} (v3-readout-berlin-A)"
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
    result = analysis_v3.analyse(_config(devices, layouts), _write(tmp_path, [run]))
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
    result = analysis_v3.analyse(_config(devices, layouts), _write(tmp_path, [run]))
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
    result = analysis_v3.analyse(config, _write(tmp_path, runs))
    corr = result["sensitivity"]["correlated_matching"]
    changed = {(c["case_id"], c["reference"]): c for c in corr["changed"]}
    assert changed[("v3-readout-boston-A", "naive")]["sensitivity"] == "tie"
    assert changed[("v3-readout-boston-A", "naive")]["headline"] == "better"
    assert result["sensitivity"]["rounds_equal_distance"]["changed"] == []


def test_config_revision_is_checked(tmp_path: Path) -> None:
    config = _config(HEAVY_HEX_DEVICES, {"heavy-hex": HEAVY_HEX})
    config["spec_revision"] = 4
    with pytest.raises(ValueError, match="revision"):
        analysis_v3.analyse(config, tmp_path)
