"""R3-C8: develop the fast search against exhaustive scores, before the freeze.

Usage::

    python -m nac_bench.fast_search_dev --workers 4                 # all but FakeMiami d=7
    nice -n 19 python -m nac_bench.fast_search_dev --workers 48 --cases miami-D7 \\
        --out results_v3/fast_search/development_miami_d7.json

Development devices only (X16): FakeFez, FakeMarrakesh and FakeMiami, circuits A, B, D5
and D7 (3 rounds, native CZ), at reset = readout and reset 0. For each case every placement
gets the decoder-graph score (the prepared lowering, with a sample checked against Nove),
and the search runs for every setting of the grid, scoring by looking up those values. The
record holds, for each case and setting, the rank of the search's choice among all
placements (1 is the exhaustive choice) and how many placements it scored.

The setting frozen at F3 is picked by the rule in ``DECISIONS_V3.md`` P2-18: the one that
finds the exhaustive choice on every development case with the fewest placements scored in
the worst case; if none does, the smallest worst-case rank, then the fewest placements.
"""

from __future__ import annotations

import argparse
import functools
import itertools
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
from nac_bench.run_reset import git_state, noise_for
from noise_aware_compilation.circuits import repetition_code
from noise_aware_compilation.decoder_graph import decoder_graph_score_from_dem
from noise_aware_compilation.devices import StoredDevice, load_device
from noise_aware_compilation.fast_lowering import Lowering
from noise_aware_compilation.fast_search import FastSearch, SearchParameters
from noise_aware_compilation.placement import (
    Placement,
    _rounded,
    approximate_dem,
    enumerate_placements,
)

ROOT = Path(__file__).resolve().parent.parent
DEVICES = ("fez", "marrakesh", "miami")
CIRCUITS = (("A", 3, "Z"), ("B", 3, "X"), ("D5", 5, "Z"), ("D7", 7, "Z"))
SETTINGS = ("readout", "zero")
ROUNDS = 3
PREFILTERS = (10, 30, 100, 300, 1000)
STARTS = (1, 3, 10, 30)
MAX_EVALUATIONS = 20_000
CHUNK = 5_000
CHECKED = 20  # placements per case checked against Nove's lowering
SEED_TAG = "v3-fast-search-dev"


@functools.cache
def _models(device: str, setting: str) -> tuple[StoredDevice, nove.HardwareNoiseModel]:
    stored = load_device(device)
    return stored, noise_for(setting, stored)


@functools.cache
def _circuit(distance: int, basis: str) -> nove.Circuit:
    return repetition_code(distance, ROUNDS, basis, "CZ")  # type: ignore[arg-type]


@functools.cache
def _lowering(device: str, setting: str, distance: int, basis: str) -> Lowering:
    stored, noise = _models(device, setting)
    return Lowering(_circuit(distance, basis), stored.device, noise)


def _score_chunk(args: tuple[str, str, int, str, list[Placement]]) -> list[float]:
    device, setting, distance, basis, chunk = args
    lowering = _lowering(device, setting, distance, basis)
    return [
        decoder_graph_score_from_dem(approximate_dem(lowering.stim_circuit(m), decompose=True))
        for m in chunk
    ]


def _check_chunk(args: tuple[str, str, int, str, list[Placement]]) -> int:
    device, setting, distance, basis, chunk = args
    lowering = _lowering(device, setting, distance, basis)
    for m in chunk:
        lowering.check(m)
    return len(chunk)


def grid() -> list[SearchParameters]:
    return [
        SearchParameters(p, s, MAX_EVALUATIONS) for p, s in itertools.product(PREFILTERS, STARTS)
    ]


