"""v3 timing pilot (SPEC_V3.md R3-C7): what the exhaustive held-out runs cost on this machine.

Usage::

    nice -n 19 python -m nac_bench.timing_pilot --workers 48 --machine-label patty

It runs on development devices only (X16), at reset = readout, with native CZ circuits:

* **FakeFez d=7** (circuit D7): every placement is enumerated. 100 random placements are
  scored and get the R3-C6 simulation, a 100,000-shot pilot on a pilot seed and then the
  main-stage shots per seed from the pilot count, on three seeds. The selections that run
  over every candidate at once (v2's VF2Layout, Qiskit's default at levels 1 to 3 and
  mapomatic, its layouts' costs computed on the workers) are timed on all of them.
* **FakeFez d=3 and d=5**: 20 random placements each, scored, with the pilot stage, for
  the per-shot cost and P_L at those distances.
* **FakeMiami d=7**, the square-lattice proxy for FakeNighthawk d=7: every placement is
  enumerated (time and peak memory) and 1,000 random ones are scored. FakeMiami d=3 and
  d=5: 100 random placements scored.
* **The d=5 surface code** (R3-S6): the scaled score's whole selection, both hook-safe
  schedules on every embedding, in one process, on FakeMiami with its removed couplers
  restored (it has no d=5 embedding otherwise), the proxy for FakeNighthawk.

Scoring a placement is the held-out runs' path: one prepared lowering
(:class:`noise_aware_compilation.fast_lowering.Lowering`) shared by the v1 score, the
decoder-graph score and summed error, all on the workers. Every scored placement's circuit
is also checked against Nove's own lowering, outside the timed part.

The projection multiplies these costs by each held-out case's placement count. The counts
come from the coupling maps alone (``conf_<device>.json`` of qiskit-ibm-runtime, the one
field ``coupling_map``): removed couplers are not subtracted, so they are upper bounds, and
no calibration value of a held-out device is read. Both reset settings are projected, as
v3 runs both. The seeds are timing seeds (tag ``v3-timing-pilot``); no count from this run
enters any result. The first pilot, with Nove's lowering for each score and summed error
and mapomatic in one process, is ``results_v3/timing_pilot/timing_pilot.json``.
"""

from __future__ import annotations

import argparse
import functools
import json
import math
import os
import random
import resource
import statistics
import sys
import time
import warnings
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import nove

from nac_bench.baselines_v3 import mapomatic_cost_chunk, run_baselines
from nac_bench.machine import machine
from nac_bench.qiskit_layout import qiskit_layout
from nac_bench.run_reset import git_state, noise_for
from nac_bench.surface_check import restored
from noise_aware_compilation.baselines import summed_error
from noise_aware_compilation.circuits import repetition_code
from noise_aware_compilation.decoder_graph import decoder_graph_score_from_dem, matching_graph
from noise_aware_compilation.devices import StoredDevice, load_device
from noise_aware_compilation.evaluation import CHUNK_SHOTS, PlacementEvaluator, derive_seed
from noise_aware_compilation.fast_lowering import Lowering, v1_score_from_dem
from noise_aware_compilation.placement import (
    Placement,
    approximate_dem,
    enumerate_placements,
)
from noise_aware_compilation.scaled_score import ScaledParameters, scaled_score_from_graph
from noise_aware_compilation.surface_code import hook_safe_schedules, surface_code

ROOT = Path(__file__).resolve().parent.parent
SEED_TAG = "v3-timing-pilot"
SETTING = "readout"
ROUNDS = 3
#: (label, distance, basis) of the four repetition circuits of the v3-headline family
CIRCUITS = (("A", 3, "Z"), ("B", 3, "X"), ("D5", 5, "Z"), ("D7", 7, "Z"))
PILOT_SHOTS = 100_000
MAIN_MIN_SHOTS = 200_000
MAIN_MAX_SHOTS = 25_600_000
EXPECTED_FAILURES = 400  # 3 seeds x shots x p >= 400 (R3-C6)
BASE_SEEDS = (1, 2, 3)
SEED_TRANSPILER = 20261021
#: R3-C5: the simulated set at d=5 and d=7 on the square lattice, at most every method's
#: choice (8), the top 30 by three rankings and 100 random placements
BEST_FOUND_SET = 8 + 3 * 30 + 100
HELD_OUT = {
    "kingston": ("heavy-hex", "fez"),
    "aachen": ("heavy-hex", "fez"),
    "berlin": ("square", "miami"),
    "nighthawk": ("square", "miami"),
}
#: R3-C4 and R3-C7 thresholds, in hours on this machine at the worker count used
SELECTION_LIMIT_HOURS = 24.0
SIMULATION_LIMIT_HOURS = 48.0
#: R3-S6, seconds on one core
SURFACE_SELECTION_LIMIT_SECONDS = 60.0
CALIBRATION = ROOT / "results_v3" / "scaled" / "calibration.json"


