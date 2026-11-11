"""Dev-only: copy the stored devices' fake-backend files out of qiskit-ibm-runtime.

Requires the ``dev`` extra (``qiskit-ibm-runtime==0.50.0``). The package itself
never imports Qiskit; it reads only the copies written here (R-D6, X3).

Usage: python scripts/fetch_device_files.py [--held-out]

``--held-out`` also copies the six held-out devices (SPEC_V3.md section 3). It refuses to
run, before any held-out file is opened, unless freeze step F4 has happened, carries its
annotated tag and the checkout is clean (``freeze_guard.require_after_f4``, X16, T3-F1).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import shutil
import sys
from pathlib import Path

from freeze_guard import require_after_f4

REQUIRED_VERSION = "0.50.0"
FILES = {
    "jakarta": ("conf_jakarta.json", "props_jakarta.json"),
    "hanoi": ("conf_hanoi.json", "props_hanoi.json"),
    # v2 held-out device (SPEC_V2.md R-G5), added after the decoder-graph freeze (R-G8)
    "algiers": ("conf_algiers.json", "props_algiers.json"),
    # v3 development devices (SPEC_V3.md section 3)
    "fez": ("conf_fez.json", "props_fez.json"),
    "marrakesh": ("conf_marrakesh.json", "props_marrakesh.json"),
    "miami": ("conf_miami.json", "props_miami.json"),
}
#: v3 held-out devices (SPEC_V3.md section 3), copied only with --held-out after F4 (X16)
HELD_OUT_FILES = {
    name: (f"conf_{name}.json", f"props_{name}.json")
    for name in ("boston", "pittsburgh", "berlin", "kingston", "aachen", "nighthawk")
}
DEST = Path(__file__).resolve().parent.parent / "data" / "devices"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--held-out", action="store_true", help="also copy the held-out devices")
    args = parser.parse_args(argv)
    files = dict(FILES)
    if args.held_out:
        require_after_f4()  # raises before any held-out file is opened
        files.update(HELD_OUT_FILES)
    version = importlib.metadata.version("qiskit-ibm-runtime")
    if version != REQUIRED_VERSION:
        print(f"qiskit-ibm-runtime {REQUIRED_VERSION} required, found {version}", file=sys.stderr)
        return 1
    spec = importlib.util.find_spec("qiskit_ibm_runtime")
    assert spec is not None and spec.submodule_search_locations is not None
    root = Path(next(iter(spec.submodule_search_locations))) / "fake_provider" / "backends"
    DEST.mkdir(parents=True, exist_ok=True)
    lines = []
    for backend, names in files.items():
        for name in names:
            source = root / backend / name
            target = DEST / name
            shutil.copyfile(source, target)
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            lines.append(f"{digest}  {name}")
    (DEST / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (DEST / "SOURCE.txt").write_text(
        f"Copied from qiskit-ibm-runtime=={version}, "
        f"package path qiskit_ibm_runtime/fake_provider/backends/{{{','.join(files)}}}/\n",
        encoding="utf-8",
    )
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
