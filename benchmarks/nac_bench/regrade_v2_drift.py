"""R3-D5: v2's synthetic drift (SPEC_V2.md Part A) re-graded with stale decoder weights.

Usage::

    nice -n 19 python -m nac_bench.regrade_v2_drift configs_v3/regrade_v2_drift.json \\
        --workers 48 --machine-label patty [--only CASE_ID ...] [--skip-existing]
    python -m nac_bench.regrade_v2_drift configs_v3/regrade_v2_drift.json --analyse

v2 graded each choice a method made from a seen (drifted) model with the true model's
measurements, decoded with the true model's weights. A real decoder only has the seen
calibration. For every v2 development case, drift strength above 0 and draw, the fixed
placement and every method's choice are simulated again under the true model with v2's own
chunk seeds and shots, so the samples are v2's, and decoded with the seen model's weights.
The same samples are decoded with the true model's weights too, and those counts must equal
v2's stored counts exactly; the run stops on any difference. Every seen snapshot is rebuilt
from its recorded seed and must hash to v2's recorded hash. v2's files are only read.

``--analyse`` computes Part A's metrics with both weightings side by side: the mean kept
gain over draws, M6 where v2's gate allowed it, and M7, with intervals from one bootstrap.
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
import numpy as np

from nac_bench import analysis_v2
from nac_bench.machine import machine
from nac_bench.run_drift import MultiDecoderEvaluator
from nac_bench.run_reset import _bench_source_sha256, git_state
from noise_aware_compilation.benchmark import provenance
from noise_aware_compilation.circuits import repetition_code
from noise_aware_compilation.conversion import noise_model_from_snapshot
from noise_aware_compilation.devices import StoredDevice, load_device
from noise_aware_compilation.drift import DriftDraw, draw_seed, drift_snapshot
from noise_aware_compilation.evaluation import CHUNK_SHOTS, Counts, derive_seed
from noise_aware_compilation.placement import Placement
from noise_aware_compilation.stats import Interval, verdict, wilson

ROOT = Path(__file__).resolve().parent.parent
RUN_SCHEMA = "noise-aware-compilation.regrade-v2-drift-run-v3/1"
ANALYSIS_SCHEMA = "noise-aware-compilation.regrade-v2-drift-analysis-v3/1"
BOOT_TAG = "boot-v3-regrade-v2-drift"
TASK_CHUNKS = 8
COUNT_KEYS = ("shots", "logical_failures", "detector_fires", "detector_fires_sq", "num_detectors")

#: ("true", device) or ("seen", device, delta, draw)
ModelKey = tuple[Any, ...]
#: (distance, rounds, basis)
CircuitKey = tuple[Any, ...]


def load_config(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    data["_config_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return data


# --- models (cached per process) ------------------------------------------------------------


@functools.cache
def _stored(device: str) -> StoredDevice:
    return load_device(device)


@functools.cache
def _seen(device: str, delta: float, draw: int) -> DriftDraw:
    return drift_snapshot(_stored(device).snapshot, delta, draw_seed(device, draw), draw=draw)


@functools.cache
def _model(key: ModelKey) -> tuple[nove.DeviceModel, nove.HardwareNoiseModel]:
    stored = _stored(key[1])
    if key[0] == "true":
        return stored.device, stored.noise
    return stored.device, noise_model_from_snapshot(_seen(key[1], key[2], key[3]).snapshot)


def decoder_name(key: ModelKey) -> str:
    return "true" if key[0] == "true" else f"seen:{key[2]}:{key[3]}"


@functools.cache
def _circuit(key: CircuitKey) -> nove.Circuit:
    distance, rounds, basis = key
    return repetition_code(distance, rounds, basis)


@functools.lru_cache(maxsize=4)
def _evaluator(
    device: str, ckey: CircuitKey, decoders: tuple[ModelKey, ...], mapping: Placement
) -> MultiDecoderEvaluator:
    return MultiDecoderEvaluator(
        _circuit(ckey),
        _model(("true", device)),
        {decoder_name(k): _model(k) for k in decoders},
        mapping,
    )


def _sim_task(
    args: tuple[str, CircuitKey, tuple[ModelKey, ...], Placement, int, list[int]],
) -> dict[str, dict[str, int]]:
    device, ckey, decoders, mapping, shots, seeds = args
    counts = _evaluator(device, ckey, decoders, mapping).run(shots, seeds)
    return {name: c.to_dict() for name, c in counts.items()}


# --- one case -------------------------------------------------------------------------------


def _v2_run(config: dict[str, Any], case_id: str) -> tuple[Path, dict[str, Any]]:
    path = ROOT / config["v2_runs"] / f"{case_id}.json"
    return path, json.loads(path.read_text(encoding="utf-8"))


def run_case(
    case_id: str,
    config: dict[str, Any],
    pool: ProcessPoolExecutor,
    *,
    workers: int,
    out_dir: Path,
    machine_label: str | None,
) -> Path:
    start = time.perf_counter()
    v2_path, raw = _v2_run(config, case_id)
    case = raw["case"]
    if not case["drift"]:
        raise ValueError(f"{case_id} has no drift draws")
    device = case["device"]
    spec = case["circuit"]
    ckey: CircuitKey = (int(spec["distance"]), int(spec["rounds"]), spec["basis"])
    circuit = _circuit(ckey)
    if circuit.sha256 != raw["provenance"]["circuit_sha256"]:
        raise RuntimeError(f"{case_id}: the circuit differs from v2's")
    stored = _stored(device)
    if stored.snapshot.sha256 != raw["provenance"]["true_snapshot_sha256"]:
        raise RuntimeError(f"{case_id}: the true snapshot differs from v2's")

    strengths = [float(d) for d in config["strengths"]]
    fixed: Placement = tuple(raw["fixed_mapping"])
    roles: dict[Placement, list[str]] = {fixed: ["fixed"]}
    needs: dict[Placement, set[ModelKey]] = {fixed: set()}
    seen_models = []
    for rec in raw["drift"]["records"]:
        if float(rec["delta"]) not in strengths:
            continue
        key: ModelKey = ("seen", device, float(rec["delta"]), int(rec["draw"]))
        drawn = _seen(device, key[2], key[3])
        if rec["draw_seed"] != draw_seed(device, key[3]):
            raise RuntimeError(f"{case_id}: draw {key[3]}'s seed differs from v2's")
        if drawn.snapshot.sha256 != rec["seen_snapshot_sha256"]:
            raise RuntimeError(f"{case_id}: seen model {decoder_name(key)} differs from v2's")
        seen_models.append(
            {
                "decoder": decoder_name(key),
                "delta": key[2],
                "draw": key[3],
                "draw_seed": rec["draw_seed"],
                "seen_snapshot_sha256": rec["seen_snapshot_sha256"],
            }
        )
        needs[fixed].add(key)
        for method, choice in sorted(rec["choices"].items()):
            if choice.get("failure") or choice["mapping"] is None:
                continue
            mapping = tuple(choice["mapping"])
            roles.setdefault(mapping, []).append(f"{decoder_name(key)}:{method}")
            needs.setdefault(mapping, set()).add(key)

    v2_rows = {tuple(p["mapping"]): p for p in raw["placements"]}
    shots = int(raw["shots"]["shots_per_seed"])
    base_seeds = [int(s) for s in raw["seeds"]["base_seeds"]]
    rows_in = []
    tasks, owners = [], []
    for i, mapping in enumerate(sorted(needs)):
        seen_keys = sorted(needs[mapping], key=lambda k: (k[2], k[3]))
        decoders: tuple[ModelKey, ...] = (("true", device), *seen_keys)
        rows_in.append((mapping, decoders))
        for si, seed_row in enumerate(v2_rows[mapping]["per_seed"]):
            chunk_seeds = [int(s) for s in seed_row["chunk_seeds"]]
            if int(seed_row["base_seed"]) != base_seeds[si]:
                raise RuntimeError(f"{case_id}: base seeds out of order")
            if len(chunk_seeds) != -(-shots // CHUNK_SHOTS):
                raise RuntimeError(f"{case_id}: v2's chunk seeds do not cover its shots")
            for part in range(0, len(chunk_seeds), TASK_CHUNKS):
                part_shots = min(TASK_CHUNKS * CHUNK_SHOTS, shots - part * CHUNK_SHOTS)
                tasks.append(
                    (
                        device,
                        ckey,
                        decoders,
                        mapping,
                        part_shots,
                        chunk_seeds[part : part + TASK_CHUNKS],
                    )
                )
                owners.append((i, si))
    t = time.perf_counter()
    totals: dict[tuple[int, int], dict[str, Counts]] = {}
    for owner, part_counts in zip(owners, pool.map(_sim_task, tasks, chunksize=1), strict=True):
        acc = totals.setdefault(owner, {})
        for name, c in part_counts.items():
            acc[name] = Counts(**c) if name not in acc else acc[name] + Counts(**c)
    simulation_seconds = time.perf_counter() - t

    rows = []
    for i, (mapping, decoders) in enumerate(rows_in):
        per_seed = []
        for si, seed_row in enumerate(v2_rows[mapping]["per_seed"]):
            got = totals[(i, si)]
            true = got["true"].to_dict()
            if any(true[k] != int(seed_row[k]) for k in COUNT_KEYS):
                raise RuntimeError(
                    f"{case_id}: {mapping} seed {base_seeds[si]} does not reproduce v2's counts"
                )
            per_seed.append(
                {"base_seed": base_seeds[si], "decoders": {k: c.to_dict() for k, c in got.items()}}
            )
        rows.append(
            {
                "mapping": list(mapping),
                "roles": roles[mapping],
                "decoders": [decoder_name(k) for k in decoders],
                "per_seed": per_seed,
            }
        )

    commit, dirty = git_state()
    prov = provenance(stored.snapshot, stored.device, stored.noise, circuit)
    prov.update(
        {
            "package_git_commit": commit,
            "package_git_dirty": dirty,
            "nac_bench_source_sha256": _bench_source_sha256(),
            "machine": machine(machine_label, workers=workers),
            "simulation_seconds": simulation_seconds,
        }
    )
    payload = {
        "schema": RUN_SCHEMA,
        "case": case,
        "config_file_sha256": config["_config_sha256"],
        "v2_run_file": str(v2_path.relative_to(ROOT) if v2_path.is_relative_to(ROOT) else v2_path),
        "v2_run_file_sha256": hashlib.sha256(v2_path.read_bytes()).hexdigest(),
        "strengths": strengths,
        "seen_models": seen_models,
        "true_counts_equal_v2": True,
        "shots_per_seed": shots,
        "simulated": rows,
        "provenance": prov,
        "wall_clock_seconds": time.perf_counter() - start,
        "workers": workers,
    }
    path = out_dir / "runs" / f"{case_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return path


# --- analysis -------------------------------------------------------------------------------


def _iv(i: Interval) -> dict[str, float]:
    return {"estimate": i.estimate, "low": i.low, "high": i.high}


def _percentile(values: np.ndarray, estimate: float) -> dict[str, Any]:
    finite = values[np.isfinite(values)]
    low, high = np.percentile(finite, [2.5, 97.5]) if len(finite) else (np.nan, np.nan)
    return {
        "estimate": estimate,
        "low": float(low),
        "high": float(high),
        "finite_draws": len(finite),
    }


def _resampled(rates: np.ndarray, sample: np.ndarray, which: list[tuple[int, int]]) -> np.ndarray:
    """Each replicate's mean over its resampled draws of rate[a] - rate[b]."""

    rows = np.arange(rates.shape[0])[:, None]
    a = np.array([w[0] for w in which])[sample]
    b = np.array([w[1] for w in which])[sample]
    return np.asarray(np.mean(rates[rows, a] - rates[rows, b], axis=1))


