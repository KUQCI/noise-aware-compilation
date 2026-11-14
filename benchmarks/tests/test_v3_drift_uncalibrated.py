"""Drift with uncalibrated couplers at their older value (revision 6, exploratory).

The older-value model differs from the drift runs' grading model only on the couplers that
became uncalibrated, where it carries the older snapshot's channels; every other placement
lowers to the same circuit; and the committed regrade reproduces the drift analysis's own
figures at the 0.8 limit before it adds the older-value grading.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import nove
import pytest

from nac_bench import drift_uncalibrated as du
from nac_bench import run_drift

ROOT = Path(__file__).resolve().parent.parent
RESULT = ROOT / "results_v3" / "drift" / "uncalibrated" / "regrade.json"
ANALYSIS = ROOT / "results_v3" / "drift" / "analysis.json"


def _two_qubit(noise: nove.HardwareNoiseModel) -> dict[Any, Any]:
    return {k: v for k, v in noise.gate_channels.items() if len(k.targets) == 2}


def test_older_value_model_changes_only_the_uncalibrated_couplers() -> None:
    chip, old, new, setting = "torino", "2024-02-28", "2025-02-26", "readout"
    dead = set(run_drift.dead_couplers(chip, old, new))
    assert len(dead) == 11
    capped_device, capped = run_drift._grading_model(chip, old, new, setting)
    device, alt = du.old_value_model(chip, old, new, setting)
    _old_device, old_noise = run_drift._snapshot_model(chip, old, setting)
    assert device == capped_device
    assert alt.readout_flip_probabilities == capped.readout_flip_probabilities
    assert alt.reset_flip_probabilities == capped.reset_flip_probabilities
    assert alt.idle_rates == capped.idle_rates
    changed = {k for k, v in _two_qubit(alt).items() if capped.gate_channels[k] != v}
    assert {tuple(sorted(k.targets)) for k in changed} == dead
    for key in changed:
        assert alt.gate_channels[key] == old_noise.gate_channels[key]
        # the limit, gate error 0.8: every one of the 15 Pauli terms at 1/15
        assert capped.gate_channels[key].probabilities == pytest.approx((1 / 15,) * 15)
        assert sum(alt.gate_channels[key].probabilities) < 1.0
    errors = du.coupler_errors(chip, old, new, setting, sorted(dead))
    assert all(0 < e < run_drift.CAP_ERROR for pair in errors.values() for e in pair.values())


def test_rows_without_such_a_coupler_are_unchanged() -> None:
    run = json.loads(
        (ROOT / "results_v3/drift/runs/v3-drift-readout-torino-12m-A.json").read_text("utf-8")
    )
    assert any(r["dead_couplers_used"] for r in run["simulated"])
    assert du.same_circuit_elsewhere(run)


def test_seed_tags_are_new() -> None:
    assert {du.SEED_TAG, du.PILOT_TAG}.isdisjoint({run_drift.SEED_TAG, run_drift.PILOT_TAG})


def test_committed_regrade_reproduces_the_drift_analysis_at_the_limit() -> None:
    if not RESULT.exists():
        pytest.skip("no regrade yet")
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    analysis = {c["case_id"]: c for c in json.loads(ANALYSIS.read_text("utf-8"))["cases"]}
    affected = set()
    for path in sorted((ROOT / "results_v3" / "drift" / "runs").glob("v3-drift-*.json")):
        run = json.loads(path.read_text(encoding="utf-8"))
        if any(r["dead_couplers_used"] for r in run["simulated"]):
            affected.add(run["case"]["case_id"])
    assert {c["case_id"] for c in result["cases"]} == affected
    for case in result["cases"]:
        for row in case["rows"]:
            assert row["shots_per_seed"] >= 200_000
            for seed in row["per_seed"]:
                assert set(seed["decoders"]) == {"stale", "new"}
            for name, record in row["methods"].items():
                committed = analysis[case["case_id"]]["methods"][name]
                limit = record["limit_0.8"]
                assert limit["kept_gain"] == committed["kept_gain"]
                assert limit["kept_gain_new_weights"] == committed["kept_gain_new_weights"]
                assert limit.get("m6") == committed.get("m6")
                assert "kept_gain" in record["older_value"]
