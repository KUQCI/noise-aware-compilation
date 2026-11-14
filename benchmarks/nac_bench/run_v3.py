"""v3 held-out runner: SPEC_V3.md Part A and the surface code of Part C (revision 6).

Usage::

    nice -n 19 python -m nac_bench.run_v3 configs_v3/held_out.json --workers 48 \\
        --machine-label patty [--only CASE_ID ...] [--skip-existing]

For each case of the config (device, reset setting, circuit), with the score parameters and
the fast search frozen at F3:

1. **Selection (R3-C4).** Every valid placement is enumerated and lowered once by
   :class:`noise_aware_compilation.fast_lowering.Lowering`; the decoder-graph score, the v1
   score, summed error and the idle-aware summed error (revision 5) are computed from that
   lowering on the workers, and a seeded sample of placements is checked against Nove's own
   lowering. Each score's choice follows its own
   v1/v2 tie rule. Qiskit's default transpile at levels 1 to 3 and mapomatic run on the
   IBM-shaped CZ Target of the setting (R3-C9), mapomatic's layout costs on the workers;
   v2's VF2Layout baseline and the fast search (R3-C8) are recorded as exploratory. The
   naive placement is the first valid placement.
2. **Simulated set (R3-C5).** Every placement on a heavy-hex device and at d=3; on the square
   lattice at d=5 and d=7, the union of every method's choice, the top 30 by the
   decoder-graph score, by summed error and by mapomatic's cost, and 100 placements drawn
   uniformly (seeded).
3. **Shots (R3-C6).** A 100,000-shot pilot on the pilot seed, then each placement's
   main-stage shots per seed from its pilot count, on the three base seeds: about 4,000
   expected failures for a *family placement* (the headline method's and the headline
   references' choices on the family's devices and FakeNighthawk), about 400 otherwise.
   Pilot counts set the shots and may select (the exploratory *pilot-best* method of the
   analysis) but never enter a failure rate (T3-C5).
4. **Sensitivity (R3-C10, exploratory).** On the confirmatory devices at the headline
   setting, every family placement's main-stage samples are decoded again with correlated
   matching (:mod:`nac_bench.sensitivity_v3`), and the family placements of D5 and D7 are
   simulated again in a memory of *d* rounds, with their own pilot and the family's shots.
5. Writes ``<out>/runs/<case_id>.json`` and every placement's scores to
   ``<out>/scores/<case_id>.npz``, whose SHA-256 the run file records (the score files of the
   square-lattice cases run to hundreds of megabytes and stay out of git).

Surface-code cases (R3-S4, R3-S5) score every embedding under both hook-safe schedules with
the scaled score at the frozen surface-code *k* (headline), at the sensitivity *k* values of
R3-S2, with v2's exact decoder-graph score and with the idle-aware summed error, give summed
error, the naive
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
import traceback
import warnings
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import nove
import numpy as np

from nac_bench import detection_rate as detection
from nac_bench.baselines_v3 import mapomatic_cost_chunk, mapomatic_version, run_baselines
from nac_bench.directional import directional_noise, directional_snapshot
from nac_bench.grading_variants import variant_model, variant_names
from nac_bench.machine import machine
from nac_bench.qiskit_layout import qiskit_layout, qiskit_version
from nac_bench.run_reset import _bench_source_sha256, git_state, noise_for
from nac_bench.sensitivity_v3 import CORRELATED_DECODER_DESCRIPTION, CorrelatedRedecode
from noise_aware_compilation.baselines import (
    IDLE_AWARE_SUMMED_ERROR_METHOD,
    SUMMED_ERROR_METHOD,
    idle_aware_summed_error,
    summed_error,
)
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
RUN_SCHEMA = "noise-aware-compilation.held-out-run-v3/3"
SPEC_REVISION = 6
SEED_TAG = "eval-v3-held-out"
PILOT_TAG = "pilot-v3-held-out"
ROUNDS_SEED_TAG = "eval-v3-rounds"
ROUNDS_PILOT_TAG = "pilot-v3-rounds"
VARIANT_SEED_TAG = "eval-v3-variant"  # + ":" + the variant's name (R3-C15)
#: setting -> the reset error of the IBM-shaped Target Qiskit and mapomatic read (R3-C9);
#: "directional" also gives the Target each qubit's P(1|0) as its readout error (R3-C13)
SETTINGS = {"zero": "none", "readout": "readout", "directional": "directional"}
#: stages of a case, in order; a failure record names the one that raised
NOVE_CHECK_STAGE = "nove_checks"
#: device roles (config ``devices.<name>.role``)
CONFIRMATORY = "confirmatory"
ROLES = (CONFIRMATORY, "seen", "synthetic")
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
    if data.get("spec_revision") != SPEC_REVISION:
        raise ValueError(f"config is not for SPEC_V3.md revision {SPEC_REVISION}")
    data["_config_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return data


def cases(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Every case of the config, headline setting first."""

    out = []
    for setting in config["settings_order"]:
        if setting not in SETTINGS:
            raise ValueError(f"unknown reset setting {setting!r}")
        scope = (config.get("setting_scope") or {}).get(setting)
        for device, info in config["devices"].items():
            if info["role"] not in ROLES:
                raise ValueError(f"unknown device role {info['role']!r}")
            if scope is not None and device not in scope["devices"]:
                continue
            base = {
                "device": device,
                "lattice": info["lattice"],
                "role": info["role"],
                "synthetic_calibration": bool(info["synthetic_calibration"]),
                "setting": setting,
                "headline_setting": setting == config["headline_setting"],
            }
            for label in info["circuits"]:
                if scope is not None and label not in scope["circuits"]:
                    continue
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


def stage_rule(rule: dict[str, Any], family: bool) -> dict[str, int]:
    """R3-C6's flat rule for a family placement (``family``) or any other placement."""

    tier = rule["family" if family else "other"]
    return {
        "min_shots_per_seed": int(rule["min_shots_per_seed"]),
        "max_shots_per_seed": int(tier["max_shots_per_seed"]),
        "expected_failures": int(tier["expected_failures"]),
    }


def family_placements_apply(case: dict[str, Any], config: dict[str, Any]) -> bool:
    """R3-C6: whether a case's family placements get the family's shots (both settings)."""

    return bool(case["role"] in config["shots_rule"]["family_placement_roles"])


