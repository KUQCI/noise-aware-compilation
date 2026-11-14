"""Choose the scaled score's grid step and k on development data (SPEC_V3.md R3-S2, T3-S1).

Usage::

    python -m nac_bench.scaled_calibration --workers 4

On every candidate placement of the 10 v2 cases (FakeJakartaV2, FakeHanoiV2 and FakeAlgiers,
all spent in v2), under v2's noise models, it compares the scaled score with v2's exact
decoder-graph score as recorded in ``results_v2/runs``:

1. **Grid step.** With the chains fixed to the exact score's own minimum-length cycles, so
   that only the subset sum is replaced by the dynamic program, the step is the largest of
   ``STEPS`` for which the scaled score is within 1% of the exact score on every candidate.
2. **k.** With that step, the smallest ``k`` of ``KS`` for which, on all 10 cases, the
   scaled score's top choice (the package's tie rule) equals the exact score's and
   Spearman's rho between the two over all candidates is at least 0.99. If none does, the
   ``k`` with the largest minimum rho, and the record says the criterion failed.

Every (step, k) pair's agreement is recorded (T3-S1). The exact scores are recomputed and
checked against the recorded ones. As context only (no rule uses it), each score's
Spearman's rho against the placements' simulated P_L in the v2 run files is recorded too.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

from nac_bench.machine import machine
from nac_bench.run_reset import git_state
from noise_aware_compilation.circuits import repetition_code
from noise_aware_compilation.decoder_graph import (
    cycle_failure_probability,
    logical_cycles,
    matching_graph,
)
from noise_aware_compilation.devices import load_device
from noise_aware_compilation.placement import _rounded, approximate_dem, lowered_circuit
from noise_aware_compilation.scaled_score import chain_failure_probability, lightest_logical_chains

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "results_v2" / "runs"
STEPS = (0.5, 0.2, 0.1, 0.05, 0.02, 0.01)
KS = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000)
STEP_TOLERANCE = 0.01
RHO_THRESHOLD = 0.99


def _task(args: tuple[str, dict[str, Any], tuple[int, ...]]) -> dict[str, Any]:
    device_name, spec, mapping = args
    stored = load_device(device_name)
    circuit = repetition_code(spec["distance"], spec["rounds"], spec["basis"])
    t = time.perf_counter()
    graph = matching_graph(
        approximate_dem(
            lowered_circuit(circuit, stored.device, stored.noise, mapping), decompose=True
        )
    )
    lowering = time.perf_counter() - t
    cycles = logical_cycles(graph)
    cycle_p = [[graph.edges[e] for e in c] for c in cycles]
    exact = math.fsum(
        [graph.undetectable_logical, *(cycle_failure_probability(p) for p in cycle_p)]
    )
    on_exact_cycles = {
        str(step): math.fsum(
            [graph.undetectable_logical, *(chain_failure_probability(p, step) for p in cycle_p)]
        )
        for step in STEPS
    }
    t = time.perf_counter()
    chains = lightest_logical_chains(graph, max(KS))
    search = time.perf_counter() - t
    per_chain = {
        str(step): [chain_failure_probability([graph.edges[e] for e in c], step) for c in chains]
        for step in STEPS
    }
    scaled = {
        step: {str(k): math.fsum([graph.undetectable_logical, *terms[:k]]) for k in KS}
        for step, terms in per_chain.items()
    }
    return {
        "exact": exact,
        "on_exact_cycles": on_exact_cycles,
        "scaled": scaled,
        "chains_found": len(chains),
        "min_length_cycles": len(cycles),
        "seconds_lowering": lowering,
        "seconds_chain_search": search,
    }


def spearman(a: Sequence[float], b: Sequence[float]) -> float:
    """Spearman's rho with average ranks for ties."""

    def ranks(x: Sequence[float]) -> np.ndarray:
        values = np.asarray(x, dtype=float)
        order = np.argsort(values, kind="stable")
        r = np.empty(len(values))
        r[order] = np.arange(len(values), dtype=float)
        for v in np.unique(values):
            tied = values == v
            r[tied] = r[tied].mean()
        return r

    ra, rb = ranks(a), ranks(b)
    if ra.std() == 0 or rb.std() == 0:
        return 1.0 if np.array_equal(ra, rb) else 0.0
    return float(np.corrcoef(ra, rb)[0, 1])


