"""Dev-only: copy the FakeJakartaV2 / FakeHanoiV2 files out of qiskit-ibm-runtime.

Requires the ``dev`` extra (``qiskit-ibm-runtime==0.50.0``). The package itself
never imports Qiskit; it reads only the copies written here (R-D6, X3).

Usage: python scripts/fetch_device_files.py
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import shutil
import sys
from pathlib import Path

REQUIRED_VERSION = "0.50.0"
FILES = {
    "jakarta": ("conf_jakarta.json", "props_jakarta.json"),
    "hanoi": ("conf_hanoi.json", "props_hanoi.json"),
}
DEST = Path(__file__).resolve().parent.parent / "data" / "devices"


def main() -> int:
    version = importlib.metadata.version("qiskit-ibm-runtime")
    if version != REQUIRED_VERSION:
        print(f"qiskit-ibm-runtime {REQUIRED_VERSION} required, found {version}", file=sys.stderr)
        return 1
    spec = importlib.util.find_spec("qiskit_ibm_runtime")
    assert spec is not None and spec.submodule_search_locations is not None
    root = Path(next(iter(spec.submodule_search_locations))) / "fake_provider" / "backends"
    DEST.mkdir(parents=True, exist_ok=True)
    lines = []
    for backend, names in FILES.items():
        for name in names:
            source = root / backend / name
            target = DEST / name
            shutil.copyfile(source, target)
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            lines.append(f"{digest}  {name}")
    (DEST / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (DEST / "SOURCE.txt").write_text(
        f"Copied from qiskit-ibm-runtime=={version}, "
        "package path qiskit_ibm_runtime/fake_provider/backends/{jakarta,hanoi}/\n",
        encoding="utf-8",
    )
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
