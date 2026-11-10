"""v3 held-out analysis (SPEC_V3.md revision 5): the family, the claims and the endpoints.

Usage::

    python -m nac_bench.analysis_v3 configs_v3/held_out.json \\
        [--runs results_v3/held_out/runs] [--out results_v3/held_out/analysis.json]

Written before any held-out result exists (SPEC_V3.md sections 3 and 7, T3-F2). From the run
files of ``nac_bench.run_v3`` it computes:

* **Comparisons.** Every method's choice against every reference's choice in every case,
  from the main-stage counts pooled over the three base seeds (pilot counts never enter a
  rate): the Newcombe 95% interval of P_L(choice) - P_L(reference), its verdict (*better*
  when its upper end is below 0, *worse* when its lower end is above 0, otherwise *tie*; the
  same placement is a tie) and the two-sided pooled two-proportion z-test.
* **The family** ``v3-headline``, at the headline setting on the confirmatory devices: the
  decoder-graph score against the naive placement, lowest summed error, Qiskit's default at
  level 2 and mapomatic on circuits A, B, D5 and D7, and the scaled score against the same
  four on the d=5 surface code of the confirmatory square-lattice device, 52 comparisons. A
  case with no valid placement leaves it, and so does a comparison whose reference has no
  SWAP-free placement (``DECISIONS_V3.md`` P2-20); the record names what left. Holm's
  step-down correction at alpha = 0.05. A member is *better after Holm* when Holm rejects it
  and its estimated difference is below zero, *worse after Holm* when Holm rejects it and
  the difference is above zero, otherwise *tie*.
* **Claims** per layout and reference (section 7's four rows, with each layout's devices
  named and the threshold *t* = floor(n / 2) + 1 checked against the config) and, for the
  surface code, per device and reference.
* **The isolation family** ``v3-decoder-graph-isolation`` (secondary, pre-registered,
  revision 5): on the same cases, the headline score against the idle-aware summed error and
  (repetition codes) the v1 score, with its own Holm correction and the same claim rules. It
  answers whether the decoder graph helps beyond counting all the noise the simulator has.
* **Secondary endpoints**: each method's cross-fitted regret (*best found* on the square
  lattice at d=5 and d=7, a lower bound, K15, with the random sample beside it, R3-R4),
  Spearman's rho between each score and simulated P_L, the all-placement mean P_L, each
  choice's percentile among the simulated placements, and every P_L per round.
* **Exploratory**: the same comparisons at reset 0 (Holm over the same plan, labelled
  exploratory); the seen and synthetic devices (Newcombe verdicts and unadjusted p-values);
  levels 1 and 3, v2's VF2Layout baseline, the fast search (R3-C8), *pilot-best* (the
  simulated placement with the fewest pilot failures, graded on main-stage counts only), the
  surface-code score at the sensitivity *k* values, and the family re-evaluated with
  correlated matching and, for D5 and D7, in a memory of *d* rounds (R3-C10).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
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
SCHEMA = "noise-aware-compilation.held-out-analysis-v3/2"
SPEC_REVISION = 5
CONFIRMATORY = "confirmatory"
PILOT_BEST = "pilot-best"
#: methods compared against every reference, by case kind (headline method first); the
#: surface code adds ``scaled-k<j>`` for each sensitivity *k* of the config
SCORE_METHODS = {
    "repetition": ("decoder-graph", "v1", "fast-search", PILOT_BEST),
    "surface": ("scaled", "exact-decoder-graph", PILOT_BEST),
}
REFERENCES = {
    "repetition": (
        "naive",
        "lowest-summed-error",
        "qiskit-default-l2",
        "mapomatic",
        "idle-aware-summed-error",
        "v1",
        "qiskit-default-l1",
        "qiskit-default-l3",
        "qiskit-vf2",
    ),
    "surface": (
        "naive",
        "lowest-summed-error",
        "qiskit-default-l2",
        "mapomatic",
        "idle-aware-summed-error",
        "qiskit-default-l1",
        "qiskit-default-l3",
    ),
}
HEADLINE_METHOD = {"repetition": "decoder-graph", "surface": "scaled"}
SCORE_KEYS = {
    "repetition": (
        "decoder_graph",
        "v1",
        "summed_error",
        "idle_aware_summed_error",
        "mapomatic_cost",
    ),
    "surface": ("scaled", "exact_decoder_graph", "summed_error", "idle_aware_summed_error"),
}
LATTICE_NAMES = {"heavy-hex": "heavy-hex", "square": "square lattice"}
DEVICE_NAMES = {
    "boston": "FakeBoston",
    "pittsburgh": "FakePittsburgh",
    "berlin": "FakeBerlin",
    "kingston": "FakeKingston",
    "aachen": "FakeAachen",
    "nighthawk": "FakeNighthawk",
}


def _iv(i: Interval) -> dict[str, float]:
    return {"estimate": i.estimate, "low": i.low, "high": i.high}


@dataclass
class Case:
    """One held-out run file: its main-stage counts and each method's simulated row."""

    case_id: str
    device: str
    lattice: str
    role: str
    synthetic: bool
    setting: str
    label: str
    kind: str
    rounds: int
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


