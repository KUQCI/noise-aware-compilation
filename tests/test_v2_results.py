"""Checks on the committed v2 results and REPORT_V2.md (SPEC_V2.md section 7).

T2-S1 (on the results), T2-S4 to T2-S6, T2-G2 to T2-G4, T2-R1, T2-R2, T2-P1 to
T2-P4 and R2-R7. These read ``results_v2/runs/*.json``, ``configs_v2/`` and
``REPORT_V2.md``.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from nac_bench.analysis_v2 import load, r_g7
from nac_bench.run_v2 import cases, load_config, run_case
from noise_aware_compilation.api import convert_device
from noise_aware_compilation.devices import stored_hashes
from noise_aware_compilation.scoring import DEFAULT_SCORER

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "configs_v2" / "benchmark_v2.json"
RUNS = ROOT / "results_v2" / "runs"
REPORT = ROOT / "REPORT_V2.md"
FREEZE = json.loads((ROOT / "configs_v2" / "freeze.json").read_text(encoding="utf-8"))
#: Last v1 commit (SPEC.md rev 4); v2 branches off it.
V1_TIP = "d86835f6642edc69bcaa7168c8f99dc1f79a807b"
NOISE_AWARE = ("v1", "decoder-graph", "lowest-summed-error", "qiskit-vf2")
SCORE_NAMES = {"v1": "v1 score", "decoder-graph": "decoder-graph score"}
METHOD_NAMES = {
    **SCORE_NAMES,
    "lowest-summed-error": "lowest summed error",
    "qiskit-vf2": "Qiskit VF2Layout",
}

pytestmark = [
    pytest.mark.results,
    pytest.mark.skipif(not any(RUNS.glob("*.json")), reason="v2 results not generated yet"),
]


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def _is_ancestor(older: str, newer: str) -> bool:
    check = subprocess.run(["git", "merge-base", "--is-ancestor", older, newer], cwd=ROOT)
    return check.returncode == 0


@pytest.fixture(scope="module")
def config() -> dict[str, Any]:
    return load_config(CONFIG)


@pytest.fixture(scope="module")
def runs(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out = {}
    for case in cases(config):
        path = RUNS / f"{case['case_id']}.json"
        assert path.exists(), f"missing results file {path.name}"
        out[case["case_id"]] = json.loads(path.read_text(encoding="utf-8"))
    return out


@pytest.fixture(scope="module")
def report() -> str:
    return REPORT.read_text(encoding="utf-8")


def _drift_runs(runs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in runs.values() if r["case"]["drift"]]


def test_every_configured_case_has_one_results_file(
    config: dict[str, Any], runs: dict[str, dict[str, Any]]
) -> None:
    on_disk = {p.stem for p in RUNS.glob("*.json")}
    assert on_disk == {c["case_id"] for c in cases(config)}
    assert all(r["schema"] == "noise-aware-compilation.run-v2/1" for r in runs.values())


def test_t2_s1_zero_drift_choices_equal_true_model_choices(
    runs: dict[str, dict[str, Any]],
) -> None:
    for run in _drift_runs(runs):
        true = run["true_model"]
        for record in run["drift"]["records"]:
            if record["delta"] != 0:
                continue
            assert record["seen_snapshot_sha256"] == true["snapshot_sha256"]
            for method in NOISE_AWARE:
                assert record["choices"][method]["mapping"] == true["choices"][method]["mapping"]


def test_t2_s4_one_seen_model_per_draw(runs: dict[str, dict[str, Any]]) -> None:
    for run in _drift_runs(runs):
        for record in run["drift"]["records"]:
            hashes = record["seen_hash_by_method"]
            assert set(hashes) == set(NOISE_AWARE)
            assert set(hashes.values()) == {record["seen_snapshot_sha256"]}


def test_t2_s5_graded_with_the_true_model(runs: dict[str, dict[str, Any]]) -> None:
    stored = stored_hashes()
    for run in runs.values():
        device = run["case"]["device"]
        true_hash = stored[device]["snapshot_sha256"]
        assert run["provenance"]["true_snapshot_sha256"] == true_hash
        assert run["true_model"]["snapshot_sha256"] == true_hash
        for p in run["placements"]:
            assert p["run_config"]["calibration_snapshot_sha256"] == true_hash
        for record in run["drift"]["records"] if run["case"]["drift"] else []:
            if record["delta"] > 0:
                assert record["seen_snapshot_sha256"] != true_hash


def test_t2_s6_draw_count(config: dict[str, Any], runs: dict[str, dict[str, Any]]) -> None:
    strengths = config["drift"]["strengths"]
    for run in _drift_runs(runs):
        count = Counter(record["delta"] for record in run["drift"]["records"])
        assert set(count) == set(strengths)
        assert all(n >= 20 for n in count.values()), (run["case"]["case_id"], count)


def test_t2_g2_freeze_before_held_out(runs: dict[str, dict[str, Any]]) -> None:
    freeze = FREEZE["commit"]
    algiers = sorted(f"results_v2/runs/{c}.json" for c in runs if c.startswith("v2-algiers-"))
    assert algiers
    first = _git("log", "--diff-filter=A", "--format=%H", "--reverse", "--", *algiers)
    first_commit = first.splitlines()[0]
    assert first_commit != freeze and _is_ancestor(freeze, first_commit)
    for path, blob in FREEZE["frozen_files_git_blobs"].items():
        assert _git("rev-parse", f"{freeze}:{path}") == blob, path
        assert _git("hash-object", path) == blob, f"{path} changed after the freeze"
    for case_id in runs:
        if case_id.startswith("v2-algiers-"):
            assert _is_ancestor(freeze, runs[case_id]["provenance"]["package_git_commit"])


def test_t2_g3_held_out_device_untouched(runs: dict[str, dict[str, Any]], tmp_path: Path) -> None:
    freeze = FREEZE["commit"]
    for pattern in ("results_v2/runs/v2-algiers-*", "data/devices/*algiers*"):
        added = _git("log", "--diff-filter=A", "--format=%H", "--", pattern).splitlines()
        assert added, pattern
        for commit in added:
            assert commit != freeze and _is_ancestor(freeze, commit), (pattern, commit)
    converted = convert_device(
        ROOT / "data" / "devices" / "conf_algiers.json",
        ROOT / "data" / "devices" / "props_algiers.json",
        tmp_path,
        name="algiers",
    )
    stored = stored_hashes()["algiers"]
    assert converted.snapshot_sha256 == stored["snapshot_sha256"]
    assert converted.device_sha256 == stored["device_sha256"]
    for case_id, run in runs.items():
        if case_id.startswith("v2-algiers-"):
            assert run["provenance"]["device_sha256"] == stored["device_sha256"]


def test_t2_g4_default_score_follows_r_g7(config: dict[str, Any]) -> None:
    results = load(RUNS, config)
    held = [r for r in results.values() if r.role == "held-out"]
    assert len(held) == 4
    assert r_g7(held).default_scorer == DEFAULT_SCORER


@pytest.mark.slow
def test_t2_r1_rerun_is_bit_identical(
    config: dict[str, Any], runs: dict[str, dict[str, Any]], tmp_path: Path
) -> None:
    case = next(c for c in cases(config) if c["case_id"] == "v2-jakarta-A")
    rerun = json.loads(
        run_case(case, config, workers=2, out_dir=tmp_path).read_text(encoding="utf-8")
    )
    stored = runs["v2-jakarta-A"]

    def counts(run: dict[str, Any]) -> list[Any]:
        return [
            (p["mapping"], [(ps["chunk_seeds"], ps["logical_failures"]) for ps in p["per_seed"]])
            for p in run["placements"]
        ]

    def selections(run: dict[str, Any]) -> list[Any]:
        true = {m: c["mapping"] for m, c in run["true_model"]["choices"].items()}
        seen = [
            (
                r["delta"],
                r["draw"],
                r["seen_snapshot_sha256"],
                {m: c["mapping"] for m, c in r["choices"].items()},
            )
            for r in run["drift"]["records"]
        ]
        return [true, seen]

    assert counts(rerun) == counts(stored)
    assert selections(rerun) == selections(stored)


def test_t2_r2_provenance(config: dict[str, Any], runs: dict[str, dict[str, Any]]) -> None:
    config_sha = hashlib.sha256(CONFIG.read_bytes()).hexdigest()
    config_commit = _git("log", "-1", "--format=%H", "--", "configs_v2/benchmark_v2.json")
    for case_id, run in runs.items():
        prov = run["provenance"]
        assert prov["nove_commit"].startswith("485b413")
        assert prov["stim_version"] and prov["pymatching_version"]
        assert prov["qiskit_ibm_runtime_version"] == "0.50.0"
        assert prov["qiskit_version"] == config["qiskit_version"]
        assert len(prov["source_files"]) == 2
        for field in (
            "calibration_snapshot_sha256",
            "device_sha256",
            "noise_model_sha256",
            "true_snapshot_sha256",
        ):
            assert len(prov[field]) == 64, (case_id, field)
        assert prov["package_git_dirty"] is False, case_id
        assert _is_ancestor(config_commit, prov["package_git_commit"]), case_id
        assert run["config_file_sha256"] == config_sha, case_id
        assert set(run["methods"]) == set(NOISE_AWARE)
        assert run["qiskit_settings"]["seed"] == -1
        assert set(run["true_model"]["choices"]) == set(NOISE_AWARE)
        assert run["decoder"]["decoder_model"] == "approximate"
        if run["case"]["drift"]:
            for record in run["drift"]["records"]:
                for field in ("delta", "draw", "draw_seed", "seen_snapshot_sha256"):
                    assert record[field] is not None, (case_id, field)
                assert set(record["choices"]) == set(NOISE_AWARE)


def test_v2_seeds_disjoint_from_v1(runs: dict[str, dict[str, Any]]) -> None:
    v1: set[int] = set()
    for path in (ROOT / "results" / "runs").glob("*.json"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        v1 |= set(raw["seeds"]["base_seeds"]) | set(raw["seeds"]["selection_seeds"])
        v1 |= {s for p in raw["placements"] for ps in p["per_seed"] for s in ps["chunk_seeds"]}
    assert v1
    for run in runs.values():
        v2 = set(run["seeds"]["base_seeds"]) | set(run["seeds"]["drift_seeds"])
        v2 |= {s for p in run["placements"] for ps in p["per_seed"] for s in ps["chunk_seeds"]}
        assert not v1 & v2, run["case"]["case_id"]


def _drift_rows(report: str, title: str) -> dict[tuple[str, str], list[str]]:
    section = report.split("## 3.")[1].split("## 4.")[0]
    block = section.split(f"### {title}\n")[1].split("### ")[0]
    rows = {}
    for line in block.splitlines():
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) == 6 and cells[0] in METHOD_NAMES.values():
            rows[(cells[0], cells[1])] = cells
    return rows


def _titles(report: str) -> list[str]:
    section = report.split("## 3.")[1].split("## 4.")[0]
    return [line[4:].strip() for line in section.splitlines() if line.startswith("### ")]


def test_t2_p1_p2_retained_gain_and_harm_rate(
    config: dict[str, Any], runs: dict[str, dict[str, Any]], report: str
) -> None:
    titles = _titles(report)
    assert len(titles) == len(_drift_runs(runs))
    for title in titles:
        rows = _drift_rows(report, title)
        for method in NOISE_AWARE:
            for delta in config["drift"]["strengths"]:
                if delta == 0:
                    continue
                cells = rows[(METHOD_NAMES[method], f"{delta:g}")]
                assert "[" in cells[2] or cells[2] == "no gain to retain", (title, method, delta)
                assert "[" in cells[3], (title, method, delta)


def test_t2_p3_baseline_comparison(runs: dict[str, dict[str, Any]], report: str) -> None:
    section = report.split("## 2.")[1].split("## 3.")[0]
    blocks = section.split("\n### ")[1:]
    assert len(blocks) == len(runs)
    for block in blocks:
        for score in SCORE_NAMES.values():
            for reference in (
                "fixed",
                "all-placement average",
                "lowest summed error",
                "Qiskit VF2Layout",
            ):
                rows = [
                    line
                    for line in block.splitlines()
                    if line.startswith(f"| {score}") and f"| {reference}" in line
                ]
                assert len(rows) == 1, (block.splitlines()[0], score, reference)
                assert "pooled:" in rows[0] and "seeds)" in rows[0]
    assert "No two verdicts for one comparison are opposite across seeds: **yes**" in section


def test_t2_p4_held_out_comparison(runs: dict[str, dict[str, Any]], report: str) -> None:
    section = report.split("## 4.")[1].split("## 5.")[0]
    held = [r for r in runs.values() if r["case"]["role"] == "held-out"]
    assert len(held) == 4
    for score in SCORE_NAMES.values():
        rows = [line for line in section.splitlines() if f"| {score} |" in line]
        assert len(rows) >= len(held), score
    assert "R-G7 calculation" in section
    assert "This verdict covers only these four cases" in section
    assert "This verdict covers only these four cases" in report.split("## 2.")[0]


def test_r2_r7_development_devices_labelled(report: str) -> None:
    header = report.split("## 1.")[0]
    assert "development-device results" in header
    assert "Only FakeAlgiers is the real test" in header
    section = report.split("## 2.")[1].split("## 3.")[0]
    for line in section.splitlines():
        if line.startswith("### FakeJakartaV2") or line.startswith("### FakeHanoiV2"):
            assert line.endswith("(development device)"), line
        elif line.startswith("### FakeAlgiers"):
            assert line.endswith("(held-out device)"), line


def test_figures_exist(report: str) -> None:
    for line in report.splitlines():
        if line.startswith("!["):
            assert (ROOT / line.split("](")[1].rstrip(")")).exists(), line


def test_v1_results_untouched() -> None:
    # X14: v2 adds files; it never changes v1's config, results or report.
    changed = _git("diff", "--name-only", V1_TIP, "HEAD", "--", "configs", "results", "REPORT.md")
    assert changed == ""