def case_metrics(
    run: dict[str, Any], raw: dict[str, Any], config: dict[str, Any], v2_config: dict[str, Any]
) -> list[dict[str, Any]]:
    """Part A's metrics for one case, with true-model and seen-model decoder weights."""

    result = analysis_v2.summarize(raw, v2_config["bootstrap"])
    fixed = tuple(raw["fixed_mapping"])
    counts: dict[tuple[Placement, str], tuple[int, int]] = {}
    for row in run["simulated"]:
        for name in row["decoders"]:
            f = sum(s["decoders"][name]["logical_failures"] for s in row["per_seed"])
            n = sum(s["decoders"][name]["shots"] for s in row["per_seed"])
            counts[(tuple(row["mapping"]), name)] = (f, n)
    v2_counts = {p.mapping: (p.failures, p.shots) for p in result.placements}
    strengths = [float(d) for d in config["strengths"]]
    draws_n = int(config["bootstrap_draws"])
    out = []
    for dm in result.drift:
        if dm.delta not in strengths:
            continue
        method = dm.method
        true_index = result.choices[method]
        pairs: list[tuple[Placement, str]] = []
        for rec in raw["drift"]["records"]:
            choice = rec["choices"][method]
            if float(rec["delta"]) != dm.delta or choice.get("failure") or not choice["mapping"]:
                continue
            pairs.append((tuple(choice["mapping"]), f"seen:{dm.delta}:{rec['draw']}"))
        record: dict[str, Any] = {
            "method": method,
            "delta": dm.delta,
            "draws": dm.draws,
            "failures": dm.failures,
            "gated": dm.gated,
            "m6_v2": _iv(dm.m6),
            "m7_v2": {"harm_draws": dm.harm_draws, **_iv(dm.m7)},
        }
        if not pairs:
            out.append(record)
            continue
        # every (placement, decoder) count the metrics use, redrawn together
        keys: list[tuple[Placement, str]] = [(fixed, "true")]
        for m, seen in pairs:
            keys += [(m, "true"), (fixed, seen), (m, seen)]
        fresh_choice = None if true_index is None else result.placements[true_index].mapping
        if fresh_choice is not None:
            keys.append((fresh_choice, "true"))
        keys = sorted(set(keys))
        col = {k: j for j, k in enumerate(keys)}
        f = np.array([counts[k][0] for k in keys], dtype=np.int64)
        n = np.array([counts[k][1] for k in keys], dtype=np.int64)
        p = f / n
        for k in keys:
            if k[1] == "true" and counts[k] != v2_counts[k[0]]:
                raise RuntimeError(f"{run['case']['case_id']}: {k} differs from v2's counts")
        kept_true_cols = [(col[(fixed, "true")], col[(m, "true")]) for m, _s in pairs]
        kept_stale_cols = [(col[(fixed, s)], col[(m, s)]) for m, s in pairs]

        rng = np.random.Generator(
            np.random.PCG64(
                derive_seed(
                    BOOT_TAG,
                    int(config["bootstrap_seed"]),
                    run["case"]["case_id"],
                    method,
                    dm.delta,
                )
            )
        )
        rates = rng.binomial(n, p, size=(draws_n, len(keys))) / n
        sample = rng.integers(0, len(pairs), size=(draws_n, len(pairs)))
        kept_true = _resampled(rates, sample, kept_true_cols)
        kept_stale = _resampled(rates, sample, kept_stale_cols)
        est_true = float(np.mean([p[a] - p[b] for a, b in kept_true_cols]))
        est_stale = float(np.mean([p[a] - p[b] for a, b in kept_stale_cols]))
        record["kept_gain_true_weights"] = _percentile(kept_true, est_true)
        record["kept_gain_stale_weights"] = _percentile(kept_stale, est_stale)
        record["kept_gain_lost"] = _percentile(kept_true - kept_stale, est_true - est_stale)
        harm = 0
        for m, s in pairs:
            if m != fixed and verdict(wilson(*counts[(m, s)]), wilson(*counts[(fixed, s)])) == (
                "worse"
            ):
                harm += 1
        record["m7_stale_weights"] = {"harm_draws": harm, **_iv(wilson(harm, len(pairs)))}
        if dm.gated and fresh_choice is not None:
            den_true = p[col[(fixed, "true")]] - p[col[(fresh_choice, "true")]]
            den = rates[:, col[(fixed, "true")]] - rates[:, col[(fresh_choice, "true")]]
            # v2's M6 is the mean over draws of each draw's kept gain over the fresh gain
            with np.errstate(divide="ignore", invalid="ignore"):
                m6_true = np.where(den != 0, kept_true / den, np.nan)
                m6_stale = np.where(den != 0, kept_stale / den, np.nan)
            e_true, e_stale = est_true / den_true, est_stale / den_true
            if not np.isclose(e_true, dm.m6.estimate, rtol=1e-12, atol=0.0):
                raise RuntimeError(f"{run['case']['case_id']} {method}: M6 differs from v2's")
            record["fresh_gain"] = float(den_true)
            record["m6_true_weights"] = _percentile(m6_true, e_true)
            record["m6_stale_weights"] = _percentile(m6_stale, e_stale)
            record["m6_lost"] = _percentile(m6_true - m6_stale, e_true - e_stale)
        out.append(record)
    return out


