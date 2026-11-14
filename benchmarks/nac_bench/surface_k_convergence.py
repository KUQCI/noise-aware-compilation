"""R3-S2 (revision 4): the surface code's *k*, chosen by convergence on FakeMiami.

Usage::

    python -m nac_bench.surface_k_convergence --workers 4

Development data only. Four cases: FakeMiami's d=3 surface code as converted and its d=5
surface code with the removed couplers restored at the median CZ channel (R3-S3, labelled
synthetic), each at reset = readout and reset 0, over every candidate of R3-S4 (every
SWAP-free embedding with both hook-safe schedules). Each candidate is lowered by Nove and its
lightest logical chains are found once, up to ``K_MAX``; the scaled score at any smaller *k*
is the same sum over the first *k* of them (``lightest_logical_chains`` stops its generator
after *k* chains), which every candidate checks against ``scaled_score_from_graph`` at the
smallest *k*.

The rule (SPEC_V3.md R3-S2): the surface-code *k* is the smallest of ``K_VALUES`` for which,
on all four cases, the scores at *k* and 2*k* have Spearman's rho of at least 0.99 and the
same top choice (the rounded score, then the default schedule, then the mapping). If none
does, it is the *k* with the largest minimum rho (ties to the smaller *k*), and the record
says the criterion failed. As context that no rule uses, each *k*'s rho against R3-S3's
simulated P_L (reset = readout) is recorded too.
"""

from __future__ import annotations

import argparse
import functools
import json
import math
import sys
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import nove

from nac_bench.machine import machine
from nac_bench.run_reset import git_state, noise_for
from nac_bench.scaled_calibration import spearman
from nac_bench.surface_check import _choice, restored
from noise_aware_compilation.decoder_graph import matching_graph
from noise_aware_compilation.devices import load_device
from noise_aware_compilation.placement import approximate_dem, enumerate_placements, lowered_circuit
from noise_aware_compilation.scaled_score import (
    ScaledParameters,
    chain_failure_probability,
    lightest_logical_chains,
    scaled_score_from_graph,
)
from noise_aware_compilation.surface_code import (
    DEFAULT_SCHEDULE,
    Schedule,
    hook_safe_schedules,
    surface_code,
)

ROOT = Path(__file__).resolve().parent.parent
SCORE_FREEZE = ROOT / "configs_v3" / "score_freeze.json"
SURFACE_CHECK = ROOT / "results_v3" / "scaled" / "surface_check.json"
K_VALUES = (10, 20, 40, 80, 160, 320, 640)
K_MAX = 2 * K_VALUES[-1]
RHO_MIN = 0.99
SETTINGS = ("readout", "zero")
CASES = (("miami", 3, False), ("miami", 5, True))  # (device, distance, couplers restored)


@functools.cache
def _models(
    device_name: str, synthetic: bool, setting: str
) -> tuple[nove.DeviceModel, nove.HardwareNoiseModel]:
    if synthetic:
        return restored(device_name, setting)
    stored = load_device(device_name)
    return stored.device, noise_for(setting, stored)


@functools.cache
def _circuit(distance: int, schedule: Schedule) -> nove.Circuit:
    return surface_code(distance, distance, schedule, "CZ")


def _chain_task(
    args: tuple[str, bool, str, int, Schedule, tuple[int, ...], float],
) -> dict[str, Any]:
    """The undetectable part and the first ``K_MAX`` chain terms of one candidate."""

    device_name, synthetic, setting, distance, schedule, mapping, step = args
    device, noise = _models(device_name, synthetic, setting)
    dem = approximate_dem(
        lowered_circuit(_circuit(distance, schedule), device, noise, mapping), decompose=True
    )
    graph = matching_graph(dem)
    t = time.perf_counter()
    chains = lightest_logical_chains(graph, K_MAX)
    terms = [chain_failure_probability([graph.edges[e] for e in c], step) for c in chains]
    seconds = time.perf_counter() - t
    smallest = K_VALUES[0]
    direct = scaled_score_from_graph(graph, ScaledParameters(smallest, step))
    if prefix_score(graph.undetectable_logical, terms, smallest) != direct:
        raise RuntimeError(f"prefix score differs from the scaled score at k={smallest}")
    return {
        "undetectable": graph.undetectable_logical,
        "terms": terms,
        "seconds_chains": seconds,
    }


def prefix_score(undetectable: float, terms: Sequence[float], k: int) -> float:
    """The scaled score at ``k``, from the lightest-first chain terms of a larger ``k``."""

    return math.fsum([undetectable, *terms[:k]])


