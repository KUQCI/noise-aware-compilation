"""Checks on the committed benchmark results and REPORT.md.

T-C4, T-C7, T-F1, T-F2, T-F3, T-F4, T-I5 (results part), T-P1-T-P6, T-R2, T-R3,
T-L1, T-L2. These read ``results/runs/*.json`` and ``REPORT.md``.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any

import pytest

from noise_aware_compilation.analysis import (
    CaseSummary,
    evidence,
    load_summaries,
    precision_ok,
    seed_breakdown,
    verdicts_consistent,
)
from noise_aware_compilation.benchmark import expand_cases, load_config

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "results" / "runs"
L1_VARIANTS = ("L1-cx-min", "L1-cx-max", "L1-reset-readout", "L1-coupler-better")
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
        variant_key = f"{key}-{s.case['variant']}"
        if variant_key in baselines["fixed"]:
            key = variant_key
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


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_t_f4_baseline_committed_before_n2_results(summaries: dict[str, CaseSummary]) -> None:
    # The last commit touching the config or fixed baselines must strictly precede every
    # results file: each file names a clean commit that already contains the current config,
    # and records the SHA-256 of that config (X5: nothing changed after the runs).
    config_commit = _git("log", "-1", "--format=%H", "--", "configs/")
    assert config_commit, "configs/ is not committed"
    config_sha = hashlib.sha256((ROOT / "configs" / "benchmark.json").read_bytes()).hexdigest()
    results_commit = _git("log", "-1", "--format=%H", "--", "results/runs")
    assert results_commit and results_commit != config_commit
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", config_commit, results_commit], cwd=ROOT
    )
    assert ancestor.returncode == 0
    for s in summaries.values():
        assert s.raw["config_file_sha256"] == config_sha, s.case_id
        prov = s.raw["provenance"]
        assert prov["package_git_dirty"] is False, s.case_id
        run_commit = prov["package_git_commit"]
        check = subprocess.run(
            ["git", "merge-base", "--is-ancestor", config_commit, run_commit], cwd=ROOT
        )
        assert check.returncode == 0, (s.case_id, run_commit)


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


def test_t_p5_per_round_consistency(summaries: dict[str, CaseSummary], report: str) -> None:
    # SPEC.md rev 3: no two round counts give opposite verdicts for the same comparison.
    for device in ("jakarta", "hanoi"):
        cases = [summaries[f"{device}-{label}-N2"] for label in ("A1", "A3", "A5")]
        assert verdicts_consistent([s.vs_fixed.verdict for s in cases]), device
        assert verdicts_consistent([s.vs_average.verdict for s in cases]), device
    section = report.split("## 5.")[1].split("## 6.")[0]
    assert section.count("no two point in opposite directions") == 2


def test_verdicts_consistent_rule() -> None:
    assert verdicts_consistent(["better", "indistinguishable", "better"])
    assert verdicts_consistent(["worse", "indistinguishable"])
    assert verdicts_consistent(["indistinguishable"] * 3)
    assert not verdicts_consistent(["better", "indistinguishable", "worse"])


def test_seed_breakdown_and_evidence_rule() -> None:
    mixed = ["better", "indistinguishable", "better"]
    assert seed_breakdown("better", mixed) == "better (2/3 seeds), pooled: better"
    assert evidence("better", mixed) == "gain, weak evidence"
    assert evidence("better", ["better"] * 3) == "gain"
    assert (
        seed_breakdown("better", ["indistinguishable"] * 3) == "better (0/3 seeds), pooled: better"
    )
    assert evidence("better", ["indistinguishable"] * 3) == "gain, weak evidence"
    assert evidence("worse", ["worse", "indistinguishable", "worse"]) == "loss, weak evidence"
    assert evidence("indistinguishable", ["indistinguishable"] * 3) == "no gain shown"
    assert evidence("indistinguishable", mixed) == "no gain shown, seeds mixed"
    assert (
        seed_breakdown("indistinguishable", ["better", "worse", "indistinguishable"])
        == "better (1/3 seeds), worse (1/3 seeds), pooled: indistinguishable"
    )


def test_rev4_claims_rest_on_pooled_verdicts(
    summaries: dict[str, CaseSummary], report: str
) -> None:
    """SPEC.md rev 4: seed tally next to every headline verdict; gains only from pooled *better*."""

    headline = report.split("## 1.")[1].split("## 2.")[0]
    claims = [line for line in headline.splitlines() if line.startswith("- Fake")]
    expected = 0
    for s in _base(summaries, "N2"):
        for comparison in ("vs_fixed", "vs_average"):
            pooled = getattr(s, comparison).verdict
            seeds = s.seed_verdicts(comparison)
            assert (
                seed_breakdown(pooled, seeds).replace(f"pooled: {pooled}", f"pooled: **{pooled}**")
                in headline
            )
            if pooled != "indistinguishable":
                expected += 1
    assert len(claims) == expected
    for line in claims:
        label = line.split("**")[1]
        pooled = line.split("pooled: ")[1].rstrip(")")
        assert label.startswith("gain") == (pooled == "better")
        assert ("weak evidence" in label) == (not line.count("(3/3 seeds)"))


def test_t_p6_gain_vs_distance(report: str) -> None:
    section = report.split("## 6.")[1].split("## 7.")[0]
    assert all(f"| {d} |" in section for d in (3, 5, 7))
    assert any(f"**{t}**" in section for t in ("grows", "shrinks", "flat"))


def test_t_r2_verdict_same_across_seeds(summaries: dict[str, CaseSummary]) -> None:
    # SPEC.md rev 3: no two seeds give opposite verdicts for the same comparison.
    inconsistent = [
        (s.case_id, s.per_seed_verdicts)
        for s in _base(summaries, "N2")
        if not (
            verdicts_consistent([v["vs_fixed"] for v in s.per_seed_verdicts])
            and verdicts_consistent([v["vs_average"] for v in s.per_seed_verdicts])
        )
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
        for variant in L1_VARIANTS:
            assert f"{s.case_id}-{variant}" in summaries
    rows = [line for line in section.splitlines() if line.startswith("| Fake")]
    assert len(rows) == len(L1_VARIANTS) * len(_base(summaries, "N2"))
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