def main_shots(failures: int, shots: int) -> int:
    """R3-C6: the smallest 200,000 x 2^k with 3 x n x p >= 400, capped; p = 0 gives the cap."""

    if failures == 0:
        return MAIN_MAX_SHOTS
    p = failures / shots
    n = MAIN_MIN_SHOTS
    while len(BASE_SEEDS) * n * p < EXPECTED_FAILURES and n < MAIN_MAX_SHOTS:
        n *= 2
    return min(n, MAIN_MAX_SHOTS)


def timing_seed(*parts: object) -> int:
    return derive_seed(SEED_TAG, *parts)


@functools.cache
def _models(device: str) -> tuple[StoredDevice, nove.HardwareNoiseModel]:
    stored = load_device(device)
    return stored, noise_for(SETTING, stored)


@functools.cache
def _circuit(distance: int, basis: str) -> nove.Circuit:
    return repetition_code(distance, ROUNDS, basis, "CZ")  # type: ignore[arg-type]


@functools.cache
def _lowering(device: str, distance: int, basis: str) -> Lowering:
    stored, noise = _models(device)
    return Lowering(_circuit(distance, basis), stored.device, noise)


def _score_task(args: tuple[str, int, str, Placement]) -> dict[str, Any]:
    """Every per-placement selection score, as the held-out runner computes them."""

    device, distance, basis, mapping = args
    _stored, noise = _models(device)
    circuit = _circuit(distance, basis)
    lowering = _lowering(device, distance, basis)
    t = time.perf_counter()
    noisy = lowering.stim_circuit(mapping)
    t_lower = time.perf_counter() - t
    t = time.perf_counter()
    v1 = v1_score_from_dem(approximate_dem(noisy, decompose=False))
    t_v1 = time.perf_counter() - t
    t = time.perf_counter()
    dg = decoder_graph_score_from_dem(approximate_dem(noisy, decompose=True))
    t_dg = time.perf_counter() - t
    t = time.perf_counter()
    summed = summed_error(circuit, noise, mapping)
    t_summed = time.perf_counter() - t
    lowering.check(mapping)  # Nove's own lowering gives the same circuit (not timed)
    return {
        "decoder_graph": dg,
        "v1": v1,
        "summed": summed,
        "seconds_lowering": t_lower,
        "seconds_decoder_graph": t_dg,
        "seconds_v1": t_v1,
        "seconds_summed": t_summed,
    }


def _mapomatic_chunks(pool: ProcessPoolExecutor, device: str) -> Any:
    def run(small: Any, chunks: list[list[list[int]]]) -> Any:
        return pool.map(mapomatic_cost_chunk, [(device, SETTING, small, c) for c in chunks])

    return run


def surface_selection(k: int, step: float) -> dict[str, Any]:
    """R3-S6: the whole d=5 surface-code selection in this one process."""

    t = time.perf_counter()
    device, noise = restored("miami")
    best: tuple[float, int] | None = None
    n = 0
    for schedule in hook_safe_schedules(5, 5, "CZ"):
        circuit = surface_code(5, 5, schedule)
        lowering = Lowering(circuit, device, noise)
        for m in enumerate_placements(circuit, device):
            graph = matching_graph(approximate_dem(lowering.stim_circuit(m), decompose=True))
            score = scaled_score_from_graph(graph, ScaledParameters(k, step))
            best = (score, n) if best is None or score < best[0] else best
            n += 1
    seconds = time.perf_counter() - t
    return {
        "device": "miami, removed couplers restored at the median CZ channel (synthetic)",
        "candidates": n,
        "scaled_parameters": {"k": k, "step": step},
        "seconds_one_core": seconds,
        "limit_seconds": SURFACE_SELECTION_LIMIT_SECONDS,
        "over_limit": seconds > SURFACE_SELECTION_LIMIT_SECONDS,
    }


