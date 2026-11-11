"""K17 check: how many distinct calibration snapshots each release of qiskit-ibm-runtime ships.

Usage::

    python scripts/scan_fake_backend_snapshots.py [--clone PATH]
        [--out results_v3/x16/snapshot_scan.json]

Each fake backend ships one ``props_<device>.json``; if a device's file changed between
releases, the releases hold more than one snapshot of it, and a second snapshot would answer
K17 (SPEC_V3.md) for that device the way the drift runs do for the development devices.
This script answers the question from git blob hashes alone. It uses a blobless, bare clone of
the public ``Qiskit/qiskit-ibm-runtime`` repository (``--clone``, or a fresh one in a
temporary directory: trees only, no file contents), lists every release tag's tree with
``git ls-tree`` and keeps the blob hash of every file named ``props_<device>.json`` for the
devices below. It also lists every commit on any branch that added, changed or deleted such
a file, with the blob hashes ``git log --raw`` reports (tree diffs only, rename detection
off). It never runs ``git cat-file`` or ``git show`` on a blob and opens no installed file, so
no file's contents are fetched or read (X16).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
REPO = "https://github.com/Qiskit/qiskit-ibm-runtime"
DEVICES = ("boston", "pittsburgh", "berlin", "miami")
NAME = re.compile(r"^props_(" + "|".join(DEVICES) + r")\.json$")
RELEASE = re.compile(r"^\d+\.\d+\.\d+$")


def _git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _version(tag: str) -> tuple[int, ...]:
    return tuple(int(part) for part in tag.split("."))


def release_tags(clone: Path) -> list[str]:
    """Every release tag (``X.Y.Z``), oldest first."""

    return sorted(
        (t for t in _git("tag", "-l", cwd=clone).split() if RELEASE.match(t)), key=_version
    )


def props_blobs(clone: Path, tag: str) -> dict[str, dict[str, str]]:
    """Per device, the path and blob hash of its props file at ``tag`` (trees only)."""

    out: dict[str, dict[str, str]] = {}
    for line in _git("ls-tree", "-r", "--full-tree", tag, cwd=clone).splitlines():
        meta, path = line.split("\t", 1)
        match = NAME.match(path.rsplit("/", 1)[-1])
        if match and meta.split()[1] == "blob":
            device = match.group(1)
            if device in out:
                raise RuntimeError(f"{tag}: two props files for {device}")
            out[device] = {"path": path, "blob": meta.split()[2]}
    return out


def history(clone: Path, device: str) -> list[dict[str, str]]:
    """Every commit on any branch that touched ``props_<device>.json``, with its blobs."""

    out: list[dict[str, str]] = []
    text = _git(
        "log",
        "--all",
        "--no-renames",
        "--no-abbrev",
        "--raw",
        "--format=C %H %cs",
        "--",
        f":(glob)**/props_{device}.json",
        cwd=clone,
    )
    commit = date = ""
    for line in text.splitlines():
        if line.startswith("C "):
            _c, commit, date = line.split()
        elif line.startswith(":"):
            meta, path = line.split("\t", 1)
            fields = meta.split()
            out.append(
                {
                    "commit": commit,
                    "date": date,
                    "status": fields[4],
                    "old_blob": fields[2],
                    "new_blob": fields[3],
                    "path": path,
                }
            )
    return out


def scan(clone: Path) -> dict[str, Any]:
    tags = release_tags(clone)
    per_tag = {tag: props_blobs(clone, tag) for tag in tags}
    devices: dict[str, Any] = {}
    for device in DEVICES:
        snapshots: dict[str, dict[str, list[str]]] = {}
        for tag in tags:
            entry = per_tag[tag].get(device)
            if entry is None:
                continue
            blob = snapshots.setdefault(entry["blob"], {"tags": [], "paths": []})
            blob["tags"].append(tag)
            if entry["path"] not in blob["paths"]:
                blob["paths"].append(entry["path"])
        changes = history(clone, device)
        ever = {c[k] for c in changes for k in ("old_blob", "new_blob")} - {"0" * 40}
        devices[device] = {
            "distinct_snapshots": len(snapshots),
            "distinct_blobs_in_history": len(ever),
            "history": changes,
            "first_tag": next((t for t in tags if device in per_tag[t]), None),
            "last_tag": next((t for t in reversed(tags) if device in per_tag[t]), None),
            "blobs": snapshots,
        }
    return {
        "schema": "noise-aware-compilation.fake-backend-snapshot-scan/1",
        "description": __doc__,
        "repository": REPO,
        "release_tags_scanned": tags,
        "devices": devices,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--clone", type=Path, default=None, help="an existing blobless clone")
    parser.add_argument(
        "--out", type=Path, default=ROOT / "results_v3" / "x16" / "snapshot_scan.json"
    )
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        clone = args.clone
        if clone is None:
            clone = Path(tmp) / "runtime.git"
            _git("clone", "--bare", "--filter=blob:none", "--no-checkout", "-q", REPO, str(clone))
        result = scan(clone)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    for device, info in result["devices"].items():
        print(
            f"{device}: {info['distinct_snapshots']} distinct props blob(s) in releases, "
            f"{info['distinct_blobs_in_history']} in all history",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