def analyse(config: dict[str, Any], runs_dir: Path, out: Path) -> int:
    v2_config = json.loads((ROOT / config["v2_config"]).read_text(encoding="utf-8"))
    records = []
    for case_id in config["cases"]:
        path = runs_dir / f"{case_id}.json"
        if not path.exists():
            records.append({"case_id": case_id, "missing": True})
            continue
        run = json.loads(path.read_text(encoding="utf-8"))
        if run["config_file_sha256"] != config["_config_sha256"]:
            raise RuntimeError(f"{path.name} was run with another config")
        v2_path, raw = _v2_run(config, case_id)
        if hashlib.sha256(v2_path.read_bytes()).hexdigest() != run["v2_run_file_sha256"]:
            raise RuntimeError(f"{case_id}: v2's run file changed since the re-grade")
        records.append(
            {
                "case_id": case_id,
                "run_file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "metrics": case_metrics(run, raw, config, v2_config),
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
    parser.add_argument("--out", type=Path, default=ROOT / "results_v3" / "regrade_v2_drift")
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
        for case_id in config["cases"]:
            if wanted is not None and case_id not in wanted:
                continue
            if args.skip_existing and (args.out / "runs" / f"{case_id}.json").exists():
                continue
            t = time.perf_counter()
            path = run_case(
                case_id,
                config,
                pool,
                workers=args.workers,
                out_dir=args.out,
                machine_label=args.machine_label,
            )
            print(f"{case_id}: {time.perf_counter() - t:.1f}s -> {path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
