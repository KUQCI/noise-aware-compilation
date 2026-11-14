"""Drift with uncalibrated couplers graded at their older value (exploratory, revision 6).

Usage::

    nice -n 19 python -m nac_bench.drift_uncalibrated configs_v3/drift.json --workers 4 \\
        [--machine-label LABEL] [--allow-dirty]

The drift runs (:mod:`nac_bench.run_drift`, P2-22) grade every chosen placement under the
newer snapshot. A coupler that is valid on the older snapshot and uncalibrated (error 1.0) on
the newer one has no usable newer value, so the drift runs simulate it at the converter's
limit, two-qubit error 0.8. That is one end of what the coupler could be: an uncalibrated
coupler is stale in IBM's data, and its true error is unknown in both directions. This
module grades the same placements at the other end: each such coupler at its older-snapshot
channel, everything else as the drift runs' grading model.

Only rows of the committed drift run files that use such a coupler are simulated again, since
the two grading models give the same circuit for every other placement (checked here, not
assumed). Each row is sampled under the older-value model and decoded twice, as the drift runs
do: with the older snapshot's weights (*stale*, the headline) and with the older-value model's
own weights (*new*). The kept gain, its interval and M6 are recomputed against the naive
placement's committed row with :mod:`nac_bench.run_drift`'s own functions; the fresh choices
cannot use such a coupler (they are chosen on the newer snapshot, where it is removed), so their
rows stand. Shots follow the drift config's R3-C6 rule; the seeds have their own tags. Every
device here is a development device; no held-out device is read (X16).
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import sys
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import nove

from nac_bench.machine import machine
from nac_bench.run_drift import (
    BOOT_TAG,
    CAP_ERROR,
    MultiDecoderEvaluator,
    _circuit,
    _gain,
    _grading_model,
    _iv,
    _m6,
    _pooled,
    _seeds,
    _snapshot_model,
    dead_couplers,
    load_config,
)
from nac_bench.run_reset import _bench_source_sha256, git_state
from nac_bench.run_v3 import LOCK_FILE, main_shots
from noise_aware_compilation.conversion import CX_FACTOR
from noise_aware_compilation.evaluation import CHUNK_SHOTS, Counts, derive_seed
from noise_aware_compilation.placement import Placement, lowered_circuit
from noise_aware_compilation.stats import wilson

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = "noise-aware-compilation.drift-uncalibrated-v3/1"
SEED_TAG = "eval-v3-drift-uncalibrated"
PILOT_TAG = "pilot-v3-drift-uncalibrated"
TASK_CHUNKS = 8

#: ("old-value", chip, old, new, setting) or ("snapshot", chip, date, setting)
ModelKey = tuple[str, ...]


@functools.cache
def old_value_model(
    chip: str, old: str, new: str, setting: str
) -> tuple[nove.DeviceModel, nove.HardwareNoiseModel]:
    """The drift runs' grading model with each coupler that became uncalibrated between
    ``old`` and ``new`` at its older-snapshot channel instead of the 0.8 limit."""

    device, capped = _grading_model(chip, old, new, setting)
    _old_device, old_noise = _snapshot_model(chip, old, setting)
    channels = dict(capped.gate_channels)
    gate = next(k.gate for k in capped.gate_channels if len(k.targets) == 2)
    for a, b in dead_couplers(chip, old, new):
        for pair in ((a, b), (b, a)):
            key = nove.OperationKey(gate, pair)
            if key not in old_noise.gate_channels:
                raise RuntimeError(f"{chip} {old}: no channel for {gate} {pair}")
            channels[key] = old_noise.gate_channels[key]
    return device, nove.HardwareNoiseModel(
        gate_channels=channels,
        gate_defaults=dict(capped.gate_defaults),
        idle_rates=dict(capped.idle_rates),
        default_idle_rate=capped.default_idle_rate,
        readout_flip_probabilities=dict(capped.readout_flip_probabilities),
        default_readout_flip_probability=capped.default_readout_flip_probability,
        reset_flip_probabilities=dict(capped.reset_flip_probabilities),
        default_reset_flip_probability=capped.default_reset_flip_probability,
    )


def _model(key: ModelKey) -> tuple[nove.DeviceModel, nove.HardwareNoiseModel]:
    if key[0] == "old-value":
        return old_value_model(key[1], key[2], key[3], key[4])
    return _snapshot_model(key[1], key[2], key[3])


@functools.lru_cache(maxsize=8)
def _evaluator(ckey: tuple[Any, ...], simulated: ModelKey, mapping: Placement) -> Any:
    _kind, chip, old, _new, setting = simulated
    return MultiDecoderEvaluator(
        _circuit(ckey),
        _model(simulated),
        {"stale": _model(("snapshot", chip, old, setting)), "new": _model(simulated)},
        mapping,
    )


def _sim_task(
    args: tuple[tuple[Any, ...], ModelKey, Placement, int, list[int]],
) -> dict[str, dict[str, int]]:
    ckey, simulated, mapping, shots, seeds = args
    counts = _evaluator(ckey, simulated, mapping).run(shots, seeds)
    return {name: c.to_dict() for name, c in counts.items()}


def coupler_errors(
    chip: str, old: str, new: str, setting: str, pairs: Sequence[Sequence[int]]
) -> dict[str, dict[str, float]]:
    """Each coupler's two-qubit gate error (both directions) in the older-value model.

    The channel's Pauli total divided by the converter's factor 1.25 (``cx_probabilities``),
    so the 0.8 limit reads as 0.8 here; the limit's channel has a Pauli total of 1.
    """

    _device, noise = old_value_model(chip, old, new, setting)
    gate = next(k.gate for k in noise.gate_channels if len(k.targets) == 2)
    out = {}
    for a, b in pairs:
        out[f"{a}-{b}"] = {
            f"{x}->{y}": float(
                sum(noise.gate_channels[nove.OperationKey(gate, (x, y))].probabilities) / CX_FACTOR
            )
            for x, y in ((a, b), (b, a))
        }
    return out


def same_circuit_elsewhere(run: dict[str, Any]) -> bool:
    """Whether every row that uses no such coupler lowers identically under both models."""

    case = run["case"]
    spec = case["circuit"]
    ckey = (int(spec["distance"]), int(spec["rounds"]), spec["basis"], case["two_qubit_gate"])
    circuit = _circuit(ckey)
    capped = _grading_model(case["chip"], case["old"], case["new"], case["setting"])
    alt = old_value_model(case["chip"], case["old"], case["new"], case["setting"])
    for row in run["simulated"]:
        if row["dead_couplers_used"]:
            continue
        m = tuple(row["mapping"])
        if lowered_circuit(circuit, *capped, m) != lowered_circuit(circuit, *alt, m):
            return False
    return True


def regrade_run(
    pool: ProcessPoolExecutor, run: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any] | None:
    """The older-value grading of one drift case, or None if no row uses such a coupler."""

    rows = [r for r in run["simulated"] if r["dead_couplers_used"]]
    if not rows:
        return None
    case = run["case"]
    case_id, chip, setting = case["case_id"], case["chip"], case["setting"]
    old, new = case["old"], case["new"]
    spec = case["circuit"]
    ckey = (int(spec["distance"]), int(spec["rounds"]), spec["basis"], case["two_qubit_gate"])
    simulated: ModelKey = ("old-value", chip, old, new, setting)
    if not same_circuit_elsewhere(run):
        raise RuntimeError(f"{case_id}: a row without such a coupler changed its circuit")
    rule = config["shots_rule"]
    base_seeds = [int(s) for s in config["base_seeds"]]
    pilot_shots = int(rule["pilot_shots"])
    t = time.perf_counter()
    pilot = list(
        pool.map(
            _sim_task,
            [
                (
                    ckey,
                    simulated,
                    tuple(r["mapping"]),
                    pilot_shots,
                    _seeds(
                        PILOT_TAG, int(config["pilot_seed"]), case_id, r["mapping"], pilot_shots
                    ),
                )
                for r in rows
            ],
        )
    )
    shots = [
        main_shots(
            min(c["logical_failures"] for c in p.values()), pilot_shots, rule, len(base_seeds)
        )
        for p in pilot
    ]
    tasks, owners = [], []
    for i, (r, n) in enumerate(zip(rows, shots, strict=True)):
        for si, base in enumerate(base_seeds):
            seeds = _seeds(SEED_TAG, base, case_id, r["mapping"], n)
            for part in range(0, len(seeds), TASK_CHUNKS):
                part_shots = min(TASK_CHUNKS * CHUNK_SHOTS, n - part * CHUNK_SHOTS)
                tasks.append(
                    (
                        ckey,
                        simulated,
                        tuple(r["mapping"]),
                        part_shots,
                        seeds[part : part + TASK_CHUNKS],
                    )
                )
                owners.append((i, si))
    totals: dict[tuple[int, int], dict[str, Counts]] = {}
    for owner, part_counts in zip(owners, pool.map(_sim_task, tasks, chunksize=1), strict=True):
        acc = totals.setdefault(owner, {})
        for name, c in part_counts.items():
            acc[name] = Counts(**c) if name not in acc else acc[name] + Counts(**c)
    seconds = time.perf_counter() - t

    by_mapping = {tuple(r["mapping"]): r for r in run["simulated"]}
    stale_naive = tuple(run["stale"]["methods"]["naive"]["mapping"])
    fresh_naive = tuple(run["fresh"]["methods"]["naive"]["mapping"])
    out_rows = []
    for i, r in enumerate(rows):
        per_seed = []
        for si, base in enumerate(base_seeds):
            counts = totals[(i, si)]
            if any(c.shots != shots[i] for c in counts.values()):
                raise RuntimeError("a row's main-stage shots differ from the rule's")
            per_seed.append(
                {"base_seed": base, "decoders": {k: c.to_dict() for k, c in counts.items()}}
            )
        regraded = {**r, "pilot": pilot[i], "shots_per_seed": shots[i], "per_seed": per_seed}
        m = tuple(r["mapping"])
        if m in (stale_naive, fresh_naive):
            raise RuntimeError(f"{case_id}: a naive placement uses an uncalibrated coupler")
        methods = {}
        for role in r["roles"]:
            when, name = role.split(":", 1)
            if when != "stale":
                raise RuntimeError(f"{case_id}: a fresh choice uses an uncalibrated coupler")
            sn = by_mapping[stale_naive]
            record: dict[str, Any] = {}
            for label, row in (("limit_0.8", r), ("older_value", regraded)):
                kept = _gain(_pooled(sn, "stale"), _pooled(row, "stale"), False)
                kept_new = _gain(_pooled(sn, "new"), _pooled(row, "new"), False)
                entry: dict[str, Any] = {
                    "p_l_stale_weights": _iv(wilson(*_pooled(row, "stale"))),
                    "p_l_new_weights": _iv(wilson(*_pooled(row, "new"))),
                    "kept_gain": _iv(kept),
                    "kept_gain_new_weights": _iv(kept_new),
                }
                fresh_entry = run["fresh"]["methods"].get(name)
                if fresh_entry is not None and fresh_entry["mapping"] is not None:
                    fn = by_mapping[fresh_naive]
                    fc = by_mapping[tuple(fresh_entry["mapping"])]
                    same = fresh_naive == tuple(fresh_entry["mapping"])
                    fresh = _gain(_pooled(fn, "new"), _pooled(fc, "new"), same)
                    entry["fresh_gain"] = _iv(fresh)
                    if fresh.estimate > 0 and fresh.low >= fresh.estimate / 2:
                        entry["m6"] = _m6(
                            [(sn, "stale"), (row, "stale"), (fn, "new"), (fc, "new")],
                            kept.estimate / fresh.estimate,
                            # the limit's draws are the committed analysis's own
                            derive_seed(
                                BOOT_TAG,
                                int(config["bootstrap_seed"]),
                                case_id,
                                name,
                                *(() if label == "limit_0.8" else (label,)),
                            ),
                            int(config["bootstrap_draws"]),
                        )
                record[label] = entry
            methods[name] = record
        out_rows.append(
            {
                "mapping": list(m),
                "roles": r["roles"],
                "dead_couplers_used": r["dead_couplers_used"],
                "coupler_gate_errors_older_value": coupler_errors(
                    chip, old, new, setting, r["dead_couplers_used"]
                ),
                "cap_error": CAP_ERROR,
                "pilot": pilot[i],
                "shots_per_seed": shots[i],
                "per_seed": per_seed,
                "methods": methods,
            }
        )
    return {
        "case_id": case_id,
        "pair": case["pair"],
        "setting": setting,
        "circuit": spec["label"],
        "naive_p_l_stale_weights": _iv(wilson(*_pooled(by_mapping[stale_naive], "stale"))),
        "rows": out_rows,
        "seconds": seconds,
    }


def run(config_path: Path, out: Path, workers: int, machine_label: str | None) -> dict[str, Any]:
    config = load_config(config_path)
    runs_dir = ROOT / "results_v3" / "drift" / "runs"
    commit, dirty = git_state()
    cases = []
    sources = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for path in sorted(runs_dir.glob("v3-drift-*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            if data["config_file_sha256"] != config["_config_sha256"]:
                raise RuntimeError(f"{path.name} was run with another config")
            result = regrade_run(pool, data, config)
            if result is not None:
                sources[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
                cases.append(result)
                print(f"{result['case_id']}: {result['seconds']:.1f}s", flush=True)
    payload = {
        "schema": SCHEMA,
        "description": __doc__,
        "config_file_sha256": config["_config_sha256"],
        "drift_run_files_sha256": sources,
        "seeds": {
            "base_seeds": [int(s) for s in config["base_seeds"]],
            "pilot_seed": int(config["pilot_seed"]),
            "tags": {"main": SEED_TAG, "pilot": PILOT_TAG},
        },
        "cases": cases,
        "provenance": {
            "package_git_commit": commit,
            "package_git_dirty": dirty,
            "nac_bench_source_sha256": _bench_source_sha256(),
            "lock_file_sha256": hashlib.sha256(LOCK_FILE.read_bytes()).hexdigest(),
            "machine": machine(machine_label, workers=workers),
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("config", type=Path)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "results_v3" / "drift" / "uncalibrated" / "regrade.json",
    )
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    parser.add_argument("--machine-label", default=None)
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args(argv)
    _, dirty = git_state()
    if dirty and not args.allow_dirty:
        parser.error("uncommitted changes to code, data or config; commit them first")
    run(args.config, args.out, args.workers, args.machine_label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
