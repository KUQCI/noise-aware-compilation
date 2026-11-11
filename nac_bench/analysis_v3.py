"""v3 held-out analysis (SPEC_V3.md revision 6): the family, the claims and the endpoints.

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
  surface-code score at the sensitivity *k* values, and both families re-evaluated with
  correlated matching and, for D5 and D7, in a memory of *d* rounds (R3-C10).
* **Revision 6, exploratory**: the directional setting (R3-C13), with the headline
  setting's choices graded under it; the Qiskit level 2 seed sweep (R3-C14); both families
  under each grading variant (R3-C15); the detection-rate proxy (R3-C16); the surface code
  split into placement and schedule (R3-C17); and, for the surface code, the headroom and
  suppression checks of R3-R6.

Revision 6 also makes the analysis refuse what it cannot vouch for (``Checks``): a planned
case with no run file, a failed or unfinished run (a failed Nove check, R3-C4's
pre-registered outcome, takes its case out of the family instead), and a run whose
provenance does not trace to F4's frozen code on one machine per device.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from nac_bench import stats_v3
from nac_bench.run_reset import git_state
from noise_aware_compilation.stats import (
    Interval,
    difference_from_mean,
    newcombe_difference,
    ratio_interval,
    spearman,
    wilson,
)

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = "noise-aware-compilation.held-out-analysis-v3/3"
SPEC_REVISION = 6
FREEZE_F4 = ROOT / "configs_v3" / "freeze_f4.json"
LOCK_FILE = ROOT / "requirements-lock.txt"
NOVE_CHECK_STAGE = "nove_checks"
HEADLINE_SETTING_SUFFIX = "@headline-setting"
SWEEP_PREFIX = "qiskit-default-l2-seed"
CONFIRMATORY = "confirmatory"
PILOT_BEST = "pilot-best"
#: methods compared against every reference, by case kind (headline method first); the
#: surface code adds ``scaled-k<j>`` for each sensitivity *k* of the config
SCORE_METHODS = {
    "repetition": ("decoder-graph", "v1", "fast-search", PILOT_BEST),
    "surface": ("scaled", "exact-decoder-graph", "scaled-default-schedule", PILOT_BEST),
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
        "detection-rate",
    ),
    "surface": (
        "naive",
        "lowest-summed-error",
        "qiskit-default-l2",
        "mapomatic",
        "idle-aware-summed-error",
        "qiskit-default-l1",
        "qiskit-default-l3",
        "detection-rate",
        # R3-C17: the same embeddings under the scaled choice's schedule
        "naive@scaled-schedule",
        "lowest-summed-error@scaled-schedule",
        "qiskit-default-l2@scaled-schedule",
        "qiskit-default-l3@scaled-schedule",
        "mapomatic@scaled-schedule",
    ),
}
HEADLINE_METHOD = {"repetition": "decoder-graph", "surface": "scaled"}
SCORE_KEYS = {
    "repetition": (
        "decoder_graph",
        "v1",
        "summed_error",
        "idle_aware_summed_error",
        "detection_rate",
        "mapomatic_cost",
    ),
    "surface": (
        "scaled",
        "exact_decoder_graph",
        "summed_error",
        "idle_aware_summed_error",
        "detection_rate",
    ),
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


def _finite(x: float) -> float | None:
    return None if math.isnan(x) else x


def _iv(i: Interval) -> dict[str, float | None]:
    return {"estimate": _finite(i.estimate), "low": _finite(i.low), "high": _finite(i.high)}


@dataclass(frozen=True)
class Checks:
    """What :func:`analyse` refuses (revision 6). Both are on for the held-out analysis;
    tests on synthetic run files turn off what their files do not carry."""

    complete: bool = True  # every planned case has a finished run file
    provenance: bool = True  # F4 ancestry, clean tree, frozen code, lock, one machine


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
    distance: int = 0

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
    if raw.get("no_valid_placement") or raw.get("failed"):
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
    # R3-C14: each distinct level 2 choice of the seed sweep other than the main one
    level2 = raw["methods"].get("qiskit-default-l2") or {}
    distinct: list[int] = []
    for i in level2.get("seed_sweep_indices") or []:
        if i is not None and i != level2.get("index") and i not in distinct:
            distinct.append(i)
    for k, i in enumerate(distinct):
        picks[f"{SWEEP_PREFIX}{k}"] = position[i]
    # R3-C13: the headline setting's choices, graded under this setting
    graded = (raw.get("simulated_set") or {}).get("headline_setting_choices") or {}
    for name, i in (graded.get("choices") or {}).items():
        picks[f"{name}{HEADLINE_SETTING_SUFFIX}"] = position[i]
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
        distance=int(case["circuit"].get("distance") or 0),
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
    if PILOT_BEST in (method, reference) and not case.every_placement:
        row["note"] = "pilot-best chose within the best-found set (R3-C12)"
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
        others = ["fast-search", PILOT_BEST] + [
            m for m in case.picks if m.startswith(("scaled-k", SWEEP_PREFIX))
        ]
        if case.picks.get(headline) is not None:
            for method in others:
                if case.picks.get(method) is not None:
                    rows.append(compare(case, method, headline))
        # R3-C13: the headline setting's choices against each other, under this setting
        graded = headline + HEADLINE_SETTING_SUFFIX
        if case.picks.get(graded) is not None:
            for reference in REFERENCES[case.kind]:
                name = reference + HEADLINE_SETTING_SUFFIX
                if name in case.picks:
                    rows.append(compare(case, graded, name))
    return rows


def in_scope(config: dict[str, Any], setting: str, device: str, label: str) -> bool:
    """Whether the config runs ``label`` on ``device`` at ``setting`` (``setting_scope``)."""

    scope = (config.get("setting_scope") or {}).get(setting)
    return scope is None or (device in scope["devices"] and label in scope["circuits"])


def planned_cases(config: dict[str, Any]) -> list[str]:
    """Every case id the config runs (``run_v3.cases``, without importing the runner)."""

    return [
        f"v3-{setting}-{device}-{label}"
        for setting in config["settings_order"]
        for device, info in config["devices"].items()
        for label in info["circuits"]
        if in_scope(config, setting, device, label)
    ]


def planned(
    config: dict[str, Any],
    setting: str,
    references: dict[str, Sequence[str]],
    suffix: str = "",
) -> list[tuple[str, str, str]]:
    """(case id, method, reference) of every planned member at ``setting``; circuits marked
    ``"family": false`` (FakeBerlin's d=3 surface code, revision 6) are never members."""

    wanted = []
    for device, info in config["devices"].items():
        if info["role"] != CONFIRMATORY:
            continue
        for label in info["circuits"]:
            spec = config["circuits"][label]
            if spec.get("family", True) is False or not in_scope(config, setting, device, label):
                continue
            kind = spec["kind"]
            for reference in references[kind]:
                wanted.append(
                    (
                        f"v3-{setting}-{device}-{label}",
                        HEADLINE_METHOD[kind] + suffix,
                        reference + suffix,
                    )
                )
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
    missing_cases: dict[str, str] | Sequence[str],
    references: dict[str, Sequence[str]] | None = None,
    suffix: str = "",
) -> dict[str, Any]:
    """Holm over the planned members at ``setting``; what left the family is named.

    ``missing_cases`` maps each case without a result to why (no valid placement, or a
    failed Nove check); a planned case absent from it and from ``rows`` has no run file.
    """

    if references is None:
        references = {k: list(config["headline_references"]) for k in ("repetition", "surface")}
    if not isinstance(missing_cases, dict):
        missing_cases = {c: "no valid placement" for c in missing_cases}
    wanted = planned(config, setting, references, suffix)
    by_key = {(r["case_id"], r["method"], r["reference"]): r for r in rows}
    loaded = {r["case_id"] for r in rows}
    members, left = [], []
    for key in wanted:
        r = by_key.get(key)
        if r is None:
            if key[0] in missing_cases:
                reason = missing_cases[key[0]]
            elif key[0] in loaded:
                reason = f"the run file has no {key[1]} or no {key[2]} entry"
            else:
                reason = "no run file"
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
    reference: str,
    better: Sequence[str],
    worse: Sequence[str],
    t: int,
    where: str,
    n: int | None = None,
    beats: str = "beats",
) -> str:
    """Section 7's four rows. Revision 6: the last row says how many cases were better and
    that no layout claim is made, instead of "no difference shown"; ``beats`` is the
    isolation family's "the decoder graph helps beyond"."""

    if better and worse:
        named = ", ".join(worse)
        return (
            f"mixed against {reference} {where}: better in {len(better)} cases, "
            f"worse in {len(worse)} ({named})"
        )
    if worse:
        return f"loses to {reference} {where} ({', '.join(worse)})"
    if len(better) >= t:
        return f"{beats} {reference} {where}"
    of = "" if n is None else f" of {n}"
    return (
        f"better after Holm in {len(better)}{of} cases (fewer than t = {t}); "
        f"no layout claim against {reference} {where}"
    )


