"""v3 real drift: choose on an old snapshot, grade on a newer one (SPEC_V3.md Part B).

Usage::

    nice -n 19 python -m nac_bench.run_drift configs_v3/drift.json --workers 48 \\
        --machine-label patty [--only CASE_ID ...] [--skip-existing]
    python -m nac_bench.run_drift configs_v3/drift.json --analyse

For each pair of dated snapshots of a development device (``data/devices/drift``, R3-D1),
reset setting and circuit (R3-D3):

1. Every method chooses on the old snapshot (the *stale* choice) and on the new one (the
   *fresh* choice), exhaustively and with the held-out runs' rules: the decoder-graph and v1
   scores and summed error from the prepared lowering (a sample checked against Nove's),
   Qiskit's default at level 1 and mapomatic on the snapshot's Target, and the naive
   placement. On FakeHanoi's 14-day pair the robust rule of R3-D6 chooses too.
2. Every chosen placement is simulated under the new snapshot (R3-D2). The samples are
   decoded twice: with weights from the old snapshot (*stale*, the headline for stale
   choices) and from the new one (*new*). A coupler valid on the old snapshot and removed
   on the new one is simulated at the converter's limit, error 0.8 (P2-22).
3. Shots follow R3-C6, from the pilot's smaller failure count over the two decoders.

``--analyse`` computes R3-D4's metrics from the run files: the gain kept, the fresh gain
and M6 where the fresh gain is stable, with intervals.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import math
import os
import random
import sys
import time
import warnings
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import nove
import numpy as np
from nove.decoders.pymatching import PyMatchingDecoder

from nac_bench.baselines_v3 import mapomatic_version, run_baselines
from nac_bench.machine import machine
from nac_bench.qiskit_layout import qiskit_version
from nac_bench.run_reset import SETTINGS, _bench_source_sha256, git_state
from nac_bench.run_v3 import LOCK_FILE, index_of, main_shots, rank_of, rounded
from noise_aware_compilation.baselines import summed_error
from noise_aware_compilation.benchmark import provenance
from noise_aware_compilation.circuits import repetition_code
from noise_aware_compilation.conditions import reset_equals_readout
from noise_aware_compilation.conversion import (
    ConvertedDevice,
    convert_fake_backend,
    cx_probabilities,
    noise_model_from_snapshot,
)
from noise_aware_compilation.decoder_graph import decoder_graph_score_from_dem
from noise_aware_compilation.evaluation import CHUNK_SHOTS, Counts, derive_seed
from noise_aware_compilation.fast_lowering import Lowering, v1_score_from_dem
from noise_aware_compilation.placement import (
    TIE_BREAK_RULE,
    Placement,
    _rounded,
    approximate_dem,
    enumerate_placements,
    interaction_edges,
    is_valid_placement,
    lowered_circuit,
    relabel_circuit,
)
from noise_aware_compilation.stats import Interval, newcombe_difference, wilson

ROOT = Path(__file__).resolve().parent.parent
DRIFT = ROOT / "data" / "devices" / "drift"
RUN_SCHEMA = "noise-aware-compilation.drift-run-v3/1"
ANALYSIS_SCHEMA = "noise-aware-compilation.drift-analysis-v3/1"
SEED_TAG = "eval-v3-drift"
PILOT_TAG = "pilot-v3-drift"
CHECK_TAG = "check-v3-drift"
BOOT_TAG = "boot-v3-drift"
SCORE_CHUNK = 500
TASK_CHUNKS = 8
#: the converter's limit (``conversion.MAX_PAULI_CX_ERROR``): each of 15 Pauli terms at 1/15
CAP_ERROR = 0.8

#: ("snapshot", chip, date, setting) or ("grading", chip, old, new, setting)
ModelKey = tuple[str, ...]
#: (distance, rounds, basis, two-qubit gate)
CircuitKey = tuple[Any, ...]


# --- config ---------------------------------------------------------------------------------


def load_config(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    data["_config_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return data


def cases(config: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for setting in config["settings_order"]:
        if setting not in SETTINGS:
            raise ValueError(f"unknown reset setting {setting!r}")
        for pair in config["pairs"]:
            for label, spec in config["circuits"].items():
                out.append(
                    {
                        "case_id": f"v3-drift-{setting}-{pair['pair']}-{label}",
                        "setting": setting,
                        **pair,
                        "circuit": {"label": label, **spec},
                    }
                )
    return out


# --- models (cached per process) ------------------------------------------------------------


@functools.cache
def _converted(chip: str, date: str) -> ConvertedDevice:
    return convert_fake_backend(
        DRIFT / f"conf_{chip}_{date}.json", DRIFT / f"props_{chip}_{date}.json"
    )


@functools.cache
def _snapshot_model(
    chip: str, date: str, setting: str
) -> tuple[nove.DeviceModel, nove.HardwareNoiseModel]:
    converted = _converted(chip, date)
    noise = noise_model_from_snapshot(converted.snapshot)
    if setting == "readout":
        noise = reset_equals_readout(noise, converted.device)
    return converted.device, noise


def _pair_key(a: int, b: int) -> tuple[int, int]:
    return (min(a, b), max(a, b))


@functools.cache
def dead_couplers(chip: str, old: str, new: str) -> tuple[tuple[int, int], ...]:
    """Couplers valid on the old snapshot and removed on the new one."""

    before = {_pair_key(*p) for p in _converted(chip, old).device.connectivity}
    after = {_pair_key(*p) for p in _converted(chip, new).device.connectivity}
    return tuple(sorted(before - after))


@functools.cache
def _grading_model(
    chip: str, old: str, new: str, setting: str
) -> tuple[nove.DeviceModel, nove.HardwareNoiseModel]:
    """The new snapshot's model, with every coupler that died since ``old`` at the limit."""

    device, noise = _snapshot_model(chip, new, setting)
    dead = dead_couplers(chip, old, new)
    if not dead:
        return device, noise
    gate = next(k.gate for k in noise.gate_channels if len(k.targets) == 2)
    channels = dict(noise.gate_channels)
    cap = nove.PauliChannel(probabilities=cx_probabilities(CAP_ERROR))
    for a, b in dead:
        for pair in ((a, b), (b, a)):
            channels[nove.OperationKey(gate, pair)] = cap
    graded = nove.HardwareNoiseModel(
        gate_channels=channels,
        gate_defaults=dict(noise.gate_defaults),
        idle_rates=dict(noise.idle_rates),
        default_idle_rate=noise.default_idle_rate,
        readout_flip_probabilities=dict(noise.readout_flip_probabilities),
        default_readout_flip_probability=noise.default_readout_flip_probability,
        reset_flip_probabilities=dict(noise.reset_flip_probabilities),
        default_reset_flip_probability=noise.default_reset_flip_probability,
    )
    data = device.to_dict()
    data["connectivity"] = sorted([list(p) for p in device.connectivity] + [list(p) for p in dead])
    return nove.DeviceModel.from_dict(data), graded


