"""Write configs/fixed_baselines.json: the R-B1 fixed placement per device and circuit.

The fixed placement is the first valid mapping in lexicographic order. It is
computed from the circuit and the device connectivity only; no noise data is
read. A sensitivity variant that changes the device connectivity (T-L1
``L1-coupler-better``) gets its own entry, keyed ``<device>-<circuit>-<variant>``.

Usage: python scripts/write_fixed_baselines.py configs/benchmark.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from noise_aware_compilation.benchmark import CaseSpec, build_case_models
from noise_aware_compilation.placement import enumerate_placements


def main() -> int:
    config_path = Path(sys.argv[1])
    config = json.loads(config_path.read_text(encoding="utf-8"))
    fixed: dict[str, list[int]] = {}
    counts: dict[str, int] = {}
    for entry in config["devices"]:
        for circuit in entry["circuits"]:
            key = f"{entry['name']}-{circuit['label']}"
            for variant in ["base", *config["sensitivity"]["variants"]]:
                spec = CaseSpec(
                    case_id=key,
                    device=entry["name"],
                    circuit_label=circuit["label"],
                    distance=int(circuit["distance"]),
                    rounds=int(circuit["rounds"]),
                    basis=str(circuit["basis"]),
                    condition="N2",
                    variant=variant,
                )
                c, device, _, _ = build_case_models(spec, config)
                mappings = enumerate_placements(c, device)
                if variant == "base":
                    fixed[key] = list(mappings[0])
                    counts[key] = len(mappings)
                elif list(mappings[0]) != fixed[key] or len(mappings) != counts[key]:
                    fixed[f"{key}-{variant}"] = list(mappings[0])
                    counts[f"{key}-{variant}"] = len(mappings)
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
