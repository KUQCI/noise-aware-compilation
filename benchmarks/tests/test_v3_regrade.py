"""R3-D5's re-grade of v2's synthetic drift (P2-23) on FakeJakarta A."""

from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from nac_bench import regrade_v2_drift as rg

CONFIG = rg.ROOT / "configs_v3" / "regrade_v2_drift.json"
CASE = "v2-jakarta-A"


def _config(tmp_path: Path, v2_runs: Path | None = None) -> dict[str, Any]:
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    data["strengths"] = [0.1]
    data["bootstrap_draws"] = 500
    if v2_runs is not None:
        data["v2_runs"] = str(v2_runs)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return rg.load_config(path)


def _run(config: dict[str, Any], out: Path) -> Path:
    with ProcessPoolExecutor(max_workers=2) as pool:
        return rg.run_case(CASE, config, pool, workers=2, out_dir=out, machine_label=None)


def test_regrade_reproduces_v2_and_analyses(tmp_path: Path) -> None:
    config = _config(tmp_path)
    run = json.loads(_run(config, tmp_path).read_text(encoding="utf-8"))
    assert run["true_counts_equal_v2"]
    assert [m["draw"] for m in run["seen_models"]] == list(range(20))
    fixed = [r for r in run["simulated"] if "fixed" in r["roles"]]
    assert len(fixed) == 1 and len(fixed[0]["decoders"]) == 21  # true + every draw
    for row in run["simulated"]:
        assert row["decoders"][0] == "true"
        for seed in row["per_seed"]:
            fires = {c["detector_fires"] for c in seed["decoders"].values()}
            assert len(fires) == 1  # every decoder decodes the same samples

    assert rg.analyse(config, tmp_path / "runs", tmp_path / "analysis.json") == 0
    analysis = json.loads((tmp_path / "analysis.json").read_text(encoding="utf-8"))
    metrics = next(c for c in analysis["cases"] if c["case_id"] == CASE)["metrics"]
    assert {m["method"] for m in metrics} == {
        "v1",
        "decoder-graph",
        "lowest-summed-error",
        "qiskit-vf2",
    }
    for m in metrics:
        assert m["delta"] == 0.1
        lost = m["kept_gain_lost"]["estimate"]
        assert lost == pytest.approx(
            m["kept_gain_true_weights"]["estimate"] - m["kept_gain_stale_weights"]["estimate"]
        )
        if m["gated"]:
            assert m["m6_true_weights"]["estimate"] == pytest.approx(m["m6_v2"]["estimate"])


def test_regrade_stops_when_v2_counts_differ(tmp_path: Path) -> None:
    raw = json.loads((rg.ROOT / "results_v2" / "runs" / f"{CASE}.json").read_text("utf-8"))
    raw["placements"][0]["per_seed"][0]["logical_failures"] += 1
    v2_runs = tmp_path / "v2"
    v2_runs.mkdir()
    (v2_runs / f"{CASE}.json").write_text(json.dumps(raw), encoding="utf-8")
    config = _config(tmp_path, v2_runs)
    with pytest.raises(RuntimeError, match="does not reproduce v2's counts"):
        _run(config, tmp_path)
