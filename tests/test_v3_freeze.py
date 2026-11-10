"""SPEC_V3.md section 8: the score freeze (F3, X18), its second commit and the freeze order.

F3 has two commits (revision 4 reopened it for the surface-code *k*, revision 5's X18
allows exactly that). F4 is the last commit that touches the held-out config, and every
held-out converted file and run must descend from it (T3-F1).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
F3 = json.loads((ROOT / "configs_v3" / "freeze_f3.json").read_text(encoding="utf-8"))
FIRST, SECOND = F3["commits"]
CONFIG = json.loads((ROOT / "configs_v3" / "held_out.json").read_text(encoding="utf-8"))
#: every device a held-out converted file could name, including the seen ones
HELD_OUT = tuple(CONFIG["devices"])


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def _is_ancestor(a: str, b: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", a, b], cwd=ROOT).returncode == 0


@pytest.fixture(scope="module")
def history() -> None:
    try:
        for record in F3["commits"]:
            _git("cat-file", "-e", f"{record['commit']}^{{commit}}")
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("an F3 commit is not in this checkout's history")


def test_held_out_devices_are_the_config_devices() -> None:
    assert set(HELD_OUT) == {"boston", "pittsburgh", "berlin", "kingston", "aachen", "nighthawk"}


def test_f3_frozen_files_unchanged(history: None) -> None:
    for path, blob in F3["frozen_files_git_blobs"].items():
        assert _git("rev-parse", f"{FIRST['commit']}:{path}") == blob, path
        assert _git("hash-object", path) == blob, f"{path} changed after F3 (X18)"


def test_f3_score_freeze_blobs(history: None) -> None:
    for record in F3["commits"]:
        blob = F3["score_freeze_git_blobs"][record["commit"]]
        assert _git("rev-parse", f"{record['commit']}:configs_v3/score_freeze.json") == blob


def test_f3_parameters_unchanged(history: None) -> None:
    """Each commit's parameters are as it froze them; the second added only the surface k."""

    now = json.loads((ROOT / "configs_v3" / "score_freeze.json").read_text(encoding="utf-8"))
    first = json.loads(_git("show", f"{FIRST['commit']}:configs_v3/score_freeze.json"))
    second = json.loads(_git("show", f"{SECOND['commit']}:configs_v3/score_freeze.json"))
    assert second == now
    for key in FIRST["parameters"]:
        assert first[key] == now[key], key
    added = set(now) - set(first)
    assert {k for k in added if not k.endswith("_source")} == set(SECOND["parameters"])
    assert now["surface_scaled_score"] == CONFIG["surface_scaled_score"]


def test_second_f3_commit_changes_only_the_score_freeze(history: None) -> None:
    assert _is_ancestor(FIRST["commit"], SECOND["commit"])
    changed = _git("diff", "--name-only", f"{SECOND['commit']}^", SECOND["commit"]).split()
    assert changed == ["configs_v3/score_freeze.json"]


def _held_out_files() -> list[str]:
    out = []
    for name in HELD_OUT:
        for pattern in (
            f"data/devices/*{name}*",
            f"src/noise_aware_compilation/device_data/{name}_*",
        ):
            out += _git("log", "--diff-filter=A", "--format=%H", "--", pattern).split()
    return out


def f4_commit() -> str:
    """F4: the last commit that touches the held-out config."""

    return _git("log", "-1", "--format=%H", "--", "configs_v3/held_out.json")


def test_t3_f1_freeze_order(history: None) -> None:
    """Both F3 commits precede F4, which precedes every held-out converted file and run."""

    f4 = f4_commit()
    assert f4, "F4's config is not committed"
    for f3 in F3["commits"]:
        assert _is_ancestor(f3["commit"], f4), f3["tag"]
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
        assert _is_ancestor(FIRST["commit"], made_by), path.name
        assert _is_ancestor(f4[-1], made_by), path.name
        assert record["provenance"]["package_git_dirty"] is False, path.name
