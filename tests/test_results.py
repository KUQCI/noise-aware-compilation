"""Checks on the committed benchmark results and REPORT.md.

T-C4, T-C7, T-F1, T-F2, T-F3, T-F4, T-I5 (results part), T-P1-T-P6, T-R2, T-R3,
T-L1, T-L2. These read ``results/runs/*.json`` and ``REPORT.md``.
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any

import pytest

from noise_aware_compilation.analysis import CaseSummary, load_summaries, precision_ok
from noise_aware_compilation.benchmark import expand_cases, load_config

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "results" / "runs"
REPORT = ROOT / "REPORT.md"

pytestmark = [
    pytest.mark.results,
    pytest.mark.skipif(not any(RUNS.glob("*.json")), reason="results not generated yet"),
]


@pytest.fixture(scope="module")
def summaries() -> dict[str, CaseSummary]:
    return load_summaries(RUNS)


@pytest.fixture(scope="module")
def report() -> str:
    return REPORT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def config() -> dict[str, Any]:
    return load_config(ROOT / "configs" / "benchmark.json")


def _base(summaries: dict[str, CaseSummary], condition: str) -> list[CaseSummary]:
    return [
        s
        for s in summaries.values()
        if s.case["condition"] == condition and s.case["variant"] == "base"
    ]


def test_every_configured_case_has_one_results_file(
    summaries: dict[str, CaseSummary], config: dict[str, Any]
) -> None:
    expected = {c.case_id for c in expand_cases(config)}
    assert set(summaries) == expected


def test_t_c3_results_hold_every_valid_placement(summaries: dict[str, CaseSummary]) -> None:
    baselines = json.loads((ROOT / "configs" / "fixed_baselines.json").read_text())
    for s in summaries.values():
        key = f"{s.case['device']}-{s.case['circuit']['label']}"
        assert len(s.placements) == baselines["num_valid_placements"][key]
        assert list(s.placements[s.fixed_index].mapping) == baselines["fixed"][key]


def test_n0_zero_noise_expected_outcome(summaries: dict[str, CaseSummary]) -> None:
    for s in _base(summaries, "N0"):
        for p in s.placements:
            assert p.failures == 0
            assert p.detection.estimate == 0


def test_t_c4_planted_fault(summaries: dict[str, CaseSummary]) -> None:
    cases = _base(summaries, "N3-qubit") + _base(summaries, "N3-connection")
    assert cases
    for s in cases:
        chosen = s.placements[s.chosen_index]
        faulty = [p for p in s.placements if p.contains_fault]
        assert faulty and len(faulty) < len(s.placements)  # avoidable, and present
        assert chosen.contains_fault is False, s.case_id
        for f in faulty:
            if s.metric == "P_L":
                assert not chosen.p_l.low > f.p_l.high, (s.case_id, f.mapping)
            else:
                assert not chosen.detection.low > f.detection.high, (s.case_id, f.mapping)


def test_t_c7_no_placements_merged(summaries: dict[str, CaseSummary]) -> None:
    # No placements are treated as equivalent (R-F5); every valid mapping is
    # evaluated separately, so the T-C7 condition holds vacuously.
    for s in summaries.values():
        mappings = [p.mapping for p in s.placements]
        assert len(set(mappings)) == len(mappings)
        assert "merged_placements" not in s.raw


def test_t_f1_only_the_mapping_differs(summaries: dict[str, CaseSummary]) -> None:
    for s in summaries.values():
        configs = [dict(p["run_config"]) for p in s.raw["placements"]]
        mappings = [c.pop("mapping") for c in configs]
        assert all(c == configs[0] for c in configs), s.case_id
        assert len({tuple(m) for m in mappings}) == len(mappings)


def test_t_f2_uniform_noise_null(summaries: dict[str, CaseSummary], report: str) -> None:
    cases = _base(summaries, "N1")
    assert cases
    for s in cases:
        assert s.homogeneity.p_value > 0.01, (s.case_id, s.homogeneity)
    section = report.split("## 7.")[1].split("## 8.")[0]
    assert section.count("**no gain** under N1") == len(cases)


def test_t_f3_seed_separation(summaries: dict[str, CaseSummary]) -> None:
    for s in summaries.values():
        selection = set(s.raw["selection"]["selection_seeds"]) | set(
            s.raw["seeds"]["selection_seeds"]
        )
        evaluation = {
            seed for p in s.raw["placements"] for ps in p["per_seed"] for seed in ps["chunk_seeds"]
        }
        assert evaluation
        assert not selection & evaluation


def _first_commit(path: str) -> tuple[str, int]:
    out = subprocess.run(
        ["git", "log", "--diff-filter=A", "--format=%H %ct", "--", path],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    if not out:
        pytest.fail(f"{path} is not committed")
    return out[-2], int(out[-1])


def test_t_f4_baseline_committed_before_n2_results() -> None:
    config_commit, config_time = _first_commit("configs/benchmark.json")
    baseline_commit, baseline_time = _first_commit("configs/fixed_baselines.json")
    results_commit, results_time = _first_commit("results/runs")
    for commit in (config_commit, baseline_commit):
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", commit, results_commit], cwd=ROOT
        )
        assert ancestor.returncode == 0
        assert commit != results_commit
    assert max(config_time, baseline_time) <= results_time
    # the config has not changed since (X5)
    changes = subprocess.run(
        ["git", "log", "--format=%H", "--", "configs/"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert changes == [config_commit]


def test_t_i5_results_record_approximate_decoder(summaries: dict[str, CaseSummary]) -> None:
    for s in summaries.values():
        assert s.raw["decoder"]["decoder_model"] == "approximate"
        assert s.raw["decoder"]["decoder"] == "pymatching"


def test_t_p1_main_comparison(summaries: dict[str, CaseSummary], report: str) -> None:
    for s in summaries.values():
        for comp in (s.vs_fixed, s.vs_average):
            assert comp.verdict in {"better", "indistinguishable", "worse"}
            assert comp.absolute.low <= comp.absolute.estimate <= comp.absolute.high
    headline = report.split("## 1.")[1].split("## 2.")[0]
    rows = [line for line in headline.splitlines() if line.startswith("| Fake")]
    assert len(rows) == len(_base(summaries, "N2"))
    for row in rows:
        verdicts = [v for v in ("better", "indistinguishable", "worse") if f"**{v}**" in row]
        assert row.count("**") == 4 and verdicts  # one verdict per comparison
        assert row.count("rel ") == 2  # absolute and relative differences


def test_t_p2_regret_everywhere(summaries: dict[str, CaseSummary], report: str) -> None:
    section = report.split("## 3.")[1].split("## 4.")[0]
    for s in summaries.values():
        assert s.chosen_rank >= 1
        assert not math.isnan(s.regret.estimate)
    assert section.count(" of ") >= len(
        [s for s in summaries.values() if s.case["variant"] == "base"]
    )


def test_t_p3_score_validity_reported_and_flagged(
    summaries: dict[str, CaseSummary], report: str
) -> None:
    section = report.split("## 3.")[1].split("## 4.")[0]
    for s in summaries.values():
        if s.case["variant"] != "base":
            continue
        if not math.isnan(s.spearman) and s.spearman < 0:
            assert f"{s.spearman:+.3f} ⚠ negative" in section


def test_t_p4_precision(summaries: dict[str, CaseSummary]) -> None:
    failing = [
        (s.case_id, p.mapping)
        for s in summaries.values()
        if s.case["condition"] in ("N2", "N3-qubit", "N3-connection")
        for p in s.placements
        if not precision_ok(p)
    ]
    assert not failing, failing[:10]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Reported unmet (REPORT.md section 5, DECISIONS.md D15): on FakeHanoiV2 the chosen-vs-"
        "fixed verdict is indistinguishable at 1 round and better at 3 and 5 rounds. The pass "
        "criterion is kept unchanged (X6); strict=True makes this test fail if it starts passing."
    ),
)
def test_t_p5_per_round_consistency(summaries: dict[str, CaseSummary], report: str) -> None:
    for device in ("jakarta", "hanoi"):
        verdicts = {
            (
                summaries[f"{device}-{label}-N2"].vs_fixed.verdict,
                summaries[f"{device}-{label}-N2"].vs_average.verdict,
            )
            for label in ("A1", "A3", "A5")
        }
        assert len(verdicts) == 1, (device, verdicts)
    section = report.split("## 5.")[1].split("## 6.")[0]
    assert section.count("the same for all three") == 2


def test_t_p6_gain_vs_distance(report: str) -> None:
    section = report.split("## 6.")[1].split("## 7.")[0]
    assert all(f"| {d} |" in section for d in (3, 5, 7))
    assert any(f"**{t}**" in section for t in ("grows", "shrinks", "flat"))


def test_t_r2_verdict_same_across_seeds(summaries: dict[str, CaseSummary]) -> None:
    inconsistent = [
        (s.case_id, s.per_seed_verdicts)
        for s in _base(summaries, "N2")
        if len({(v["vs_fixed"], v["vs_average"]) for v in s.per_seed_verdicts}) != 1
    ]
    assert not inconsistent, inconsistent


def test_t_r3_provenance(summaries: dict[str, CaseSummary]) -> None:
    for s in summaries.values():
        prov = s.raw["provenance"]
        assert prov["nove_commit"].startswith("485b413")
        assert prov["stim_version"] and prov["pymatching_version"]
        assert prov["qiskit_ibm_runtime_version"] == "0.50.0"
        assert len(prov["source_files"]) == 2
        assert all(len(h) == 64 for h in prov["source_files"].values())
        for field in ("calibration_snapshot_sha256", "device_sha256", "noise_model_sha256"):
            assert len(prov[field]) == 64
        assert s.raw["seeds"]["base_seeds"] and "selection_seeds" in s.raw["seeds"]
        for p in s.raw["placements"]:
            for ps in p["per_seed"]:
                assert ps["chunk_seeds"]
        assert s.raw["wall_clock_seconds"] > 0


def test_t_l1_sensitivity(summaries: dict[str, CaseSummary], report: str) -> None:
    section = report.split("## 10.")[1].split("## 11.")[0]
    for s in _base(summaries, "N2"):
        for variant in ("L1-cx-min", "L1-cx-max", "L1-reset-readout"):
            assert f"{s.case_id}-{variant}" in summaries
    rows = [line for line in section.splitlines() if line.startswith("| Fake")]
    assert len(rows) == 3 * len(_base(summaries, "N2"))
    assert all(line.rstrip().endswith(("| yes |", "| **no** |")) for line in rows)


def test_t_l2_limitations_cover_constraints(report: str) -> None:
    section = report.split("## 11.")[1].split("## 12.")[0]
    for k in range(1, 9):
        assert f"**K{k} " in section


def test_r_r5_figures_exist(summaries: dict[str, CaseSummary], report: str) -> None:
    for s in _base(summaries, "N2"):
        name = f"results/figures/{s.case['device']}-{s.case['circuit']['label']}-N2.png"
        assert (ROOT / name).exists()
        assert f"]({name})" in report


def test_r_r1_appendix_lists_every_placement(
    summaries: dict[str, CaseSummary], report: str
) -> None:
    appendix = report.split("## Appendix")[1]
    rows = [line for line in appendix.splitlines() if line.startswith("| (")]
    assert len(rows) == sum(
        len(s.placements) for s in summaries.values() if s.case["variant"] == "base"
    )


def test_report_states_noise_model_scope(report: str) -> None:
    assert "under this noise model" in report