def _sim_task(args: tuple[str, int, str, Placement, int, list[int]]) -> dict[str, Any]:
    device, distance, basis, mapping, shots, seeds = args
    stored, noise = _models(device)
    t = time.perf_counter()
    evaluator = PlacementEvaluator(_circuit(distance, basis), stored.device, noise, mapping)
    build = time.perf_counter() - t
    t = time.perf_counter()
    counts = evaluator.run(shots, seeds)
    return {
        "shots": counts.shots,
        "failures": counts.logical_failures,
        "seconds_build": build,
        "seconds_sample": time.perf_counter() - t,
    }


def _count_task(args: tuple[str, int, str]) -> dict[str, Any]:
    name, distance, basis = args
    t = time.perf_counter()
    count = len(enumerate_placements(_circuit(distance, basis), coupling_device(name)))
    return {"placements": count, "seconds": time.perf_counter() - t}


def coupling_from_conf(path: Path, device_id: str) -> nove.DeviceModel:
    """A device model with only the coupling map of the conf file at ``path``.

    Only the fields ``coupling_map`` and ``n_qubits`` are used; no calibration value is read.
    """

    conf = json.loads(path.read_text(encoding="utf-8"))
    pairs = sorted({(min(a, b), max(a, b)) for a, b in conf["coupling_map"]})
    return nove.DeviceModel(
        connectivity=pairs, device_id=device_id, metadata={"num_qubits": int(conf["n_qubits"])}
    )


def coupling_device(name: str) -> nove.DeviceModel:
    """A device model with only ``name``'s coupling map: no calibration value is read.

    Called only by the timing pilot itself; the tests exercise :func:`coupling_from_conf` on a
    development device's conf, so no test run opens a held-out device's file (X16).
    """

    if name not in HELD_OUT:
        raise ValueError(f"{name} is not a held-out device")
    import qiskit_ibm_runtime

    path = (
        Path(qiskit_ibm_runtime.__file__).parent
        / "fake_provider"
        / "backends"
        / name
        / f"conf_{name}.json"
    )
    return coupling_from_conf(path, name)


