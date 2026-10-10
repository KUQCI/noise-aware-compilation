"""Convert data/devices/ fake-backend files into the package's stored snapshots.

Uses only the package converter (no Qiskit). Writes
``src/noise_aware_compilation/device_data/{name}_snapshot.json``,
``{name}_device.json`` and ``hashes.json``.

Usage: python scripts/build_device_snapshots.py
"""

from __future__ import annotations

import json
from pathlib import Path

from noise_aware_compilation.conversion import convert_fake_backend

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "devices"
OUT = ROOT / "src" / "noise_aware_compilation" / "device_data"
DEVICES = ("jakarta", "hanoi")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, dict[str, str]] = {}
    for name in DEVICES:
        converted = convert_fake_backend(RAW / f"conf_{name}.json", RAW / f"props_{name}.json")
        (OUT / f"{name}_snapshot.json").write_text(
            converted.snapshot.to_json() + "\n", encoding="utf-8"
        )
        converted.device.write_json(OUT / f"{name}_device.json")
        hashes[name] = {
            "snapshot_sha256": converted.snapshot.sha256,
            "device_sha256": converted.device.sha256,
        }
        for note in converted.notes:
            print(f"{name}: {note}")
    (OUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True) + "\n")
    print(json.dumps(hashes, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
