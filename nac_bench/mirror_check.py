"""Roadmap Step 5.1: does a mirror pair's gap follow the gate order or the qubits?

Usage::

    python -m nac_bench.mirror_check configs_v3/mirror_check.json [--workers N]

A placement ``M`` and its mirror ``reversed(M)`` use the same qubits and
couplers, but reversing the path moves every coupler to the other CX layer.
Swapping the two CX layers on ``M`` makes exactly the physical schedule of the
mirror in the original order: the same couplers in the same layers, the same
CX directions, the same idle windows. So if the gap comes from the gate order,
``(M, swapped)`` matches ``(mirror, original)`` and ``(mirror, swapped)``
matches ``(M, original)``. The two circuits in each matched pair are
physically identical by construction, so this checks the explanation and the
simulation pipeline; it is not new evidence about hardware.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import nove
import stim

from nac_bench import stats_v3
from nac_bench.machine import machine
from nac_bench.reanalysis import git_state
from noise_aware_compilation.benchmark import _evaluate_task
from noise_aware_compilation.circuits import repetition_code
from noise_aware_compilation.devices import load_device
from noise_aware_compilation.evaluation import CHUNK_SHOTS, derive_seed
from noise_aware_compilation.placement import enumerate_placements
from noise_aware_compilation.stats import newcombe_difference, ratio_interval, wilson

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = "noise-aware-compilation.mirror-check-v3/1"


def swap_cx_layers(circuit: nove.Circuit) -> nove.Circuit:
    """The same circuit with each round's two consecutive CX layers in the other order."""

    flat = circuit.to_stim().flattened()
    ops = list(flat)
    out = stim.Circuit()
    i = 0
    swapped = 0
    while i < len(ops):
        op = ops[i]
        if (
            op.name == "CX"
            and i + 3 < len(ops)
            and ops[i + 1].name == "TICK"
            and ops[i + 2].name == "CX"
            and ops[i + 3].name == "TICK"
        ):
            out.append(ops[i + 2])
            out.append("TICK")
            out.append(op)
            out.append("TICK")
            i += 4
            swapped += 1
            continue
        out.append(op)
        i += 1
    if swapped == 0:
        raise ValueError("no pair of consecutive CX layers found")
    return nove.Circuit.from_stim(out)


def chunk_seeds(base: int, order: str, mapping: Sequence[int], shots: int) -> list[int]:
    chunks = -(-shots // CHUNK_SHOTS)
    key = ",".join(map(str, mapping))
    return [derive_seed("mirror-v3", base, order, key, k) for k in range(chunks)]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("config", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--machine-label", default=None)
    parser.add_argument("--out", type=Path, default=ROOT / "results_v3" / "mirror_check.json")
    args = parser.parse_args(argv)
    started = time.perf_counter()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    stored = load_device(config["device"])
    spec = config["circuit"]
    original = repetition_code(spec["distance"], spec["rounds"], spec["basis"])
    swapped = swap_cx_layers(original)
    mapping = tuple(int(q) for q in config["mapping"])
    mirror = tuple(reversed(mapping))
    candidates = set(enumerate_placements(original, stored.device))
    if mapping not in candidates or mirror not in candidates:
        raise RuntimeError("the pair must be SWAP-free candidates")
    circuits = {"original": original.to_stim_text(), "swapped": swapped.to_stim_text()}
    device_json = stored.device.to_json()
    noise_json = json.dumps(stored.noise.to_dict())
    shots = int(config["shots_per_seed"])
    runs = [
        (name, m, order) for name, m in (("M", mapping), ("mirror", mirror)) for order in circuits
    ]
    tasks, keys = [], []
    for name, m, order in runs:
        for base in config["base_seeds"]:
            seeds = chunk_seeds(int(base), order, m, shots)
            tasks.append((circuits[order], device_json, noise_json, m, shots, seeds))
            keys.append((name, order, int(base)))
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        counts = list(pool.map(_evaluate_task, tasks))
    per: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for (name, order, _), c in zip(keys, counts, strict=True):
        per.setdefault((name, order), []).append((int(c["logical_failures"]), int(c["shots"])))

    def pooled(key: tuple[str, str]) -> tuple[int, int]:
        return sum(f for f, _ in per[key]), sum(n for _, n in per[key])

    def compare(a: tuple[str, str], b: tuple[str, str]) -> dict[str, Any]:
        fa, na = pooled(a)
        fb, nb = pooled(b)
        diff = newcombe_difference(fa, na, fb, nb)
        rel = ratio_interval(fa, na, fb, nb)
        return {
            "a": "/".join(a),
            "b": "/".join(b),
            "relative": {"estimate": rel.estimate, "low": rel.low, "high": rel.high},
            "verdict": stats_v3.difference_verdict(diff),
            "p_value": stats_v3.two_proportion_p(fa, na, fb, nb),
        }

    rates = {}
    for key in per:
        f, n = pooled(key)
        w = wilson(f, n)
        rates["/".join(key)] = {
            "failures": f,
            "shots": n,
            "per_seed": per[key],
            "p_l": {"estimate": w.estimate, "low": w.low, "high": w.high},
        }
    v2 = json.loads((ROOT / "results_v2" / "runs" / "v2-hanoi-D7.json").read_text(encoding="utf-8"))
    v2_rates = {}
    for p in v2["placements"]:
        if tuple(p["mapping"]) in (mapping, mirror):
            f = sum(int(s["logical_failures"]) for s in p["per_seed"])
            n = sum(int(s["shots"]) for s in p["per_seed"])
            v2_rates["M" if tuple(p["mapping"]) == mapping else "mirror"] = {
                "failures": f,
                "shots": n,
            }
    commit, dirty = git_state()
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
        "mapping": list(mapping),
        "mirror": list(mirror),
        "rates": rates,
        "v2_counts_original_order": v2_rates,
        "comparisons": {
            "mirror gap, original order (replicates v2)": compare(
                ("M", "original"), ("mirror", "original")
            ),
            "M swapped vs mirror original (same schedule)": compare(
                ("M", "swapped"), ("mirror", "original")
            ),
            "mirror swapped vs M original (same schedule)": compare(
                ("mirror", "swapped"), ("M", "original")
            ),
            "M original vs M swapped (same qubits, other order)": compare(
                ("M", "original"), ("M", "swapped")
            ),
        },
        "provenance": {
            "package_git_commit": commit,
            "package_git_dirty": dirty,
            "written_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "machine": machine(args.machine_label, workers=args.workers),
            "stim_version": stim.__version__,
            "wall_clock_seconds": time.perf_counter() - started,
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    for name, c in result["comparisons"].items():
        r = c["relative"]
        low, high = 100 * r["low"], 100 * r["high"]
        print(f"{name}: {100 * r['estimate']:+.1f}% [{low:+.1f}, {high:+.1f}] {c['verdict']}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
