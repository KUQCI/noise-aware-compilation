"""v2 benchmark runner (SPEC_V2.md Parts A to C): every v2 number from a config and seeds.

Usage::

    python -m nac_bench.run_v2 configs_v2/benchmark_v2.json [--only CASE_ID ...]

For each case (device, circuit) the runner

1. measures every valid placement's P_L under the true model (the stored
   converted snapshot) on three fresh base seeds, with v1's decoder rule and
   precision rule (R-F1, T-P4), once;
2. records each noise-aware method's choice under the true model, with every
   candidate's score for the two package scores (M4);
3. for development devices, records each method's choice under every seen
   model: every drift strength and draw (R-S3, R-S4), the same seen model for
   every method (R-S5, R-B8);
4. writes ``results_v2/runs/<case_id>.json``.

Drift adds selections, not simulation: every choice is graded with the
measurements of step 1, decoded with true-model weights (SPEC_V2.md Part A).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import nove

from nac_bench.qiskit_layout import METHOD as QISKIT_METHOD
from nac_bench.qiskit_layout import VF2_SETTINGS, qiskit_layout, qiskit_version
from noise_aware_compilation.baselines import SUMMED_ERROR_METHOD, lowest_summed_error
from noise_aware_compilation.benchmark import _evaluate_task, _pooled, provenance
from noise_aware_compilation.circuits import repetition_code
from noise_aware_compilation.conversion import noise_model_from_snapshot
from noise_aware_compilation.decoder_graph import DECODER_GRAPH_SCORER
from noise_aware_compilation.devices import load_device
from noise_aware_compilation.drift import DRIFT_SEED_RULE, draw_seed, drift_snapshot
from noise_aware_compilation.evaluation import (
    CHUNK_SHOTS,
    DECODER_DESCRIPTION,
    Counts,
    derive_seed,
)
from noise_aware_compilation.placement import (
    TIE_BREAK_RULE,
    V1_SCORER,
    Placement,
    enumerate_placements,
    interaction_edges,
    place,
)
from noise_aware_compilation.stats import wilson

RESULTS_SCHEMA = "noise-aware-compilation.run-v2/1"
ROOT = Path(__file__).resolve().parent.parent
METHODS = ("v1", "decoder-graph", "lowest-summed-error", "qiskit-vf2")
METHOD_DESCRIPTIONS = {
    "v1": V1_SCORER.method,
    "decoder-graph": DECODER_GRAPH_SCORER.method,
    "lowest-summed-error": SUMMED_ERROR_METHOD,
    "qiskit-vf2": QISKIT_METHOD,
}


def load_config(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("config must be a JSON object")
    data["_config_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return data


def cases(config: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for entry in config["devices"]:
        for label in entry["circuits"]:
            out.append(
                {
                    "case_id": f"v2-{entry['name']}-{label}",
                    "device": entry["name"],
                    "role": entry["role"],
                    "drift": bool(entry["drift"]),
                    "circuit": {"label": label, **config["circuits"][label]},
                }
            )
    return out


def git_state() -> tuple[str | None, bool | None]:
    """HEAD and whether tracked inputs differ from it (v2 results and report excluded)."""

    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", ".", ":!results_v2", ":!REPORT_V2.md"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None, None
    return commit, bool(status)


def eval_chunk_seed(base_seed: int, case_id: str, mapping: Sequence[int], chunk: int) -> int:
    return derive_seed("eval-v2", base_seed, case_id, ",".join(map(str, mapping)), chunk)


def choose_all(
    circuit: nove.Circuit,
    device: nove.DeviceModel,
    noise: nove.HardwareNoiseModel,
    candidates: Sequence[Placement],
    *,
    with_scores: bool,
) -> dict[str, Any]:
    """Every noise-aware method's choice from one noise model (the same model for all)."""

    out: dict[str, Any] = {}
    for name, scorer in (("v1", V1_SCORER), ("decoder-graph", DECODER_GRAPH_SCORER)):
        result = place(circuit, device, noise, scorer)
        out[name] = {"mapping": list(result.mapping), "score": result.score}
        if with_scores:
            out[name]["candidate_scores"] = [c.score for c in result.candidates]
    summed = lowest_summed_error(circuit, device, noise)
    out["lowest-summed-error"] = {"mapping": list(summed.mapping), "score": summed.score}
    if with_scores:
        out["lowest-summed-error"]["candidate_scores"] = [c.score for c in summed.candidates]
    q = qiskit_layout(circuit, device, noise, candidates)
    out["qiskit-vf2"] = {
        "mapping": None if q.mapping is None else list(q.mapping),
        "failure": q.failure,
        "stop_reason": q.stop_reason,
    }
    return out


