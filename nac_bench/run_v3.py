"""v3 held-out runner: SPEC_V3.md Part A and the surface code of Part C.

Usage::

    nice -n 19 python -m nac_bench.run_v3 configs_v3/held_out.json --workers 48 \\
        --machine-label patty [--only CASE_ID ...] [--skip-existing]

For each case of the config (device, reset setting, circuit), with the score parameters and
the fast search frozen at F3:

1. **Selection (R3-C4).** Every valid placement is enumerated and lowered once by
   :class:`noise_aware_compilation.fast_lowering.Lowering`; the decoder-graph score, the v1
   score and summed error are computed from that lowering on the workers, and a seeded sample
   of placements is checked against Nove's own lowering. Each score's choice follows its own
   v1/v2 tie rule. Qiskit's default transpile at levels 1 to 3 and mapomatic run on the
   IBM-shaped CZ Target of the setting (R3-C9), mapomatic's layout costs on the workers;
   v2's VF2Layout baseline and the fast search (R3-C8) are recorded as exploratory. The
   naive placement is the first valid placement.
2. **Simulated set (R3-C5).** Every placement on a heavy-hex device and at d=3; on the square
   lattice at d=5 and d=7, the union of every method's choice, the top 30 by the
   decoder-graph score, by summed error and by mapomatic's cost, and 100 placements drawn
   uniformly (seeded).
3. **Shots (R3-C6).** A 100,000-shot pilot on the pilot seed, then each placement's
   main-stage shots per seed from its pilot count, on the three base seeds. Pilot counts are
   recorded only so the shots rule can be checked; no result uses them.
4. Writes ``<out>/runs/<case_id>.json`` and every placement's scores to
   ``<out>/scores/<case_id>.npz``, whose SHA-256 the run file records (the score files of the
   square-lattice cases run to hundreds of megabytes and stay out of git).

Surface-code cases (R3-S4, R3-S5) score every embedding under both hook-safe schedules with
the scaled score (headline) and v2's exact decoder-graph score, give summed error, the naive
placement, Qiskit's default and mapomatic the default schedule, and simulate every
candidate under the same shots rule.

A case with no valid placement writes a run file that says so (it leaves the family).
"""

from __future__ import annotations

import argparse
import bisect
import functools
import hashlib
import importlib.metadata
import io
import json
import math
import os
import random
import sys
import time
import warnings
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import nove
import numpy as np

from nac_bench.baselines_v3 import mapomatic_cost_chunk, mapomatic_version, run_baselines
from nac_bench.machine import machine
from nac_bench.qiskit_layout import qiskit_layout, qiskit_version
from nac_bench.run_reset import SETTINGS, _bench_source_sha256, git_state, noise_for
from noise_aware_compilation.baselines import SUMMED_ERROR_METHOD, summed_error
from noise_aware_compilation.benchmark import provenance
from noise_aware_compilation.circuits import repetition_code
from noise_aware_compilation.decoder_graph import (
    DECODER_GRAPH_METHOD,
    decoder_graph_score_from_dem,
    matching_graph,
)
from noise_aware_compilation.devices import StoredDevice, load_device
from noise_aware_compilation.evaluation import (
    CHUNK_SHOTS,
    DECODER_DESCRIPTION,
    Counts,
    PlacementEvaluator,
    derive_seed,
)
from noise_aware_compilation.fast_lowering import Lowering, v1_score_from_dem
from noise_aware_compilation.fast_search import FastSearch, SearchParameters
from noise_aware_compilation.placement import (
    SCORE_METHOD,
    TIE_BREAK_RULE,
    NoValidPlacementError,
    Placement,
    _rounded,
    approximate_dem,
    enumerate_placements,
    interaction_edges,
)
from noise_aware_compilation.scaled_score import ScaledParameters, scaled_score_from_graph
from noise_aware_compilation.surface_code import (
    DEFAULT_SCHEDULE,
    Schedule,
    hook_safe_schedules,
    surface_code,
)

