"""v3 reset run: the v2 cases with reset error = readout error, and A1/A5 at both settings.

Usage::

    python -m nac_bench.run_reset configs_v3/reset_sensitivity.json [--only CASE_ID ...]

Every v1 and v2 headline used reset error 0, because IBM publishes none. v1's
T-L1 variant (REPORT.md section 10) set each qubit's reset flip probability to
its readout flip probability and turned three FakeHanoiV2 d=3 comparisons of
the v1 score against the fixed placement from gains or ties into losses; v2
never ran it. This runner does, for every case in the config:

1. builds the noise model of the case's setting: the converted snapshot as
   stored (``zero``) or with :func:`reset_equals_readout` applied (``readout``);
2. records every method's choice under that model, the same model for every
   method: the v1 and decoder-graph scores, lowest summed error, v2's Qiskit
   VF2Layout baseline, and Qiskit's default transpile (levels 1 to 3) and
   mapomatic on the IBM-shaped Target, whose ``reset`` carries the readout
   error in the ``readout`` setting (``baselines_v3.native_target``); Qiskit's
   default on the Target as IBM publishes it (no reset error) is recorded as
   context;
3. measures every valid placement's P_L under that model on the config's base
   seeds with v2's decoder and precision rules;
4. writes ``results_v3/reset/runs/<case_id>.json`` in v2's run format, so
   ``nac_bench.analysis_v2.load`` reads it.

The config, its seeds and its Holm family were committed before any count of
this run existed (``configs_v3/reset_sensitivity.json``).
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

from nac_bench.baselines_v3 import mapomatic_version, qiskit_default, run_baselines
from nac_bench.baselines_v3 import native_target as published_target
from nac_bench.machine import machine
from nac_bench.qiskit_layout import VF2_SETTINGS, qiskit_circuit, qiskit_version
from nac_bench.run_v2 import METHOD_DESCRIPTIONS, METHODS, RESULTS_SCHEMA, choose_all
from noise_aware_compilation.benchmark import _evaluate_task, _pooled, provenance
from noise_aware_compilation.circuits import repetition_code
from noise_aware_compilation.conditions import reset_equals_readout
from noise_aware_compilation.devices import StoredDevice, load_device
from noise_aware_compilation.evaluation import CHUNK_SHOTS, DECODER_DESCRIPTION, Counts, derive_seed
from noise_aware_compilation.placement import (
    TIE_BREAK_RULE,
    Placement,
    enumerate_placements,
    interaction_edges,
)
from noise_aware_compilation.stats import wilson

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = {"zero": "none", "readout": "readout"}  # setting -> native Target reset error
SEED_TAG = "eval-v3-reset"


def load_config(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("config must be a JSON object")
    data["_config_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return data


def cases(config: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for entry in config["runs"]:
        setting = entry["setting"]
        if setting not in SETTINGS:
            raise ValueError(f"unknown reset setting {setting!r}")
        for label in entry["circuits"]:
            out.append(
                {
                    "case_id": f"v3-reset-{setting}-{entry['device']}-{label}",
                    "device": entry["device"],
                    "role": entry["role"],
                    "setting": setting,
                    "drift": False,
                    "circuit": {"label": label, **config["circuits"][label]},
                }
            )
    return out


def git_state() -> tuple[str | None, bool | None]:
    """HEAD and whether tracked inputs differ from it (v3 results excluded)."""

    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", ".", ":!results_v3"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None, None
    return commit, bool(status)


def eval_chunk_seed(base_seed: int, case_id: str, mapping: Sequence[int], chunk: int) -> int:
    return derive_seed(SEED_TAG, base_seed, case_id, ",".join(map(str, mapping)), chunk)


def noise_for(setting: str, stored: StoredDevice) -> nove.HardwareNoiseModel:
    if setting == "zero":
        return stored.noise
    return reset_equals_readout(stored.noise, stored.device)


def baselines(
    circuit: nove.Circuit,
    stored: StoredDevice,
    candidates: Sequence[Placement],
    setting: str,
    seed_transpiler: int,
) -> dict[str, Any]:
    """Qiskit's default (levels 1-3) and mapomatic on the setting's Target, plus context."""

    record = run_baselines(
        circuit,
        stored.snapshot,
        stored.device,
        candidates,
        seed_transpiler,
        (),
        reset_error=SETTINGS[setting],
    )
    for name in record:
        record[name]["target_reset_error"] = SETTINGS[setting]
    # Context: Qiskit's default on the Target as IBM publishes it (reset without an error).
    qc = qiskit_circuit(circuit)
    edges = sorted(interaction_edges(circuit))
    c = qiskit_default(
        qc,
        published_target(stored.snapshot, stored.device),
        1,
        seed_transpiler,
        edges,
        set(candidates),
    )
    record["qiskit-default-l1-published"] = {
        "mapping": None if c.mapping is None else list(c.mapping),
        "swap_free": c.swap_free,
        "layout_pass": c.layout_pass,
        "failure": c.failure,
        "seed_transpiler": seed_transpiler,
        "target_reset_error": "none",
        "selection_seconds": c.seconds,
    }
    return record


def run_case(
    case: dict[str, Any],
    config: dict[str, Any],
    *,
    workers: int,
    out_dir: Path,
    machine_label: str | None,
) -> Path:
    start = time.perf_counter()
    stored = load_device(case["device"])
    spec = case["circuit"]
    circuit = repetition_code(spec["distance"], spec["rounds"], spec["basis"])
    device = stored.device
    noise = noise_for(case["setting"], stored)
    candidates = enumerate_placements(circuit, device)
    fixed = candidates[0]
    committed = tuple(config["fixed_baselines"][f"{case['device']}-{spec['label']}"])
    if committed != fixed:
        raise RuntimeError(f"fixed baseline for {case['case_id']} differs from the committed one")
    if qiskit_version() != config["qiskit_version"]:
        raise RuntimeError(f"qiskit {config['qiskit_version']} required, found {qiskit_version()}")
    if mapomatic_version() != config["mapomatic_version"]:
        raise RuntimeError(f"mapomatic {config['mapomatic_version']} required")

    t = time.perf_counter()
    true_choices = choose_all(circuit, device, noise, candidates, with_scores=True)
    selection_seconds = time.perf_counter() - t
    t = time.perf_counter()
    step6b = baselines(circuit, stored, candidates, case["setting"], int(config["seed_transpiler"]))
    baseline_seconds = time.perf_counter() - t

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
    t = time.perf_counter()
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
    simulation_seconds = time.perf_counter() - t

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
            "mapomatic_version": mapomatic_version(),
            "nac_bench_source_sha256": _bench_source_sha256(),
            "true_snapshot_sha256": stored.snapshot.sha256,
            "machine": machine(machine_label, workers=workers),
            "stage_seconds": {
                "selection": selection_seconds,
                "baselines": baseline_seconds,
                "simulation": simulation_seconds,
            },
        }
    )
    payload = {
        "schema": RESULTS_SCHEMA,
        "case": case,
        "setting": case["setting"],
        "setting_description": config["settings"][case["setting"]],
        "config_file_sha256": config["_config_sha256"],
        "fixed_mapping": list(fixed),
        "methods": {m: METHOD_DESCRIPTIONS[m] for m in METHODS},
        "tie_break_rule": TIE_BREAK_RULE,
        "qiskit_settings": {k: v for k, v in VF2_SETTINGS.items()},
        "true_model": {
            "snapshot_sha256": stored.snapshot.sha256,
            "noise_model_sha256": prov["noise_model_sha256"],
            "choices": true_choices,
            "selection_seconds": selection_seconds,
        },
        "baselines": step6b,
        "drift": {"strengths": [], "draws": 0, "seed_rule": None, "records": []},
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
            "drift_seeds": [],
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
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("config", type=Path)
    parser.add_argument("--only", nargs="*", default=None, help="case ids to run")
    parser.add_argument("--out", type=Path, default=ROOT / "results_v3" / "reset" / "runs")
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
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
    for case in cases(config):
        if args.only and case["case_id"] not in set(args.only):
            continue
        target = args.out / f"{case['case_id']}.json"
        if args.skip_existing and target.exists():
            continue
        t0 = time.perf_counter()
        path = run_case(
            case, config, workers=args.workers, out_dir=args.out, machine_label=args.machine_label
        )
        print(f"{case['case_id']}: {time.perf_counter() - t0:.1f}s -> {path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