def develop(
    pool: ProcessPoolExecutor, device: str, setting: str, label: str, distance: int, basis: str
) -> dict[str, Any]:
    stored, noise = _models(device, setting)
    circuit = _circuit(distance, basis)
    t = time.perf_counter()
    placements = enumerate_placements(circuit, stored.device)
    enumeration = time.perf_counter() - t
    t = time.perf_counter()
    chunks = [placements[i : i + CHUNK] for i in range(0, len(placements), CHUNK)]
    scores = [
        s
        for part in pool.map(_score_chunk, [(device, setting, distance, basis, c) for c in chunks])
        for s in part
    ]
    scoring = time.perf_counter() - t
    rng = random.Random(f"{SEED_TAG}|{device}|{setting}|{label}")
    sample = rng.sample(placements, min(CHECKED, len(placements)))
    checked = sum(pool.map(_check_chunk, [(device, setting, distance, basis, [m]) for m in sample]))
    value = dict(zip(placements, scores, strict=True))
    ranked = sorted(placements, key=lambda m: (_rounded(value[m]), m))
    rank = {m: i + 1 for i, m in enumerate(ranked)}
    search = FastSearch(circuit, stored.device, noise, placements)
    t = time.perf_counter()
    prefilter_order = search.prefiltered(max(PREFILTERS))
    prefilter_seconds = time.perf_counter() - t
    runs = []
    for parameters in grid():
        t = time.perf_counter()
        result = search.run(value.__getitem__, parameters)
        runs.append(
            {
                "prefilter": parameters.prefilter,
                "starts": parameters.starts,
                "max_evaluations": parameters.max_evaluations,
                "choice": list(result.mapping),
                "rank": rank[result.mapping],
                "evaluations": result.evaluations,
                "descents": list(result.descents),
                "seconds_without_scoring": time.perf_counter() - t,
            }
        )
    return {
        "device": device,
        "setting": setting,
        "circuit": label,
        "distance": distance,
        "basis": basis,
        "rounds": ROUNDS,
        "placements": len(placements),
        "checked_against_nove": checked,
        "exhaustive_choice": list(ranked[0]),
        "exhaustive_score": value[ranked[0]],
        "decoder_graph_rank_of_summed_error_best": rank[prefilter_order[0]],
        "seconds": {
            "enumeration": enumeration,
            "exhaustive_scoring_wall": scoring,
            "prefilter": prefilter_seconds,
        },
        "runs": runs,
    }


def pick(cases: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """P2-18's rule over the grid."""

    rows = []
    for i, parameters in enumerate(grid()):
        ranks = [c["runs"][i]["rank"] for c in cases]
        evaluations = [c["runs"][i]["evaluations"] for c in cases]
        rows.append(
            {
                "prefilter": parameters.prefilter,
                "starts": parameters.starts,
                "found_everywhere": all(r == 1 for r in ranks),
                "cases_found": sum(1 for r in ranks if r == 1),
                "worst_rank": max(ranks),
                "worst_evaluations": max(evaluations),
            }
        )
    best = min(
        rows,
        key=lambda r: (
            not r["found_everywhere"],
            0 if r["found_everywhere"] else r["worst_rank"],
            r["worst_evaluations"],
            r["prefilter"],
            r["starts"],
        ),
    )
    return {"chosen": best, "grid": rows}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--machine-label", default=None)
    parser.add_argument("--cases", nargs="*", default=None, help="device-circuit, e.g. miami-D7")
    parser.add_argument(
        "--out", type=Path, default=ROOT / "results_v3" / "fast_search" / "development.json"
    )
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args(argv)
    commit, dirty = git_state()
    if dirty and not args.allow_dirty:
        parser.error("uncommitted changes to code, data or config; commit them first")
    wanted = set(args.cases) if args.cases else None
    jobs = [
        (device, setting, label, distance, basis)
        for device in DEVICES
        for setting in SETTINGS
        for label, distance, basis in CIRCUITS
        if (wanted is None and not (device == "miami" and label == "D7"))
        or (wanted is not None and f"{device}-{label}" in wanted)
    ]
    start = time.perf_counter()
    cases = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for job in jobs:
            t = time.perf_counter()
            case = develop(pool, *job)
            cases.append(case)
            found = sum(1 for r in case["runs"] if r["rank"] == 1)
            print(
                f"{job[0]} {job[1]} {job[2]}: {case['placements']} placements, "
                f"found by {found}/{len(case['runs'])} settings, {time.perf_counter() - t:.0f}s",
                flush=True,
            )
    payload = {
        "schema": "noise-aware-compilation.fast-search-dev-v3/1",
        "description": __doc__,
        "grid": {"prefilter": list(PREFILTERS), "starts": list(STARTS)},
        "max_evaluations": MAX_EVALUATIONS,
        "cases": cases,
        "pick": pick(cases),
        "package_git_commit": commit,
        "package_git_dirty": dirty,
        "machine": machine(args.machine_label, workers=args.workers),
        "wall_clock_seconds": time.perf_counter() - start,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