ROOT = Path(__file__).resolve().parent.parent
RUN_SCHEMA = "noise-aware-compilation.held-out-run-v3/1"
SEED_TAG = "eval-v3-held-out"
PILOT_TAG = "pilot-v3-held-out"
SAMPLE_TAG = "sample-v3-held-out"
CHECK_TAG = "check-v3-held-out"
SCORE_CHUNK = 2_000  # placements per scoring task
TASK_CHUNKS = 8  # simulation chunks (of CHUNK_SHOTS) per task at most
LOCK_FILE = ROOT / "requirements-lock.txt"

#: ("repetition", distance, rounds, basis) or ("surface", distance, rounds, schedule)
CircuitKey = tuple[Any, ...]


# --- config ---------------------------------------------------------------------------------


def load_config(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("config must be a JSON object")
    data["_config_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return data


def cases(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Every case of the config, headline setting first."""

    out = []
    for setting in config["settings_order"]:
        if setting not in SETTINGS:
            raise ValueError(f"unknown reset setting {setting!r}")
        for device, info in config["devices"].items():
            base = {
                "device": device,
                "lattice": info["lattice"],
                "synthetic_calibration": bool(info["synthetic_calibration"]),
                "setting": setting,
                "headline_setting": setting == config["headline_setting"],
            }
            for label in info["circuits"]:
                spec = config["circuits"][label]
                out.append(
                    {
                        **base,
                        "case_id": f"v3-{setting}-{device}-{label}",
                        "kind": spec["kind"],
                        "circuit": {"label": label, **spec},
                    }
                )
    return out


def main_shots(failures: int, shots: int, rule: dict[str, Any], seeds: int) -> int:
    """R3-C6: the smallest min x 2^k with seeds x n x p >= target, capped; p = 0 gives the cap."""

    low, high = int(rule["min_shots_per_seed"]), int(rule["max_shots_per_seed"])
    target = int(rule["expected_failures"])
    if failures == 0:
        return high
    p = failures / shots
    n = low
    while seeds * n * p < target and n < high:
        n *= 2
    return min(n, high)


# --- worker state (cached per process) ------------------------------------------------------


@functools.cache
def _models(device: str, setting: str) -> tuple[StoredDevice, nove.HardwareNoiseModel]:
    stored = load_device(device)
    return stored, noise_for(setting, stored)


@functools.cache
def _circuit(key: CircuitKey) -> nove.Circuit:
    if key[0] == "repetition":
        _kind, distance, rounds, basis = key
        return repetition_code(distance, rounds, basis, "CZ")
    _kind, distance, rounds, schedule = key
    return surface_code(distance, rounds, schedule, "CZ")


@functools.cache
def _lowering(device: str, setting: str, key: CircuitKey) -> Lowering:
    stored, noise = _models(device, setting)
    return Lowering(_circuit(key), stored.device, noise)


@functools.cache
def _schedules(distance: int, rounds: int) -> tuple[Schedule, ...]:
    return hook_safe_schedules(distance, rounds, "CZ")


def _score_chunk(
    args: tuple[str, str, CircuitKey, list[Placement]],
) -> tuple[list[float], list[float], list[float]]:
    """Decoder-graph score, v1 score and summed error of each placement of a chunk."""

    device, setting, key, chunk = args
    _stored, noise = _models(device, setting)
    circuit = _circuit(key)
    lowering = _lowering(device, setting, key)
    dg, v1, summed = [], [], []
    for m in chunk:
        noisy = lowering.stim_circuit(m)
        dg.append(decoder_graph_score_from_dem(approximate_dem(noisy, decompose=True)))
        v1.append(v1_score_from_dem(approximate_dem(noisy, decompose=False)))
        summed.append(summed_error(circuit, noise, m))
    return dg, v1, summed


def _surface_score(
    args: tuple[str, str, CircuitKey, Placement, int, float],
) -> tuple[float, float, float]:
    """Scaled score, exact decoder-graph score and summed error of one surface candidate."""

    device, setting, key, mapping, k, step = args
    _stored, noise = _models(device, setting)
    dem = approximate_dem(_lowering(device, setting, key).stim_circuit(mapping), decompose=True)
    scaled = scaled_score_from_graph(matching_graph(dem), ScaledParameters(k, step))
    return scaled, decoder_graph_score_from_dem(dem), summed_error(_circuit(key), noise, mapping)


def _check_task(args: tuple[str, str, CircuitKey, Placement]) -> int:
    device, setting, key, mapping = args
    _lowering(device, setting, key).check(mapping)  # raises if it differs from Nove's
    return 1


@functools.lru_cache(maxsize=8)
def _evaluator(
    device: str, setting: str, key: CircuitKey, mapping: Placement
) -> PlacementEvaluator:
    stored, noise = _models(device, setting)
    return PlacementEvaluator(_circuit(key), stored.device, noise, mapping)


def _sim_task(args: tuple[str, str, CircuitKey, Placement, int, list[int]]) -> dict[str, int]:
    device, setting, key, mapping, shots, seeds = args
    return _evaluator(device, setting, key, mapping).run(shots, seeds).to_dict()


def _mapomatic_chunks(pool: ProcessPoolExecutor, device: str, setting: str) -> Any:
    def run(small: Any, chunks: list[list[list[int]]]) -> Any:
        return pool.map(
            mapomatic_cost_chunk, [(device, SETTINGS[setting], small, c) for c in chunks]
        )

    return run


# --- selection helpers ----------------------------------------------------------------------


def rounded(values: np.ndarray) -> np.ndarray:
    """Each score rounded to 12 significant digits, as the package's tie rule compares them."""

    return np.fromiter((_rounded(float(x)) for x in values), dtype=np.float64, count=len(values))


def rank_of(keys: np.ndarray, i: int) -> int:
    """Rank of placement ``i`` by (key, enumeration order); 1 is the choice."""

    return 1 + int(np.count_nonzero(keys < keys[i])) + int(np.count_nonzero(keys[:i] == keys[i]))


def index_of(placements: Sequence[Placement], mapping: Sequence[int] | None) -> int | None:
    """Index of ``mapping`` in the lexicographically ordered placements, or None."""

    if mapping is None:
        return None
    m = tuple(int(q) for q in mapping)
    i = bisect.bisect_left(placements, m)
    return i if i < len(placements) and placements[i] == m else None


def _chunk_seeds(tag: str, base: int, case_id: str, key: str, shots: int) -> list[int]:
    return [derive_seed(tag, base, case_id, key, k) for k in range(-(-shots // CHUNK_SHOTS))]


def simulate(
    pool: ProcessPoolExecutor,
    device: str,
    setting: str,
    case_id: str,
    items: Sequence[tuple[CircuitKey, Placement, str]],
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """R3-C6 for each (circuit, placement, seed key): pilot, then main stage on every base seed."""

    rule = config["shots_rule"]
    pilot_seed = int(config["pilot_seed"])
    base_seeds = [int(s) for s in config["base_seeds"]]
    pilot_shots = int(rule["pilot_shots"])
    t = time.perf_counter()
    pilot = list(
        pool.map(
            _sim_task,
            [
                (
                    device,
                    setting,
                    key,
                    m,
                    pilot_shots,
                    _chunk_seeds(PILOT_TAG, pilot_seed, case_id, seed_key, pilot_shots),
                )
                for key, m, seed_key in items
            ],
        )
    )
    pilot_seconds = time.perf_counter() - t
    shots = [main_shots(p["logical_failures"], p["shots"], rule, len(base_seeds)) for p in pilot]
    tasks, owners = [], []
    for i, ((key, m, seed_key), n) in enumerate(zip(items, shots, strict=True)):
        for si, base in enumerate(base_seeds):
            seeds = _chunk_seeds(SEED_TAG, base, case_id, seed_key, n)
            for part in range(0, len(seeds), TASK_CHUNKS):
                part_shots = min(TASK_CHUNKS * CHUNK_SHOTS, n - part * CHUNK_SHOTS)
                tasks.append(
                    (device, setting, key, m, part_shots, seeds[part : part + TASK_CHUNKS])
                )
                owners.append((i, si))
    t = time.perf_counter()
    totals: dict[tuple[int, int], Counts] = {}
    for owner, counts in zip(owners, pool.map(_sim_task, tasks, chunksize=1), strict=True):
        c = Counts(**counts)
        totals[owner] = c if owner not in totals else totals[owner] + c
    main_seconds = time.perf_counter() - t
    rows = []
    for i in range(len(items)):
        per_seed = []
        for si, base in enumerate(base_seeds):
            c = totals[(i, si)]
            if c.shots != shots[i]:
                raise RuntimeError("a placement's main-stage shots differ from the rule's")
            per_seed.append({"base_seed": base, **c.to_dict()})
        rows.append(
            {
                "pilot": {
                    "shots": pilot[i]["shots"],
                    "logical_failures": pilot[i]["logical_failures"],
                },
                "shots_per_seed": shots[i],
                "per_seed": per_seed,
            }
        )
    return rows, {"pilot": pilot_seconds, "main": main_seconds}


def _save_scores(path: Path, arrays: dict[str, np.ndarray]) -> str:
    buffer = io.BytesIO()
    np.savez_compressed(buffer, allow_pickle=False, **arrays)
    data = buffer.getvalue()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _check_frozen(config: dict[str, Any]) -> tuple[ScaledParameters, SearchParameters]:
    """The F3 parameters, which the config must repeat exactly."""

    freeze_path = ROOT / config["score_freeze_file"]
    frozen = json.loads(freeze_path.read_text(encoding="utf-8"))
    if frozen["scaled_score"] != config["scaled_score"]:
        raise RuntimeError("the config's scaled-score parameters differ from the F3 freeze")
    if frozen["fast_search"] != config["fast_search"]:
        raise RuntimeError("the config's fast-search parameters differ from the F3 freeze")
    scaled = config["scaled_score"]
    return ScaledParameters(int(scaled["k"]), float(scaled["step"])), SearchParameters(
        **config["fast_search"]
    )


def _common_provenance(
    stored: StoredDevice,
    noise: nove.HardwareNoiseModel,
    circuit: nove.Circuit,
    case: dict[str, Any],
    workers: int,
    machine_label: str | None,
    stage_seconds: dict[str, float],
) -> dict[str, Any]:
    commit, dirty = git_state()
    prov = provenance(stored.snapshot, stored.device, noise, circuit)
    prov.update(
        {
            "package_git_commit": commit,
            "package_git_dirty": dirty,
            "qiskit_version": qiskit_version(),
            "mapomatic_version": mapomatic_version(),
            "nac_bench_source_sha256": _bench_source_sha256(),
            "lock_file_sha256": hashlib.sha256(LOCK_FILE.read_bytes()).hexdigest(),
            "reset_setting": case["setting"],
            "machine": machine(machine_label, workers=workers),
            "stage_seconds": stage_seconds,
        }
    )
    return prov


def _header(case: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": RUN_SCHEMA,
        "case": case,
        "setting": case["setting"],
        "setting_description": config["settings"][case["setting"]],
        "config_file_sha256": config["_config_sha256"],
        "family": config["family"] if case["headline_setting"] else None,
        "seeds": {
            "base_seeds": [int(s) for s in config["base_seeds"]],
            "pilot_seed": int(config["pilot_seed"]),
            "sample_seed": int(config["sample_seed"]),
            "seed_transpiler": int(config["seed_transpiler"]),
            "rule": (
                f"chunk k of a placement's main stage on base seed b: derive_seed({SEED_TAG!r}, "
                f"b, case_id, key, k); pilot: derive_seed({PILOT_TAG!r}, pilot_seed, case_id, "
                "key, k); key is the mapping joined by commas (surface code: the schedule's two "
                "letters, '|', then the mapping)"
            ),
        },
        "shots_rule": config["shots_rule"],
        "chunk_shots": CHUNK_SHOTS,
        "decoder": DECODER_DESCRIPTION,
    }


def _no_placement(
    case: dict[str, Any],
    config: dict[str, Any],
    reason: str,
    workers: int,
    machine_label: str | None,
) -> dict[str, Any]:
    commit, dirty = git_state()
    return {
        **_header(case, config),
        "no_valid_placement": True,
        "reason": reason,
        "provenance": {
            "package_git_commit": commit,
            "package_git_dirty": dirty,
            "lock_file_sha256": hashlib.sha256(LOCK_FILE.read_bytes()).hexdigest(),
            "reset_setting": case["setting"],
            "machine": machine(machine_label, workers=workers),
        },
    }


# --- repetition-code cases ------------------------------------------------------------------


def run_repetition(
    case: dict[str, Any],
    config: dict[str, Any],
    pool: ProcessPoolExecutor,
    *,
    workers: int,
    out_dir: Path,
    machine_label: str | None,
) -> dict[str, Any]:
    _scaled, search_parameters = _check_frozen(config)
    device, setting, case_id = case["device"], case["setting"], case["case_id"]
    spec = case["circuit"]
    key: CircuitKey = ("repetition", int(spec["distance"]), int(spec["rounds"]), spec["basis"])
    stored, noise = _models(device, setting)
    circuit = _circuit(key)
    seconds: dict[str, float] = {}

    t = time.perf_counter()
    try:
        placements = enumerate_placements(circuit, stored.device)
    except NoValidPlacementError as error:
        return _no_placement(case, config, str(error), workers, machine_label)
    n = len(placements)
    seconds["enumeration"] = time.perf_counter() - t

    t = time.perf_counter()
    chunks = [placements[i : i + SCORE_CHUNK] for i in range(0, n, SCORE_CHUNK)]
    dg_parts, v1_parts, se_parts = [], [], []
    for dg, v1, se in pool.map(_score_chunk, [(device, setting, key, c) for c in chunks]):
        dg_parts.append(dg)
        v1_parts.append(v1)
        se_parts.append(se)
    scores = {
        "decoder_graph": np.concatenate([np.asarray(p, dtype=np.float64) for p in dg_parts]),
        "v1": np.concatenate([np.asarray(p, dtype=np.float64) for p in v1_parts]),
        "summed_error": np.concatenate([np.asarray(p, dtype=np.float64) for p in se_parts]),
    }
    seconds["scoring"] = time.perf_counter() - t

    t = time.perf_counter()
    rng = random.Random(derive_seed(CHECK_TAG, int(config["sample_seed"]), case_id))
    checked = rng.sample(range(n), min(int(config["nove_checks_per_case"]), n))
    total_checked = sum(
        pool.map(_check_task, [(device, setting, key, placements[i]) for i in checked])
    )
    seconds["nove_checks"] = time.perf_counter() - t

    # Each score's ordering key: the scores' tie rule rounds to 12 significant digits;
    # summed error's (R-B4) compares raw values. Ties go to enumeration order.
    keys = {
        "decoder_graph": rounded(scores["decoder_graph"]),
        "v1": rounded(scores["v1"]),
        "summed_error": scores["summed_error"],
    }
    order = {name: np.argsort(k, kind="stable") for name, k in keys.items()}

    t = time.perf_counter()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        record = run_baselines(
            circuit,
            stored.snapshot,
            stored.device,
            placements,
            int(config["seed_transpiler"]),
            reset_error=SETTINGS[setting],
            map_chunks=_mapomatic_chunks(pool, device, setting),
            top=int(config["best_found"]["top"]),
        )
    costs = record["mapomatic"].pop("candidate_costs")
    mapomatic_cost = np.full(n, math.nan) if costs is None else np.asarray(costs, dtype=np.float64)
    mapomatic_top = record["mapomatic"].pop("top_layouts", [])
    seconds["qiskit_and_mapomatic"] = time.perf_counter() - t
    t = time.perf_counter()
    vf2 = qiskit_layout(circuit, stored.device, noise, placements)
    seconds["qiskit_vf2"] = time.perf_counter() - t

    t = time.perf_counter()
    search = FastSearch(circuit, stored.device, noise, placements)
    lowering = _lowering(device, setting, key)

    def score(m: Placement) -> float:
        dem = approximate_dem(lowering.stim_circuit(m), decompose=True)
        return decoder_graph_score_from_dem(dem)

    found = search.run(score, search_parameters)
    seconds["fast_search"] = time.perf_counter() - t
    found_index = index_of(placements, found.mapping)
    if found_index is None or found.score != scores["decoder_graph"][found_index]:
        raise RuntimeError("the fast search's score differs from the exhaustive one")

    choices: dict[str, int | None] = {
        "decoder-graph": int(order["decoder_graph"][0]),
        "v1": int(order["v1"][0]),
        "lowest-summed-error": int(order["summed_error"][0]),
        "naive": 0,
        **{
            name: index_of(placements, record[name]["mapping"])
            for name in ("qiskit-default-l1", "qiskit-default-l2", "qiskit-default-l3", "mapomatic")
        },
        "qiskit-vf2": index_of(placements, vf2.mapping),
        "fast-search": found_index,
    }
    details: dict[str, dict[str, Any]] = {
        "decoder-graph": {"method": DECODER_GRAPH_METHOD, "tie_break_rule": TIE_BREAK_RULE},
        "v1": {"method": SCORE_METHOD, "tie_break_rule": TIE_BREAK_RULE},
        "lowest-summed-error": {"method": SUMMED_ERROR_METHOD},
        "naive": {"method": "the first valid placement in lexicographic order"},
        "qiskit-vf2": {
            "mapping": None if vf2.mapping is None else list(vf2.mapping),
            "failure": vf2.failure,
            "stop_reason": vf2.stop_reason,
        },
        "fast-search": {
            "parameters": config["fast_search"],
            "evaluations": found.evaluations,
            "descents": list(found.descents),
        },
        **{
            name: record[name]
            for name in ("qiskit-default-l1", "qiskit-default-l2", "qiskit-default-l3", "mapomatic")
        },
    }

    def describe(i: int) -> dict[str, Any]:
        return {
            "index": i,
            "mapping": list(placements[i]),
            "scores": {
                "decoder_graph": float(scores["decoder_graph"][i]),
                "v1": float(scores["v1"][i]),
                "summed_error": float(scores["summed_error"][i]),
                "mapomatic_cost": None
                if math.isnan(mapomatic_cost[i])
                else float(mapomatic_cost[i]),
            },
        }

    methods = {}
    for name, i in choices.items():
        entry: dict[str, Any] = {"detail": details[name]}
        if i is not None:
            entry.update(describe(i))
            entry["ranks"] = {s: rank_of(k, i) for s, k in keys.items()}
        else:
            entry["index"] = None
        methods[name] = entry

    top = int(config["best_found"]["top"])
    full = case["lattice"] == "heavy-hex" or int(spec["distance"]) == 3
    sample_rng = random.Random(derive_seed(SAMPLE_TAG, int(config["sample_seed"]), case_id))
    random_sample = sorted(sample_rng.sample(range(n), min(int(config["best_found"]["random"]), n)))
    tops = {
        "decoder_graph": [int(i) for i in order["decoder_graph"][:top]],
        "summed_error": [int(i) for i in order["summed_error"][:top]],
        "mapomatic": [i for i in (index_of(placements, m) for m in mapomatic_top) if i is not None],
    }
    if full:
        simulated = list(range(n))
    else:
        chosen = {i for i in choices.values() if i is not None}
        simulated = sorted(chosen.union(*map(set, tops.values()), random_sample))

    items = [(key, placements[i], ",".join(map(str, placements[i]))) for i in simulated]
    rows, sim_seconds = simulate(pool, device, setting, case_id, items, config)
    seconds["pilot"], seconds["main"] = sim_seconds["pilot"], sim_seconds["main"]
    in_sample = set(random_sample)
    simulated_rows = [
        {**describe(i), "in_random_sample": i in in_sample, **row}
        for i, row in zip(simulated, rows, strict=True)
    ]

    score_path = out_dir / "scores" / f"{case_id}.npz"
    sha = _save_scores(
        score_path,
        {
            "placements": np.asarray(placements, dtype=np.int16),
            "mapomatic_cost": mapomatic_cost,
            **scores,
        },
    )

    def summary(values: np.ndarray) -> dict[str, float]:
        finite = values[np.isfinite(values)]
        if not len(finite):
            return {}
        return {
            "min": float(finite.min()),
            "median": float(np.median(finite)),
            "max": float(finite.max()),
        }

    return {
        **_header(case, config),
        "no_valid_placement": False,
        "placements": n,
        "naive_mapping": list(placements[0]),
        "interaction_edges": [list(e) for e in sorted(interaction_edges(circuit))],
        "methods": methods,
        "headline_method": "decoder-graph",
        "headline_references": list(config["headline_references"]),
        "simulated_set": {
            "rule": "every placement" if full else "R3-C5 best-found union",
            "size": len(simulated),
            "every_placement": full,
            "top": tops if not full else None,
            "random_sample": random_sample,
        },
        "simulated": simulated_rows,
        "nove_checks": {"placements_checked": total_checked, "all_equal": True},
        "score_file": {
            "path": score_path.relative_to(ROOT).as_posix()
            if score_path.is_relative_to(ROOT)
            else str(score_path),
            "sha256": sha,
            "arrays": ["placements", "decoder_graph", "v1", "summed_error", "mapomatic_cost"],
        },
        "score_summary": {
            **{name: summary(v) for name, v in scores.items()},
            "mapomatic_cost": summary(mapomatic_cost),
        },
        "provenance": _common_provenance(
            stored, noise, circuit, case, workers, machine_label, seconds
        ),
    }


# --- surface-code cases ---------------------------------------------------------------------


def run_surface(
    case: dict[str, Any],
    config: dict[str, Any],
    pool: ProcessPoolExecutor,
    *,
    workers: int,
    out_dir: Path,
    machine_label: str | None,
) -> dict[str, Any]:
    scaled_parameters, _search = _check_frozen(config)
    device, setting, case_id = case["device"], case["setting"], case["case_id"]
    spec = case["circuit"]
    distance, rounds = int(spec["distance"]), int(spec["rounds"])
    stored, noise = _models(device, setting)
    seconds: dict[str, float] = {}

    t = time.perf_counter()
    schedules = _schedules(distance, rounds)
    if DEFAULT_SCHEDULE not in schedules:
        raise RuntimeError("the default schedule is not hook-safe")
    candidates: list[tuple[Schedule, Placement]] = []
    for schedule in schedules:
        try:
            embeddings = enumerate_placements(
                _circuit(("surface", distance, rounds, schedule)), stored.device
            )
        except NoValidPlacementError:
            embeddings = []
        candidates.extend((schedule, m) for m in embeddings)
    if not candidates:
        return _no_placement(
            case, config, "no SWAP-free embedding of the surface-code patch", workers, machine_label
        )
    seconds["enumeration"] = time.perf_counter() - t

    t = time.perf_counter()
    k, step = scaled_parameters.k, scaled_parameters.step
    scored = list(
        pool.map(
            _surface_score,
            [
                (device, setting, ("surface", distance, rounds, s), m, k, step)
                for s, m in candidates
            ],
        )
    )
    seconds["scoring"] = time.perf_counter() - t
    t = time.perf_counter()
    rng = random.Random(derive_seed(CHECK_TAG, int(config["sample_seed"]), case_id))
    checked = rng.sample(
        range(len(candidates)), min(int(config["nove_checks_per_case"]), len(candidates))
    )
    total_checked = sum(
        pool.map(
            _check_task,
            [
                (device, setting, ("surface", distance, rounds, candidates[i][0]), candidates[i][1])
                for i in checked
            ],
        )
    )
    seconds["nove_checks"] = time.perf_counter() - t

    # R3-S3's tie rule: rounded score, then the default schedule first, then the mapping.
    tie = [(s != DEFAULT_SCHEDULE, m) for s, m in candidates]

    def pick(values: Sequence[float], among: Sequence[int], key: Callable[[float], float]) -> int:
        return min(among, key=lambda i: (key(values[i]), tie[i]))

    everything = range(len(candidates))
    default = [i for i, (s, _m) in enumerate(candidates) if s == DEFAULT_SCHEDULE]
    scaled = [s[0] for s in scored]
    exact = [s[1] for s in scored]
    summed = [s[2] for s in scored]
    default_circuit = _circuit(("surface", distance, rounds, DEFAULT_SCHEDULE))
    default_mappings = [candidates[i][1] for i in default]

    t = time.perf_counter()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        record = run_baselines(
            default_circuit,
            stored.snapshot,
            stored.device,
            default_mappings,
            int(config["seed_transpiler"]),
            reset_error=SETTINGS[setting],
        )
    record["mapomatic"].pop("candidate_costs")
    seconds["qiskit_and_mapomatic"] = time.perf_counter() - t

    def default_index(mapping: Sequence[int] | None) -> int | None:
        j = index_of(default_mappings, mapping)
        return None if j is None else default[j]

    choices: dict[str, int | None] = {
        "scaled": pick(scaled, everything, _rounded),
        "exact-decoder-graph": pick(exact, everything, _rounded),
        "lowest-summed-error": pick(summed, default, float),
        "naive": default[0],
        **{
            name: default_index(record[name]["mapping"])
            for name in ("qiskit-default-l1", "qiskit-default-l2", "qiskit-default-l3", "mapomatic")
        },
    }
    methods = {}
    for name, i in choices.items():
        entry: dict[str, Any] = {"index": i}
        if name in record:
            entry["detail"] = record[name]
        if i is not None:
            entry["schedule"] = list(candidates[i][0])
            entry["mapping"] = list(candidates[i][1])
        methods[name] = entry

    items = [
        (("surface", distance, rounds, s), m, f"{s[0]}{s[1]}|" + ",".join(map(str, m)))
        for s, m in candidates
    ]
    rows, sim_seconds = simulate(pool, device, setting, case_id, items, config)
    seconds["pilot"], seconds["main"] = sim_seconds["pilot"], sim_seconds["main"]
    simulated_rows = [
        {
            "index": i,
            "schedule": list(s),
            "mapping": list(m),
            "scores": {
                "scaled": scaled[i],
                "exact_decoder_graph": exact[i],
                "summed_error": summed[i],
            },
            **row,
        }
        for i, ((s, m), row) in enumerate(zip(candidates, rows, strict=True))
    ]
    return {
        **_header(case, config),
        "no_valid_placement": False,
        "placements": len(candidates),
        "schedules": [list(s) for s in schedules],
        "default_schedule": list(DEFAULT_SCHEDULE),
        "scaled_parameters": {"k": k, "step": step},
        "tie_break_rule": (
            "rounded score (12 significant digits), then the default schedule first, then the "
            "lexicographically smallest mapping; summed error compares raw values among "
            "default-schedule candidates"
        ),
        "methods": methods,
        "headline_method": "scaled",
        "headline_references": list(config["headline_references"]),
        "simulated_set": {
            "rule": "every candidate",
            "size": len(candidates),
            "every_placement": True,
        },
        "simulated": simulated_rows,
        "nove_checks": {"placements_checked": total_checked, "all_equal": True},
        "provenance": _common_provenance(
            stored, noise, default_circuit, case, workers, machine_label, seconds
        ),
    }


# --- driver ---------------------------------------------------------------------------------


def run_case(
    case: dict[str, Any],
    config: dict[str, Any],
    pool: ProcessPoolExecutor,
    *,
    workers: int,
    out_dir: Path,
    machine_label: str | None,
) -> Path:
    start = time.perf_counter()
    if qiskit_version() != config["qiskit_version"]:
        raise RuntimeError(f"qiskit {config['qiskit_version']} required, found {qiskit_version()}")
    if mapomatic_version() != config["mapomatic_version"]:
        raise RuntimeError(f"mapomatic {config['mapomatic_version']} required")
    run = run_repetition if case["kind"] == "repetition" else run_surface
    payload = run(case, config, pool, workers=workers, out_dir=out_dir, machine_label=machine_label)
    payload["wall_clock_seconds"] = time.perf_counter() - start
    payload["workers"] = workers
    path = out_dir / "runs" / f"{case['case_id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Compact: a heavy-hex d=7 case holds thousands of simulated placements.
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    path.write_text(text + "\n", encoding="utf-8")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("config", type=Path)
    parser.add_argument("--only", nargs="*", default=None, help="case ids to run")
    parser.add_argument("--out", type=Path, default=ROOT / "results_v3" / "held_out")
    parser.add_argument("--workers", type=int, default=min(48, os.cpu_count() or 1))
    parser.add_argument("--machine-label", default=None)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args(argv)
    _, dirty = git_state()
    if dirty and not args.allow_dirty:
        parser.error("uncommitted changes to code, data or config; commit them first")
    config = load_config(args.config)
    if importlib.metadata.version("qiskit") != config["qiskit_version"]:
        parser.error(f"qiskit=={config['qiskit_version']} is required (bench extra)")
    wanted = set(args.only) if args.only else None
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for case in cases(config):
            if wanted is not None and case["case_id"] not in wanted:
                continue
            target = args.out / "runs" / f"{case['case_id']}.json"
            if args.skip_existing and target.exists():
                continue
            t = time.perf_counter()
            path = run_case(
                case,
                config,
                pool,
                workers=args.workers,
                out_dir=args.out,
                machine_label=args.machine_label,
            )
            print(f"{case['case_id']}: {time.perf_counter() - t:.1f}s -> {path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
