"""R3-S3: the scaled score against simulated P_L on FakeMiami surface codes, before the freeze.

Usage::

    python -m nac_bench.surface_check --workers 4

Development data only. Every candidate of the rotated surface-code Z-memory (native CZ,
d rounds, reset = readout) is scored and simulated:

* **d=3** on FakeMiami as converted: every SWAP-free embedding x both hook-safe schedules;
* **d=5** on FakeMiami with its removed couplers restored at the median CZ channel of the
  kept couplers (labelled synthetic), because every d=5 embedding of FakeMiami crosses
  one of them (DECISIONS_V3.md P2-11).

Scores: the scaled score at the calibrated parameters (``results_v3/scaled/calibration.json``),
v2's exact decoder-graph score, and summed error (schedule-blind). Each candidate gets
``SHOTS`` shots on each of three seeds (tag ``v3-surface-check``). The record holds every
candidate's scores and counts, each score's Spearman's rho against the pooled P_L, and the
P_L of each score's choice beside the naive placement and the best found. No threshold is
applied (R3-S3).
"""

from __future__ import annotations

import argparse
import functools
import json
import math
import statistics
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
from noise_aware_compilation.baselines import summed_error
from noise_aware_compilation.decoder_graph import decoder_graph_score_from_dem, matching_graph
from noise_aware_compilation.devices import load_device
from noise_aware_compilation.evaluation import CHUNK_SHOTS, PlacementEvaluator, derive_seed
from noise_aware_compilation.placement import (
    _rounded,
    approximate_dem,
    enumerate_placements,
    lowered_circuit,
)
from noise_aware_compilation.scaled_score import ScaledParameters, scaled_score_from_graph
from noise_aware_compilation.stats import wilson
from noise_aware_compilation.surface_code import (
    DEFAULT_SCHEDULE,
    Schedule,
    hook_safe_schedules,
    surface_code,
)

ROOT = Path(__file__).resolve().parent.parent
CALIBRATION = ROOT / "results_v3" / "scaled" / "calibration.json"
SEED_TAG = "v3-surface-check"
BASE_SEEDS = (1, 2, 3)
SHOTS = 200_000
SETTING = "readout"


