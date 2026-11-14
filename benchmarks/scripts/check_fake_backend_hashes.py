"""X16 hash check: are the held-out fake-backend files the same in two runtime releases?

Usage::

    python scripts/check_fake_backend_hashes.py [--out results_v3/x16/fake_backend_hashes.json]

The X16 incident (SPEC_V3.md section 1) read aggregates from the held-out devices' files in
``qiskit-ibm-runtime`` 0.52.0; this project converts from 0.50.0 (``requirements-lock.txt``).
This script compares the files' git blob hashes: it makes a blobless, bare clone of the
public ``Qiskit/qiskit-ibm-runtime`` repository (trees only, no file contents), lists each
file's blob hash at both release tags with ``git ls-tree``, checks that the installed
``qiskit-ibm-runtime`` is 0.50.0, and hashes the installed files with ``git hash-object``.
``git hash-object`` reads each installed file's bytes to hash them; nothing is parsed or
printed, so no calibration value is read. No wheel is downloaded.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "https://github.com/Qiskit/qiskit-ibm-runtime"
TAGS = ("0.50.0", "0.52.0")
INSTALLED_VERSION = "0.50.0"  # requirements-lock.txt
DEVICES = ("boston", "pittsburgh", "berlin", "kingston", "aachen", "nighthawk")
FILES = tuple(f"{kind}_{d}.json" for d in DEVICES for kind in ("conf", "props"))
PREFIX = "qiskit_ibm_runtime/fake_provider/backends"


def _git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def tag_blobs(clone: Path, tag: str) -> dict[str, str]:
    out = {}
    for line in _git("ls-tree", "-r", tag, "--", f"{PREFIX}/", cwd=clone).splitlines():
        meta, path = line.split("\t", 1)
        name = path.rsplit("/", 1)[-1]
        if name in FILES:
            out[name] = meta.split()[2]
    return out


def installed_blobs() -> dict[str, str]:
    version = importlib.metadata.version("qiskit-ibm-runtime")
    if version != INSTALLED_VERSION:
        raise RuntimeError(f"qiskit-ibm-runtime {INSTALLED_VERSION} required, found {version}")
    spec = importlib.util.find_spec("qiskit_ibm_runtime")
    assert spec is not None and spec.origin is not None
    base = Path(spec.origin).parent / "fake_provider" / "backends"
    return {
        name: _git("hash-object", str(base / name.split("_", 1)[1][:-5] / name)) for name in FILES
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", type=Path, default=ROOT / "results_v3" / "x16" / "fake_backend_hashes.json"
    )
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        clone = Path(tmp) / "runtime.git"
        _git("clone", "--bare", "--filter=blob:none", "--no-checkout", "-q", REPO, str(clone))
        tags = {tag: tag_blobs(clone, tag) for tag in TAGS}
        commits = {tag: _git("rev-parse", f"{tag}^{{commit}}", cwd=clone) for tag in TAGS}
    installed = installed_blobs()
    version = importlib.metadata.version("qiskit-ibm-runtime")
    same = {name: tags[TAGS[0]][name] == tags[TAGS[1]][name] == installed[name] for name in FILES}
    result = {
        "schema": "noise-aware-compilation.x16-hash-check/1",
        "description": __doc__,
        "repository": REPO,
        "tag_commits": commits,
        "installed_version": version,
        "git_blobs": {"installed": installed, **tags},
        "identical": same,
        "all_identical": all(same.values()),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("all identical" if result["all_identical"] else "DIFFERENT", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
