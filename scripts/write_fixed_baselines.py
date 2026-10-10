"""Write configs/fixed_baselines.json: the R-B1 fixed placement per device and circuit.

The fixed placement is the first valid mapping in lexicographic order. It is
computed from the circuit and the device connectivity only; no noise data is
read.

Usage: python scripts/write_fixed_baselines.py configs/benchmark.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from noise_aware_compilation.circuits import repetition_code
from noise_aware_compilation.devices import load_device
from noise_aware_compilation.placement import enumerate_placements


def main() -> int:
    config_path = Path(sys.argv[1])
    config = json.loads(config_path.read_text(encoding="utf-8"))
    fixed: dict[str, list[int]] = {}
    counts: dict[str, int] = {}
    for entry in config["devices"]:
        device = load_device(entry["name"]).device
        for circuit in entry["circuits"]:
            c = repetition_code(circuit["distance"], circuit["rounds"], circuit["basis"])
            mappings = enumerate_placements(c, device)
            key = f"{entry['name']}-{circuit['label']}"
            fixed[key] = list(mappings[0])
            counts[key] = len(mappings)
    out = config_path.parent / config["fixed_baselines_file"]
    payload = {
        "rule": "R-B1: first valid placement in lexicographic order of the mapping "
        "(circuit qubit -> physical qubit); no noise information used",
        "fixed": fixed,
        "num_valid_placements": counts,
    }
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
