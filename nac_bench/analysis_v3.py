"""v3 held-out analysis: the v3-headline family, the claims and the secondary endpoints.

Usage::

    python -m nac_bench.analysis_v3 configs_v3/held_out.json \\
        [--runs results_v3/held_out/runs] [--out results_v3/held_out/analysis.json]

Written before any held-out result exists (SPEC_V3.md sections 3 and 7, T3-F2). From the run
files of ``nac_bench.run_v3`` it computes:

* **Comparisons.** Every score method's choice against every reference's choice in every
  case, from the main-stage counts pooled over the three base seeds (pilot counts are never
  read): the Newcombe 95% interval of P_L(choice) - P_L(reference), the verdict (*better*
  when its upper end is below 0, *worse* when its lower end is above 0, otherwise *tie*;
  the same placement is a tie) and the two-sided pooled two-proportion z-test.
* **The family.** ``v3-headline``: at the headline setting, the decoder-graph score against
  the naive placement, lowest summed error, Qiskit's default at level 1 and mapomatic on
  circuits A, B, D5 and D7 of every held-out device, and the scaled score against the same
  four on the d=5 surface code of the square-lattice devices, 72 comparisons. A case with no
  valid placement leaves it, and so does a comparison whose reference has no SWAP-free
  placement (``DECISIONS_V3.md`` P2-20); the family shrinks and the record names what left.
  Holm's step-down correction at alpha = 0.05; a comparison *holds after Holm* when its
  adjusted p-value is at most alpha and its verdict is not a tie.
* **Claims** per layout and reference (section 7's rules) and, for the surface code, per
  device and reference.
* **Secondary endpoints** (pre-registered, outside the family): each method's cross-fitted
  regret against the best simulated placement (*best found* on the square lattice at d=5 and
  d=7, a lower bound, K15, with the random sample's size, mean P_L and its own cross-fitted
  best beside it, R3-R4), and Spearman's rho between each score and simulated P_L.
* **Exploratory**: the same comparisons at reset 0 (Holm over the same 72, labelled
  exploratory), levels 2 and 3, v2's VF2Layout baseline, the v1 score, the fast search's rank
  and P_L against the exhaustive choice (R3-C8) and, where every placement is simulated, each
  choice against the all-placement average.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from nac_bench import stats_v3
from noise_aware_compilation.stats import (
    Interval,
    difference_from_mean,
    newcombe_difference,
    ratio_interval,
    spearman,
    wilson,
)

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = "noise-aware-compilation.held-out-analysis-v3/1"
#: score methods compared against every reference, by case kind (headline method first)
SCORE_METHODS = {
    "repetition": ("decoder-graph", "v1", "fast-search"),
    "surface": ("scaled", "exact-decoder-graph"),
}
REFERENCES = {
    "repetition": (
        "naive",
        "lowest-summed-error",
        "qiskit-default-l1",
        "mapomatic",
        "qiskit-default-l2",
        "qiskit-default-l3",
        "qiskit-vf2",
    ),
    "surface": (
        "naive",
        "lowest-summed-error",
        "qiskit-default-l1",
        "mapomatic",
        "qiskit-default-l2",
        "qiskit-default-l3",
    ),
}
HEADLINE_METHOD = {"repetition": "decoder-graph", "surface": "scaled"}
SCORE_KEYS = {
    "repetition": ("decoder_graph", "v1", "summed_error", "mapomatic_cost"),
    "surface": ("scaled", "exact_decoder_graph", "summed_error"),
}


def _iv(i: Interval) -> dict[str, float]:
    return {"estimate": i.estimate, "low": i.low, "high": i.high}


@dataclass
class Case:
    """One held-out run file: its main-stage counts and each method's simulated row."""

    case_id: str
    device: str
    lattice: str
    synthetic: bool
    setting: str
    label: str
    kind: str
    every_placement: bool
    per_seed: list[list[tuple[int, int]]]  # per simulated row, per base seed
    scores: dict[str, list[float | None]]
    picks: dict[str, int | None]  # method -> row in ``per_seed``
    unavailable: dict[str, str]  # method -> why it has no SWAP-free placement
    random_rows: list[int]
    raw: dict[str, Any]

    def counts(self, row: int) -> tuple[int, int]:
        return sum(c[0] for c in self.per_seed[row]), sum(c[1] for c in self.per_seed[row])