def sensitivity_applies(case: dict[str, Any], config: dict[str, Any]) -> bool:
    """R3-C10: the sensitivity runs cover the family's own cases."""

    return case["role"] == CONFIRMATORY and bool(case["headline_setting"])


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
    if setting == "directional":
        return stored, directional_noise(stored, device)
    return stored, noise_for(setting, stored)


def _target_inputs(device: str, setting: str) -> tuple[nove.CalibrationSnapshot, str]:
    """The snapshot and Target reset error Qiskit and mapomatic read at ``setting``."""

    stored, _noise = _models(device, setting)
    if setting == "directional":
        return directional_snapshot(stored, device), "readout"
    return stored.snapshot, SETTINGS[setting]


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
) -> tuple[list[float], list[float], list[float], list[float], list[float]]:
    """Decoder-graph score, v1 score, summed error, idle-aware summed error and detection
    rate of a chunk."""

    device, setting, key, chunk = args
    _stored, noise = _models(device, setting)
    circuit = _circuit(key)
    lowering = _lowering(device, setting, key)
    dg, v1, summed, idle, rate = [], [], [], [], []
    for m in chunk:
        noisy = lowering.stim_circuit(m)
        dg.append(decoder_graph_score_from_dem(approximate_dem(noisy, decompose=True)))
        flat = approximate_dem(noisy, decompose=False)
        v1.append(v1_score_from_dem(flat))
        summed.append(summed_error(circuit, noise, m))
        idle.append(idle_aware_summed_error(noisy))
        rate.append(detection.detection_rate(flat))
    return dg, v1, summed, idle, rate


def _surface_score(
    args: tuple[str, str, CircuitKey, Placement, int, float, tuple[int, ...]],
) -> tuple[float, float, float, float, tuple[float, ...], float]:
    """Scaled score, exact decoder-graph score, summed error, idle-aware summed error, the
    scaled score at each sensitivity *k* and the detection rate of one surface candidate."""

    device, setting, key, mapping, k, step, sensitivity = args
    _stored, noise = _models(device, setting)
    noisy = _lowering(device, setting, key).stim_circuit(mapping)
    dem = approximate_dem(noisy, decompose=True)
    graph = matching_graph(dem)
    scaled = scaled_score_from_graph(graph, ScaledParameters(k, step))
    others = tuple(scaled_score_from_graph(graph, ScaledParameters(j, step)) for j in sensitivity)
    return (
        scaled,
        decoder_graph_score_from_dem(dem),
        summed_error(_circuit(key), noise, mapping),
        idle_aware_summed_error(noisy),
        others,
        detection.detection_rate(approximate_dem(noisy, decompose=False)),
    )


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


def _main_task(
    args: tuple[str, str, CircuitKey, Placement, int, list[int], bool],
) -> tuple[dict[str, int], list[int] | None]:
    """A main-stage task; family placements also return each chunk's failures."""

    device, setting, key, mapping, shots, seeds, family = args
    task = (device, setting, key, mapping, shots, seeds)
    if family:
        return _sim_chunks_task(task)
    return _sim_task(task), None


def _sim_chunks_task(
    args: tuple[str, str, CircuitKey, Placement, int, list[int]],
) -> tuple[dict[str, int], list[int]]:
    """:func:`_sim_task` that also returns each chunk's failures (family placements, so the
    correlated re-decode can be checked chunk by chunk, T3-C8)."""

    device, setting, key, mapping, shots, seeds = args
    evaluator = _evaluator(device, setting, key, mapping)
    total: Counts | None = None
    failures = []
    remaining = shots
    for seed in seeds[: -(-shots // CHUNK_SHOTS)]:
        c = evaluator.run_chunk(min(CHUNK_SHOTS, remaining), seed)
        failures.append(c.logical_failures)
        total = c if total is None else total + c
        remaining -= c.shots
    assert total is not None
    return total.to_dict(), failures


@functools.cache
def _variant_models(
    device: str, setting: str, name: str, spec_json: str, sample_seed: int
) -> tuple[nove.DeviceModel, nove.HardwareNoiseModel]:
    stored, base = _models(device, setting)
    return variant_model(stored, device, base, name, json.loads(spec_json), sample_seed)


@functools.lru_cache(maxsize=4)
def _variant_evaluator(
    device: str,
    setting: str,
    key: CircuitKey,
    mapping: Placement,
    name: str,
    spec_json: str,
    sample_seed: int,
) -> Any:
    from nac_bench.run_drift import MultiDecoderEvaluator  # run_drift imports this module

    stored, base = _models(device, setting)
    return MultiDecoderEvaluator(
        _circuit(key),
        _variant_models(device, setting, name, spec_json, sample_seed),
        {"selecting": (stored.device, base)},
        mapping,
    )


def _variant_task(
    args: tuple[str, str, CircuitKey, Placement, str, str, int, int, list[int]],
) -> dict[str, int]:
    """One variant's samples of one placement, decoded with the selecting model's weights."""

    device, setting, key, mapping, name, spec_json, sample_seed, shots, seeds = args
    evaluator = _variant_evaluator(device, setting, key, mapping, name, spec_json, sample_seed)
    counts: Counts = evaluator.run(shots, seeds)["selecting"]
    return counts.to_dict()


def _redecode_task(
    args: tuple[str, str, CircuitKey, Placement, int, list[int]],
) -> dict[str, Any]:
    device, setting, key, mapping, shots, seeds = args
    redecoder = CorrelatedRedecode(_evaluator(device, setting, key, mapping))
    chunks = []
    remaining = shots
    for seed in seeds[: -(-shots // CHUNK_SHOTS)]:
        size = min(CHUNK_SHOTS, remaining)
        chunks.append(redecoder.run_chunk(size, seed))
        remaining -= size
    return {
        "shots": shots,
        "uncorrelated_failures": sum(a for a, _b in chunks),
        "correlated_failures": sum(b for _a, b in chunks),
        "chunks": chunks,
    }


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
    items: Sequence[tuple[CircuitKey, Placement, str, bool]],
    config: dict[str, Any],
    *,
    tags: tuple[str, str] = (PILOT_TAG, SEED_TAG),
    limit_seconds: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """R3-C6 for each (circuit, placement, seed key, family placement): pilot, then the main
    stage on every base seed, with the family's or the other placements' shots rule.

    With ``limit_seconds`` (R3-C5's every-placement guard), the main stage's wall time is
    projected from the pilot stage's, in proportion to the shots; the projection is an upper
    bound, since every placement's main stage has at least as many shots per evaluator built
    as its pilot. Over the limit, :class:`OverLimit` is raised before the main stage starts.
    """

    rule = config["shots_rule"]
    pilot_tag, seed_tag = tags
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
                    _chunk_seeds(pilot_tag, pilot_seed, case_id, seed_key, pilot_shots),
                )
                for key, m, seed_key, _family in items
            ],
        )
    )
    pilot_seconds = time.perf_counter() - t
    shots = [
        main_shots(p["logical_failures"], p["shots"], stage_rule(rule, item[3]), len(base_seeds))
        for p, item in zip(pilot, items, strict=True)
    ]
    if limit_seconds is not None and items:
        projected = pilot_seconds * len(base_seeds) * sum(shots) / (pilot_shots * len(items))
        if projected > limit_seconds:
            raise OverLimit(projected, limit_seconds)
    tasks, owners = [], []
    for i, ((key, m, seed_key, family), n) in enumerate(zip(items, shots, strict=True)):
        for si, base in enumerate(base_seeds):
            seeds = _chunk_seeds(seed_tag, base, case_id, seed_key, n)
            for part in range(0, len(seeds), TASK_CHUNKS):
                part_shots = min(TASK_CHUNKS * CHUNK_SHOTS, n - part * CHUNK_SHOTS)
                tasks.append(
                    (device, setting, key, m, part_shots, seeds[part : part + TASK_CHUNKS], family)
                )
                owners.append((i, si))
    t = time.perf_counter()
    totals: dict[tuple[int, int], Counts] = {}
    chunk_failures: dict[tuple[int, int], list[int]] = {}
    for owner, (counts, chunks) in zip(
        owners, pool.map(_main_task, tasks, chunksize=1), strict=True
    ):
        c = Counts(**counts)
        totals[owner] = c if owner not in totals else totals[owner] + c
        if chunks is not None:
            chunk_failures.setdefault(owner, []).extend(chunks)
    main_seconds = time.perf_counter() - t
    rows = []
    for i, item in enumerate(items):
        per_seed = []
        for si, base in enumerate(base_seeds):
            c = totals[(i, si)]
            if c.shots != shots[i]:
                raise RuntimeError("a placement's main-stage shots differ from the rule's")
            seed_row: dict[str, Any] = {"base_seed": base, **c.to_dict()}
            if (i, si) in chunk_failures:
                seed_row["chunk_failures"] = chunk_failures[(i, si)]
            per_seed.append(seed_row)
        rows.append(
            {
                "family_placement": bool(item[3]),
                "pilot": {
                    "shots": pilot[i]["shots"],
                    "logical_failures": pilot[i]["logical_failures"],
                },
                "shots_per_seed": shots[i],
                "per_seed": per_seed,
            }
        )
    return rows, {"pilot": pilot_seconds, "main": main_seconds}