def _model(key: ModelKey) -> tuple[nove.DeviceModel, nove.HardwareNoiseModel]:
    if key[0] == "snapshot":
        return _snapshot_model(key[1], key[2], key[3])
    return _grading_model(key[1], key[2], key[3], key[4])


@functools.cache
def _circuit(key: CircuitKey) -> nove.Circuit:
    distance, rounds, basis, gate = key
    return repetition_code(distance, rounds, basis, gate)


@functools.cache
def _lowering(model: ModelKey, circuit: CircuitKey) -> Lowering:
    device, noise = _model(model)
    return Lowering(_circuit(circuit), device, noise)


# --- pool tasks -----------------------------------------------------------------------------


def _score_chunk(
    args: tuple[ModelKey, CircuitKey, list[Placement]],
) -> tuple[list[float], list[float], list[float]]:
    model, ckey, chunk = args
    _device, noise = _model(model)
    circuit = _circuit(ckey)
    lowering = _lowering(model, ckey)
    dg, v1, summed = [], [], []
    for m in chunk:
        noisy = lowering.stim_circuit(m)
        dg.append(decoder_graph_score_from_dem(approximate_dem(noisy, decompose=True)))
        v1.append(v1_score_from_dem(approximate_dem(noisy, decompose=False)))
        summed.append(summed_error(circuit, noise, m))
    return dg, v1, summed