def _unavailable(entry: dict[str, Any]) -> str | None:
    if entry.get("index") is None:
        detail = entry.get("detail") or {}
        return str(detail.get("failure") or "no valid placement chosen")
    detail = entry.get("detail") or {}
    if detail.get("failure"):
        return str(detail["failure"])
    if detail.get("swap_free") is False:
        return "not SWAP-free"
    return None


def load_case(path: Path) -> Case | None:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("no_valid_placement"):
        return None
    case = raw["case"]
    rows = raw["simulated"]
    position = {row["index"]: k for k, row in enumerate(rows)}
    per_seed = [[(s["logical_failures"], s["shots"]) for s in row["per_seed"]] for row in rows]
    picks: dict[str, int | None] = {}
    unavailable: dict[str, str] = {}
    for name, entry in raw["methods"].items():
        why = _unavailable(entry)
        if why is not None:
            picks[name] = None
            unavailable[name] = why
        else:
            picks[name] = position[entry["index"]]
    kind = case["kind"]
    scores = {key: [row["scores"].get(key) for row in rows] for key in SCORE_KEYS[kind]}
    random_rows = [position[i] for i in (raw.get("simulated_set") or {}).get("random_sample") or []]
    return Case(
        case_id=case["case_id"],
        device=case["device"],
        lattice=case["lattice"],
        synthetic=bool(case["synthetic_calibration"]),
        setting=case["setting"],
        label=case["circuit"]["label"],
        kind=kind,
        every_placement=bool(raw["simulated_set"]["every_placement"]),
        per_seed=per_seed,
        scores=scores,
        picks=picks,
        unavailable=unavailable,
        random_rows=random_rows,
        raw=raw,
    )


def compare(case: Case, method: str, reference: str) -> dict[str, Any]:
    i, j = case.picks[method], case.picks.get(reference)
    row: dict[str, Any] = {
        "case_id": case.case_id,
        "device": case.device,
        "lattice": case.lattice,
        "synthetic_calibration": case.synthetic,
        "setting": case.setting,
        "label": case.label,
        "kind": case.kind,
        "method": method,
        "reference": reference,
        "reference_available": j is not None,
    }
    if i is None:
        raise ValueError(f"{case.case_id}: {method} has no placement")
    fi, ni = case.counts(i)
    row["chosen"] = _iv(wilson(fi, ni))
    if reference == "average":
        counts = [case.counts(k) for k in range(len(case.per_seed))]
        absolute = difference_from_mean(counts, i)
        row.update(
            {
                "same_placement": False,
                "absolute": _iv(absolute),
                "verdict": stats_v3.difference_verdict(absolute),
                "p_value": None,
            }
        )
        return row
    if j is None:
        row["reason"] = case.unavailable.get(reference, "missing")
        return row
    fj, nj = case.counts(j)
    same = i == j
    absolute = Interval(0.0, 0.0, 0.0) if same else newcombe_difference(fi, ni, fj, nj)
    row.update(
        {
            "same_placement": same,
            "reference_rate": _iv(wilson(fj, nj)),
            "absolute": _iv(absolute),
            "relative": _iv(Interval(0.0, 0.0, 0.0) if same else ratio_interval(fi, ni, fj, nj)),
            "verdict": stats_v3.TIE if same else stats_v3.difference_verdict(absolute),
            "p_value": 1.0 if same else stats_v3.two_proportion_p(fi, ni, fj, nj),
            "seed_verdicts": [
                stats_v3.TIE
                if same
                else stats_v3.difference_verdict(newcombe_difference(a[0], a[1], b[0], b[1]))
                for a, b in zip(case.per_seed[i], case.per_seed[j], strict=True)
            ],
        }
    )
    return row


