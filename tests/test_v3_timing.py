"""v3 timing pilot (SPEC_V3.md R3-C6, R3-C7): the shots rule and the projection arithmetic."""

from __future__ import annotations

from typing import Any

import pytest

from nac_bench import timing_pilot as tp


def test_main_shots_follow_the_r3_c6_rule() -> None:
    assert tp.main_shots(0, 100_000) == tp.MAIN_MAX_SHOTS
    assert tp.main_shots(1000, 100_000) == 200_000  # 3 x 200k x 0.01 = 6000
    # p = 1e-4: 3 x n x p >= 400 needs n >= 1.33M, so 200k x 2^3 = 1.6M
    assert tp.main_shots(10, 100_000) == 1_600_000
    assert tp.main_shots(1, 10_000_000) == tp.MAIN_MAX_SHOTS


def test_held_out_coupling_devices_carry_no_calibration() -> None:
    pytest.importorskip("qiskit_ibm_runtime")
    device = tp.coupling_device("kingston")
    assert device.gate_durations == {} and device.measurement_duration is None
    assert all(a < b for a, b in device.connectivity)
    with pytest.raises(ValueError):
        tp.coupling_device("fez")


def _measured(placements: int) -> dict[str, Any]:
    one = {"mean": 0.1, "median": 0.1, "max": 0.1, "n": 1}
    return {
        "placements": placements,
        "seconds_decoder_graph": one,
        "seconds_v1": one,
        "seconds_build": {**one, "mean": 0.2},
        "seconds_per_shot": {**one, "mean": 1e-6},
        "main_shots_per_seed": {**one, "mean": 1_000_000.0},
    }


def test_projection_arithmetic() -> None:
    measured = {f"{d}-{k}-Z": _measured(1000) for d in ("fez", "miami") for k in (3, 5, 7)}
    measured["fez-7-Z"]["all_candidate_seconds"] = {"lowest-summed-error": 2.0, "mapomatic": 1.0}
    counts = {
        f"{d}-{label}": {"placements": 1000 if d in ("kingston", "aachen") else 100_000}
        for d in tp.HELD_OUT
        for label, _, _ in tp.CIRCUITS
    }
    out = tp.project(measured, counts, workers=10)
    row = next(c for c in out["cases"] if c["case"] == "kingston-D7")
    # selection: 1000 x 0.2 s on 10 workers + 1000 x 3 ms in one process
    assert row["selection_wall_hours"] == pytest.approx((1000 * 0.2 / 10 + 3.0) / 3600)
    # simulation: every placement, 4 builds of 0.2 s and 3.1M shots of 1 us each
    assert row["simulated_placements"] == 1000
    assert row["simulation_core_hours"] == pytest.approx(1000 * (0.8 + 3.1) / 3600)
    square = next(c for c in out["cases"] if c["case"] == "nighthawk-D7")
    assert square["simulated_placements"] == tp.BEST_FOUND_SET
    assert next(c for c in out["cases"] if c["case"] == "nighthawk-A")["simulated_placements"] == (
        100_000
    )