def converged_k(cases: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """R3-S2's surface-code rule over the cases' per-k records."""

    passing = [
        k
        for k in K_VALUES
        if all(
            c["per_k"][str(k)]["rho_k_2k"] >= RHO_MIN and c["per_k"][str(k)]["same_top"]
            for c in cases
        )
    ]
    minimum = {k: min(c["per_k"][str(k)]["rho_k_2k"] for c in cases) for k in K_VALUES}
    if passing:
        return {"k": passing[0], "criterion_met": True, "min_rho_k_2k": minimum}
    best = max(K_VALUES, key=lambda k: (minimum[k], -k))
    return {"k": best, "criterion_met": False, "min_rho_k_2k": minimum}


def _simulated() -> dict[tuple[str, tuple[str, ...], tuple[int, ...]], float]:
    data = json.loads(SURFACE_CHECK.read_text(encoding="utf-8"))
    out = {}
    for result in data["results"]:
        for row in result["rows"]:
            key = (result["label"], tuple(row["schedule"]), tuple(row["mapping"]))
            out[key] = row["failures"] / row["shots"]
    return out


def run_case(
    pool: ProcessPoolExecutor,
    device_name: str,
    distance: int,
    synthetic: bool,
    setting: str,
    step: float,
    simulated: dict[tuple[str, tuple[str, ...], tuple[int, ...]], float],
) -> dict[str, Any]:
    device, _noise = _models(device_name, synthetic, setting)
    label = f"{device_name}{'-restored' if synthetic else ''}-d{distance}"
    schedules = hook_safe_schedules(distance, distance, "CZ")
    candidates = [
        (schedule, m)
        for schedule in schedules
        for m in enumerate_placements(_circuit(distance, schedule), device)
    ]
    t = time.perf_counter()
    chains = list(
        pool.map(
            _chain_task,
            [(device_name, synthetic, setting, distance, s, m, step) for s, m in candidates],
            chunksize=4,
        )
    )
    wall = time.perf_counter() - t
    keys = [(s != DEFAULT_SCHEDULE, m) for s, m in candidates]
    every_k = sorted({*K_VALUES, *(2 * k for k in K_VALUES)})
    scores = {k: [prefix_score(c["undetectable"], c["terms"], k) for c in chains] for k in every_k}
    tops = {k: _choice(scores[k], keys) for k in every_k}
    p_l = None
    if setting == "readout":
        p_l = [simulated[(label, tuple(s), tuple(m))] for s, m in candidates]
    per_k: dict[str, Any] = {}
    for k in every_k:
        record: dict[str, Any] = {
            "top": {
                "schedule": list(candidates[tops[k]][0]),
                "mapping": list(candidates[tops[k]][1]),
            }
        }
        if k in K_VALUES:
            record["rho_k_2k"] = spearman(scores[k], scores[2 * k])
            record["same_top"] = tops[k] == tops[2 * k]
        if p_l is not None:
            record["rho_vs_simulated"] = spearman(scores[k], p_l)
        per_k[str(k)] = record
    return {
        "label": label,
        "setting": setting,
        "synthetic_couplers": synthetic,
        "distance": distance,
        "candidates": len(candidates),
        "chains_found_min": min(len(c["terms"]) for c in chains),
        "per_k": per_k,
        "seconds": {
            "wall": wall,
            "chains_mean": math.fsum(c["seconds_chains"] for c in chains) / len(chains),
        },
        "rows": [
            {
                "schedule": list(s),
                "mapping": list(m),
                "scores": {str(k): scores[k][i] for k in every_k},
            }
            for i, (s, m) in enumerate(candidates)
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--machine-label", default=None)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "results_v3" / "scaled" / "surface_k_convergence.json",
    )
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args(argv)
    commit, dirty = git_state()
    if dirty and not args.allow_dirty:
        parser.error("uncommitted changes to code, data or config; commit them first")
    step = float(json.loads(SCORE_FREEZE.read_text(encoding="utf-8"))["scaled_score"]["step"])
    simulated = _simulated()
    start = time.perf_counter()
    cases = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for setting in SETTINGS:
            for device_name, distance, synthetic in CASES:
                case = run_case(pool, device_name, distance, synthetic, setting, step, simulated)
                cases.append(case)
                print(
                    case["label"],
                    setting,
                    case["candidates"],
                    {
                        k: round(v["rho_k_2k"], 4)
                        for k, v in case["per_k"].items()
                        if "rho_k_2k" in v
                    },
                    {k: v["same_top"] for k, v in case["per_k"].items() if "same_top" in v},
                    flush=True,
                )
    chosen = converged_k(cases)
    print("chosen", json.dumps(chosen), flush=True)
    payload = {
        "schema": "noise-aware-compilation.surface-k-convergence-v3/1",
        "description": __doc__,
        "step": step,
        "k_values": list(K_VALUES),
        "rho_min": RHO_MIN,
        "chosen": chosen,
        "cases": cases,
        "package_git_commit": commit,
        "package_git_dirty": dirty,
        "machine": machine(args.machine_label, workers=args.workers),
        "wall_clock_seconds": time.perf_counter() - start,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
