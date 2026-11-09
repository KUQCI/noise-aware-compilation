"""T3-C6: the prepared lowering against Nove's on every development placement.

Usage::

    python -m nac_bench.nove_check_dev --workers 48

``fast_lowering.Lowering`` (frozen at F3) must give the same Stim circuit as Nove's own
lowering. This checks, at reset = readout and reset 0:

* circuits A, B, D5 and D7 (3 rounds, CZ) on FakeFez and FakeMarrakesh: every placement;
* the same circuits on FakeMiami: every placement of A, B and D5, and a seeded sample of
  ``D7_SAMPLE`` of D7's 8,493,720;
* every surface-code candidate of R3-S3: FakeMiami d=3 as converted and d=5 with its removed
  couplers restored (labelled synthetic), every SWAP-free embedding with both hook-safe
  schedules.

Each mismatch is recorded with its placement, and the command exits with status 1 if there
is any. Development devices only (SPEC_V3.md T3-C6, revision 4).
"""

from __future__ import annotations

import argparse
import functools
import json
import random
import sys
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import nove

from nac_bench.machine import machine
from nac_bench.run_reset import git_state
from nac_bench.run_v3 import _circuit as repetition_circuit
from nac_bench.run_v3 import _models as stored_models
from nac_bench.surface_k_convergence import _circuit as surface_circuit
from nac_bench.surface_k_convergence import _models as surface_models
from noise_aware_compilation.evaluation import derive_seed
from noise_aware_compilation.fast_lowering import Lowering
from noise_aware_compilation.placement import enumerate_placements, lowered_circuit
from noise_aware_compilation.surface_code import hook_safe_schedules

ROOT = Path(__file__).resolve().parent.parent
SEED_TAG = "v3-nove-check"
D7_SAMPLE = 10_000
CHUNK = 250
SETTINGS = ("readout", "zero")
DEVICES = ("fez", "marrakesh", "miami")
REPETITION = {
    "A": ("repetition", 3, 3, "Z"),
    "B": ("repetition", 3, 3, "X"),
    "D5": ("repetition", 5, 3, "Z"),
    "D7": ("repetition", 7, 3, "Z"),
}
SAMPLED = {("miami", "D7"): D7_SAMPLE}
SURFACE = (("miami", 3, False), ("miami", 5, True))  # (device, distance, couplers restored)

# A case: ("repetition", device, label, setting) or ("surface", device, distance,
# restored, schedule X order, schedule Z order, setting).
Case = tuple[Any, ...]


def _models(case: Case) -> tuple[nove.Circuit, nove.DeviceModel, nove.HardwareNoiseModel]:
    if case[0] == "repetition":
        _kind, device, label, setting = case
        stored, noise = stored_models(device, setting)
        return repetition_circuit(REPETITION[label]), stored.device, noise
    _kind, device, distance, synthetic, x_order, z_order, setting = case
    model, noise = surface_models(device, synthetic, setting)
    return surface_circuit(distance, (x_order, z_order)), model, noise


@functools.cache
def _lowering(case: Case) -> Lowering:
    return Lowering(*_models(case))


def _check_chunk(args: tuple[Case, list[tuple[int, ...]]]) -> dict[str, Any]:
    case, mappings = args
    circuit, device, noise = _models(case)
    lowering = _lowering(case)
    mismatches = [
        list(m)
        for m in mappings
        if lowering.stim_circuit(m) != lowered_circuit(circuit, device, noise, m)
    ]
    return {"checked": len(mappings), "mismatches": mismatches}


def cases() -> list[tuple[Case, str]]:
    out: list[tuple[Case, str]] = []
    for setting in SETTINGS:
        for device in DEVICES:
            for label in REPETITION:
                out.append((("repetition", device, label, setting), f"{device}-{label}-{setting}"))
        for device, distance, synthetic in SURFACE:
            for schedule in hook_safe_schedules(distance, distance, "CZ"):
                name = (
                    f"{device}{'-restored' if synthetic else ''}-surface-d{distance}"
                    f"-{schedule[0]}{schedule[1]}-{setting}"
                )
                out.append((("surface", device, distance, synthetic, *schedule, setting), name))
    return out


def check_case(
    pool: ProcessPoolExecutor, case: Case, name: str, limit: int | None = None
) -> dict[str, Any]:
    t = time.perf_counter()
    circuit, device, _noise = _models(case)
    placements = enumerate_placements(circuit, device)
    total = len(placements)
    sample_size = SAMPLED.get((case[1], case[2])) if case[0] == "repetition" else None
    if limit is not None:
        sample_size = limit if sample_size is None else min(limit, sample_size)
    if sample_size is None or sample_size >= total:
        mappings = [tuple(m) for m in placements]
    else:
        rng = random.Random(derive_seed(SEED_TAG, name, "sample"))
        mappings = [tuple(placements[i]) for i in sorted(rng.sample(range(total), sample_size))]
    del placements
    chunks = [(case, mappings[i : i + CHUNK]) for i in range(0, len(mappings), CHUNK)]
    results = list(pool.map(_check_chunk, chunks))
    mismatches = [m for r in results for m in r["mismatches"]]
    return {
        "case": name,
        "placements": total,
        "checked": sum(r["checked"] for r in results),
        "sampled": len(mappings) < total,
        "mismatches": mismatches,
        "seconds": time.perf_counter() - t,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--machine-label", default=None)
    parser.add_argument(
        "--out", type=Path, default=ROOT / "results_v3" / "nove_check" / "development.json"
    )
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args(argv)
    commit, dirty = git_state()
    if dirty and not args.allow_dirty:
        parser.error("uncommitted changes to code, data or config; commit them first")
    start = time.perf_counter()
    records = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for case, name in cases():
            record = check_case(pool, case, name)
            records.append(record)
            print(
                name,
                f"{record['checked']}/{record['placements']}",
                "mismatches:",
                len(record["mismatches"]),
                f"{record['seconds']:.0f}s",
                flush=True,
            )
    failed = sum(len(r["mismatches"]) for r in records)
    payload = {
        "schema": "noise-aware-compilation.nove-check-dev-v3/1",
        "description": __doc__,
        "seed_tag": SEED_TAG,
        "all_equal": failed == 0,
        "placements_checked": sum(r["checked"] for r in records),
        "cases": records,
        "package_git_commit": commit,
        "package_git_dirty": dirty,
        "machine": machine(args.machine_label, workers=args.workers),
        "wall_clock_seconds": time.perf_counter() - start,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("all equal" if failed == 0 else f"{failed} mismatches", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