def _check_task(args: tuple[ModelKey, CircuitKey, Placement]) -> int:
    model, ckey, mapping = args
    _lowering(model, ckey).check(mapping)
    return 1


class MultiDecoderEvaluator:
    """Samples under one model and decodes the same shots with each decoder's weights."""

    def __init__(
        self,
        circuit: nove.Circuit,
        simulated: tuple[nove.DeviceModel, nove.HardwareNoiseModel],
        decoders: dict[str, tuple[nove.DeviceModel, nove.HardwareNoiseModel]],
        mapping: Placement,
    ) -> None:
        device, noise = simulated
        self.experiment = nove.Experiment(
            relabel_circuit(circuit, mapping), noise=nove.NoiseModel(hardware=noise), device=device
        )
        self.num_detectors = int(self.experiment.resolved_circuit().to_stim().num_detectors)
        self.decoders = {}
        for name, (d, n) in decoders.items():
            dem = approximate_dem(lowered_circuit(circuit, d, n, mapping), decompose=True)
            if int(dem.num_detectors) != self.num_detectors:
                raise RuntimeError("a decoder's model has a different detector count")
            self.decoders[name] = PyMatchingDecoder.from_stim_dem(
                dem,
                decoder_model_mismatch=True,
                mismatch_reason=(
                    "decoder weights from another snapshot (R3-D2) or approximate DEM (K1)"
                ),
            )

    def run_chunk(self, shots: int, seed: int) -> dict[str, Counts]:
        result = self.experiment.compile(backend="stim", seed=seed).sample(shots)
        dets = result.detection_events
        per_shot = dets.sum(axis=1, dtype=np.int64)
        fires, fires_sq = int(per_shot.sum()), int((per_shot * per_shot).sum())
        out = {}
        for name, decoder in self.decoders.items():
            predictions = decoder.decode_batch(dets)
            failures = int(np.count_nonzero(np.any(predictions != result.observables, axis=1)))
            out[name] = Counts(shots, failures, fires, fires_sq, self.num_detectors)
        return out

    def run(self, shots: int, seeds: Sequence[int]) -> dict[str, Counts]:
        total: dict[str, Counts] = {}
        remaining = shots
        for seed in seeds[: -(-shots // CHUNK_SHOTS)]:
            size = min(CHUNK_SHOTS, remaining)
            for name, c in self.run_chunk(size, seed).items():
                total[name] = c if name not in total else total[name] + c
            remaining -= size
        return total


@functools.lru_cache(maxsize=8)
def _evaluator(
    simulated: ModelKey,
    decoders: tuple[tuple[str, ModelKey], ...],
    ckey: CircuitKey,
    mapping: Placement,
) -> MultiDecoderEvaluator:
    return MultiDecoderEvaluator(
        _circuit(ckey), _model(simulated), {name: _model(k) for name, k in decoders}, mapping
    )


def _sim_task(
    args: tuple[ModelKey, tuple[tuple[str, ModelKey], ...], CircuitKey, Placement, int, list[int]],
) -> dict[str, dict[str, int]]:
    simulated, decoders, ckey, mapping, shots, seeds = args
    counts = _evaluator(simulated, decoders, ckey, mapping).run(shots, seeds)
    return {name: c.to_dict() for name, c in counts.items()}


# --- selection ------------------------------------------------------------------------------


def select(
    pool: ProcessPoolExecutor,
    model: ModelKey,
    ckey: CircuitKey,
    case_id: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Every method's choice on one snapshot, with the held-out runs' rules."""

    _kind, chip, date, setting = model
    device, _noise = _model(model)
    circuit = _circuit(ckey)
    t = time.perf_counter()
    placements = enumerate_placements(circuit, device)
    n = len(placements)
    chunks = [placements[i : i + SCORE_CHUNK] for i in range(0, n, SCORE_CHUNK)]
    parts = list(pool.map(_score_chunk, [(model, ckey, c) for c in chunks]))
    scores = {
        name: np.concatenate([np.asarray(p[k], dtype=np.float64) for p in parts])
        for k, name in enumerate(("decoder_graph", "v1", "summed_error"))
    }
    rng = random.Random(derive_seed(CHECK_TAG, case_id, date))
    checked = rng.sample(range(n), min(int(config["nove_checks_per_case"]), n))
    total_checked = sum(pool.map(_check_task, [(model, ckey, placements[i]) for i in checked]))
    keys = {
        "decoder_graph": rounded(scores["decoder_graph"]),
        "v1": rounded(scores["v1"]),
        "summed_error": scores["summed_error"],
    }
    order = {name: np.argsort(k, kind="stable") for name, k in keys.items()}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        record = run_baselines(
            circuit,
            _converted(chip, date).snapshot,
            device,
            placements,
            int(config["seed_transpiler"]),
            reset_error=SETTINGS[setting],
        )
    record["mapomatic"].pop("candidate_costs")
    choices: dict[str, int | None] = {
        "decoder-graph": int(order["decoder_graph"][0]),
        "v1": int(order["v1"][0]),
        "lowest-summed-error": int(order["summed_error"][0]),
        "naive": 0,
        "qiskit-default-l1": index_of(placements, record["qiskit-default-l1"]["mapping"]),
        "mapomatic": index_of(placements, record["mapomatic"]["mapping"]),
    }
    methods = {}
    for name in config["methods"]:
        i = choices[name]
        entry: dict[str, Any] = {"index": i, "mapping": None if i is None else list(placements[i])}
        if i is not None:
            entry["scores"] = {s: float(v[i]) for s, v in scores.items()}
            entry["ranks"] = {s: rank_of(k, i) for s, k in keys.items()}
        if name in record:
            entry["detail"] = record[name]
        methods[name] = entry
    return {
        "snapshot_date": date,
        "snapshot_sha256": _converted(chip, date).snapshot.sha256,
        "placements": n,
        "methods": methods,
        "nove_checks": total_checked,
        "selection_seconds": time.perf_counter() - t,
        "_placements": placements,
        "_scores": scores,
    }


def robust_choice(
    selections: Sequence[dict[str, Any]],
) -> tuple[Placement, float]:
    """R3-D6: the lowest mean decoder-graph score over the snapshots (rounded after averaging)."""

    by = [
        dict(zip(s["_placements"], s["_scores"]["decoder_graph"], strict=True)) for s in selections
    ]
    common = sorted(set(by[0]).intersection(*by[1:]))
    means = {m: math.fsum(float(b[m]) for b in by) / len(by) for m in common}
    best = min(common, key=lambda m: (_rounded(means[m]), m))
    return best, means[best]


# --- one case -------------------------------------------------------------------------------


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
        raise RuntimeError(f"qiskit {config['qiskit_version']} required")
    if mapomatic_version() != config["mapomatic_version"]:
        raise RuntimeError(f"mapomatic {config['mapomatic_version']} required")
    case_id, chip, setting = case["case_id"], case["chip"], case["setting"]
    old, new = case["old"], case["new"]
    spec = case["circuit"]
    ckey: CircuitKey = (
        int(spec["distance"]),
        int(spec["rounds"]),
        spec["basis"],
        case["two_qubit_gate"],
    )
    circuit = _circuit(ckey)
    old_key: ModelKey = ("snapshot", chip, old, setting)
    new_key: ModelKey = ("snapshot", chip, new, setting)
    grading_key: ModelKey = ("grading", chip, old, new, setting)
    stale = select(pool, old_key, ckey, case_id, config)
    fresh = select(pool, new_key, ckey, case_id, config)
    extra: dict[str, Any] = {}
    robust = config.get("robust_rule")
    if robust and robust["pair"] == case["pair"]:
        selections = [
            stale
            if date == old
            else select(pool, ("snapshot", chip, date, setting), ckey, case_id, config)
            for date in robust["snapshots"]
        ]
        mapping, mean = robust_choice(selections)
        extra["robust-mean"] = {"mapping": list(mapping), "mean_decoder_graph_score": mean}

    # every chosen placement, with its roles
    roles: dict[Placement, list[str]] = {}
    for when, sel in (("stale", stale), ("fresh", fresh)):
        for name, entry in sel["methods"].items():
            if entry["mapping"] is not None:
                roles.setdefault(tuple(entry["mapping"]), []).append(f"{when}:{name}")
    for name, entry in extra.items():
        roles.setdefault(tuple(entry["mapping"]), []).append(f"stale:{name}")
    old_device, _ = _model(old_key)
    grading_device, _ = _model(grading_key)
    dead = set(dead_couplers(chip, old, new))
    rows_in = []
    for mapping in sorted(roles):
        names: list[tuple[str, ModelKey]] = [("new", grading_key)]
        if is_valid_placement(circuit, old_device, mapping):
            names.insert(0, ("stale", old_key))
        if not is_valid_placement(circuit, grading_device, mapping):
            raise RuntimeError(f"{case_id}: {mapping} is not valid on the grading device")
        uses = sorted(
            _pair_key(mapping[a], mapping[b])
            for a, b in sorted(interaction_edges(circuit))
            if _pair_key(mapping[a], mapping[b]) in dead
        )
        rows_in.append((mapping, tuple(names), uses))

    rule = config["shots_rule"]
    base_seeds = [int(s) for s in config["base_seeds"]]
    t = time.perf_counter()
    pilot_shots = int(rule["pilot_shots"])
    pilot = list(
        pool.map(
            _sim_task,
            [
                (
                    grading_key,
                    decoders,
                    ckey,
                    m,
                    pilot_shots,
                    _seeds(PILOT_TAG, int(config["pilot_seed"]), case_id, m, pilot_shots),
                )
                for m, decoders, _u in rows_in
            ],
        )
    )
    pilot_seconds = time.perf_counter() - t
    shots = [
        main_shots(
            min(c["logical_failures"] for c in p.values()), pilot_shots, rule, len(base_seeds)
        )
        for p in pilot
    ]
    tasks, owners = [], []
    for i, ((m, decoders, _u), n) in enumerate(zip(rows_in, shots, strict=True)):
        for si, base in enumerate(base_seeds):
            seeds = _seeds(SEED_TAG, base, case_id, m, n)
            for part in range(0, len(seeds), TASK_CHUNKS):
                part_shots = min(TASK_CHUNKS * CHUNK_SHOTS, n - part * CHUNK_SHOTS)
                tasks.append(
                    (grading_key, decoders, ckey, m, part_shots, seeds[part : part + TASK_CHUNKS])
                )
                owners.append((i, si))
    t = time.perf_counter()
    totals: dict[tuple[int, int], dict[str, Counts]] = {}
    for owner, part_counts in zip(owners, pool.map(_sim_task, tasks, chunksize=1), strict=True):
        acc = totals.setdefault(owner, {})
        for name, c in part_counts.items():
            acc[name] = Counts(**c) if name not in acc else acc[name] + Counts(**c)
    main_seconds = time.perf_counter() - t

    rows = []
    for i, (m, decoders, uses) in enumerate(rows_in):
        per_seed = []
        for si, base in enumerate(base_seeds):
            seed_counts = totals[(i, si)]
            if any(c.shots != shots[i] for c in seed_counts.values()):
                raise RuntimeError("a placement's main-stage shots differ from the rule's")
            per_seed.append(
                {"base_seed": base, "decoders": {k: c.to_dict() for k, c in seed_counts.items()}}
            )
        rows.append(
            {
                "mapping": list(m),
                "roles": roles[m],
                "decoders": [name for name, _k in decoders],
                "dead_couplers_used": [list(p) for p in uses],
                "pilot": pilot[i],
                "shots_per_seed": shots[i],
                "per_seed": per_seed,
            }
        )

    def public(sel: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in sel.items() if not k.startswith("_")}

    commit, dirty = git_state()
    stored = _converted(chip, new)
    _gd, grading_noise = _model(grading_key)
    prov = provenance(stored.snapshot, stored.device, grading_noise, circuit)
    prov.update(
        {
            "package_git_commit": commit,
            "package_git_dirty": dirty,
            "qiskit_version": qiskit_version(),
            "mapomatic_version": mapomatic_version(),
            "nac_bench_source_sha256": _bench_source_sha256(),
            "lock_file_sha256": hashlib.sha256(LOCK_FILE.read_bytes()).hexdigest(),
            "reset_setting": setting,
            "machine": machine(machine_label, workers=workers),
            "stage_seconds": {
                "selection_stale": stale["selection_seconds"],
                "selection_fresh": fresh["selection_seconds"],
                "pilot": pilot_seconds,
                "main": main_seconds,
            },
            "old_snapshot_sha256": _converted(chip, old).snapshot.sha256,
            "new_snapshot_sha256": stored.snapshot.sha256,
            "decoder_sources": {
                "stale": f"old snapshot {old} ({_converted(chip, old).snapshot.sha256})",
                "new": f"new snapshot {new} ({stored.snapshot.sha256}), dead couplers at the limit",
            },
        }
    )
    payload = {
        "schema": RUN_SCHEMA,
        "case": case,
        "config_file_sha256": config["_config_sha256"],
        "tie_break_rule": TIE_BREAK_RULE,
        "dead_couplers": [list(p) for p in sorted(dead)],
        "cap_error": CAP_ERROR,
        "stale": public(stale),
        "fresh": public(fresh),
        "extra_methods": extra,
        "simulated": rows,
        "seeds": {
            "base_seeds": base_seeds,
            "pilot_seed": int(config["pilot_seed"]),
            "rule": config["seed_note"],
        },
        "shots_rule": rule,
        "chunk_shots": CHUNK_SHOTS,
        "provenance": prov,
        "wall_clock_seconds": time.perf_counter() - start,
        "workers": workers,
    }
    path = out_dir / "runs" / f"{case_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _seeds(tag: str, base: int, case_id: str, mapping: Placement, shots: int) -> list[int]:
    key = ",".join(map(str, mapping))
    return [derive_seed(tag, base, case_id, key, k) for k in range(-(-shots // CHUNK_SHOTS))]


# --- R3-D4 metrics --------------------------------------------------------------------------


def _iv(i: Interval) -> dict[str, float]:
    return {"estimate": i.estimate, "low": i.low, "high": i.high}


def _pooled(row: dict[str, Any], decoder: str) -> tuple[int, int]:
    f = sum(s["decoders"][decoder]["logical_failures"] for s in row["per_seed"])
    n = sum(s["decoders"][decoder]["shots"] for s in row["per_seed"])
    return f, n


def _gain(a: tuple[int, int], b: tuple[int, int], same: bool) -> Interval:
    """P_L(a) - P_L(b): naive minus choice, so positive is a gain."""

    if same:
        return Interval(0.0, 0.0, 0.0)
    return newcombe_difference(a[0], a[1], b[0], b[1])


def metrics(run: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """R3-D4 for every method of one case."""

    by_mapping = {tuple(r["mapping"]): r for r in run["simulated"]}
    stale_naive = tuple(run["stale"]["methods"]["naive"]["mapping"])
    fresh_naive = tuple(run["fresh"]["methods"]["naive"]["mapping"])
    methods = dict(run["stale"]["methods"])
    for name, entry in run["extra_methods"].items():
        methods[name] = {"mapping": entry["mapping"]}
    draws = int(config["bootstrap_draws"])
    out = {}
    for name, entry in methods.items():
        if name == "naive" or entry.get("mapping") is None:
            continue
        stale_choice = tuple(entry["mapping"])
        fresh_entry = run["fresh"]["methods"].get(name)  # none for the robust rule
        fresh_choice = (
            None
            if fresh_entry is None or fresh_entry["mapping"] is None
            else tuple(fresh_entry["mapping"])
        )
        sn, sc = by_mapping[stale_naive], by_mapping[stale_choice]
        record: dict[str, Any] = {
            "stale_choice": list(stale_choice),
            "fresh_choice": None if fresh_choice is None else list(fresh_choice),
            "choice_changed": None if fresh_choice is None else fresh_choice != stale_choice,
            "p_l_stale_choice_stale_weights": _iv(wilson(*_pooled(sc, "stale"))),
            "p_l_stale_choice_new_weights": _iv(wilson(*_pooled(sc, "new"))),
        }
        kept = _gain(_pooled(sn, "stale"), _pooled(sc, "stale"), stale_naive == stale_choice)
        kept_new = _gain(_pooled(sn, "new"), _pooled(sc, "new"), stale_naive == stale_choice)
        record["kept_gain"] = _iv(kept)
        record["kept_gain_new_weights"] = _iv(kept_new)
        if name in run["extra_methods"]:
            # R3-D6: against the headline score's stale choice, P_L(that) - P_L(this)
            head = tuple(run["stale"]["methods"][config["headline_method"]]["mapping"])
            hr = by_mapping[head]
            record["against_headline_stale_choice"] = {
                "headline_choice": list(head),
                "stale_weights": _iv(
                    _gain(_pooled(hr, "stale"), _pooled(sc, "stale"), head == stale_choice)
                ),
                "new_weights": _iv(
                    _gain(_pooled(hr, "new"), _pooled(sc, "new"), head == stale_choice)
                ),
            }
        if fresh_choice is not None:
            fn, fc = by_mapping[fresh_naive], by_mapping[fresh_choice]
            fresh = _gain(_pooled(fn, "new"), _pooled(fc, "new"), fresh_naive == fresh_choice)
            record["fresh_gain"] = _iv(fresh)
            stable = fresh.estimate > 0 and fresh.low >= fresh.estimate / 2
            record["fresh_gain_stable"] = stable
            if stable:
                record["m6"] = _m6(
                    [(sn, "stale"), (sc, "stale"), (fn, "new"), (fc, "new")],
                    kept.estimate / fresh.estimate,
                    derive_seed(
                        BOOT_TAG, int(config["bootstrap_seed"]), run["case"]["case_id"], name
                    ),
                    draws,
                )
        out[name] = record
    return out


def _m6(
    parts: Sequence[tuple[dict[str, Any], str]], estimate: float, seed: int, draws: int
) -> dict[str, Any]:
    """Parametric bootstrap of (P_L(sn) - P_L(sc)) / (P_L(fn) - P_L(fc)).

    A placement and decoder that appear twice get the same draws."""

    rng = np.random.default_rng(seed)
    cache: dict[tuple[tuple[int, ...], str], np.ndarray] = {}
    rates = []
    for row, decoder in parts:
        key = (tuple(row["mapping"]), decoder)
        if key not in cache:
            f, n = _pooled(row, decoder)
            cache[key] = rng.binomial(n, f / n, size=draws) / n
        rates.append(cache[key])
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = (rates[0] - rates[1]) / (rates[2] - rates[3])
    finite = ratio[np.isfinite(ratio)]
    return {
        "estimate": estimate,
        "low": float(np.percentile(finite, 2.5)),
        "high": float(np.percentile(finite, 97.5)),
        "draws": draws,
        "finite_draws": len(finite),
    }


def analyse(config: dict[str, Any], runs_dir: Path, out: Path) -> int:
    records = []
    for path in sorted(runs_dir.glob("v3-drift-*.json")):
        run = json.loads(path.read_text(encoding="utf-8"))
        if run["config_file_sha256"] != config["_config_sha256"]:
            raise RuntimeError(f"{path.name} was run with another config")
        records.append(
            {
                "case_id": run["case"]["case_id"],
                "pair": run["case"]["pair"],
                "setting": run["case"]["setting"],
                "circuit": run["case"]["circuit"]["label"],
                "dead_couplers": run["dead_couplers"],
                "run_file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "methods": metrics(run, config),
            }
        )
    payload = {
        "schema": ANALYSIS_SCHEMA,
        "config_file_sha256": config["_config_sha256"],
        "metrics": config["metrics"],
        "cases": records,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("config", type=Path)
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--out", type=Path, default=ROOT / "results_v3" / "drift")
    parser.add_argument("--workers", type=int, default=min(48, os.cpu_count() or 1))
    parser.add_argument("--machine-label", default=None)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--analyse", action="store_true")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if args.analyse:
        return analyse(config, args.out / "runs", args.out / "analysis.json")
    _, dirty = git_state()
    if dirty and not args.allow_dirty:
        parser.error("uncommitted changes to code, data or config; commit them first")
    wanted = set(args.only) if args.only else None
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for case in cases(config):
            if wanted is not None and case["case_id"] not in wanted:
                continue
            if args.skip_existing and (args.out / "runs" / f"{case['case_id']}.json").exists():
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