def _chunk_seeds(label: str, base: int, mapping: Placement, shots: int) -> list[int]:
    return [
        timing_seed(base, label, ",".join(map(str, mapping)), k)
        for k in range(-(-shots // CHUNK_SHOTS))
    ]


def _summary(values: Sequence[float]) -> dict[str, float]:
    return {
        "n": len(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "max": max(values),
    }


def time_device(
    pool: ProcessPoolExecutor,
    device: str,
    distance: int,
    basis: str,
    *,
    scored: int,
    simulated: int,
    main_stage: bool,
    all_candidate_methods: bool,
) -> dict[str, Any]:
    stored, noise = _models(device)
    circuit = _circuit(distance, basis)
    label = f"{device}-d{distance}-{basis}"
    t = time.perf_counter()
    candidates = enumerate_placements(circuit, stored.device)
    out: dict[str, Any] = {
        "device": device,
        "distance": distance,
        "basis": basis,
        "rounds": ROUNDS,
        "placements": len(candidates),
        "enumeration_seconds": time.perf_counter() - t,
        "peak_rss_mb_after_enumeration": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
    }
    rng = random.Random(timing_seed("sample", label))
    sample = rng.sample(candidates, min(scored, len(candidates)))

    t = time.perf_counter()
    scores = list(pool.map(_score_task, [(device, distance, basis, m) for m in sample]))
    out["score_wall_seconds"] = time.perf_counter() - t
    out["checked_against_nove"] = len(scores)
    for part in ("lowering", "decoder_graph", "v1", "summed"):
        out[f"seconds_{part}"] = _summary([s[f"seconds_{part}"] for s in scores])
    out["seconds_per_placement"] = _summary(
        [
            s["seconds_lowering"]
            + s["seconds_decoder_graph"]
            + s["seconds_v1"]
            + s["seconds_summed"]
            for s in scores
        ]
    )

    if all_candidate_methods:
        timings: dict[str, float] = {}
        t = time.perf_counter()
        qiskit_layout(circuit, stored.device, noise, candidates)
        timings["qiskit-vf2"] = time.perf_counter() - t
        t = time.perf_counter()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            record = run_baselines(
                circuit,
                stored.snapshot,
                stored.device,
                candidates,
                SEED_TRANSPILER,
                reset_error="readout",
                map_chunks=_mapomatic_chunks(pool, device),
            )
        timings["qiskit-default-l1-l3-and-mapomatic"] = time.perf_counter() - t
        timings["mapomatic"] = float(record["mapomatic"]["selection_seconds"])
        out["all_candidate_seconds"] = timings

    if simulated:
        sims = sample[:simulated]
        t = time.perf_counter()
        pilot = list(
            pool.map(
                _sim_task,
                [
                    (
                        device,
                        distance,
                        basis,
                        m,
                        PILOT_SHOTS,
                        _chunk_seeds(label, 0, m, PILOT_SHOTS),
                    )
                    for m in sims
                ],
            )
        )
        out["pilot_wall_seconds"] = time.perf_counter() - t
        out["pilot_p"] = _summary([p["failures"] / p["shots"] for p in pilot])
        out["pilot_zero_failures"] = sum(1 for p in pilot if p["failures"] == 0)
        out["seconds_build"] = _summary([p["seconds_build"] for p in pilot])
        out["seconds_per_shot"] = _summary([p["seconds_sample"] / p["shots"] for p in pilot])
        shots = [main_shots(p["failures"], p["shots"]) for p in pilot]
        out["main_shots_per_seed"] = _summary([float(s) for s in shots])
        out["main_shots_at_cap"] = sum(1 for s in shots if s == MAIN_MAX_SHOTS)
        if main_stage:
            tasks = [
                (device, distance, basis, m, n, _chunk_seeds(label, base, m, n))
                for m, n in zip(sims, shots, strict=True)
                for base in BASE_SEEDS
            ]
            t = time.perf_counter()
            main = list(pool.map(_sim_task, tasks))
            out["main_wall_seconds"] = time.perf_counter() - t
            out["main_core_seconds"] = math.fsum(
                m["seconds_build"] + m["seconds_sample"] for m in main
            )
            out["main_seconds_per_shot"] = _summary(
                [m["seconds_sample"] / m["shots"] for m in main]
            )
    return out


def project(
    measured: dict[str, dict[str, Any]], counts: dict[str, dict[str, Any]], workers: int
) -> dict[str, Any]:
    """Core-hours and wall hours of each held-out case, per reset setting and for both."""

    fez7 = measured["fez-7-Z"]
    # mapomatic's wall time on all workers, taken as proportional to the layout count
    mapomatic_per_candidate = fez7["all_candidate_seconds"]["mapomatic"] / fez7["placements"]
    cases = []
    for device, (lattice, proxy) in HELD_OUT.items():
        for label, distance, _basis in CIRCUITS:
            key = f"{device}-{label}"
            n = counts[key]["placements"]
            score = measured[f"{proxy}-{distance}-Z"]
            sim = measured[f"fez-{distance}-Z"]  # per-shot cost and P_L from FakeFez
            per_placement = score["seconds_per_placement"]["mean"]
            # enumeration runs in one process, timed on the coupling map itself
            serial = counts[key]["seconds"]
            mapomatic = n * mapomatic_per_candidate
            selection_core = n * per_placement + serial + mapomatic * workers
            selection_wall = n * per_placement / workers + serial + mapomatic
            simulated = n if (lattice == "heavy-hex" or distance == 3) else min(n, BEST_FOUND_SET)
            shots = PILOT_SHOTS + len(BASE_SEEDS) * sim["main_shots_per_seed"]["mean"]
            per_simulated = (1 + len(BASE_SEEDS)) * sim["seconds_build"]["mean"] + shots * sim[
                "seconds_per_shot"
            ]["mean"]
            simulation_core = simulated * per_simulated
            cases.append(
                {
                    "case": key,
                    "lattice": lattice,
                    "placements_upper_bound": n,
                    "simulated_placements": simulated,
                    "selection_core_hours": selection_core / 3600,
                    "selection_serial_hours": serial / 3600,
                    "selection_wall_hours": selection_wall / 3600,
                    "simulation_core_hours": simulation_core / 3600,
                    "simulation_wall_hours": simulation_core / 3600 / workers,
                    "selection_over_limit": selection_wall / 3600 > SELECTION_LIMIT_HOURS,
                }
            )
    by_device: dict[str, Any] = {}
    for device in HELD_OUT:
        rows = [c for c in cases if c["case"].startswith(f"{device}-")]
        sim = sum(c["simulation_wall_hours"] for c in rows)
        sel = sum(c["selection_wall_hours"] for c in rows)
        by_device[device] = {
            "selection_wall_hours_per_setting": sel,
            "simulation_wall_hours_per_setting": sim,
            "wall_hours_both_settings": 2 * (sel + sim),
            "simulation_over_limit": 2 * sim > SIMULATION_LIMIT_HOURS,
        }
    return {
        "workers": workers,
        "settings": 2,
        "cases": cases,
        "devices": by_device,
        "total_wall_hours_both_settings": sum(
            d["wall_hours_both_settings"] for d in by_device.values()
        ),
        "notes": [
            "placement counts are upper bounds (coupling map only, removed couplers kept)",
            "per-shot cost and P_L come from FakeFez at reset = readout; a held-out device "
            "with lower P_L needs proportionally more main-stage shots, up to the cap",
            "selection wall hours: one prepared lowering per placement for the v1 score, the "
            "decoder-graph score and summed error on every worker; enumeration (timed on the "
            "coupling map) in one process; "
            "mapomatic's layout costs on every worker, its whole time scaled by the count",
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workers", type=int, default=min(48, os.cpu_count() or 1))
    parser.add_argument("--machine-label", default=None)
    parser.add_argument(
        "--out", type=Path, default=ROOT / "results_v3" / "timing_pilot" / "timing_pilot_2.json"
    )
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--quick", action="store_true", help="tiny sizes, for a smoke test")
    args = parser.parse_args(argv)
    commit, dirty = git_state()
    if dirty and not args.allow_dirty:
        parser.error("uncommitted changes to code, data or config; commit them first")
    q = args.quick
    plan = [
        # (device, distance, scored, simulated, main stage, all-candidate methods)
        ("fez", 7, 4 if q else 100, 4 if q else 100, True, True),
        ("fez", 3, 4 if q else 20, 4 if q else 20, False, False),
        ("fez", 5, 4 if q else 20, 4 if q else 20, False, False),
        ("miami", 3, 4 if q else 100, 0, False, False),
        ("miami", 5, 4 if q else 100, 0, False, False),
        ("miami", 7, 4 if q else 1000, 0, False, False),
    ]
    start = time.perf_counter()
    measured: dict[str, dict[str, Any]] = {}
    counts: dict[str, dict[str, Any]] = {}
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        count_jobs = [
            (f"{device}-{label}", (device, distance, basis))
            for device in HELD_OUT
            for label, distance, basis in CIRCUITS
        ]
        if q:
            count_jobs = [j for j in count_jobs if j[1][1] == 3]
        pending = {key: pool.submit(_count_task, job) for key, job in count_jobs}
        for device, distance, scored, simulated, main_stage, every in plan:
            if q and device == "miami" and distance == 7:
                continue
            t = time.perf_counter()
            measured[f"{device}-{distance}-Z"] = time_device(
                pool,
                device,
                distance,
                "Z",
                scored=scored,
                simulated=simulated,
                main_stage=main_stage,
                all_candidate_methods=every,
            )
            print(f"{device} d={distance}: {time.perf_counter() - t:.1f}s", flush=True)
        for key, future in pending.items():
            counts[key] = future.result()
    chosen = json.loads(CALIBRATION.read_text(encoding="utf-8"))["chosen"]
    surface = surface_selection(int(chosen["k"]), float(chosen["step"]))
    print(f"surface d=5 selection on one core: {surface['seconds_one_core']:.1f}s", flush=True)
    payload: dict[str, Any] = {
        "schema": "noise-aware-compilation.timing-pilot-v3/2",
        "description": __doc__,
        "setting": SETTING,
        "two_qubit_gate": "CZ",
        "rounds": ROUNDS,
        "pilot_shots": PILOT_SHOTS,
        "base_seeds": list(BASE_SEEDS),
        "seed_tag": SEED_TAG,
        "measured": measured,
        "held_out_placement_counts": counts,
        "surface_selection": surface,
        "package_git_commit": commit,
        "package_git_dirty": dirty,
        "machine": machine(args.machine_label, workers=args.workers),
        "wall_clock_seconds": time.perf_counter() - start,
    }
    if not q:
        payload["projection"] = project(measured, counts, args.workers)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.out} in {payload['wall_clock_seconds']:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
