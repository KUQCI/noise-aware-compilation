"""Re-analysis of the committed v1 and v2 counts (roadmap Steps 6 and 6b).

Usage::

    python -m nac_bench.reanalysis configs_v3/reanalysis.json [--machine-label NAME]

No placement is simulated. The script

1. runs the Step 6b baselines (Qiskit's default transpile at levels 1 to 3 and
   mapomatic) on each v2 case's converted snapshot and looks up the chosen
   placement's committed counts;
2. re-grades every v1 and v2 comparison with the difference rule
   (:data:`nac_bench.stats_v3.VERDICT_RULE`) and lists every verdict that changes;
3. applies Holm's correction to the families named in the config;
4. replaces the in-sample oracle (best placement picked and measured on the same
   data) with the cross-fitted one;
5. reports drift's retained gain in absolute terms, with M6 only where stable;
6. writes ``results_v3/reanalysis/{baselines,reanalysis}.json`` and
   ``REANALYSIS.md``.

Everything here was written after the v1 and v2 results were seen, so every
family is labelled a re-analysis, not a pre-registered test.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import subprocess
import time
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nac_bench import stats_v3
from nac_bench.analysis_v2 import CaseResult, compare_average, compare_pair
from nac_bench.analysis_v2 import load as load_v2
from nac_bench.machine import machine
from noise_aware_compilation.analysis import (
    CaseSummary,
    _average,
    _compare_average,
    _compare_fixed,
    _seed_view,
    load_summaries,
)
from noise_aware_compilation.circuits import repetition_code
from noise_aware_compilation.devices import load_device
from noise_aware_compilation.evaluation import derive_seed
from noise_aware_compilation.placement import enumerate_placements
from noise_aware_compilation.stats import (
    Z95,
    Interval,
    difference_from_mean,
    newcombe_difference,
    spearman,
)

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = "noise-aware-compilation.reanalysis-v3/1"
V2_METHODS = ("v1", "decoder-graph", "lowest-summed-error", "qiskit-vf2")
NEW_METHODS = ("qiskit-default-l1", "qiskit-default-l2", "qiskit-default-l3", "mapomatic")
METHODS = V2_METHODS + NEW_METHODS
SCORES = ("v1", "decoder-graph")


def _iv(i: Interval) -> dict[str, float]:
    return {"estimate": i.estimate, "low": i.low, "high": i.high}


def _p_from_interval(i: Interval) -> float:
    """Two-sided normal p-value for an interval centred near its estimate."""

    se = (i.high - i.low) / (2 * Z95)
    if se <= 0:
        return 1.0
    return math.erfc(abs(i.estimate / se) / math.sqrt(2))


def git_state() -> tuple[str | None, bool | None]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", ".", ":!results_v3", ":!REANALYSIS.md"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None, None
    return commit, bool(status)


# --- Step 6b selections ----------------------------------------------------------------------


def run_step6b(
    cases: dict[str, CaseResult], v2_config: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    from nac_bench.baselines_v3 import mapomatic_version, run_baselines

    out: dict[str, Any] = {}
    for case_id, case in cases.items():
        stored = load_device(case.device)
        spec = v2_config["circuits"][case.label]
        circuit = repetition_code(spec["distance"], spec["rounds"], spec["basis"])
        candidates = enumerate_placements(circuit, stored.device)
        committed = [p.mapping for p in case.placements]
        if [tuple(c) for c in candidates] != committed:
            raise RuntimeError(f"{case_id}: candidate order differs from the committed run")
        start = time.perf_counter()
        record = run_baselines(
            circuit,
            stored.snapshot,
            stored.device,
            candidates,
            int(config["seed_transpiler"]),
            [int(s) for s in config["seed_sweep"]],
        )
        record["_seconds"] = time.perf_counter() - start
        record["_snapshot_sha256"] = stored.snapshot.sha256
        record["_fake_backend_target"] = fake_target_context(case.device, circuit, stored, config)
        out[case_id] = record
    return {
        "qiskit_version": importlib.metadata.version("qiskit"),
        "mapomatic_version": mapomatic_version(),
        "cases": out,
    }


FAKE_BACKENDS = {"jakarta": "FakeJakartaV2", "hanoi": "FakeHanoiV2", "algiers": "FakeAlgiers"}


def fake_target_context(
    device: str, circuit: Any, stored: Any, config: dict[str, Any]
) -> dict[str, Any] | None:
    """Context only: Qiskit's default on the fake backend's own Target.

    That Target still lists the couplers the conversion removed (one direction at
    CX error 1.0), so Qiskit can pick a placement through one in its working
    direction; such a placement is outside the candidate set and has no counts.
    """

    try:
        from qiskit_ibm_runtime import fake_provider
    except ImportError:
        return None
    from nac_bench.baselines_v3 import LEVELS, qiskit_default
    from nac_bench.qiskit_layout import qiskit_circuit
    from noise_aware_compilation.placement import interaction_edges

    backend = getattr(fake_provider, FAKE_BACKENDS[device])()
    removed = {
        frozenset(int(q) for q in c["pair"])
        for c in stored.device.metadata.get("removed_couplers", [])
    }
    qc = qiskit_circuit(circuit)
    edges = sorted(interaction_edges(circuit))
    candidates = set(enumerate_placements(circuit, stored.device))
    out: dict[str, Any] = {"backend": FAKE_BACKENDS[device]}
    for level in LEVELS:
        c = qiskit_default(
            qc, backend.target, level, int(config["seed_transpiler"]), edges, candidates
        )
        used = (
            []
            if c.mapping is None
            else sorted(
                sorted((c.mapping[a], c.mapping[b]))
                for a, b in edges
                if frozenset((c.mapping[a], c.mapping[b])) in removed
            )
        )
        out[f"qiskit-default-l{level}"] = {
            "mapping": None if c.mapping is None else list(c.mapping),
            "layout_pass": c.layout_pass,
            "failure": c.failure,
            "removed_couplers_used": used,
        }
    return out


# --- comparisons -----------------------------------------------------------------------------


def _comparison_record(
    case: CaseResult, method: str, reference: str, i: int, j: int | None
) -> dict[str, Any]:
    ps = case.placements
    if reference == "average":
        comp = compare_average(ps, i)
        p_value = _p_from_interval(comp.absolute)
        seeds_new = []
        for s in range(len(ps[0].per_seed)):
            view = [p.per_seed[s] for p in ps]
            seeds_new.append(stats_v3.difference_verdict(difference_from_mean(view, i)))
    else:
        assert j is not None
        comp = compare_pair(ps, i, j, reference)
        a, b = ps[i], ps[j]
        p_value = (
            1.0 if i == j else stats_v3.two_proportion_p(a.failures, a.shots, b.failures, b.shots)
        )
        seeds_new = [
            stats_v3.TIE
            if i == j
            else stats_v3.difference_verdict(newcombe_difference(fa, na, fb, nb))
            for (fa, na), (fb, nb) in zip(a.per_seed, b.per_seed, strict=True)
        ]
    return {
        "version": "v2",
        "case_id": case.case_id,
        "device": case.device,
        "role": case.role,
        "label": case.label,
        "distance": case.distance,
        "variant": "base",
        "method": method,
        "reference": reference,
        "same_placement": reference != "average" and i == j,
        "chosen": _iv(comp.chosen),
        "reference_rate": _iv(comp.reference_interval),
        "absolute": _iv(comp.absolute),
        "relative": _iv(comp.relative),
        "old_verdict": comp.verdict,
        "old_seed_verdicts": list(comp.seed_verdicts),
        "verdict": stats_v3.difference_verdict(comp.absolute),
        "seed_verdicts": seeds_new,
        "p_value": p_value,
    }


def v2_comparisons(
    cases: dict[str, CaseResult], choices: dict[str, dict[str, int | None]]
) -> list[dict[str, Any]]:
    rows = []
    for case_id, case in cases.items():
        pick = choices[case_id]
        for method in METHODS:
            i = pick[method]
            if i is None:
                continue
            rows.append(_comparison_record(case, method, "fixed", i, case.fixed_index))
            if method in SCORES:
                rows.append(_comparison_record(case, method, "average", i, None))
                for baseline in METHODS[2:]:
                    j = pick[baseline]
                    if j is not None:
                        rows.append(_comparison_record(case, method, baseline, i, j))
    return rows


def v1_comparisons(summaries: dict[str, CaseSummary]) -> list[dict[str, Any]]:
    rows = []
    for case_id, s in summaries.items():
        case = s.case
        rounds = int(case["circuit"]["rounds"])
        for name, comp in (("fixed", s.vs_fixed), ("average", s.vs_average)):
            seeds_new = []
            for seed in range(len(s.placements[0].per_seed)):
                view = [_seed_view(p, seed, rounds, s.num_detectors) for p in s.placements]
                if name == "fixed":
                    c = _compare_fixed(view[s.chosen_index], view[s.fixed_index], s.metric)
                    seeds_new.append(stats_v3.difference_verdict(c.absolute))
                else:
                    avg, _ = _average(view, s.metric)
                    c = _compare_average(view, s.chosen_index, s.metric, avg)
                    seeds_new.append(stats_v3.difference_verdict(c.absolute))
            same = name == "fixed" and s.chosen_index == s.fixed_index
            if name == "fixed" and s.metric == "P_L" and not same:
                a, b = s.placements[s.chosen_index], s.placements[s.fixed_index]
                p_value = stats_v3.two_proportion_p(a.failures, a.shots, b.failures, b.shots)
            elif same:
                p_value = 1.0
            else:
                p_value = _p_from_interval(comp.absolute)
            rows.append(
                {
                    "version": "v1",
                    "case_id": case_id,
                    "device": case["device"],
                    "role": "development",
                    "label": case["circuit"]["label"],
                    "distance": int(case["circuit"]["distance"]),
                    "condition": case["condition"],
                    "variant": case["variant"],
                    "metric": s.metric,
                    "method": "v1",
                    "reference": name,
                    "same_placement": same,
                    "chosen": _iv(comp.chosen),
                    "reference_rate": _iv(comp.reference_interval),
                    "absolute": _iv(comp.absolute),
                    "relative": _iv(comp.relative),
                    "old_verdict": comp.verdict,
                    "old_seed_verdicts": s.seed_verdicts(f"vs_{name}"),
                    "verdict": stats_v3.difference_verdict(comp.absolute),
                    "seed_verdicts": seeds_new,
                    "p_value": p_value,
                }
            )
    return rows


def _old_as_new(old: str) -> str:
    return stats_v3.TIE if old == "indistinguishable" else old


def changed_verdicts(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if _old_as_new(r["old_verdict"]) != r["verdict"]]


# --- Holm families ---------------------------------------------------------------------------


def _in_family(row: dict[str, Any], family: dict[str, Any]) -> bool:
    return all(row.get(key) in allowed for key, allowed in family["select"].items())


def holm_families(rows: Sequence[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    out = {}
    alpha = float(config["alpha"])
    for name, family in config["families"].items():
        members = [r for r in rows if _in_family(r, family)]
        expected = int(family["size"])
        if len(members) != expected:
            raise RuntimeError(f"family {name}: {len(members)} comparisons, config says {expected}")
        result = stats_v3.holm([r["p_value"] for r in members], alpha)
        out[name] = {
            "description": family["description"],
            "alpha": alpha,
            "size": len(members),
            "members": [
                {
                    "case_id": r["case_id"],
                    "role": r["role"],
                    "method": r["method"],
                    "reference": r["reference"],
                    "same_placement": r["same_placement"],
                    "relative": r["relative"],
                    "verdict": r["verdict"],
                    "p_value": r["p_value"],
                    "holm_p": adj,
                    "holds_after_holm": bool(rej) and r["verdict"] != stats_v3.TIE,
                }
                for r, adj, rej in zip(members, result.adjusted, result.rejected, strict=True)
            ],
        }
    return out


# --- cross-fitted oracle ---------------------------------------------------------------------


def _fit_record(
    per_seed: list[list[tuple[int, int]]], picks: dict[str, int | None]
) -> dict[str, Any]:
    fit = stats_v3.cross_fit(per_seed)
    regrets = {}
    for method, i in picks.items():
        if i is None:
            continue
        r = stats_v3.cross_fit_regret(per_seed, fit, i)
        pooled = [sum(c[0] for c in p) / sum(c[1] for c in p) for p in per_seed]
        in_sample = (pooled[i] - min(pooled)) / min(pooled)
        regrets[method] = {
            "absolute": _iv(r.absolute),
            "relative": _iv(r.relative),
            "verdict": r.verdict,
            "in_sample_relative": in_sample,
        }
    return {
        "folds": [asdict(f) for f in fit.folds],
        "best": _iv(fit.best),
        "worst": _iv(fit.worst),
        "in_sample_best": fit.in_sample_best,
        "in_sample_worst": fit.in_sample_worst,
        "oracle_bias": fit.oracle_bias,
        "headroom": _iv(fit.headroom),
        "headroom_separated": fit.separated,
        "regret": regrets,
    }


def cross_fits_v2(
    cases: dict[str, CaseResult], choices: dict[str, dict[str, int | None]]
) -> dict[str, Any]:
    return {
        cid: _fit_record([list(p.per_seed) for p in c.placements], choices[cid])
        for cid, c in cases.items()
    }


def cross_fits_v1(summaries: dict[str, CaseSummary]) -> dict[str, Any]:
    out = {}
    for cid, s in summaries.items():
        if s.metric != "P_L" or s.case["condition"] == "N0":
            continue
        per_seed = [list(p.per_seed) for p in s.placements]
        out[cid] = _fit_record(per_seed, {"v1": s.chosen_index, "fixed": s.fixed_index})
    return out


# --- drift -----------------------------------------------------------------------------------


def drift_v2(cases: dict[str, CaseResult], config: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    replicates = int(config["bootstrap"]["replicates"])
    seed = int(config["bootstrap"]["seed"])
    for cid, case in cases.items():
        raw = case.raw
        if not raw["case"]["drift"]:
            continue
        ps = case.placements
        index = {p.mapping: i for i, p in enumerate(ps)}
        failures = [p.failures for p in ps]
        shots = [p.shots for p in ps]
        for delta in raw["drift"]["strengths"]:
            records = [r for r in raw["drift"]["records"] if r["delta"] == delta]
            for method in V2_METHODS:
                true = case.choices[method]
                picked = [
                    index[tuple(r["choices"][method]["mapping"])]
                    for r in records
                    if r["choices"][method]["mapping"] is not None
                    and not r["choices"][method].get("failure")
                ]
                if true is None or not picked:
                    continue
                g = stats_v3.retained_gain(
                    failures,
                    shots,
                    case.fixed_index,
                    true,
                    picked,
                    replicates,
                    derive_seed("reanalysis-m6", seed, cid, method, f"{float(delta):.2f}"),
                )
                rows.append(
                    {
                        "case_id": cid,
                        "device": case.device,
                        "label": case.label,
                        "method": method,
                        "delta": float(delta),
                        "draws": len(records),
                        "gain": _iv(g.gain),
                        "stable": g.stable,
                        "retained": _iv(g.retained),
                        "choice_rate": _iv(g.choice_rate),
                        "m6": None if g.ratio is None else _iv(g.ratio),
                        "v2_m6": next(
                            (
                                _iv(d.m6)
                                for d in case.drift
                                if d.method == method and d.delta == float(delta)
                            ),
                            None,
                        ),
                    }
                )
    return rows


# --- score quality (M4) for the new baselines -------------------------------------------------


def mapomatic_m4(cases: dict[str, CaseResult], step6b: dict[str, Any]) -> dict[str, float]:
    out = {}
    for cid, case in cases.items():
        costs = step6b["cases"][cid]["mapomatic"]["candidate_costs"]
        if costs is None:
            continue
        out[cid] = spearman([float(c) for c in costs], [p.estimate for p in case.placements])
    return out


# --- main ------------------------------------------------------------------------------------


def choices_with_new(
    cases: dict[str, CaseResult], step6b: dict[str, Any]
) -> dict[str, dict[str, int | None]]:
    out: dict[str, dict[str, int | None]] = {}
    for cid, case in cases.items():
        index = {p.mapping: i for i, p in enumerate(case.placements)}
        picks: dict[str, int | None] = dict(case.choices)
        for method in NEW_METHODS:
            rec = step6b["cases"][cid][method]
            mapping = rec["mapping"]
            picks[method] = None if rec["failure"] or mapping is None else index[tuple(mapping)]
        out[cid] = picks
    return out


def tied_mean(case: CaseResult, tied: Sequence[Sequence[int]]) -> float:
    index = {p.mapping: i for i, p in enumerate(case.placements)}
    rates = [case.placements[index[tuple(t)]].estimate for t in tied]
    return sum(rates) / len(rates)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("config", type=Path)
    parser.add_argument("--machine-label", default=None)
    parser.add_argument("--out", type=Path, default=ROOT / "results_v3" / "reanalysis")
    args = parser.parse_args(argv)

    started = time.perf_counter()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    config_sha = hashlib.sha256(args.config.read_bytes()).hexdigest()
    v2_config = json.loads((ROOT / config["v2_config"]).read_text(encoding="utf-8"))
    timings: dict[str, float] = {}

    t = time.perf_counter()
    cases = load_v2(ROOT / config["v2_runs"], v2_config)
    summaries = load_summaries(ROOT / config["v1_runs"])
    timings["load"] = time.perf_counter() - t

    t = time.perf_counter()
    step6b = run_step6b(cases, v2_config, config)
    timings["step6b_selection"] = time.perf_counter() - t

    t = time.perf_counter()
    choices = choices_with_new(cases, step6b)
    rows = v1_comparisons(summaries) + v2_comparisons(cases, choices)
    families = holm_families(rows, config)
    fits = {"v1": cross_fits_v1(summaries), "v2": cross_fits_v2(cases, choices)}
    timings["comparisons"] = time.perf_counter() - t

    t = time.perf_counter()
    drift = drift_v2(cases, config)
    timings["drift_bootstrap"] = time.perf_counter() - t

    commit, dirty = git_state()
    provenance = {
        "package_git_commit": commit,
        "package_git_dirty": dirty,
        "config_sha256": config_sha,
        "written_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "machine": machine(args.machine_label, workers=1),
        "versions": {
            name: importlib.metadata.version(name)
            for name in ("qiskit", "mapomatic", "numpy", "stim", "pymatching")
        },
        "stage_seconds": timings,
    }
    choice_table = {
        cid: {
            m: (None if i is None else list(cases[cid].placements[i].mapping))
            for m, i in picks.items()
        }
        for cid, picks in choices.items()
    }
    result = {
        "schema": SCHEMA,
        "status": config["status"],
        "verdict_rule": stats_v3.VERDICT_RULE,
        "m6_rule": stats_v3.STABILITY_RULE,
        "choices": choice_table,
        "mapomatic_tied_best_mean": {
            cid: tied_mean(cases[cid], step6b["cases"][cid]["mapomatic"]["tied_best"])
            for cid in cases
        },
        "mapomatic_m4": mapomatic_m4(cases, step6b),
        "comparisons": rows,
        "changed_verdicts": changed_verdicts(rows),
        "holm": families,
        "cross_fit": fits,
        "drift": drift,
        "provenance": provenance,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    step6b["provenance"] = provenance
    (args.out / "baselines.json").write_text(json.dumps(step6b, indent=1) + "\n", encoding="utf-8")
    timings["total"] = time.perf_counter() - started
    (args.out / "reanalysis.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")

    from nac_bench.report_reanalysis import write_report

    path = write_report(result, step6b, args.out / "REANALYSIS.md")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