def comparisons(cases: Sequence[Case]) -> list[dict[str, Any]]:
    rows = []
    for case in cases:
        for method in SCORE_METHODS[case.kind]:
            if case.picks.get(method) is None:
                continue
            for reference in REFERENCES[case.kind]:
                rows.append(compare(case, method, reference))
            if case.every_placement:
                rows.append(compare(case, method, "average"))
        if case.kind == "repetition" and case.picks.get("fast-search") is not None:
            rows.append(compare(case, "fast-search", "decoder-graph"))
    return rows


def family(
    rows: Sequence[dict[str, Any]],
    config: dict[str, Any],
    setting: str,
    missing_cases: Sequence[str],
) -> dict[str, Any]:
    """Holm over the headline comparisons at ``setting``; what left the family is named."""

    references = list(config["headline_references"])
    wanted = []
    for device, info in config["devices"].items():
        for label in info["circuits"]:
            kind = config["circuits"][label]["kind"]
            for reference in references:
                wanted.append((f"v3-{setting}-{device}-{label}", HEADLINE_METHOD[kind], reference))
    by_key = {(r["case_id"], r["method"], r["reference"]): r for r in rows}
    members, left = [], []
    for key in wanted:
        r = by_key.get(key)
        if r is None:
            reason = "no valid placement" if key[0] in missing_cases else "no run file"
            left.append(
                {"case_id": key[0], "method": key[1], "reference": key[2], "reason": reason}
            )
        elif not r["reference_available"]:
            left.append(
                {"case_id": key[0], "method": key[1], "reference": key[2], "reason": r["reason"]}
            )
        else:
            members.append(r)
    alpha = float(config["alpha"])
    result = stats_v3.holm([r["p_value"] for r in members], alpha)
    out = []
    for r, adjusted, rejected in zip(members, result.adjusted, result.rejected, strict=True):
        holds = bool(rejected) and r["verdict"] != stats_v3.TIE
        out.append(
            {
                **{k: r[k] for k in ("case_id", "device", "lattice", "label", "kind")},
                **{k: r[k] for k in ("method", "reference", "same_placement", "verdict")},
                "absolute": r["absolute"],
                "relative": r["relative"],
                "p_value": r["p_value"],
                "holm_p": adjusted,
                "holds_after_holm": holds,
                "after_holm": r["verdict"] if holds else stats_v3.TIE,
            }
        )
    return {
        "setting": setting,
        "alpha": alpha,
        "planned_size": len(wanted),
        "size": len(out),
        "left_the_family": left,
        "members": out,
    }


