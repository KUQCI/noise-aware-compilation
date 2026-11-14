"""v3 reset run: the reset = readout setting, its config and its committed results."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from nac_bench import run_reset
from nac_bench.baselines_v3 import native_target
from noise_aware_compilation.circuits import repetition_code
from noise_aware_compilation.devices import load_device
from noise_aware_compilation.evaluation import derive_seed
from noise_aware_compilation.placement import enumerate_placements

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "configs_v3" / "reset_sensitivity.json"
RUNS = ROOT / "results_v3" / "reset" / "runs"
RESET = ROOT / "results_v3" / "reset" / "reset.json"


def _config() -> dict[str, Any]:
    return run_reset.load_config(CONFIG)


def test_native_target_carries_the_readout_error_on_reset_only_when_asked() -> None:
    stored = load_device("hanoi")
    published = native_target(stored.snapshot, stored.device)
    readout = native_target(stored.snapshot, stored.device, "readout")
    for q in range(len(stored.snapshot.qubit_parameters)):
        assert published["reset"][(q,)].error is None
        assert readout["reset"][(q,)].error == readout["measure"][(q,)].error
        assert readout["measure"][(q,)].error == published["measure"][(q,)].error
    with pytest.raises(ValueError):
        native_target(stored.snapshot, stored.device, "half")


@pytest.mark.parametrize("device", ["jakarta", "hanoi", "algiers"])
def test_reset_readout_noise_sets_every_reset_to_the_readout_flip(device: str) -> None:
    stored = load_device(device)
    zero = run_reset.noise_for("zero", stored)
    readout = run_reset.noise_for("readout", stored)
    assert zero.to_dict() == stored.noise.to_dict()
    for q in range(len(stored.snapshot.qubit_parameters)):
        assert (zero.reset_probability_for(q) or 0.0) == 0.0
        p = readout.readout_probability_for(q)
        assert p is not None and p > 0
        assert readout.reset_probability_for(q) == p
        assert readout.readout_probability_for(q) == zero.readout_probability_for(q)


def test_config_cases_and_fixed_baselines() -> None:
    config = _config()
    cases = run_reset.cases(config)
    ids = [c["case_id"] for c in cases]
    assert len(ids) == len(set(ids)) == 18
    assert sum(1 for c in cases if c["setting"] == "readout") == 14
    readout_v2 = {
        (c["device"], c["circuit"]["label"])
        for c in cases
        if c["setting"] == "readout" and c["circuit"]["label"] in ("A", "B", "D5", "D7")
    }
    v2 = json.loads((ROOT / "configs_v2" / "benchmark_v2.json").read_text(encoding="utf-8"))
    assert readout_v2 == {(d["name"], label) for d in v2["devices"] for label in d["circuits"]}
    for c in cases:
        spec = c["circuit"]
        circuit = repetition_code(spec["distance"], spec["rounds"], spec["basis"])
        first = enumerate_placements(circuit, load_device(c["device"]).device)[0]
        assert tuple(config["fixed_baselines"][f"{c['device']}-{spec['label']}"]) == first
    for label in ("A", "B", "D5", "D7"):
        assert config["circuits"][label] == v2["circuits"][label]


def test_v2_circuit_a_is_v1_circuit_a3() -> None:
    config = _config()
    for label, v1_name in (("A1", "hanoi-A1-N2"), ("A", "hanoi-A3-N2"), ("A5", "hanoi-A5-N2")):
        spec = config["circuits"][label]
        circuit = repetition_code(spec["distance"], spec["rounds"], spec["basis"])
        path = ROOT / "results" / "runs" / f"{v1_name}.json"
        v1 = json.loads(path.read_text(encoding="utf-8"))
        assert circuit.sha256 == v1["provenance"]["circuit_sha256"]


def test_evaluation_seeds_follow_the_config_rule() -> None:
    config = _config()
    assert "eval-v3-reset" in config["evaluation_seed_rule"]
    seed = run_reset.eval_chunk_seed(20261108, "v3-reset-readout-hanoi-A", (0, 1, 2, 3, 5), 0)
    assert seed == derive_seed(
        "eval-v3-reset", 20261108, "v3-reset-readout-hanoi-A", "0,1,2,3,5", 0
    )


# --- committed results -----------------------------------------------------------------------


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        pytest.skip(f"{path.relative_to(ROOT)} not committed")
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _is_ancestor(commit: str) -> bool:
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", commit, "HEAD"], cwd=ROOT, check=False
        ).returncode
        == 0
    )


def _committed_before(path: str, commit: str) -> bool:
    """The config at ``path`` was committed in ``commit`` or an ancestor of it."""

    first = subprocess.run(
        ["git", "log", "--follow", "--diff-filter=A", "--format=%H", "--", path],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    if not first:
        return False
    check = ["git", "merge-base", "--is-ancestor", first[-1], commit]
    return subprocess.run(check, cwd=ROOT, check=False).returncode == 0


@pytest.mark.results
def test_reset_runs_are_complete_clean_and_pre_registered() -> None:
    config = _config()
    sha = hashlib.sha256(CONFIG.read_bytes()).hexdigest()
    for case in run_reset.cases(config):
        raw = _load(RUNS / f"{case['case_id']}.json")
        assert raw["config_file_sha256"] == sha
        prov = raw["provenance"]
        assert prov["package_git_dirty"] is False
        assert _is_ancestor(prov["package_git_commit"])
        assert _committed_before("configs_v3/reset_sensitivity.json", prov["package_git_commit"])
        assert raw["setting"] == case["setting"]
        for p in raw["placements"]:
            for s in p["per_seed"]:
                assert s["chunk_seeds"][0] == run_reset.eval_chunk_seed(
                    s["base_seed"], case["case_id"], p["mapping"], 0
                )
        for name, record in raw["baselines"].items():
            if name.startswith("qiskit-default"):
                assert record["swap_free"], (case["case_id"], name)


@pytest.mark.results
def test_reset_analysis_family_and_zero_counterpart() -> None:
    result = _load(RESET)
    assert result["holm"]["reset-headline"]["size"] == 40
    zero = result["holm"]["reset-headline at reset 0"]
    phase1 = _load(ROOT / "results_v3" / "reanalysis" / "reanalysis.json")["holm"]["v2-headline"]
    held = sorted((m["case_id"], m["reference"], m["holds_after_holm"]) for m in zero["members"])
    held1 = sorted((m["case_id"], m["reference"], m["holds_after_holm"]) for m in phase1["members"])
    assert held == held1
    assert result["provenance"]["package_git_dirty"] is False