def per_device_counts(
    rows: Sequence[dict[str, Any]], devices: Sequence[str]
) -> dict[str, dict[str, int]]:
    """Revision 6: each device's better and worse after Holm out of its cases in the family."""

    out = {}
    for d in devices:
        mine = [m for m in rows if m["device"] == d]
        out[DEVICE_NAMES.get(d, d)] = {
            "better": sum(1 for m in mine if m["after_holm"] == stats_v3.BETTER),
            "worse": sum(1 for m in mine if m["after_holm"] == stats_v3.WORSE),
            "cases": len(mine),
        }
    return out


def _counts_text(counts: dict[str, dict[str, int]]) -> str:
    return "; ".join(
        f"{name}: better in {c['better']}, worse in {c['worse']} of {c['cases']}"
        for name, c in counts.items()
    )


def claims(
    fam: dict[str, Any],
    config: dict[str, Any],
    beats: str = "beats",
    headroom: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Section 7's wording, per layout and reference, and per surface device and reference.
    ``headroom`` (case id -> R3-R6's checks) qualifies a surface "no difference shown"."""

    check_claim_rules(config)
    notes = config["claim_rules"].get("device_notes") or {}

    def note(devices: Sequence[str]) -> str | None:
        """Revision 6: what was seen of a claim's devices before F4 (section 1)."""

        return "; ".join(notes[d] for d in devices if d in notes) or None

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
            counts = per_device_counts(rows, rule["devices"])
            wording = layout_claim(
                reference, better, worse, int(rule["t"]), where, len(rows), beats
            )
            if len(rule["devices"]) > 1:
                wording += f" ({_counts_text(counts)})"
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
                    "per_device": counts,
                    "claim": wording,
                    "note": note(rule["devices"]),
                }
            )
    for m in members:
        if m["kind"] != "surface":
            continue
        where = _where([m["device"]], m["lattice"])
        if m["after_holm"] == stats_v3.BETTER:
            wording = f"{beats} {m['reference']} {where}"
        elif m["after_holm"] == stats_v3.WORSE:
            wording = f"loses to {m['reference']} {where}"
        else:
            wording = f"no difference shown against {m['reference']} {where}"
            check = (headroom or {}).get(m["case_id"]) or {}
            if check.get("naive_regret_verdict") == stats_v3.TIE:
                wording += (
                    "; the best placement found was not better than naive either, so the "
                    "device left no room to show one (R3-R6)"
                )
        out.append(
            {
                "scope": "surface device",
                "device": m["device"],
                "method": m["method"],
                "reference": m["reference"],
                "claim": wording,
                "note": note([m["device"]]),
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


def percentile(case: Case, row: int, among: Sequence[int] | None = None) -> float:
    """Share (in %) of the simulated placements (or of the rows ``among``) with a lower
    pooled P_L than ``row``'s."""

    rows = range(len(case.per_seed)) if among is None else among
    rates = [case.counts(k)[0] / case.counts(k)[1] for k in rows]
    mine = case.counts(row)[0] / case.counts(row)[1]
    return 100.0 * sum(1 for r in rates if r < mine) / len(rates)


def _rho(case: Case, rows: Sequence[int]) -> dict[str, float | None]:
    pooled = {k: case.counts(k)[0] / case.counts(k)[1] for k in rows}
    out: dict[str, float | None] = {}
    for key, values in case.scores.items():
        pairs = [(values[k], pooled[k]) for k in rows if values[k] is not None]
        finite = [(v, q) for v, q in pairs if v is not None and not math.isnan(v)]
        out[key] = (
            _finite(spearman([v for v, _ in finite], [q for _, q in finite])) if finite else None
        )
    return out


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
        if case.random_rows:
            # revision 6: the uniform sample gives an unbiased percentile where not every
            # placement is simulated
            context[method]["percentile_among_random_sample"] = percentile(
                case, k, case.random_rows
            )
    pooled = [case.counts(k)[0] / case.counts(k)[1] for k in range(len(case.per_seed))]
    rho = _rho(case, range(len(case.per_seed)))
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
            "oracle_bias": _finite(fit.oracle_bias),
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
            "spearman_rho_with_p_l": _rho(case, case.random_rows),
        }
    raw_fast = case.raw["methods"].get("fast-search")
    if raw_fast is not None and raw_fast.get("index") is not None:
        out["fast_search"] = {
            "ranks": raw_fast.get("ranks"),
            "evaluations": (raw_fast.get("detail") or {}).get("evaluations"),
            "same_as_exhaustive": raw_fast["index"]
            == case.raw["methods"]["decoder-graph"]["index"],
        }
    level2 = case.raw["methods"].get("qiskit-default-l2") or {}
    sweep = level2.get("seed_sweep_indices")
    if sweep:
        out["qiskit_l2_seed_sweep"] = {
            "seeds": len(sweep),
            "same_as_main": sum(1 for i in sweep if i == level2.get("index")),
            "no_swap_free_choice": sum(1 for i in sweep if i is None),
            "distinct_other_choices": len(
                {i for i in sweep if i is not None and i != level2.get("index")}
            ),
        }
    naive_row = case.picks.get("naive")
    if case.kind == "surface" and naive_row is not None:
        naive = stats_v3.cross_fit_regret(case.per_seed, fit, naive_row)
        out["headroom"] = {
            "rule": "R3-R6: naive's cross-fitted regret; a tie means no placement was shown "
            "to beat naive on this case",
            "naive_regret": _iv(naive.absolute),
            "naive_regret_verdict": naive.verdict,
            "best_per_round": per_round(fit.best.estimate, case.rounds),
        }
    return out


def suppression(secondaries: Sequence[dict[str, Any]], cases: Sequence[Case]) -> list[Any]:
    """R3-R6 (revision 6): per device and setting with surface codes at two distances, the
    best per-round error at each and their ratio Lambda = d3 / d5 (above 1: more distance
    helps under this model; at or below 1: at or above threshold)."""

    by_id = {c.case_id: c for c in cases}
    best: dict[tuple[str, str], dict[int, float]] = {}
    for sec in secondaries:
        c = by_id[sec["case_id"]]
        if c.kind == "surface" and "headroom" in sec:
            best.setdefault((c.device, c.setting), {})[c.distance] = sec["headroom"][
                "best_per_round"
            ]
    out = []
    for (device, setting), per_d in sorted(best.items()):
        if 3 in per_d and 5 in per_d:
            out.append(
                {
                    "device": device,
                    "setting": setting,
                    "best_per_round": {str(d): v for d, v in sorted(per_d.items())},
                    "lambda_3_5": per_d[3] / per_d[5] if per_d[5] > 0 else None,
                }
            )
    return out


# --- R3-C10 sensitivity ---------------------------------------------------------------------


def _variant(case: Case, part: str) -> tuple[Case, set[int]] | None:
    """``case`` with its family placements' counts replaced by a sensitivity run's (``part``
    is a key of the sensitivity record, or ``grading_variants/<name>``)."""

    sens_all = case.raw.get("sensitivity") or {}
    if part.startswith("grading_variants/"):
        name = part.split("/", 1)[1]
        sens = ((sens_all.get("grading_variants") or {}).get("variants") or {}).get(name)
    else:
        sens = sens_all.get(part)
    if not sens or sens.get("failed"):
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
    missing: dict[str, str] | Sequence[str],
    isolation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Both families re-evaluated under each sensitivity run (exploratory, R3-C10) and each
    grading variant (R3-C15, revision 6).

    A case without that run keeps its main-stage counts (A, B and the surface code already
    have *d* rounds; the directional variant skips B and the surface code). Holm runs over
    the same members; changes from each family are listed.
    """

    setting = config["headline_setting"]
    isolation_before = isolation if isolation is not None else {"members": []}
    parts = ["correlated_matching", "rounds_equal_distance"]
    names: set[str] = set()
    for case in cases:
        sens = case.raw.get("sensitivity") or {}
        names |= set((sens.get("grading_variants") or {}).get("variants") or {})
    parts += [f"grading_variants/{n}" for n in sorted(names)]
    out: dict[str, Any] = {}
    for part in parts:
        variant_cases = []
        for case in cases:
            v = _variant(case, part)
            if v is None:
                variant_cases.append(case)
                continue
            vc, family_rows = v
            refs = [*config["headline_references"], *config["isolation_references"][case.kind]]
            for name in [HEADLINE_METHOD[case.kind], *refs]:
                k = case.picks.get(name)
                if k is not None and k not in family_rows:
                    raise RuntimeError(f"{case.case_id}: {name}'s choice is not a family placement")
            variant_cases.append(vc)
        rows = comparisons(variant_cases)
        entry: dict[str, Any] = {}
        plans: list[tuple[str, dict[str, Any], dict[str, Sequence[str]] | None]] = [
            ("headline", headline, None),
            ("isolation", isolation_before, config["isolation_references"]),
        ]
        for label, before_fam, plan_refs in plans:
            before = {
                (m["case_id"], m["reference"]): m["after_holm"] for m in before_fam["members"]
            }
            fam = family(rows, config, setting, missing, plan_refs)
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
            beats = "beats" if label == "headline" else "the decoder graph helps beyond"
            entry[label] = {"family": fam, "claims": claims(fam, config, beats), "changed": changed}
        # the headline family's keys at the top level, as in revision 5
        out[part] = {**entry["headline"], "isolation": entry["isolation"]}
    return out


def _is_ancestor(ancestor: str, commit: str, root: Path) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, commit], cwd=root, check=False
    )
    return result.returncode == 0


def _machine_identity(prov: dict[str, Any]) -> str:
    m = prov.get("machine") or {}
    keys = ("label", "cpu_model", "logical_cores", "os", "python", "libc", "stim_build")
    return json.dumps({k: m.get(k) for k in keys}, sort_keys=True)


def check_provenance(
    raws: dict[str, dict[str, Any]], runs_dir: Path, root: Path = ROOT
) -> dict[str, Any]:
    """A8 (revision 6): every run traces to F4's frozen code, on one machine per device.

    Each run file must record a clean commit that descends from the F4 commit pinned in
    ``configs_v3/freeze_f4.json``, the frozen runner source and lock file recorded there,
    and the same machine as every other run of its device (X19); a score file that is
    present must match its recorded SHA-256. Raises on any difference.
    """

    freeze = json.loads((root / "configs_v3" / "freeze_f4.json").read_text(encoding="utf-8"))
    f4 = freeze.get("f4_commit")
    if not f4:
        raise RuntimeError("freeze_f4.json pins no F4 commit")
    problems: list[str] = []
    machines: dict[str, set[str]] = {}
    checked, absent = 0, []
    for name, raw in sorted(raws.items()):
        prov = raw.get("provenance") or {}
        commit = prov.get("package_git_commit")
        if prov.get("package_git_dirty") is not False:
            problems.append(f"{name}: run from a dirty tree")
        if not commit or not _is_ancestor(f4, commit, root):
            problems.append(f"{name}: commit {commit} does not descend from F4 {f4}")
        if prov.get("nac_bench_source_sha256") != freeze["nac_bench_source_sha256"]:
            problems.append(f"{name}: runner source differs from F4's")
        if prov.get("lock_file_sha256") != freeze["lock_file_sha256"]:
            problems.append(f"{name}: lock file differs from F4's")
        machines.setdefault(raw["case"]["device"], set()).add(_machine_identity(prov))
        score = raw.get("score_file")
        if score:
            path = root / score["path"]
            if not path.exists():  # score files stay out of git; look beside the runs
                path = runs_dir.parent / "scores" / Path(score["path"]).name
            if path.exists():
                if hashlib.sha256(path.read_bytes()).hexdigest() != score["sha256"]:
                    problems.append(f"{name}: score file hash differs")
                checked += 1
            else:
                absent.append(name)
    for device, ids in machines.items():
        if len(ids) > 1:
            problems.append(f"{device}: runs from {len(ids)} machines (X19)")
    if problems:
        raise RuntimeError("provenance check failed:\n" + "\n".join(problems))
    return {
        "f4_commit": f4,
        "runs_checked": len(raws),
        "machines_per_device": {d: len(v) for d, v in sorted(machines.items())},
        "score_files_checked": checked,
        "score_files_absent": absent,
    }


def analyse(config: dict[str, Any], runs_dir: Path, checks: Checks | None = None) -> dict[str, Any]:
    checks = checks or Checks()
    if config.get("spec_revision") != SPEC_REVISION:
        raise ValueError(f"config is not for SPEC_V3.md revision {SPEC_REVISION}")
    cases: list[Case] = []
    missing: dict[str, str] = {}
    unexplained: list[str] = []
    files = {}
    raws: dict[str, dict[str, Any]] = {}
    for path in sorted(runs_dir.glob("v3-*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw["config_file_sha256"] != config["_config_sha256"]:
            raise RuntimeError(f"{path.name} was run with another config")
        files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        raws[path.name] = raw
        case_id = raw["case"]["case_id"]
        if raw.get("failed"):
            if raw.get("stage") == NOVE_CHECK_STAGE:
                missing[case_id] = f"the Nove check failed (R3-C4): {raw.get('error')}"
            else:
                unexplained.append(f"{case_id} failed in {raw.get('stage')}: {raw.get('error')}")
            continue
        if raw.get("sensitivity") == {"pending": True}:
            unexplained.append(f"{case_id} stopped before its sensitivity runs finished")
        case = load_case(path)
        if case is None:
            missing[case_id] = "no valid placement"
        else:
            cases.append(case)
    earlier = sorted(p.name for p in (runs_dir / "failed").glob("*.json"))
    if checks.complete:
        absent = [c for c in planned_cases(config) if f"{c}.json" not in files]
        if absent:
            unexplained.append(f"no run file for {', '.join(absent)}")
        if unexplained:
            raise RuntimeError("cannot analyse:\n" + "\n".join(unexplained))
    provenance_record = check_provenance(raws, runs_dir) if checks.provenance else None
    rows = comparisons(cases)
    setting = config["headline_setting"]
    headline = family(rows, config, setting, missing)
    isolation = family(rows, config, setting, missing, config["isolation_references"])
    others = [s for s in config["settings_order"] if s != setting]
    secondaries = [secondary(c) for c in cases]
    headroom = {s["case_id"]: s["headroom"] for s in secondaries if "headroom" in s}
    graded = {
        f"{config['family']} with the headline setting's choices graded at the {s} setting "
        "(exploratory)": (family(rows, config, s, missing, suffix=HEADLINE_SETTING_SUFFIX))
        for s in others
        if (config.get("setting_scope") or {}).get(s, {}).get("simulated_set") == "choices"
    }
    return {
        "schema": SCHEMA,
        "spec_revision": SPEC_REVISION,
        "config_file_sha256": config["_config_sha256"],
        "run_files_sha256": files,
        "earlier_failed_attempts": earlier,
        "provenance": provenance_record,
        "verdict_rule": stats_v3.VERDICT_RULE,
        "cases_without_valid_placement": sorted(missing),
        "cases_left_and_why": missing,
        "every_placement_fallbacks": {
            name.removesuffix(".json"): fallback
            for name, raw in sorted(raws.items())
            if (fallback := (raw.get("simulated_set") or {}).get("every_placement_fallback"))
        },
        "comparisons": rows,
        "families": {
            config["family"]: headline,
            config["isolation_family"]: isolation,
            **{
                f"{config['family']} at the {s} setting (exploratory)": family(
                    rows, config, s, missing
                )
                for s in others
            },
            **graded,
        },
        "claims": claims(headline, config, headroom=headroom),
        "isolation_claims": claims(isolation, config, "the decoder graph helps beyond"),
        "secondary": secondaries,
        "surface_suppression": suppression(secondaries, cases),
        "sensitivity": sensitivity(cases, config, headline, missing, isolation),
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
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args(argv)
    commit, dirty = git_state()
    if dirty and not args.allow_dirty:
        parser.error("uncommitted changes to code, data or config; commit them first")
    result = analyse(load_config(args.config), args.runs)
    result["analysis_git_commit"], result["analysis_git_dirty"] = commit, dirty
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    fam = result["families"][next(iter(result["families"]))]
    print(f"{fam['size']} of {fam['planned_size']} comparisons in the family", flush=True)
    for claim in result["claims"]:
        print(claim["claim"], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