def claims(fam: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    """Section 7's wording, per layout and reference, and per surface device and reference."""

    rules = config["claim_rules"]
    out = []
    lattices = sorted({info["lattice"] for info in config["devices"].values()})
    members = fam["members"]
    for lattice in lattices:
        for reference in config["headline_references"]:
            rows = [
                m
                for m in members
                if m["lattice"] == lattice
                and m["reference"] == reference
                and m["kind"] == "repetition"
            ]
            better = [m["case_id"] for m in rows if m["after_holm"] == stats_v3.BETTER]
            worse = [m["case_id"] for m in rows if m["after_holm"] == stats_v3.WORSE]
            if worse:
                wording = f"loses to {reference} on this layout"
            elif len(better) >= int(rules["beats_min_cases"]):
                wording = f"beats {reference} on this layout"
            else:
                wording = f"no difference shown against {reference}"
            out.append(
                {
                    "scope": "layout",
                    "lattice": lattice,
                    "method": HEADLINE_METHOD["repetition"],
                    "reference": reference,
                    "cases_in_family": len(rows),
                    "better_after_holm": better,
                    "worse_after_holm": worse,
                    "claim": wording,
                }
            )
    for m in members:
        if m["kind"] != "surface":
            continue
        if m["after_holm"] == stats_v3.BETTER:
            wording = f"beats {m['reference']}"
        elif m["after_holm"] == stats_v3.WORSE:
            wording = f"loses to {m['reference']}"
        else:
            wording = f"no difference shown against {m['reference']}"
        out.append(
            {
                "scope": "surface device",
                "device": m["device"],
                "method": m["method"],
                "reference": m["reference"],
                "claim": wording,
            }
        )
    return out


def _mean_rate(case: Case, rows: Sequence[int]) -> float:
    return statistics.fmean(case.counts(k)[0] / case.counts(k)[1] for k in rows)


def secondary(case: Case) -> dict[str, Any]:
    """Cross-fitted regret of every method's choice, and each score's rho with P_L."""

    fit = stats_v3.cross_fit(case.per_seed)
    regrets = {}
    for method, k in case.picks.items():
        if k is None:
            continue
        r = stats_v3.cross_fit_regret(case.per_seed, fit, k)
        regrets[method] = {
            "absolute": _iv(r.absolute),
            "relative": _iv(r.relative),
            "verdict": r.verdict,
        }
    pooled = [case.counts(k)[0] / case.counts(k)[1] for k in range(len(case.per_seed))]
    rho = {}
    for key, values in case.scores.items():
        pairs = [(v, p) for v, p in zip(values, pooled, strict=True) if v is not None]
        finite = [(v, p) for v, p in pairs if not math.isnan(v)]
        rho[key] = spearman([v for v, _ in finite], [p for _, p in finite]) if finite else None
    out: dict[str, Any] = {
        "case_id": case.case_id,
        "simulated": len(case.per_seed),
        "best_is": "best simulated placement" if case.every_placement else "best found",
        "cross_fit": {
            "folds": [asdict(f) for f in fit.folds],
            "best": _iv(fit.best),
            "worst": _iv(fit.worst),
            "headroom": _iv(fit.headroom),
            "headroom_separated": fit.separated,
            "oracle_bias": fit.oracle_bias,
        },
        "regret": regrets,
        "spearman_rho_with_p_l": rho,
        "spearman_over": "every placement" if case.every_placement else "the best-found set",
    }
    if case.random_rows:
        sample = [case.per_seed[k] for k in case.random_rows]
        sample_fit = stats_v3.cross_fit(sample)
        out["random_sample"] = {
            "size": len(sample),
            "mean_p_l": _mean_rate(case, case.random_rows),
            "cross_fitted_best": _iv(sample_fit.best),
        }
    raw_fast = case.raw["methods"].get("fast-search")
    if raw_fast is not None and raw_fast.get("index") is not None:
        out["fast_search"] = {
            "ranks": raw_fast.get("ranks"),
            "evaluations": (raw_fast.get("detail") or {}).get("evaluations"),
            "same_as_exhaustive": raw_fast["index"]
            == case.raw["methods"]["decoder-graph"]["index"],
        }
    return out


def analyse(config: dict[str, Any], runs_dir: Path) -> dict[str, Any]:
    cases: list[Case] = []
    missing: list[str] = []
    files = {}
    for path in sorted(runs_dir.glob("v3-*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw["config_file_sha256"] != config["_config_sha256"]:
            raise RuntimeError(f"{path.name} was run with another config")
        files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        case = load_case(path)
        if case is None:
            missing.append(raw["case"]["case_id"])
        else:
            cases.append(case)
    rows = comparisons(cases)
    headline = family(rows, config, config["headline_setting"], missing)
    others = [s for s in config["settings_order"] if s != config["headline_setting"]]
    return {
        "schema": SCHEMA,
        "config_file_sha256": config["_config_sha256"],
        "run_files_sha256": files,
        "verdict_rule": stats_v3.VERDICT_RULE,
        "cases_without_valid_placement": missing,
        "comparisons": rows,
        "families": {
            config["family"]: headline,
            **{
                f"{config['family']} at reset {s} (exploratory)": family(rows, config, s, missing)
                for s in others
            },
        },
        "claims": claims(headline, config),
        "secondary": [secondary(c) for c in cases],
    }


def load_config(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    data["_config_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return data


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("config", type=Path)
    parser.add_argument("--runs", type=Path, default=ROOT / "results_v3" / "held_out" / "runs")
    parser.add_argument(
        "--out", type=Path, default=ROOT / "results_v3" / "held_out" / "analysis.json"
    )
    args = parser.parse_args(argv)
    result = analyse(load_config(args.config), args.runs)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    fam = result["families"][next(iter(result["families"]))]
    print(f"{fam['size']} of {fam['planned_size']} comparisons in the family", flush=True)
    for claim in result["claims"]:
        print(claim.get("lattice") or claim.get("device"), claim["claim"], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