def _top(scores: Sequence[float], mappings: Sequence[tuple[int, ...]]) -> tuple[int, ...]:
    return min(zip(scores, mappings, strict=True), key=lambda x: (_rounded(x[0]), x[1]))[1]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--machine-label", default=None)
    parser.add_argument(
        "--out", type=Path, default=ROOT / "results_v3" / "scaled" / "calibration.json"
    )
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args(argv)
    commit, dirty = git_state()
    if dirty and not args.allow_dirty:
        parser.error("uncommitted changes to code, data or config; commit them first")
    start = time.perf_counter()
    cases: dict[str, Any] = {}
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for path in sorted(RUNS.glob("v2-*.json")):
            run = json.loads(path.read_text(encoding="utf-8"))
            spec, device = run["case"]["circuit"], run["case"]["device"]
            mappings = [tuple(p["mapping"]) for p in run["placements"]]
            recorded = run["true_model"]["choices"]["decoder-graph"]["candidate_scores"]
            rows = list(pool.map(_task, [(device, spec, m) for m in mappings]))
            if any(r["exact"] != s for r, s in zip(rows, recorded, strict=True)):
                raise RuntimeError(f"{path.name}: recomputed exact scores differ from v2's")
            exact_top = _top(recorded, mappings)
            assert list(exact_top) == run["true_model"]["choices"]["decoder-graph"]["mapping"]
            step_error = {
                str(step): max(abs(r["on_exact_cycles"][str(step)] / r["exact"] - 1) for r in rows)
                for step in STEPS
            }
            agreement: dict[str, Any] = {}
            simulated = [
                sum(s["logical_failures"] for s in p["per_seed"])
                / sum(s["shots"] for s in p["per_seed"])
                for p in run["placements"]
            ]
            for step in STEPS:
                for k in KS:
                    scores = [r["scaled"][str(step)][str(k)] for r in rows]
                    rho = spearman(scores, recorded)
                    agreement[f"{step}|{k}"] = {
                        "rho": rho,
                        "same_top": _top(scores, mappings) == exact_top,
                        "rho_vs_simulated": spearman(scores, simulated),
                    }
            cases[run["case"]["case_id"]] = {
                "placements": len(rows),
                "max_relative_error_on_exact_cycles": step_error,
                "agreement": agreement,
                "exact_rho_vs_simulated": spearman(recorded, simulated),
                "min_chains_found": min(r["chains_found"] for r in rows),
                "seconds_lowering_mean": sum(r["seconds_lowering"] for r in rows) / len(rows),
                "seconds_chain_search_mean": sum(r["seconds_chain_search"] for r in rows)
                / len(rows),
            }
            print(f"{path.name}: {len(rows)} placements", flush=True)

    passing_steps = [
        s
        for s in STEPS
        if all(
            c["max_relative_error_on_exact_cycles"][str(s)] <= STEP_TOLERANCE
            for c in cases.values()
        )
    ]
    step = max(passing_steps) if passing_steps else min(STEPS)
    per_k = {}
    for k in KS:
        rows = [c["agreement"][f"{step}|{k}"] for c in cases.values()]
        per_k[str(k)] = {
            "min_rho": min(r["rho"] for r in rows),
            "all_same_top": all(r["same_top"] for r in rows),
            "cases_same_top": sum(r["same_top"] for r in rows),
            "mean_rho_vs_simulated": sum(r["rho_vs_simulated"] for r in rows) / len(rows),
        }
    meets = [
        k for k in KS if per_k[str(k)]["all_same_top"] and per_k[str(k)]["min_rho"] >= RHO_THRESHOLD
    ]
    if meets:
        k_chosen, criterion_met = min(meets), True
    else:
        k_chosen = max(KS, key=lambda k: (per_k[str(k)]["min_rho"], -k))
        criterion_met = False
    payload = {
        "schema": "noise-aware-compilation.scaled-calibration-v3/1",
        "description": __doc__,
        "steps": list(STEPS),
        "ks": list(KS),
        "step_tolerance": STEP_TOLERANCE,
        "rho_threshold": RHO_THRESHOLD,
        "chosen": {"step": step, "k": k_chosen, "criterion_met": criterion_met},
        "steps_within_tolerance": passing_steps,
        "per_k_at_chosen_step": per_k,
        "cases": cases,
        "package_git_commit": commit,
        "package_git_dirty": dirty,
        "machine": machine(args.machine_label, workers=args.workers),
        "wall_clock_seconds": time.perf_counter() - start,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"chosen": payload["chosen"], "per_k": per_k}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
