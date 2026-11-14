"""X16 and T3-F1: the guard the device scripts run before they touch a held-out device.

A held-out device (SPEC_V3.md section 3) may be fetched or converted only after freeze step
F4 (section 8): F4, the last commit that touches ``configs_v3/held_out.json``, must exist,
both F3 commits must precede it, it must carry an annotated ``v3-held-out-config`` tag (the
tags are pushed and their hashes recorded with a server-side timestamp before any
conversion), HEAD must descend from it, and the checkout must be clean apart from the
device files the scripts write. :func:`require_after_f4` raises otherwise, before any held-out
file is opened.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELD_OUT_CONFIG = "configs_v3/held_out.json"
F3_RECORD = ROOT / "configs_v3" / "freeze_f3.json"
F4_TAG_PREFIX = "v3-held-out-config"
#: what the device scripts write; everything else must match HEAD
OUTPUTS = ("data/devices", "../src/noise_aware_compilation/device_data")


class FreezeOrderError(RuntimeError):
    """A held-out device was requested before F4 or from an unclean checkout."""


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def _is_ancestor(a: str, b: str) -> bool:
    command = ["git", "merge-base", "--is-ancestor", a, b]
    return subprocess.run(command, cwd=ROOT, capture_output=True).returncode == 0


def f4_commit() -> str:
    """F4: the last commit that touches the held-out config (empty if none)."""

    return _git("log", "-1", "--format=%H", "--", HELD_OUT_CONFIG)


def require_after_f4() -> str:
    """Return F4's commit if a held-out device may be fetched or converted now; else raise."""

    f4 = f4_commit()
    if not f4:
        raise FreezeOrderError("F4 has not happened: configs_v3/held_out.json is not committed")
    record = json.loads(F3_RECORD.read_text(encoding="utf-8"))
    for f3 in record["commits"]:
        if not _is_ancestor(f3["commit"], f4):
            raise FreezeOrderError(f"F3 commit {f3['tag']} does not precede F4 {f4}")
    if not _is_ancestor(f4, "HEAD"):
        raise FreezeOrderError(f"HEAD does not descend from F4 {f4}")
    tags = [
        t
        for t in _git("tag", "--points-at", f4).split()
        if t.startswith(F4_TAG_PREFIX) and _git("cat-file", "-t", t) == "tag"
    ]
    if not tags:
        raise FreezeOrderError(
            f"F4 {f4} carries no annotated {F4_TAG_PREFIX} tag here; push the tag, record its "
            "hash on the GitHub issue, then run 'git fetch --tags'"
        )
    excluded = [f":!{path}" for path in OUTPUTS]
    status = _git("status", "--porcelain", "--untracked-files=all", "--", ".", *excluded)
    if status:
        raise FreezeOrderError(f"uncommitted changes outside the device outputs:\n{status}")
    return f4


__all__ = ["FreezeOrderError", "f4_commit", "require_after_f4"]