def restored(
    device_name: str, setting: str = SETTING
) -> tuple[nove.DeviceModel, nove.HardwareNoiseModel]:
    """The device with its removed couplers back, each at the median kept CZ channel."""

    stored = load_device(device_name)
    noise = noise_for(setting, stored)
    removed = [tuple(int(q) for q in e["pair"]) for e in stored.device.metadata["removed_couplers"]]
    data = stored.device.to_dict()
    data["connectivity"] = sorted(
        [list(p) for p in stored.device.connectivity] + [list(p) for p in removed]
    )
    channels = dict(noise.gate_channels)
    kept = sorted(
        (ch for key, ch in channels.items() if key.gate == "CZ"),
        key=lambda ch: (math.fsum(ch.probabilities), ch.probabilities),
    )
    median = kept[len(kept) // 2]
    for a, b in removed:
        for pair in ((a, b), (b, a)):
            channels[nove.OperationKey("CZ", pair)] = median
    repaired = nove.HardwareNoiseModel(
        gate_channels=channels,
        gate_defaults=dict(noise.gate_defaults),
        idle_rates=dict(noise.idle_rates),
        default_idle_rate=noise.default_idle_rate,
        readout_flip_probabilities=dict(noise.readout_flip_probabilities),
        default_readout_flip_probability=noise.default_readout_flip_probability,
        reset_flip_probabilities=dict(noise.reset_flip_probabilities),
        default_reset_flip_probability=noise.default_reset_flip_probability,
    )
    return nove.DeviceModel.from_dict(data), repaired


@functools.cache
def _models(device_name: str, synthetic: bool) -> tuple[nove.DeviceModel, nove.HardwareNoiseModel]:
    if synthetic:
        return restored(device_name)
    stored = load_device(device_name)
    return stored.device, noise_for(SETTING, stored)


@functools.cache
def _circuit(distance: int, schedule: Schedule) -> nove.Circuit:
    return surface_code(distance, distance, schedule, "CZ")


def _score_task(
    args: tuple[str, bool, int, Schedule, tuple[int, ...], int, float],
) -> dict[str, Any]:
    device_name, synthetic, distance, schedule, mapping, k, step = args
    device, noise = _models(device_name, synthetic)
    circuit = _circuit(distance, schedule)
    t = time.perf_counter()
    dem = approximate_dem(lowered_circuit(circuit, device, noise, mapping), decompose=True)
    lowering = time.perf_counter() - t
    graph = matching_graph(dem)
    t = time.perf_counter()
    scaled = scaled_score_from_graph(graph, ScaledParameters(k, step))
    t_scaled = time.perf_counter() - t
    t = time.perf_counter()
    exact = decoder_graph_score_from_dem(dem)
    t_exact = time.perf_counter() - t
    return {
        "scaled": scaled,
        "exact": exact,
        "summed": summed_error(circuit, noise, mapping),
        "seconds_lowering": lowering,
        "seconds_scaled": t_scaled,
        "seconds_exact": t_exact,
    }


def _sim_task(
    args: tuple[str, bool, int, Schedule, tuple[int, ...], int, list[int]],
) -> dict[str, int]:
    device_name, synthetic, distance, schedule, mapping, shots, seeds = args
    device, noise = _models(device_name, synthetic)
    counts = PlacementEvaluator(_circuit(distance, schedule), device, noise, mapping).run(
        shots, seeds
    )
    return {"shots": counts.shots, "failures": counts.logical_failures}


def _seeds(label: str, base: int, schedule: Schedule, mapping: Sequence[int]) -> list[int]:
    key = f"{schedule[0]}{schedule[1]}|" + ",".join(map(str, mapping))
    return [derive_seed(SEED_TAG, base, label, key, k) for k in range(-(-SHOTS // CHUNK_SHOTS))]


def _choice(scores: Sequence[float], keys: Sequence[tuple[Any, ...]]) -> int:
    return min(range(len(scores)), key=lambda i: (_rounded(scores[i]), keys[i]))


def run(
    pool: ProcessPoolExecutor, device_name: str, distance: int, synthetic: bool, k: int, step: float
) -> dict[str, Any]:
    device, _noise = _models(device_name, synthetic)
    label = f"{device_name}{'-restored' if synthetic else ''}-d{distance}"
    schedules = hook_safe_schedules(distance, distance, "CZ")
    candidates: list[tuple[Schedule, tuple[int, ...]]] = []
    for schedule in schedules:
        for m in enumerate_placements(_circuit(distance, schedule), device):
            candidates.append((schedule, m))
    t = time.perf_counter()
    scores = list(
        pool.map(
            _score_task, [(device_name, synthetic, distance, s, m, k, step) for s, m in candidates]
        )
    )
    score_wall = time.perf_counter() - t
    tasks = [
        (device_name, synthetic, distance, s, m, SHOTS, _seeds(label, base, s, m))
        for s, m in candidates
        for base in BASE_SEEDS
    ]
    t = time.perf_counter()
    sims = list(pool.map(_sim_task, tasks))
    sim_wall = time.perf_counter() - t
    rows = []
    for i, (schedule, m) in enumerate(candidates):
        per_seed = sims[i * len(BASE_SEEDS) : (i + 1) * len(BASE_SEEDS)]
        failures = sum(s["failures"] for s in per_seed)
        shots = sum(s["shots"] for s in per_seed)
        rows.append(
            {
                "schedule": list(schedule),
                "mapping": list(m),
                **scores[i],
                "failures": failures,
                "shots": shots,
                "p_l": failures / shots,
            }
        )
    p = [r["p_l"] for r in rows]
    keys = [(tuple(r["schedule"]) != DEFAULT_SCHEDULE, tuple(r["mapping"])) for r in rows]
    naive = min(i for i, (s, _m) in enumerate(candidates) if s == DEFAULT_SCHEDULE)

    def picked(i: int) -> dict[str, Any]:
        w = wilson(rows[i]["failures"], rows[i]["shots"])
        return {
            "schedule": rows[i]["schedule"],
            "mapping": rows[i]["mapping"],
            "p_l": w.estimate,
            "wilson": [w.low, w.high],
        }

    summed_default = [i for i, (s, _m) in enumerate(candidates) if s == DEFAULT_SCHEDULE]
    summed_pick = summed_default[
        _choice([rows[i]["summed"] for i in summed_default], [keys[i] for i in summed_default])
    ]
    best = min(range(len(rows)), key=lambda i: (p[i], keys[i]))
    return {
        "label": label,
        "synthetic_couplers": synthetic,
        "distance": distance,
        "rounds": distance,
        "schedules": [list(s) for s in schedules],
        "candidates": len(rows),
        "rho_vs_simulated": {
            "scaled": spearman([r["scaled"] for r in rows], p),
            "exact": spearman([r["exact"] for r in rows], p),
            "summed": spearman([r["summed"] for r in rows], p),
        },
        "choices": {
            "naive": picked(naive),
            "scaled": picked(_choice([r["scaled"] for r in rows], keys)),
            "exact": picked(_choice([r["exact"] for r in rows], keys)),
            "summed-error": picked(summed_pick),
            "best-simulated": picked(best),
        },
        "p_l_summary": {"min": min(p), "median": statistics.median(p), "max": max(p)},
        "seconds": {
            "score_wall": score_wall,
            "simulation_wall": sim_wall,
            "lowering_mean": statistics.fmean(r["seconds_lowering"] for r in rows),
            "scaled_mean": statistics.fmean(r["seconds_scaled"] for r in rows),
            "exact_mean": statistics.fmean(r["seconds_exact"] for r in rows),
            "selection_one_core": math.fsum(
                r["seconds_lowering"] + r["seconds_scaled"] for r in rows
            ),
        },
        "rows": rows,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--machine-label", default=None)
    parser.add_argument(
        "--out", type=Path, default=ROOT / "results_v3" / "scaled" / "surface_check.json"
    )
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args(argv)
    commit, dirty = git_state()
    if dirty and not args.allow_dirty:
        parser.error("uncommitted changes to code, data or config; commit them first")
    chosen = json.loads(CALIBRATION.read_text(encoding="utf-8"))["chosen"]
    k, step = int(chosen["k"]), float(chosen["step"])
    start = time.perf_counter()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        results = [run(pool, "miami", 3, False, k, step), run(pool, "miami", 5, True, k, step)]
    for r in results:
        print(
            r["label"],
            r["candidates"],
            json.dumps(r["rho_vs_simulated"]),
            {n: round(c["p_l"], 5) for n, c in r["choices"].items()},
            flush=True,
        )
    payload = {
        "schema": "noise-aware-compilation.surface-check-v3/1",
        "description": __doc__,
        "setting": SETTING,
        "scaled_parameters": {"k": k, "step": step},
        "shots_per_seed": SHOTS,
        "base_seeds": list(BASE_SEEDS),
        "seed_tag": SEED_TAG,
        "results": results,
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