def _drift_task(args: tuple[str, str, str, float, int]) -> dict[str, Any]:
    device_name, circuit_text, _case_id, delta, draw = args
    stored = load_device(device_name)
    circuit = nove.Circuit.from_stim_text(circuit_text)
    candidates = enumerate_placements(circuit, stored.device)
    seed = draw_seed(device_name, draw)
    drifted = drift_snapshot(stored.snapshot, delta, seed, draw=draw)
    noise = noise_model_from_snapshot(drifted.snapshot)
    start = time.perf_counter()
    choices = choose_all(circuit, stored.device, noise, candidates, with_scores=False)
    return {
        "delta": delta,
        "draw": draw,
        "draw_seed": seed,
        "seen_snapshot_sha256": drifted.snapshot.sha256,
        "seen_hash_by_method": {m: drifted.snapshot.sha256 for m in METHODS},
        "capped_channels": list(drifted.capped),
        "choices": choices,
        "selection_seconds": time.perf_counter() - start,
    }


def run_case(case: dict[str, Any], config: dict[str, Any], *, workers: int, out_dir: Path) -> Path:
    start = time.perf_counter()
    stored = load_device(case["device"])
    spec = case["circuit"]
    circuit = repetition_code(spec["distance"], spec["rounds"], spec["basis"])
    device, noise = stored.device, stored.noise
    candidates = enumerate_placements(circuit, device)
    fixed = candidates[0]
    committed = tuple(config["fixed_baselines"][f"{case['device']}-{spec['label']}"])
    if committed != fixed:
        raise RuntimeError(f"fixed baseline for {case['case_id']} differs from the committed one")
    if qiskit_version() != config["qiskit_version"]:
        raise RuntimeError(f"qiskit {config['qiskit_version']} required, found {qiskit_version()}")

    selection_start = time.perf_counter()
    true_choices = choose_all(circuit, device, noise, candidates, with_scores=True)
    selection_seconds = time.perf_counter() - selection_start

    case_id = case["case_id"]
    base_seeds = [int(s) for s in config["base_seeds"]]
    min_shots = int(config["min_shots_per_seed"])
    max_shots = int(config["max_shots_per_seed"])
    target = float(config["precision_rule"]["target_relative_halfwidth"])
    circuit_text = circuit.to_stim_text()
    device_json = device.to_json()
    noise_json = json.dumps(noise.to_dict())
    per_seed: dict[tuple[int, int], Counts] = {}
    seeds_used: dict[tuple[int, int], list[int]] = {}
    shots_per_seed = 0
    next_shots = min_shots
    rounds_log: list[dict[str, Any]] = []
    drift_records: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        while True:
            first_chunk = shots_per_seed // CHUNK_SHOTS
            last_chunk = -(-next_shots // CHUNK_SHOTS)
            new_shots = next_shots - shots_per_seed
            tasks, keys = [], []
            for mi, mapping in enumerate(candidates):
                for si, base in enumerate(base_seeds):
                    seeds = [
                        eval_chunk_seed(base, case_id, mapping, k)
                        for k in range(first_chunk, last_chunk)
                    ]
                    seeds_used.setdefault((mi, si), []).extend(seeds)
                    tasks.append((circuit_text, device_json, noise_json, mapping, new_shots, seeds))
                    keys.append((mi, si))
            for key, counts in zip(keys, pool.map(_evaluate_task, tasks), strict=True):
                c = Counts(**counts)
                per_seed[key] = c if key not in per_seed else per_seed[key] + c
            shots_per_seed = next_shots
            worst = 0.0
            for mi in range(len(candidates)):
                pooled = _pooled(per_seed, mi, len(base_seeds))
                if pooled.logical_failures == 0:
                    worst = float("inf")
                    continue
                w = wilson(pooled.logical_failures, pooled.shots)
                worst = max(worst, max(w.high - w.estimate, w.estimate - w.low) / w.estimate)
            rounds_log.append({"shots_per_seed": shots_per_seed, "worst_relative_halfwidth": worst})
            if worst <= target or shots_per_seed >= max_shots:
                break
            next_shots = min(2 * shots_per_seed, max_shots)
        if case["drift"]:
            drift_tasks = [
                (case["device"], circuit_text, case_id, float(delta), draw)
                for delta in config["drift"]["strengths"]
                for draw in range(int(config["drift"]["draws"]))
            ]
            drift_records = list(pool.map(_drift_task, drift_tasks))

    placements_out = []
    for mi, mapping in enumerate(candidates):
        placements_out.append(
            {
                "mapping": list(mapping),
                "is_fixed": mapping == fixed,
                "run_config": {
                    "circuit_sha256": circuit.sha256,
                    "device_sha256": device.sha256,
                    "calibration_snapshot_sha256": stored.snapshot.sha256,
                    "shots_per_seed": shots_per_seed,
                    "base_seeds": base_seeds,
                    "decoder_rule": DECODER_DESCRIPTION["construction"],
                    "backend": "stim",
                    "mapping": list(mapping),
                },
                "per_seed": [
                    {
                        "base_seed": base_seeds[si],
                        "chunk_seeds": seeds_used[(mi, si)],
                        **per_seed[(mi, si)].to_dict(),
                    }
                    for si in range(len(base_seeds))
                ],
            }
        )

    commit, dirty = git_state()
    prov = provenance(stored.snapshot, device, noise, circuit)
    prov.update(
        {
            "package_git_commit": commit,
            "package_git_dirty": dirty,
            "qiskit_version": qiskit_version(),
            "nac_bench_source_sha256": _bench_source_sha256(),
            "true_snapshot_sha256": stored.snapshot.sha256,
        }
    )
    payload = {
        "schema": RESULTS_SCHEMA,
        "case": case,
        "config_file_sha256": config["_config_sha256"],
        "fixed_mapping": list(fixed),
        "methods": {m: METHOD_DESCRIPTIONS[m] for m in METHODS},
        "tie_break_rule": TIE_BREAK_RULE,
        "qiskit_settings": {k: v for k, v in VF2_SETTINGS.items()},
        "true_model": {
            "snapshot_sha256": stored.snapshot.sha256,
            "choices": true_choices,
            "selection_seconds": selection_seconds,
        },
        "drift": {
            "strengths": config["drift"]["strengths"] if case["drift"] else [],
            "draws": int(config["drift"]["draws"]) if case["drift"] else 0,
            "seed_rule": DRIFT_SEED_RULE,
            "records": drift_records,
        },
        "decoder": DECODER_DESCRIPTION,
        "shots": {
            "shots_per_seed": shots_per_seed,
            "chunk_shots": CHUNK_SHOTS,
            "rounds": rounds_log,
        },
        "seeds": {
            "base_seeds": base_seeds,
            "evaluation_seed_rule": config["evaluation_seed_rule"],
            "selection_seeds": [],
            "drift_seeds": sorted({r["draw_seed"] for r in drift_records}),
        },
        "interaction_edges": [list(e) for e in sorted(interaction_edges(circuit))],
        "num_detectors": per_seed[(0, 0)].num_detectors,
        "placements": placements_out,
        "provenance": prov,
        "wall_clock_seconds": time.perf_counter() - start,
        "workers": workers,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{case_id}.json"
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _bench_source_sha256() -> str:
    digest = hashlib.sha256()
    for path in sorted((ROOT / "nac_bench").rglob("*.py")):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="v2 benchmark runner (SPEC_V2.md)")
    parser.add_argument("config", type=Path)
    parser.add_argument("--only", nargs="*", default=None, help="case ids to run")
    parser.add_argument("--out", type=Path, default=ROOT / "results_v2" / "runs")
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args(argv)
    _, dirty = git_state()
    if dirty and not args.allow_dirty:
        parser.error("uncommitted changes to code, data or config; commit them first")
    config = load_config(args.config)
    if importlib.metadata.version("qiskit") != config["qiskit_version"]:
        parser.error(f"qiskit=={config['qiskit_version']} is required (bench extra)")
    for case in cases(config):
        if args.only and case["case_id"] not in set(args.only):
            continue
        target = args.out / f"{case['case_id']}.json"
        if args.skip_existing and target.exists():
            continue
        t0 = time.perf_counter()
        path = run_case(case, config, workers=args.workers, out_dir=args.out)
        print(f"{case['case_id']}: {time.perf_counter() - t0:.1f}s -> {path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