def pilot_best(rows: Sequence[dict[str, Any]]) -> int:
    """Row of the simulated placement with the lowest pilot failure rate (ties: first row).

    The pilot runs on its own seed, so choosing by it and grading on the base seeds' main
    stage keeps selection and grading apart (T3-C5): pilot counts select, never grade.
    """

    def rate(k: int) -> float:
        pilot = rows[k]["pilot"]
        return float(pilot["logical_failures"]) / float(pilot["shots"])

    return min(range(len(rows)), key=lambda k: (rate(k), k))


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
    if rows and all("pilot" in row for row in rows):
        picks[PILOT_BEST] = pilot_best(rows)
    kind = case["kind"]
    keys = list(SCORE_KEYS[kind])
    if kind == "surface":
        keys += sorted(k for k in rows[0]["scores"] if k.startswith("scaled_k")) if rows else []
    scores = {key: [row["scores"].get(key) for row in rows] for key in keys}
    random_rows = [position[i] for i in (raw.get("simulated_set") or {}).get("random_sample") or []]
    return Case(
        case_id=case["case_id"],
        device=case["device"],
        lattice=case["lattice"],
        role=case.get("role", CONFIRMATORY),
        synthetic=bool(case["synthetic_calibration"]),
        setting=case["setting"],
        label=case["circuit"]["label"],
        kind=kind,
        rounds=int(case["circuit"].get("rounds") or 1),
        every_placement=bool(raw["simulated_set"]["every_placement"]),
        per_seed=per_seed,
        scores=scores,
        picks=picks,
        unavailable=unavailable,
        random_rows=random_rows,
        raw=raw,
    )


def per_round(p: float, rounds: int) -> float:
    """Logical error per round, 1 - (1 - P_L)^(1/rounds)."""

    return 1.0 - (1.0 - p) ** (1.0 / rounds) if p < 1 else 1.0