def redecode(
    pool: ProcessPoolExecutor,
    device: str,
    setting: str,
    case_id: str,
    items: Sequence[tuple[CircuitKey, Placement, str]],
    rows: Sequence[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """R3-C10: each item's main-stage samples decoded again with correlated matching.

    ``rows`` are the items' main-stage rows. T3-C8: the uncorrelated count of every chunk
    must equal the main stage's count of that chunk, which proves both decoders saw the same
    syndromes; a row that does not reproduce raises, and the caller records the failure.
    """

    tasks, owners = [], []
    for i, ((key, m, seed_key), row) in enumerate(zip(items, rows, strict=True)):
        n = int(row["shots_per_seed"])
        for si, base in enumerate(int(s) for s in config["base_seeds"]):
            seeds = _chunk_seeds(SEED_TAG, base, case_id, seed_key, n)
            for part in range(0, len(seeds), TASK_CHUNKS):
                part_shots = min(TASK_CHUNKS * CHUNK_SHOTS, n - part * CHUNK_SHOTS)
                tasks.append(
                    (device, setting, key, m, part_shots, seeds[part : part + TASK_CHUNKS])
                )
                owners.append((i, si))
    totals: dict[tuple[int, int], dict[str, Any]] = {}
    for owner, c in zip(owners, pool.map(_redecode_task, tasks, chunksize=1), strict=True):
        if owner in totals:
            before = totals[owner]
            c = {
                "shots": before["shots"] + c["shots"],
                "uncorrelated_failures": before["uncorrelated_failures"]
                + c["uncorrelated_failures"],
                "correlated_failures": before["correlated_failures"] + c["correlated_failures"],
                "chunks": before["chunks"] + c["chunks"],
            }
        totals[owner] = c
    out = []
    for i, row in enumerate(rows):
        per_seed = []
        for si, main in enumerate(row["per_seed"]):
            c = totals[(i, si)]
            main_chunks = main.get("chunk_failures")
            if main_chunks is None:
                raise RuntimeError("the main stage kept no per-chunk counts for this placement")
            plain = [a for a, _b in c["chunks"]]
            if plain != list(main_chunks) or c["shots"] != main["shots"]:
                raise RuntimeError("the re-decode did not reproduce the main stage's chunks")
            per_seed.append(
                {
                    "base_seed": main["base_seed"],
                    "shots": c["shots"],
                    "logical_failures": c["correlated_failures"],
                    "chunks_reproduced": len(plain),
                }
            )
        out.append({"per_seed": per_seed, "reproduced": True})
    return out


def family_indices(
    choices: dict[str, int | None], config: dict[str, Any], headline: str, kind: str
) -> set[int]:
    """R3-C6's family placements of a case: the headline method's, the headline references'
    and (revision 6) the isolation references' choices; a placement chosen by several is
    one placement."""

    names = [headline, *config["headline_references"], *config["isolation_references"][kind]]
    return {i for name in names if (i := choices.get(name)) is not None}


def every_placement(case: dict[str, Any], config: dict[str, Any]) -> bool:
    """R3-C5: every placement is simulated on the heavy-hex devices, at d=3, and in the
    square-lattice cases the config names (revision 6: FakeBerlin D5)."""

    if case["lattice"] == "heavy-hex" or int(case["circuit"]["distance"]) == 3:
        return True
    return guarded(case, config)


def guarded(case: dict[str, Any], config: dict[str, Any]) -> bool:
    """R3-C5 (revision 6): a case simulated in full only because the config names it; its
    main stage must fit in ``best_found.every_placement_limit_hours``, projected from its own
    pilot stage, or it falls back to the best-found union."""

    named = config["best_found"].get("every_placement_cases", [])
    return f"{case['device']}-{case['circuit']['label']}" in named


class OverLimit(RuntimeError):
    """The main stage projects over its limit (R3-C5's every-placement guard)."""

    def __init__(self, projected_seconds: float, limit_seconds: float) -> None:
        super().__init__(
            f"main stage projected at {projected_seconds / 3600:.1f} wall hours, "
            f"over the {limit_seconds / 3600:g}-hour limit"
        )
        self.projected_seconds = projected_seconds
        self.limit_seconds = limit_seconds


def choices_only(case: dict[str, Any], config: dict[str, Any]) -> bool:
    """R3-C13: a setting whose simulated set is only the choices (the directional one)."""

    scope = (config.get("setting_scope") or {}).get(case["setting"]) or {}
    return scope.get("simulated_set") == "choices"


def qiskit_choice(record: dict[str, Any], placements: Sequence[Placement]) -> int | None:
    """A Qiskit or mapomatic choice's index; None when the run records a failure (R3-C9)."""

    if record.get("failure"):
        return None
    return index_of(placements, record["mapping"])


def _failure(error: BaseException) -> dict[str, Any]:
    return {
        "failed": True,
        "error": f"{type(error).__name__}: {error}",
        "traceback": traceback.format_exc(limit=8),
    }


def grade_variants(
    pool: ProcessPoolExecutor,
    case: dict[str, Any],
    config: dict[str, Any],
    items: Sequence[tuple[CircuitKey, Placement, str]],
    rows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """R3-C15: each family placement simulated under each grading variant and decoded with
    the selecting model's weights, with the other placements' shots rule from its own pilot
    count (the variant run has no pilot of its own)."""

    device, setting, case_id = case["device"], case["setting"], case["case_id"]
    spec = config["grading_variants"]
    spec_json = json.dumps(spec, sort_keys=True)
    sample_seed = int(config["sample_seed"])
    basis = case["circuit"].get("basis")
    rule = stage_rule(config["shots_rule"], False)
    base_seeds = [int(b) for b in config["base_seeds"]]
    out: dict[str, Any] = {"decoder_weights": "the selecting model's", "variants": {}}
    for name in variant_names(spec, case["kind"], basis):
        tag = f"{VARIANT_SEED_TAG}:{name}"
        tasks, owners, shots = [], [], []
        for i, ((key, m, seed_key), row) in enumerate(zip(items, rows, strict=True)):
            pilot = row["pilot"]
            n = main_shots(
                int(pilot["logical_failures"]), int(pilot["shots"]), rule, len(base_seeds)
            )
            shots.append(n)
            for si, base in enumerate(base_seeds):
                seeds = _chunk_seeds(tag, base, case_id, seed_key, n)
                for part in range(0, len(seeds), TASK_CHUNKS):
                    part_shots = min(TASK_CHUNKS * CHUNK_SHOTS, n - part * CHUNK_SHOTS)
                    tasks.append(
                        (
                            device,
                            setting,
                            key,
                            m,
                            name,
                            spec_json,
                            sample_seed,
                            part_shots,
                            seeds[part : part + TASK_CHUNKS],
                        )
                    )
                    owners.append((i, si))
        totals: dict[tuple[int, int], Counts] = {}
        for owner, counts in zip(owners, pool.map(_variant_task, tasks, chunksize=1), strict=True):
            c = Counts(**counts)
            totals[owner] = c if owner not in totals else totals[owner] + c
        out["variants"][name] = {
            "rows": [
                {
                    "index": row["index"],
                    "shots_per_seed": shots[i],
                    "per_seed": [
                        {"base_seed": base, **totals[(i, si)].to_dict()}
                        for si, base in enumerate(base_seeds)
                    ],
                }
                for i, row in enumerate(rows)
            ]
        }
    return out


def run_sensitivity(
    pool: ProcessPoolExecutor,
    case: dict[str, Any],
    config: dict[str, Any],
    items: Sequence[tuple[CircuitKey, Placement, str]],
    rows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """R3-C10 and R3-C15 on one case's family placements: the correlated re-decode, for the
    repetition circuits the config names a memory of *d* rounds, and the grading variants.
    Each part that raises is recorded as failed and the others still run (exploratory)."""

    device, setting, case_id = case["device"], case["setting"], case["case_id"]
    spec = config["sensitivity"]
    out: dict[str, Any] = {"rounds_equal_distance": None}
    try:
        redecoded = redecode(pool, device, setting, case_id, items, rows, config)
        out["correlated_matching"] = {
            "decoder": CORRELATED_DECODER_DESCRIPTION,
            "rows": [{"index": row["index"], **r} for row, r in zip(rows, redecoded, strict=True)],
        }
    except Exception as error:
        out["correlated_matching"] = _failure(error)
    label = case["circuit"]["label"]
    if case["kind"] == "repetition" and label in spec["rounds_equal_distance"]["circuits"]:
        distance = int(case["circuit"]["distance"])
        longer = [
            (("repetition", distance, distance, key[3]), m, seed_key, True)
            for key, m, seed_key in items
        ]
        try:
            long_rows, _seconds = simulate(
                pool,
                device,
                setting,
                case_id,
                longer,
                config,
                tags=(ROUNDS_PILOT_TAG, ROUNDS_SEED_TAG),
            )
            out["rounds_equal_distance"] = {
                "rounds": distance,
                "rows": [
                    {"index": row["index"], **r} for row, r in zip(rows, long_rows, strict=True)
                ],
            }
        except Exception as error:
            out["rounds_equal_distance"] = _failure(error)
    try:
        out["grading_variants"] = grade_variants(pool, case, config, items, rows)
    except Exception as error:
        out["grading_variants"] = _failure(error)
    return out


def readout_choices(case: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    """R3-C13: the headline-setting run of the same case, whose choices the directional run
    also grades (it runs after the headline setting; the file must exist)."""

    path = out_dir / "runs" / case["case_id"].replace(f"v3-{case['setting']}-", "v3-readout-", 1)
    path = path.with_suffix(".json")
    data = path.read_bytes()
    raw = json.loads(data)
    if raw.get("failed") or raw.get("no_valid_placement"):
        raise RuntimeError(f"{path.name} has no choices to grade")
    return {
        "file": path.name,
        "sha256": hashlib.sha256(data).hexdigest(),
        "choices": {
            name: entry["index"]
            for name, entry in raw["methods"].items()
            if entry.get("index") is not None and not (entry.get("detail") or {}).get("failure")
        },
    }


def _save_scores(path: Path, arrays: dict[str, np.ndarray]) -> str:
    buffer = io.BytesIO()
    np.savez_compressed(buffer, allow_pickle=False, **arrays)
    data = buffer.getvalue()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _check_frozen(config: dict[str, Any]) -> tuple[ScaledParameters, SearchParameters]:
    """The F3 parameters (the surface-code scaled score of the second F3 commit and the fast
    search of the first), which the config must repeat exactly."""

    freeze_path = ROOT / config["score_freeze_file"]
    frozen = json.loads(freeze_path.read_text(encoding="utf-8"))
    if frozen["surface_scaled_score"] != config["surface_scaled_score"]:
        raise RuntimeError("the config's surface-code scaled score differs from the F3 freeze")
    if frozen["fast_search"] != config["fast_search"]:
        raise RuntimeError("the config's fast-search parameters differ from the F3 freeze")
    scaled = config["surface_scaled_score"]
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
        "spec_revision": SPEC_REVISION,
        "case": case,
        "setting": case["setting"],
        "setting_description": config["settings"][case["setting"]],
        "config_file_sha256": config["_config_sha256"],
        "family": config["family"]
        if case["headline_setting"] and case["role"] == CONFIRMATORY
        else None,
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
    circuit: nove.Circuit,
    seconds: dict[str, float],
) -> dict[str, Any]:
    """A run file for a case with no valid placement, with the same provenance as any run
    (T3-R3, revision 6)."""

    stored, noise = _models(case["device"], case["setting"])
    return {
        **_header(case, config),
        "no_valid_placement": True,
        "reason": reason,
        "provenance": _common_provenance(
            stored, noise, circuit, case, workers, machine_label, seconds
        ),
    }


def failed_record(
    case: dict[str, Any],
    config: dict[str, Any],
    stage: str,
    error: BaseException,
    workers: int,
    machine_label: str | None,
) -> dict[str, Any]:
    """The run file of a case that raised (revision 6): which stage, and why. A failure in
    the Nove check is R3-C4's pre-registered outcome (the case leaves the family); any other
    failure stops the analysis until it is explained (``analysis_v3``)."""

    commit, dirty = git_state()
    return {
        **_header(case, config),
        "failed": True,
        "no_valid_placement": False,
        "stage": stage,
        **_failure(error),
        "provenance": {
            "package_git_commit": commit,
            "package_git_dirty": dirty,
            "nac_bench_source_sha256": _bench_source_sha256(),
            "lock_file_sha256": hashlib.sha256(LOCK_FILE.read_bytes()).hexdigest(),
            "reset_setting": case["setting"],
            "machine": machine(machine_label, workers=workers),
        },
    }


# --- repetition-code cases ------------------------------------------------------------------

Save = Callable[[dict[str, Any]], None]


def _sweep_seeds(config: dict[str, Any]) -> list[int]:
    return [int(x) for x in config["qiskit_seed_sweep"]["seeds"]]


def run_repetition(
    case: dict[str, Any],
    config: dict[str, Any],
    pool: ProcessPoolExecutor,
    *,
    workers: int,
    out_dir: Path,
    machine_label: str | None,
    progress: dict[str, str],
    save: Save,
) -> dict[str, Any]:
    _scaled, search_parameters = _check_frozen(config)
    device, setting, case_id = case["device"], case["setting"], case["case_id"]
    spec = case["circuit"]
    key: CircuitKey = ("repetition", int(spec["distance"]), int(spec["rounds"]), spec["basis"])
    stored, noise = _models(device, setting)
    circuit = _circuit(key)
    seconds: dict[str, float] = {}

    progress["stage"] = "enumeration"
    t = time.perf_counter()
    try:
        placements = enumerate_placements(circuit, stored.device)
    except NoValidPlacementError as error:
        seconds["enumeration"] = time.perf_counter() - t
        return _no_placement(case, config, str(error), workers, machine_label, circuit, seconds)
    n = len(placements)
    seconds["enumeration"] = time.perf_counter() - t

    progress["stage"] = "scoring"
    t = time.perf_counter()
    chunks = [placements[i : i + SCORE_CHUNK] for i in range(0, n, SCORE_CHUNK)]
    parts: dict[str, list[list[float]]] = {
        "decoder_graph": [],
        "v1": [],
        "summed_error": [],
        "idle_aware_summed_error": [],
        "detection_rate": [],
    }
    for chunk_scores in pool.map(_score_chunk, [(device, setting, key, c) for c in chunks]):
        for name, values in zip(parts, chunk_scores, strict=True):
            parts[name].append(values)
    scores = {
        name: np.concatenate([np.asarray(p, dtype=np.float64) for p in chunk_parts])
        for name, chunk_parts in parts.items()
    }
    seconds["scoring"] = time.perf_counter() - t

    progress["stage"] = NOVE_CHECK_STAGE
    t = time.perf_counter()
    rng = random.Random(derive_seed(CHECK_TAG, int(config["sample_seed"]), case_id))
    checked = rng.sample(range(n), min(int(config["nove_checks_per_case"]), n))
    total_checked = sum(
        pool.map(_check_task, [(device, setting, key, placements[i]) for i in checked])
    )
    seconds["nove_checks"] = time.perf_counter() - t

    # Each score's ordering key: the scores' tie rule rounds to 12 significant digits;
    # summed error's (R-B4), the idle-aware summed error and the detection rate compare raw
    # values. Ties go to enumeration order.
    keys = {
        "decoder_graph": rounded(scores["decoder_graph"]),
        "v1": rounded(scores["v1"]),
        "summed_error": scores["summed_error"],
        "idle_aware_summed_error": scores["idle_aware_summed_error"],
        "detection_rate": scores["detection_rate"],
    }
    order = {name: np.argsort(k, kind="stable") for name, k in keys.items()}

    progress["stage"] = "qiskit_and_mapomatic"
    t = time.perf_counter()
    target_snapshot, target_reset = _target_inputs(device, setting)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        record = run_baselines(
            circuit,
            target_snapshot,
            stored.device,
            placements,
            int(config["seed_transpiler"]),
            seed_sweep=_sweep_seeds(config),
            reset_error=target_reset,
            map_chunks=_mapomatic_chunks(pool, device, setting),
            top=int(config["best_found"]["top"]),
            sweep_levels=(int(config["qiskit_seed_sweep"]["level"]),),
        )
    costs = record["mapomatic"].pop("candidate_costs")
    mapomatic_cost = np.full(n, math.nan) if costs is None else np.asarray(costs, dtype=np.float64)
    mapomatic_top = record["mapomatic"].pop("top_layouts", [])
    seconds["qiskit_and_mapomatic"] = time.perf_counter() - t
    progress["stage"] = "qiskit_vf2"
    t = time.perf_counter()
    vf2 = qiskit_layout(circuit, stored.device, noise, placements)
    seconds["qiskit_vf2"] = time.perf_counter() - t

    progress["stage"] = "fast_search"
    t = time.perf_counter()
    search = FastSearch(circuit, stored.device, noise, placements)
    lowering = _lowering(device, setting, key)

    def score(m: Placement) -> float:
        dem = approximate_dem(lowering.stim_circuit(m), decompose=True)
        return decoder_graph_score_from_dem(dem)

    fast_detail: dict[str, Any] = {"parameters": config["fast_search"]}
    found_index: int | None
    try:
        found = search.run(score, search_parameters)
        found_index = index_of(placements, found.mapping)
        fast_detail.update({"evaluations": found.evaluations, "descents": list(found.descents)})
        if found_index is None or found.score != scores["decoder_graph"][found_index]:
            # exploratory (R3-C8): recorded, and the case goes on
            fast_detail["failure"] = "the fast search's score differs from the exhaustive one"
            found_index = None
    except Exception as error:
        fast_detail.update(_failure(error))
        fast_detail["failure"] = fast_detail.pop("error")
        found_index = None
    seconds["fast_search"] = time.perf_counter() - t

    level_names = ("qiskit-default-l1", "qiskit-default-l2", "qiskit-default-l3", "mapomatic")
    choices: dict[str, int | None] = {
        "decoder-graph": int(order["decoder_graph"][0]),
        "v1": int(order["v1"][0]),
        "lowest-summed-error": int(order["summed_error"][0]),
        "idle-aware-summed-error": int(order["idle_aware_summed_error"][0]),
        "detection-rate": int(order["detection_rate"][0]),
        "naive": 0,
        **{name: qiskit_choice(record[name], placements) for name in level_names},
        "qiskit-vf2": index_of(placements, vf2.mapping),
        "fast-search": found_index,
    }
    details: dict[str, dict[str, Any]] = {
        "decoder-graph": {"method": DECODER_GRAPH_METHOD, "tie_break_rule": TIE_BREAK_RULE},
        "v1": {"method": SCORE_METHOD, "tie_break_rule": TIE_BREAK_RULE},
        "lowest-summed-error": {"method": SUMMED_ERROR_METHOD},
        "idle-aware-summed-error": {"method": IDLE_AWARE_SUMMED_ERROR_METHOD},
        "detection-rate": {"method": detection.METHOD},
        "naive": {"method": "the first valid placement in lexicographic order"},
        "qiskit-vf2": {
            "mapping": None if vf2.mapping is None else list(vf2.mapping),
            "failure": vf2.failure,
            "stop_reason": vf2.stop_reason,
        },
        "fast-search": fast_detail,
        **{name: record[name] for name in level_names},
    }

    def describe(i: int) -> dict[str, Any]:
        return {
            "index": i,
            "mapping": list(placements[i]),
            "scores": {
                **{name: float(scores[name][i]) for name in parts},
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
    sweep = record["qiskit-default-l2"].get("seed_sweep_runs") or []
    methods["qiskit-default-l2"]["seed_sweep_indices"] = [
        None if r["failure"] else index_of(placements, r["mapping"]) for r in sweep
    ]

    top = int(config["best_found"]["top"])
    full = every_placement(case, config)
    only_choices = choices_only(case, config)
    sample_rng = random.Random(derive_seed(SAMPLE_TAG, int(config["sample_seed"]), case_id))
    uniform = sorted(sample_rng.sample(range(n), min(int(config["best_found"]["random"]), n)))
    random_sample = [] if full or only_choices else uniform
    tops = {
        name: [int(i) for i in order[name][:top]]
        for name in config["best_found"]["top_by"]
        if name != "mapomatic"
    }
    tops["mapomatic"] = [
        i for i in (index_of(placements, m) for m in mapomatic_top) if i is not None
    ]
    chosen = {i for i in choices.values() if i is not None}
    sweep_chosen = {i for i in methods["qiskit-default-l2"]["seed_sweep_indices"] if i is not None}
    best_found_union = sorted(chosen.union(sweep_chosen, *map(set, tops.values()), uniform))
    graded_from_headline: dict[str, Any] | None = None
    if only_choices:
        graded_from_headline = readout_choices(case, out_dir)
        headline_chosen = set(graded_from_headline["choices"].values())
        simulated = sorted(chosen | sweep_chosen | headline_chosen)
        rule = "R3-C13: every choice, at this setting and at the headline setting"
    elif full:
        simulated = list(range(n))
        rule = "every placement"
    else:
        simulated = best_found_union
        rule = "R3-C5 best-found union"

    family = (
        family_indices(choices, config, "decoder-graph", "repetition")
        if family_placements_apply(case, config)
        else set()
    )
    if graded_from_headline is not None and family:
        family |= family_indices(
            graded_from_headline["choices"], config, "decoder-graph", "repetition"
        )

    def items_of(indices: Sequence[int]) -> list[tuple[CircuitKey, Placement, str, bool]]:
        return [
            (key, placements[i], ",".join(map(str, placements[i])), i in family) for i in indices
        ]

    progress["stage"] = "simulation"
    fallback: dict[str, Any] | None = None
    limit = (
        3600 * float(config["best_found"]["every_placement_limit_hours"])
        if full and not only_choices and guarded(case, config)
        else None
    )
    try:
        rows, sim_seconds = simulate(
            pool, device, setting, case_id, items_of(simulated), config, limit_seconds=limit
        )
    except OverLimit as over:
        fallback = {
            "projected_main_wall_hours": over.projected_seconds / 3600,
            "limit_hours": over.limit_seconds / 3600,
            "workers": workers,
        }
        full, simulated, random_sample = False, best_found_union, uniform
        rule = "R3-C5 best-found union (every placement's main stage projected over its limit)"
        rows, sim_seconds = simulate(pool, device, setting, case_id, items_of(simulated), config)
    seconds["pilot"], seconds["main"] = sim_seconds["pilot"], sim_seconds["main"]
    in_sample = set(random_sample)
    simulated_rows = [
        {**describe(i), "in_random_sample": i in in_sample, **row}
        for i, row in zip(simulated, rows, strict=True)
    ]

    progress["stage"] = "score_file"
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

    payload: dict[str, Any] = {
        **_header(case, config),
        "no_valid_placement": False,
        "placements": n,
        "naive_mapping": list(placements[0]),
        "interaction_edges": [list(e) for e in sorted(interaction_edges(circuit))],
        "methods": methods,
        "headline_method": "decoder-graph",
        "headline_references": list(config["headline_references"]),
        "simulated_set": {
            "rule": rule,
            "size": len(simulated),
            "every_placement": full and not only_choices,
            "top": tops if not (full or only_choices) else None,
            "random_sample": random_sample,
            "headline_setting_choices": graded_from_headline,
            "every_placement_fallback": fallback,
        },
        "simulated": simulated_rows,
        "family_placements": sorted(family),
        "sensitivity": None,
        "nove_checks": {"placements_checked": total_checked, "all_equal": True},
        "score_file": {
            "path": score_path.relative_to(ROOT).as_posix()
            if score_path.is_relative_to(ROOT)
            else str(score_path),
            "sha256": sha,
            "arrays": ["placements", *parts, "mapomatic_cost"],
        },
        "score_summary": {
            **{name: summary(v) for name, v in scores.items()},
            "mapomatic_cost": summary(mapomatic_cost),
        },
        "provenance": _common_provenance(
            stored, noise, circuit, case, workers, machine_label, seconds
        ),
    }
    if sensitivity_applies(case, config):
        # The main result is on disk before the exploratory runs start (revision 6).
        payload["sensitivity"] = {"pending": True}
        save(payload)
        progress["stage"] = "sensitivity"
        t = time.perf_counter()
        payload["sensitivity"] = run_sensitivity(
            pool,
            case,
            config,
            [(key, m, seed_key) for key, m, seed_key, fam in items_of(simulated) if fam],
            [r for i, r in zip(simulated, simulated_rows, strict=True) if i in family],
        )
        seconds["sensitivity"] = time.perf_counter() - t
    return payload


# --- surface-code cases ---------------------------------------------------------------------


def run_surface(
    case: dict[str, Any],
    config: dict[str, Any],
    pool: ProcessPoolExecutor,
    *,
    workers: int,
    out_dir: Path,
    machine_label: str | None,
    progress: dict[str, str],
    save: Save,
) -> dict[str, Any]:
    scaled_parameters, _search = _check_frozen(config)
    device, setting, case_id = case["device"], case["setting"], case["case_id"]
    spec = case["circuit"]
    distance, rounds = int(spec["distance"]), int(spec["rounds"])
    stored, noise = _models(device, setting)
    seconds: dict[str, float] = {}

    progress["stage"] = "enumeration"
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
    default_circuit = _circuit(("surface", distance, rounds, DEFAULT_SCHEDULE))
    seconds["enumeration"] = time.perf_counter() - t
    if not candidates:
        return _no_placement(
            case,
            config,
            "no SWAP-free embedding of the surface-code patch",
            workers,
            machine_label,
            default_circuit,
            seconds,
        )

    progress["stage"] = "scoring"
    t = time.perf_counter()
    k, step = scaled_parameters.k, scaled_parameters.step
    sensitivity_k = tuple(int(j) for j in config["surface_k_sensitivity"])
    scored = list(
        pool.map(
            _surface_score,
            [
                (device, setting, ("surface", distance, rounds, s), m, k, step, sensitivity_k)
                for s, m in candidates
            ],
        )
    )
    seconds["scoring"] = time.perf_counter() - t
    progress["stage"] = NOVE_CHECK_STAGE
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
    idle = [s[3] for s in scored]
    at_k = {j: [s[4][n] for s in scored] for n, j in enumerate(sensitivity_k)}
    rate = [s[5] for s in scored]
    default_mappings = [candidates[i][1] for i in default]

    progress["stage"] = "qiskit_and_mapomatic"
    t = time.perf_counter()
    target_snapshot, target_reset = _target_inputs(device, setting)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        record = run_baselines(
            default_circuit,
            target_snapshot,
            stored.device,
            default_mappings,
            int(config["seed_transpiler"]),
            seed_sweep=_sweep_seeds(config),
            reset_error=target_reset,
            sweep_levels=(int(config["qiskit_seed_sweep"]["level"]),),
        )
    record["mapomatic"].pop("candidate_costs")
    seconds["qiskit_and_mapomatic"] = time.perf_counter() - t

    def default_index(mapping: Sequence[int] | None) -> int | None:
        j = index_of(default_mappings, mapping)
        return None if j is None else default[j]

    level_names = ("qiskit-default-l1", "qiskit-default-l2", "qiskit-default-l3", "mapomatic")
    choices: dict[str, int | None] = {
        "scaled": pick(scaled, everything, _rounded),
        "exact-decoder-graph": pick(exact, everything, _rounded),
        "lowest-summed-error": pick(summed, default, float),
        "idle-aware-summed-error": pick(idle, everything, float),
        "detection-rate": pick(rate, everything, float),
        **{f"scaled-k{j}": pick(at_k[j], everything, _rounded) for j in sensitivity_k},
        # R3-C17 (revision 6, exploratory): the scaled score held to the default schedule
        "scaled-default-schedule": pick(scaled, default, _rounded),
        "naive": default[0],
        **{
            name: None if record[name].get("failure") else default_index(record[name]["mapping"])
            for name in level_names
        },
    }
    # R3-C17: each default-schedule reference's embedding under the scaled choice's schedule,
    # so the comparison with the scaled score can be split into placement and schedule.
    scaled_schedule = candidates[choices["scaled"]][0] if choices["scaled"] is not None else None
    position = {c: i for i, c in enumerate(candidates)}
    derived: dict[str, str] = {}
    for name in ["naive", "lowest-summed-error", *level_names[1:]]:
        i = choices.get(name)
        if i is None or scaled_schedule is None:
            continue
        twin = position.get((scaled_schedule, candidates[i][1]))
        choices[f"{name}@scaled-schedule"] = twin
        derived[f"{name}@scaled-schedule"] = name
    methods = {}
    for name, i in choices.items():
        entry: dict[str, Any] = {"index": i}
        if name in record:
            entry["detail"] = record[name]
        elif name in derived:
            entry["detail"] = {
                "derived_from": derived[name],
                "rule": "the same embedding under the scaled choice's schedule (R3-C17)",
            }
        elif name == "detection-rate":
            entry["detail"] = {"method": detection.METHOD}
        if i is not None:
            entry["schedule"] = list(candidates[i][0])
            entry["mapping"] = list(candidates[i][1])
        methods[name] = entry
    sweep = record["qiskit-default-l2"].get("seed_sweep_runs") or []
    methods["qiskit-default-l2"]["seed_sweep_indices"] = [
        None if r["failure"] else default_index(r["mapping"]) for r in sweep
    ]

    family = (
        family_indices(choices, config, "scaled", "surface")
        if family_placements_apply(case, config)
        else set()
    )
    items = [
        (("surface", distance, rounds, s), m, f"{s[0]}{s[1]}|" + ",".join(map(str, m)), i in family)
        for i, (s, m) in enumerate(candidates)
    ]
    progress["stage"] = "simulation"
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
                "idle_aware_summed_error": idle[i],
                "detection_rate": rate[i],
                **{f"scaled_k{j}": at_k[j][i] for j in sensitivity_k},
            },
            **row,
        }
        for i, ((s, m), row) in enumerate(zip(candidates, rows, strict=True))
    ]
    payload = {
        **_header(case, config),
        "no_valid_placement": False,
        "placements": len(candidates),
        "schedules": [list(s) for s in schedules],
        "default_schedule": list(DEFAULT_SCHEDULE),
        "scaled_parameters": {"k": k, "step": step, "sensitivity_k": list(sensitivity_k)},
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
        "family_placements": sorted(family),
        "sensitivity": None,
        "nove_checks": {"placements_checked": total_checked, "all_equal": True},
        "provenance": _common_provenance(
            stored, noise, default_circuit, case, workers, machine_label, seconds
        ),
    }
    if sensitivity_applies(case, config):
        payload["sensitivity"] = {"pending": True}
        save(payload)
        progress["stage"] = "sensitivity"
        t = time.perf_counter()
        payload["sensitivity"] = run_sensitivity(
            pool,
            case,
            config,
            [items[i][:3] for i in sorted(family)],
            [simulated_rows[i] for i in sorted(family)],
        )
        seconds["sensitivity"] = time.perf_counter() - t
    return payload


# --- driver ---------------------------------------------------------------------------------


class CaseFailed(RuntimeError):
    """A case raised; its failure record is on disk (revision 6)."""


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Compact: a heavy-hex d=7 case holds thousands of simulated placements.
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(text + "\n", encoding="utf-8")
    tmp.replace(path)


def _keep_failed(path: Path) -> None:
    """Move an earlier failure record aside (``runs/failed/``) so it is never overwritten."""

    if not path.exists():
        return
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not raw.get("failed"):
        return
    folder = path.parent / "failed"
    folder.mkdir(exist_ok=True)
    k = len(list(folder.glob(f"{path.stem}.*.json")))
    path.replace(folder / f"{path.stem}.{k}.json")


def run_case(
    case: dict[str, Any],
    config: dict[str, Any],
    pool: ProcessPoolExecutor,
    *,
    workers: int,
    out_dir: Path,
    machine_label: str | None,
) -> Path:
    """Run one case and write its run file; a case that raises writes a failure record
    naming its stage and raises :class:`CaseFailed` (revision 6)."""

    start = time.perf_counter()
    if qiskit_version() != config["qiskit_version"]:
        raise RuntimeError(f"qiskit {config['qiskit_version']} required, found {qiskit_version()}")
    if mapomatic_version() != config["mapomatic_version"]:
        raise RuntimeError(f"mapomatic {config['mapomatic_version']} required")
    run = run_repetition if case["kind"] == "repetition" else run_surface
    path = out_dir / "runs" / f"{case['case_id']}.json"
    _keep_failed(path)
    progress = {"stage": "start"}

    def save(payload: dict[str, Any]) -> None:
        payload["wall_clock_seconds"] = time.perf_counter() - start
        payload["workers"] = workers
        _write(path, payload)

    try:
        payload = run(
            case,
            config,
            pool,
            workers=workers,
            out_dir=out_dir,
            machine_label=machine_label,
            progress=progress,
            save=save,
        )
    except Exception as error:
        save(failed_record(case, config, progress["stage"], error, workers, machine_label))
        raise CaseFailed(f"{case['case_id']} failed in {progress['stage']}: {error}") from error
    save(payload)
    return path


def _existing_result(path: Path) -> bool:
    """Whether ``path`` holds a finished run (a failure record or a pending one is re-run)."""

    if not path.exists():
        return False
    raw = json.loads(path.read_text(encoding="utf-8"))
    return not raw.get("failed") and raw.get("sensitivity") != {"pending": True}


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
    failed = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for case in cases(config):
            if wanted is not None and case["case_id"] not in wanted:
                continue
            target = args.out / "runs" / f"{case['case_id']}.json"
            if args.skip_existing and _existing_result(target):
                continue
            t = time.perf_counter()
            try:
                path = run_case(
                    case,
                    config,
                    pool,
                    workers=args.workers,
                    out_dir=args.out,
                    machine_label=args.machine_label,
                )
            except CaseFailed as error:
                failed.append(case["case_id"])
                print(f"FAILED {error}", file=sys.stderr, flush=True)
                continue
            print(f"{case['case_id']}: {time.perf_counter() - t:.1f}s -> {path}", flush=True)
    if failed:
        print(f"{len(failed)} case(s) failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
