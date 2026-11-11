"""F4's code freeze (SPEC_V3.md revision 6, X17): the record of what the held-out runs run.

Usage::

    python -m nac_bench.freeze_f4 --write   # before the F4 commit: record the files
    python -m nac_bench.freeze_f4 --pin COMMIT TAG   # after it: pin F4's hash and tag
    python -m nac_bench.freeze_f4 --check   # the files at HEAD still match the record

``configs_v3/freeze_f4.json`` holds the git blob of every file the runs and the analysis use
(the bench code, the package source, the lock file, the spec and the held-out config), the
runner-source hash every run file records (``nac_bench_source_sha256``) and the lock file's
SHA-256. It is written in the F4 commit; a later commit pins F4's hash and tag, and touches
nothing else. ``tests/test_v3_freeze.py`` checks HEAD against it, and the analysis checks
every run file against it (``analysis_v3.check_provenance``). A file changed after F4 must be
listed in ``changed_after_f4`` with its reason.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from nac_bench.run_reset import _bench_source_sha256

ROOT = Path(__file__).resolve().parent.parent
RECORD = ROOT / "configs_v3" / "freeze_f4.json"
SCHEMA = "noise-aware-compilation.freeze-f4-v3/1"
#: the files the held-out runs and their analysis depend on
PATTERNS = (
    "nac_bench/*.py",
    "src/noise_aware_compilation/*.py",
    "requirements-lock.txt",
    "SPEC_V3.md",
    "configs_v3/held_out.json",
    "configs_v3/score_freeze.json",
)


def git_blob(data: bytes) -> str:
    """The git blob id of ``data`` (what ``git hash-object`` prints)."""

    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def frozen_files(root: Path = ROOT) -> dict[str, str]:
    files: dict[str, str] = {}
    for pattern in PATTERNS:
        for path in sorted(root.glob(pattern)):
            files[path.relative_to(root).as_posix()] = git_blob(path.read_bytes())
    return files


def current(root: Path = ROOT) -> dict[str, Any]:
    return {
        "frozen_files_git_blobs": frozen_files(root),
        "nac_bench_source_sha256": _bench_source_sha256(),
        "lock_file_sha256": hashlib.sha256(
            (root / "requirements-lock.txt").read_bytes()
        ).hexdigest(),
    }


def differences(record: dict[str, Any], root: Path = ROOT) -> list[str]:
    """Recorded files that changed or vanished at ``root``, less the allowed changes."""

    now = frozen_files(root)
    allowed = set(record.get("changed_after_f4") or {})
    return [
        path
        for path, blob in record["frozen_files_git_blobs"].items()
        if now.get(path) != blob and path not in allowed
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true")
    group.add_argument("--pin", nargs=2, metavar=("COMMIT", "TAG"))
    group.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.write:
        record = {
            "schema": SCHEMA,
            "status": (
                "Freeze step F4 (SPEC_V3.md section 8, revision 6): the files the held-out "
                "runs and their analysis use, as committed in F4. X17: none changes after F4 "
                "unless listed in changed_after_f4 with its reason. f4_commit and f4_tag are "
                "pinned by the next commit, which touches nothing else."
            ),
            "f4_commit": None,
            "f4_tag": None,
            **current(),
            "changed_after_f4": {},
        }
        RECORD.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        return 0
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    if args.pin:
        record["f4_commit"], record["f4_tag"] = args.pin
        RECORD.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        return 0
    changed = differences(record)
    for path in changed:
        print(f"changed after F4: {path}", file=sys.stderr)
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
