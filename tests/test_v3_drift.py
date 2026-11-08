"""The v3 real-drift runner (SPEC_V3.md Part B, P2-22) on small cases."""

from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("qiskit")
pytest.importorskip("mapomatic")

from nac_bench import run_drift
from nac_bench.baselines_v3 import mapomatic_version
from nac_bench.qiskit_layout import qiskit_version

CONFIG = run_drift.ROOT / "configs_v3" / "drift.json"


def _small_config(tmp_path: Path) -> dict[str, Any]:
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    data["shots_rule"] = {
        "pilot_shots": 1_000,
        "min_shots_per_seed": 2_000,
        "max_shots_per_seed": 8_000,
        "expected_failures": 30,
    }
    data["nove_checks_per_case"] = 5
    data["bootstrap_draws"] = 500
    data["qiskit_version"] = qiskit_version()
    data["mapomatic_version"] = mapomatic_version()
    path = tmp_path / "drift.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return run_drift.load_config(path)


def test_cases() -> None:
    config = run_drift.load_config(CONFIG)
    ids = [c["case_id"] for c in run_drift.cases(config)]
    assert len(ids) == len(set(ids)) == 4 * 4 * 2
    assert ids[0] == "v3-drift-readout-hanoi-14d-A"
    assert all(i.startswith("v3-drift-readout-") for i in ids[:16])
    assert config["headline_method"] in config["methods"] and "naive" in config["methods"]


def test_grading_model_puts_dead_couplers_at_the_limit() -> None:
    chip, old, new = "fez", "2025-01-23", "2025-02-26"
    dead = run_drift.dead_couplers(chip, old, new)
    assert len(dead) == 6  # P2-21
    assert run_drift.dead_couplers("hanoi", "2025-02-12", "2025-02-26") == ()
    old_device, _ = run_drift._snapshot_model(chip, old, "readout")
    new_device, new_noise = run_drift._snapshot_model(chip, new, "readout")
    device, noise = run_drift._grading_model(chip, old, new, "readout")
    pairs = {run_drift._pair_key(*p) for p in device.connectivity}
    assert pairs == {run_drift._pair_key(*p) for p in new_device.connectivity} | set(dead)
    assert {run_drift._pair_key(*p) for p in old_device.connectivity} <= pairs
    for key, channel in noise.gate_channels.items():
        if len(key.targets) != 2:
            continue
        if run_drift._pair_key(*key.targets) in dead:
            probabilities = channel.probabilities
            assert len(probabilities) == 15
            assert sum(probabilities) == pytest.approx(1.0, abs=1e-12)
            assert max(probabilities) == min(probabilities)
        else:
            assert channel == new_noise.gate_channels[key]
    assert noise.readout_flip_probabilities == new_noise.readout_flip_probabilities
    assert noise.reset_flip_probabilities == new_noise.reset_flip_probabilities


def _untimed(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: _untimed(v)
            for k, v in value.items()
            if not k.endswith("seconds") and k not in ("machine", "written_at_utc")
        }
    if isinstance(value, list):
        return [_untimed(v) for v in value]
    return value


def test_run_case_hanoi(tmp_path: Path) -> None:
    config = _small_config(tmp_path)
    case = next(
        c for c in run_drift.cases(config) if c["case_id"] == "v3-drift-readout-hanoi-14d-A"
    )
    with ProcessPoolExecutor(max_workers=2) as pool:
        first = run_drift.run_case(
            case, config, pool, workers=2, out_dir=tmp_path / "a", machine_label=None
        )
        second = run_drift.run_case(
            case, config, pool, workers=2, out_dir=tmp_path / "b", machine_label=None
        )
    run = json.loads(first.read_text(encoding="utf-8"))
    assert _untimed(run) == _untimed(json.loads(second.read_text(encoding="utf-8")))

    simulated = {tuple(r["mapping"]): r for r in run["simulated"]}
    for when in ("stale", "fresh"):
        for name, entry in run[when]["methods"].items():
            assert f"{when}:{name}" in simulated[tuple(entry["mapping"])]["roles"]
    robust = run["extra_methods"]["robust-mean"]
    assert "stale:robust-mean" in simulated[tuple(robust["mapping"])]["roles"]
    rule = config["shots_rule"]
    for row in run["simulated"]:
        assert row["decoders"] == ["stale", "new"]  # no coupler died on FakeHanoi
        assert row["dead_couplers_used"] == []
        assert rule["min_shots_per_seed"] <= row["shots_per_seed"] <= rule["max_shots_per_seed"]
        for seed in row["per_seed"]:
            stale, new = seed["decoders"]["stale"], seed["decoders"]["new"]
            # both decoders decode the same samples
            assert stale["detector_fires"] == new["detector_fires"]
            assert stale["shots"] == new["shots"] == row["shots_per_seed"]

    metrics = run_drift.metrics(run, config)
    assert "naive" not in metrics
    for name, record in metrics.items():
        if record["stale_choice"] == run["stale"]["methods"]["naive"]["mapping"]:
            assert record["kept_gain"] == {"estimate": 0.0, "low": 0.0, "high": 0.0}
        if name == "robust-mean":
            assert record["fresh_choice"] is None and "fresh_gain" not in record
            assert (
                record["against_headline_stale_choice"]["headline_choice"]
                == (run["stale"]["methods"]["decoder-graph"]["mapping"])
            )
        else:
            assert "fresh_gain" in record
        if "m6" in record:
            assert record["fresh_gain_stable"]


def _row(mapping: list[int], failures: dict[str, int], shots: int) -> dict[str, Any]:
    return {
        "mapping": mapping,
        "per_seed": [
            {
                "base_seed": b,
                "decoders": {
                    d: {"logical_failures": f, "shots": shots} for d, f in failures.items()
                },
            }
            for b in (1, 2, 3)
        ],
    }


def test_metrics_and_m6() -> None:
    config = run_drift.load_config(CONFIG)
    shots = 1_000_000
    run = {
        "case": {"case_id": "synthetic"},
        "stale": {"methods": {"naive": {"mapping": [0]}, "decoder-graph": {"mapping": [1]}}},
        "fresh": {"methods": {"naive": {"mapping": [0]}, "decoder-graph": {"mapping": [2]}}},
        "extra_methods": {},
        "simulated": [
            _row([0], {"stale": 20_000, "new": 19_000}, shots),
            _row([1], {"stale": 15_000, "new": 14_000}, shots),
            _row([2], {"new": 9_000}, shots),
        ],
    }
    record = run_drift.metrics(run, config)["decoder-graph"]
    assert record["choice_changed"]
    assert record["kept_gain"]["estimate"] == pytest.approx(0.005)
    assert record["kept_gain_new_weights"]["estimate"] == pytest.approx(0.005)
    assert record["fresh_gain"]["estimate"] == pytest.approx(0.010)
    assert record["fresh_gain_stable"]
    m6 = record["m6"]
    assert m6["estimate"] == pytest.approx(0.5)
    assert m6["low"] < 0.5 < m6["high"]
    assert m6["finite_draws"] == m6["draws"] == config["bootstrap_draws"]
    # the same draws again from the same seed
    assert run_drift.metrics(run, config)["decoder-graph"]["m6"] == m6
