"""SPEC_V3.md section 8: the score freeze (F3, X18) and the freeze order (T3-F1)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
F3 = json.loads((ROOT / "configs_v3" / "freeze_f3.json").read_text(encoding="utf-8"))
HELD_OUT = ("kingston", "aachen", "berlin", "nighthawk")


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def _is_ancestor(a: str, b: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", a, b], cwd=ROOT).returncode == 0


@pytest.fixture(scope="module")
def history() -> None:
    try:
        _git("cat-file", "-e", f"{F3['commit']}^{{commit}}")
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("the F3 commit is not in this checkout's history")


def test_f3_frozen_files_unchanged(history: None) -> None:
    for path, blob in F3["frozen_files_git_blobs"].items():
        assert _git("rev-parse", f"{F3['commit']}:{path}") == blob, path
        assert _git("hash-object", path) == blob, f"{path} changed after F3 (X18)"


def test_f3_parameters_unchanged(history: None) -> None:
    at_freeze = json.loads(_git("show", f"{F3['commit']}:configs_v3/score_freeze.json"))
    now = json.loads((ROOT / "configs_v3" / "score_freeze.json").read_text(encoding="utf-8"))
    assert now == at_freeze


def _held_out_files() -> list[str]:
    out = []
    for name in HELD_OUT:
        for pattern in (
            f"data/devices/*{name}*",
            f"src/noise_aware_compilation/device_data/{name}_*",
        ):
            out += _git("log", "--diff-filter=A", "--format=%H", "--", pattern).split()
    return out


def test_t3_f1_freeze_order(history: None) -> None:
    """F3 precedes F4's config, which precedes every held-out converted file and run."""

    config = ROOT / "configs_v3" / "held_out.json"
    if not config.exists():
        pytest.skip("F4's config is not committed yet")
    f4 = _git("log", "--diff-filter=A", "--format=%H", "--", "configs_v3/held_out.json").split()[-1]
    assert _is_ancestor(F3["commit"], f4)
    for commit in _held_out_files():
        assert commit != f4 and _is_ancestor(f4, commit), commit
    runs = sorted((ROOT / "results_v3" / "held_out" / "runs").glob("v3-*.json"))
    for path in runs:
        record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        made_by = record["provenance"]["package_git_commit"]
        assert _is_ancestor(f4, made_by), path.name
        assert record["provenance"]["package_git_dirty"] is False, path.name


def test_drift_runs_follow_f3_and_the_held_out_config(history: None) -> None:
    """P2-22 and P2-23: drift run files come from clean commits after F3 and F4's config."""

    runs = sorted((ROOT / "results_v3" / "drift" / "runs").glob("v3-drift-*.json"))
    runs += sorted((ROOT / "results_v3" / "regrade_v2_drift" / "runs").glob("v2-*.json"))
    if not runs:
        pytest.skip("no drift run file yet")
    f4 = _git("log", "--diff-filter=A", "--format=%H", "--", "configs_v3/held_out.json").split()
    assert f4, "a drift run exists before the held-out config was committed"
    for path in runs:
        record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        made_by = record["provenance"]["package_git_commit"]
        assert _is_ancestor(F3["commit"], made_by), path.name
        assert _is_ancestor(f4[-1], made_by), path.name
        assert record["provenance"]["package_git_dirty"] is False, path.name