def compare(case: Case, method: str, reference: str) -> dict[str, Any]:
    i, j = case.picks[method], case.picks.get(reference)
    row: dict[str, Any] = {
        "case_id": case.case_id,
        "device": case.device,
        "lattice": case.lattice,
        "role": case.role,
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
    row["chosen_per_round"] = per_round(fi / ni, case.rounds)
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
            "reference_per_round": per_round(fj / nj, case.rounds),
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


def _methods(case: Case) -> list[str]:
    extra = [m for m in case.picks if m.startswith("scaled-k")] if case.kind == "surface" else []
    return [*SCORE_METHODS[case.kind], *sorted(extra)]


def comparisons(cases: Sequence[Case]) -> list[dict[str, Any]]:
    rows = []
    for case in cases:
        for method in _methods(case):
            if case.picks.get(method) is None:
                continue
            for reference in REFERENCES[case.kind]:
                if reference != method and reference in case.picks:
                    rows.append(compare(case, method, reference))
            if case.every_placement:
                rows.append(compare(case, method, "average"))
        headline = HEADLINE_METHOD[case.kind]
        others = ["fast-search", PILOT_BEST] + [m for m in case.picks if m.startswith("scaled-k")]
        for method in others:
            if case.picks.get(method) is not None and case.picks.get(headline) is not None:
                rows.append(compare(case, method, headline))
    return rows


def planned(
    config: dict[str, Any], setting: str, references: dict[str, Sequence[str]]
) -> list[tuple[str, str, str]]:
    """(case id, method, reference) of every planned member at ``setting``."""

    wanted = []
    for device, info in config["devices"].items():
        if info["role"] != CONFIRMATORY:
            continue
        for label in info["circuits"]:
            kind = config["circuits"][label]["kind"]
            for reference in references[kind]:
                wanted.append((f"v3-{setting}-{device}-{label}", HEADLINE_METHOD[kind], reference))
    return wanted


def after_holm(rejected: bool, estimate: float) -> str:
    """Section 7: *better* / *worse after Holm* by Holm's rejection and the estimate's sign."""

    if rejected and estimate < 0:
        return stats_v3.BETTER
    if rejected and estimate > 0:
        return stats_v3.WORSE
    return stats_v3.TIE


def family(
    rows: Sequence[dict[str, Any]],
    config: dict[str, Any],
    setting: str,
    missing_cases: Sequence[str],
    references: dict[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Holm over the planned members at ``setting``; what left the family is named."""

    if references is None:
        references = {k: list(config["headline_references"]) for k in ("repetition", "surface")}
    wanted = planned(config, setting, references)
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
        out.append(
            {
                **{k: r[k] for k in ("case_id", "device", "lattice", "label", "kind")},
                **{k: r[k] for k in ("method", "reference", "same_placement", "verdict")},
                "absolute": r["absolute"],
                "relative": r["relative"],
                "p_value": r["p_value"],
                "holm_p": adjusted,
                "holm_rejects": bool(rejected),
                "after_holm": after_holm(bool(rejected), r["absolute"]["estimate"]),
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


def check_claim_rules(config: dict[str, Any]) -> None:
    """Each layout's devices are the confirmatory devices of that lattice, and its threshold
    is floor(n / 2) + 1 for its n planned repetition cases (section 7)."""

    layouts = config["claim_rules"]["layouts"]
    for lattice, rule in layouts.items():
        devices = [
            d
            for d, info in config["devices"].items()
            if info["role"] == CONFIRMATORY and info["lattice"] == lattice
        ]
        if sorted(devices) != sorted(rule["devices"]):
            raise ValueError(f"{lattice}: claim devices differ from the confirmatory devices")
        n = sum(
            1
            for d in devices
            for label in config["devices"][d]["circuits"]
            if config["circuits"][label]["kind"] == "repetition"
        )
        if int(rule["repetition_cases"]) != n or int(rule["t"]) != n // 2 + 1:
            raise ValueError(f"{lattice}: t must be floor(n / 2) + 1 for its {n} cases")


def _where(devices: Sequence[str], lattice: str) -> str:
    names = [DEVICE_NAMES.get(d, d) for d in devices]
    joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"on {joined} ({LATTICE_NAMES.get(lattice, lattice)}), under this noise model"


def layout_claim(
    reference: str, better: Sequence[str], worse: Sequence[str], t: int, where: str
) -> str:
    """Section 7's four rows."""

    if better and worse:
        named = ", ".join(worse)
        return (
            f"mixed against {reference} {where}: better in {len(better)} cases, "
            f"worse in {len(worse)} ({named})"
        )
    if worse:
        return f"loses to {reference} {where} ({', '.join(worse)})"
    if len(better) >= t:
        return f"beats {reference} {where}"
    return f"no difference shown against {reference} {where}"


def claims(fam: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    """Section 7's wording, per layout and reference, and per surface device and reference."""

    check_claim_rules(config)
    out = []
    members = fam["members"]
    references = sorted({m["reference"] for m in members}, key=_reference_order(config))
    for lattice, rule in config["claim_rules"]["layouts"].items():
        where = _where(rule["devices"], lattice)
        for reference in references:
            rows = [
                m
                for m in members
                if m["lattice"] == lattice
                and m["reference"] == reference
                and m["kind"] == "repetition"
            ]
            better = [m["case_id"] for m in rows if m["after_holm"] == stats_v3.BETTER]
            worse = [m["case_id"] for m in rows if m["after_holm"] == stats_v3.WORSE]
            out.append(
                {
                    "scope": "layout",
                    "lattice": lattice,
                    "devices": list(rule["devices"]),
                    "method": HEADLINE_METHOD["repetition"],
                    "reference": reference,
                    "t": int(rule["t"]),
                    "cases_in_family": len(rows),
                    "better_after_holm": better,
                    "worse_after_holm": worse,
                    "claim": layout_claim(reference, better, worse, int(rule["t"]), where),
                }
            )
    for m in members:
        if m["kind"] != "surface":
            continue
        where = _where([m["device"]], m["lattice"])
        if m["after_holm"] == stats_v3.BETTER:
            wording = f"beats {m['reference']} {where}"
        elif m["after_holm"] == stats_v3.WORSE:
            wording = f"loses to {m['reference']} {where}"
        else:
            wording = f"no difference shown against {m['reference']} {where}"
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


def _reference_order(config: dict[str, Any]) -> Any:
    order = [*config["headline_references"]]
    for refs in config["isolation_references"].values():
        order += [r for r in refs if r not in order]
    return lambda r: order.index(r) if r in order else len(order)


def _mean_rate(case: Case, rows: Sequence[int]) -> float:
    return statistics.fmean(case.counts(k)[0] / case.counts(k)[1] for k in rows)


def percentile(case: Case, row: int) -> float:
    """Share (in %) of the simulated placements with a lower pooled P_L than ``row``'s."""

    rates = [case.counts(k)[0] / case.counts(k)[1] for k in range(len(case.per_seed))]
    return 100.0 * sum(1 for r in rates if r < rates[row]) / len(rates)


def secondary(case: Case) -> dict[str, Any]:
    """Regret, rho, the all-placement mean and each choice's percentile and P_L per round."""

    fit = stats_v3.cross_fit(case.per_seed)
    regrets = {}
    context = {}
    for method, k in case.picks.items():
        if k is None:
            continue
        r = stats_v3.cross_fit_regret(case.per_seed, fit, k)
        regrets[method] = {
            "absolute": _iv(r.absolute),
            "relative": _iv(r.relative),
            "verdict": r.verdict,
        }
        f, n = case.counts(k)
        context[method] = {
            "p_l": f / n,
            "p_l_per_round": per_round(f / n, case.rounds),
            "percentile_among_simulated": percentile(case, k),
        }
    pooled = [case.counts(k)[0] / case.counts(k)[1] for k in range(len(case.per_seed))]
    rho = {}
    for key, values in case.scores.items():
        pairs = [(v, p) for v, p in zip(values, pooled, strict=True) if v is not None]
        finite = [(v, p) for v, p in pairs if not math.isnan(v)]
        rho[key] = spearman([v for v, _ in finite], [p for _, p in finite]) if finite else None
    out: dict[str, Any] = {
        "case_id": case.case_id,
        "role": case.role,
        "simulated": len(case.per_seed),
        "rounds": case.rounds,
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
        "choices": context,
        "percentile_over": "every placement" if case.every_placement else "the best-found set",
        "all_placement_mean_p_l": statistics.fmean(pooled) if case.every_placement else None,
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


# --- R3-C10 sensitivity ---------------------------------------------------------------------


def _variant(case: Case, part: str) -> tuple[Case, set[int]] | None:
    """``case`` with its family placements' counts replaced by a sensitivity run's."""

    sens = (case.raw.get("sensitivity") or {}).get(part)
    if not sens:
        return None
    position = {row["index"]: k for k, row in enumerate(case.raw["simulated"])}
    per_seed = [list(c) for c in case.per_seed]
    for row in sens["rows"]:
        per_seed[position[row["index"]]] = [
            (s["logical_failures"], s["shots"]) for s in row["per_seed"]
        ]
    rounds = int(sens.get("rounds") or case.rounds)
    family_rows = {position[i] for i in case.raw.get("family_placements") or []}
    return replace(case, per_seed=per_seed, rounds=rounds), family_rows


def sensitivity(
    cases: Sequence[Case],
    config: dict[str, Any],
    headline: dict[str, Any],
    missing: Sequence[str],
) -> dict[str, Any]:
    """The family re-evaluated under each sensitivity run (exploratory, R3-C10).

    A case without that run keeps its main-stage counts (A, B and the surface code already
    have *d* rounds). Holm runs over the same members; changes from the headline family are
    listed.
    """

    setting = config["headline_setting"]
    before = {(m["case_id"], m["reference"]): m["after_holm"] for m in headline["members"]}
    out: dict[str, Any] = {}
    for part in ("correlated_matching", "rounds_equal_distance"):
        variant_cases = []
        for case in cases:
            v = _variant(case, part)
            if v is None:
                variant_cases.append(case)
                continue
            vc, family_rows = v
            for name in [HEADLINE_METHOD[case.kind], *config["headline_references"]]:
                k = case.picks.get(name)
                if k is not None and k not in family_rows:
                    raise RuntimeError(f"{case.case_id}: {name}'s choice is not a family placement")
            variant_cases.append(vc)
        fam = family(comparisons(variant_cases), config, setting, missing)
        changed = [
            {
                "case_id": m["case_id"],
                "reference": m["reference"],
                "headline": before.get((m["case_id"], m["reference"])),
                "sensitivity": m["after_holm"],
            }
            for m in fam["members"]
            if before.get((m["case_id"], m["reference"])) != m["after_holm"]
        ]
        out[part] = {"family": fam, "claims": claims(fam, config), "changed": changed}
    return out


def analyse(config: dict[str, Any], runs_dir: Path) -> dict[str, Any]:
    if config.get("spec_revision") != SPEC_REVISION:
        raise ValueError(f"config is not for SPEC_V3.md revision {SPEC_REVISION}")
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
    setting = config["headline_setting"]
    headline = family(rows, config, setting, missing)
    isolation = family(rows, config, setting, missing, config["isolation_references"])
    others = [s for s in config["settings_order"] if s != setting]
    return {
        "schema": SCHEMA,
        "spec_revision": SPEC_REVISION,
        "config_file_sha256": config["_config_sha256"],
        "run_files_sha256": files,
        "verdict_rule": stats_v3.VERDICT_RULE,
        "cases_without_valid_placement": missing,
        "comparisons": rows,
        "families": {
            config["family"]: headline,
            config["isolation_family"]: isolation,
            **{
                f"{config['family']} at reset {s} (exploratory)": family(rows, config, s, missing)
                for s in others
            },
        },
        "claims": claims(headline, config),
        "isolation_claims": claims(isolation, config),
        "secondary": [secondary(c) for c in cases],
        "sensitivity": sensitivity(cases, config, headline, missing),
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
        print(claim["claim"], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
