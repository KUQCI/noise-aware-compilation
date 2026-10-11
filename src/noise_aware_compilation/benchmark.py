"""Benchmark runner (R-D7, R-D8): every reported number from a config file and seeds.

Usage::

    python -m noise_aware_compilation.benchmark configs/benchmark.json [--only CASE_ID ...]

For each case (device, circuit, noise condition) the runner

1. builds the noise model for the condition (no randomness),
2. selects a placement with :func:`place` (no sampling; no seeds, R-P4/R-F3),
3. evaluates *every* valid placement with the same circuit, noise model, shot
   count and decoder rule (R-F1), on three base seeds,
4. writes ``results/runs/<case_id>.json`` with raw counts, seeds, wall-clock
   time and the T-R3 provenance fields.

Shots (T-P4): every placement of a case gets the same shots per seed, starting
at ``min_shots_per_seed``. For conditions listed under ``precision_rule`` the
count is doubled for the whole case until every placement's pooled Wilson 95%
half-width is at most ``target_relative_halfwidth * P_L`` or
``max_shots_per_seed`` is reached. The rule is fixed in the config before any
run; no placement, seed or case is dropped or rerun because of its result (X7).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import nove
import stim

from noise_aware_compilation import __version__
from noise_aware_compilation.circuits import repetition_code
from noise_aware_compilation.conditions import (
    PlantedFault,
    noise_sha256,
    plant_fault,
    reset_equals_readout,
    restore_removed_couplers,
    uniform_median,
    with_cx_duration,
    zero_noise,
)
from noise_aware_compilation.devices import load_device
from noise_aware_compilation.evaluation import (
    CHUNK_SHOTS,
    DECODER_DESCRIPTION,
    Counts,
    PlacementEvaluator,
    derive_seed,
)
from noise_aware_compilation.placement import (
    SCORE_METHOD,
    TIE_BREAK_RULE,
    enumerate_placements,
    interaction_edges,
    place,
)
from noise_aware_compilation.stats import wilson

RESULTS_SCHEMA = "noise-aware-compilation.run/1"
SELECTION_SEEDS: tuple[int, ...] = ()  # selection is deterministic and uses no seeds


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    device: str
    circuit_label: str
    distance: int
    rounds: int
    basis: str
    condition: str  # N0, N1, N2, N3-qubit, N3-connection
    variant: str  # "base", "L1-cx-min", "L1-cx-max", "L1-reset-readout", "L1-coupler-better"

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "device": self.device,
            "circuit": {
                "label": self.circuit_label,
                "distance": self.distance,
                "rounds": self.rounds,
                "basis": self.basis,
            },
            "condition": self.condition,
            "variant": self.variant,
        }


def load_config(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("config must be a JSON object")
    return data


def expand_cases(config: dict[str, Any]) -> list[CaseSpec]:
    cases: list[CaseSpec] = []
    variants = config["sensitivity"]["variants"]
    for device_entry in config["devices"]:
        device = device_entry["name"]
        for circuit in device_entry["circuits"]:
            for condition in device_entry["conditions"]:
                names = ["base"]
                if condition in config["sensitivity"]["conditions"]:
                    names += variants
                for variant in names:
                    suffix = "" if variant == "base" else f"-{variant}"
                    cases.append(
                        CaseSpec(
                            case_id=f"{device}-{circuit['label']}-{condition}{suffix}",
                            device=device,
                            circuit_label=circuit["label"],
                            distance=int(circuit["distance"]),
                            rounds=int(circuit["rounds"]),
                            basis=str(circuit["basis"]),
                            condition=condition,
                            variant=variant,
                        )
                    )
    return cases


def build_case_models(
    spec: CaseSpec, config: dict[str, Any]
) -> tuple[nove.Circuit, nove.DeviceModel, nove.HardwareNoiseModel, PlantedFault | None]:
    stored = load_device(spec.device)
    device = stored.device
    real = stored.noise
    circuit = repetition_code(spec.distance, spec.rounds, spec.basis)  # type: ignore[arg-type]
    fault: PlantedFault | None = None
    if spec.condition == "N0":
        noise = zero_noise(real)
    elif spec.condition == "N1":
        noise = uniform_median(real, device)
    elif spec.condition == "N2":
        noise = real
    elif spec.condition in ("N3-qubit", "N3-connection"):
        planted = config["planted_faults"][spec.device]
        if spec.condition == "N3-qubit":
            fault = PlantedFault("qubit", qubit=int(planted["qubit"]))
        else:
            a, b = planted["connection"]
            fault = PlantedFault("connection", connection=(int(a), int(b)))
        noise = plant_fault(uniform_median(real, device), device, fault)
    else:
        raise ValueError(f"unknown condition {spec.condition!r}")
    if spec.variant == "L1-cx-min":
        device = with_cx_duration(device, float(device.metadata["cx_duration_min"]))
    elif spec.variant == "L1-cx-max":
        device = with_cx_duration(device, float(device.metadata["cx_duration_max"]))
    elif spec.variant == "L1-reset-readout":
        noise = reset_equals_readout(noise, device)
    elif spec.variant == "L1-coupler-better":
        device, noise = restore_removed_couplers(device, noise)
    elif spec.variant != "base":
        raise ValueError(f"unknown variant {spec.variant!r}")
    return circuit, device, noise, fault


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _package_source_sha256() -> str:
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def nove_commit() -> str | None:
    try:
        text = importlib.metadata.distribution("nove").read_text("direct_url.json")
    except importlib.metadata.PackageNotFoundError:
        return None
    if not text:
        return None
    info = json.loads(text)
    vcs = info.get("vcs_info", {})
    commit = vcs.get("commit_id")
    return str(commit) if commit else None


def package_git_state() -> tuple[str | None, bool | None]:
    """Commit of the checkout the package runs from, and whether its tracked inputs are dirty.

    Results and the generated report are excluded from the dirty check, since a
    benchmark run writes them. Returns ``(None, None)`` outside a git checkout.
    """

    root = Path(__file__).resolve().parent.parent.parent
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", ".", ":!results", ":!REPORT.md"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None, None
    return commit, bool(status)


def provenance(
    stored_snapshot: nove.CalibrationSnapshot,
    device: nove.DeviceModel,
    noise: nove.HardwareNoiseModel,
    circuit: nove.Circuit,
) -> dict[str, Any]:
    snap_prov = dict(stored_snapshot.to_dict()["provenance"])
    git_commit, git_dirty = package_git_state()
    try:
        qiskit_installed: str | None = importlib.metadata.version("qiskit-ibm-runtime")
    except importlib.metadata.PackageNotFoundError:
        qiskit_installed = None
    return {
        "nove_version": nove.__version__,
        "nove_commit": nove_commit(),
        "stim_version": stim.__version__,
        "pymatching_version": importlib.metadata.version("pymatching"),
        "numpy_version": importlib.metadata.version("numpy"),
        "qiskit_ibm_runtime_version": snap_prov.get("source_package_version"),
        "qiskit_ibm_runtime_installed_at_runtime": qiskit_installed,
        "package_version": __version__,
        "package_source_sha256": _package_source_sha256(),
        "package_git_commit": git_commit,
        "package_git_dirty": git_dirty,
        "written_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_files": {
            snap_prov["conf_file"]: snap_prov["conf_sha256"],
            snap_prov["props_file"]: snap_prov["props_sha256"],
        },
        "calibration_snapshot_sha256": stored_snapshot.sha256,
        "noise_model_sha256": noise_sha256(noise),
        "noise_model_calibration_hash": noise.calibration_hash,
        "device_sha256": device.sha256,
        "circuit_sha256": circuit.sha256,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
    }


def _evaluate_task(args: tuple[str, str, str, tuple[int, ...], int, list[int]]) -> dict[str, Any]:
    circuit_text, device_json, noise_json, mapping, shots, seeds = args
    circuit = nove.Circuit.from_stim_text(circuit_text)
    device = nove.DeviceModel.from_json(device_json)
    noise = nove.HardwareNoiseModel.from_dict(json.loads(noise_json))
    evaluator = PlacementEvaluator(circuit, device, noise, mapping)
    counts = [
        evaluator.run_chunk(min(CHUNK_SHOTS, shots - i * CHUNK_SHOTS), s)
        for i, s in enumerate(seeds)
    ]
    total = counts[0]
    for c in counts[1:]:
        total = total + c
    return total.to_dict()


def chunk_seed(base_seed: int, case_id: str, mapping: Sequence[int], chunk: int) -> int:
    return derive_seed("eval", base_seed, case_id, ",".join(map(str, mapping)), chunk)


def run_case(
    spec: CaseSpec,
    config: dict[str, Any],
    *,
    workers: int,
    out_dir: Path,
) -> Path:
    start = time.perf_counter()
    circuit, device, noise, fault = build_case_models(spec, config)
    stored = load_device(spec.device)

    selection_start = time.perf_counter()
    result = place(circuit, device, noise)
    selection_seconds = time.perf_counter() - selection_start
    mappings = [c.mapping for c in result.candidates]
    fixed = enumerate_placements(circuit, device)[0]
    edges = sorted(interaction_edges(circuit))

    committed = config.get("_fixed_baselines", {})
    base_key = f"{spec.device}-{spec.circuit_label}"
    committed_fixed = committed.get(f"{base_key}-{spec.variant}", committed.get(base_key))
    if committed_fixed is not None and tuple(committed_fixed) != fixed:
        raise RuntimeError(f"fixed baseline for {spec.case_id} differs from the committed one")
    if fault is not None and all(fault.contained_in(m, edges) for m in mappings):
        raise RuntimeError(f"planted fault in {spec.case_id} is unavoidable")

    base_seeds = [int(s) for s in config["base_seeds"]]
    rule = config["precision_rule"]
    adaptive = spec.condition in rule["conditions"]
    min_shots = int(config["min_shots_per_seed"])
    max_shots = int(config["max_shots_per_seed"])
    target = float(rule["target_relative_halfwidth"])

    circuit_text = circuit.to_stim_text()
    device_json = device.to_json()
    noise_json = json.dumps(noise.to_dict())
    per_seed: dict[tuple[int, int], Counts] = {}
    seeds_used: dict[tuple[int, int], list[int]] = {}
    shots_per_seed = 0
    next_shots = min_shots
    rounds_log: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        while True:
            first_chunk = shots_per_seed // CHUNK_SHOTS
            last_chunk = -(-next_shots // CHUNK_SHOTS)
            new_shots = next_shots - shots_per_seed
            tasks = []
            keys = []
            for mi, mapping in enumerate(mappings):
                for si, base in enumerate(base_seeds):
                    seeds = [
                        chunk_seed(base, spec.case_id, mapping, k)
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
            for mi in range(len(mappings)):
                pooled = _pooled(per_seed, mi, len(base_seeds))
                if pooled.logical_failures == 0:
                    worst = float("inf")
                    continue
                w = wilson(pooled.logical_failures, pooled.shots)
                rel = max(w.high - w.estimate, w.estimate - w.low) / w.estimate
                worst = max(worst, rel)
            rounds_log.append({"shots_per_seed": shots_per_seed, "worst_relative_halfwidth": worst})
            if not adaptive or worst <= target or shots_per_seed >= max_shots:
                break
            next_shots = min(2 * shots_per_seed, max_shots)

    placements_out = []
    for mi, cand in enumerate(result.candidates):
        placements_out.append(
            {
                "mapping": list(cand.mapping),
                "score": cand.score,
                "is_chosen": cand.mapping == result.mapping,
                "is_fixed": cand.mapping == fixed,
                "contains_fault": None
                if fault is None
                else fault.contained_in(cand.mapping, edges),
                "run_config": {
                    "circuit_sha256": circuit.sha256,
                    "device_sha256": device.sha256,
                    "noise_model_sha256": noise_sha256(noise),
                    "shots_per_seed": shots_per_seed,
                    "base_seeds": base_seeds,
                    "decoder_rule": DECODER_DESCRIPTION["construction"],
                    "backend": "stim",
                    "mapping": list(cand.mapping),
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

    wall = time.perf_counter() - start
    payload = {
        "schema": RESULTS_SCHEMA,
        "case": spec.to_dict(),
        "config_file_sha256": config.get("_config_sha256"),
        "planted_fault": None if fault is None else fault.to_dict(),
        "selection": {
            "chosen_mapping": list(result.mapping),
            "chosen_score": result.score,
            "fixed_mapping": list(fixed),
            "score_method": SCORE_METHOD,
            "tie_break_rule": TIE_BREAK_RULE,
            "selection_seeds": list(SELECTION_SEEDS),
            "num_candidates": len(result.candidates),
            "wall_clock_seconds": selection_seconds,
        },
        "decoder": DECODER_DESCRIPTION,
        "shots": {
            "shots_per_seed": shots_per_seed,
            "chunk_shots": CHUNK_SHOTS,
            "adaptive": adaptive,
            "rounds": rounds_log,
        },
        "seeds": {
            "base_seeds": base_seeds,
            "selection_seeds": list(SELECTION_SEEDS),
            "evaluation_seed_rule": (
                "sha256('eval|<base_seed>|<case_id>|<mapping>|<chunk>')[:8] & (2**63-1)"
            ),
        },
        "interaction_edges": [list(e) for e in edges],
        "num_detectors": per_seed[(0, 0)].num_detectors,
        "placements": placements_out,
        "provenance": provenance(stored.snapshot, device, noise, circuit),
        "wall_clock_seconds": wall,
        "workers": workers,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{spec.case_id}.json"
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _pooled(per_seed: dict[tuple[int, int], Counts], mi: int, num_seeds: int) -> Counts:
    total = per_seed[(mi, 0)]
    for si in range(1, num_seeds):
        total = total + per_seed[(mi, si)]
    return total


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("config", type=Path)
    parser.add_argument("--only", nargs="*", default=None, help="case ids to run")
    parser.add_argument("--conditions", nargs="*", default=None, help="conditions to run")
    parser.add_argument("--out", type=Path, default=Path("results/runs"))
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="run even when tracked code, data or config differ from the checked-out commit",
    )
    args = parser.parse_args(argv)
    _, dirty = package_git_state()
    if dirty and not args.allow_dirty:
        parser.error(
            "uncommitted changes to code, data or config; commit them first so every results "
            "file names the commit that produced it (or pass --allow-dirty)"
        )

    config = load_config(args.config)
    config["_config_sha256"] = hashlib.sha256(args.config.read_bytes()).hexdigest()
    baseline_file = config.get("fixed_baselines_file")
    if baseline_file:
        path = args.config.parent / baseline_file
        config["_fixed_baselines"] = json.loads(path.read_text(encoding="utf-8"))["fixed"]
    cases = expand_cases(config)
    if args.only:
        cases = [c for c in cases if c.case_id in set(args.only)]
    if args.conditions:
        cases = [c for c in cases if c.condition in set(args.conditions)]
    for spec in cases:
        target = args.out / f"{spec.case_id}.json"
        if args.skip_existing and target.exists():
            continue
        t0 = time.perf_counter()
        path = run_case(spec, config, workers=args.workers, out_dir=args.out)
        print(f"{spec.case_id}: {time.perf_counter() - t0:.1f}s -> {path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
