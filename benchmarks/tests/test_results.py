"""Checks on the committed v1 benchmark results.

T-C4, T-C7, T-F1, T-F2, T-F3, T-F4, T-I5 (results part), T-P1-T-P6, T-R2, T-R3,
T-L1, T-L2. These read ``results/runs/*.json``. The checks on REPORT.md run at tag v3.0-results.
"""

from __future__ import annotations

import json
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

pytestmark = [
    pytest.mark.results,
    pytest.mark.skipif(not any(RUNS.glob("*.json")), reason="results not generated yet"),
]


@pytest.fixture(scope="module")
def summaries() -> dict[str, CaseSummary]:
    return load_summaries(RUNS)


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


def test_t_i5_results_record_approximate_decoder(summaries: dict[str, CaseSummary]) -> None:
    for s in summaries.values():
        assert s.raw["decoder"]["decoder_model"] == "approximate"
        assert s.raw["decoder"]["decoder"] == "pymatching"


def test_t_p4_precision(summaries: dict[str, CaseSummary]) -> None:
    failing = [
        (s.case_id, p.mapping)
        for s in summaries.values()
        if s.case["condition"] in ("N2", "N3-qubit", "N3-connection")
        for p in s.placements
        if not precision_ok(p)
    ]
    assert not failing, failing[:10]


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
