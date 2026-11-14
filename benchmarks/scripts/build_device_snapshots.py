"""Convert data/devices/ fake-backend files into the package's stored snapshots.

Uses only the package converter (no Qiskit). Writes
``src/noise_aware_compilation/device_data/{name}_snapshot.json``,
``{name}_device.json`` and ``hashes.json``.

Usage: python scripts/build_device_snapshots.py [--held-out]

``--held-out`` also converts the six held-out devices (SPEC_V3.md section 3). It refuses to
run, before any held-out file is opened, unless freeze step F4 has happened, carries its
annotated tag and the checkout is clean (``freeze_guard.require_after_f4``, X16, T3-F1).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from freeze_guard import require_after_f4
from noise_aware_compilation.conversion import convert_fake_backend

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "devices"
OUT = ROOT.parent / "src" / "noise_aware_compilation" / "device_data"
DEVICES = ("jakarta", "hanoi", "algiers", "fez", "marrakesh", "miami")
#: v3 held-out devices (SPEC_V3.md section 3), converted only with --held-out after F4 (X16)
HELD_OUT = ("boston", "pittsburgh", "berlin", "kingston", "aachen", "nighthawk")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--held-out", action="store_true", help="also convert the held-out devices")
    args = parser.parse_args(argv)
    devices: tuple[str, ...] = DEVICES
    if args.held_out:
        require_after_f4()  # raises before any held-out file is opened
        devices = DEVICES + HELD_OUT
    OUT.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, dict[str, str]] = {}
    for name in devices:
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
