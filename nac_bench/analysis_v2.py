"""v2 metrics from results files: comparisons (R-B9), M6/M7 (Part A), R-G7 (Part C).

Needs no Qiskit; it reads the choices the runner recorded.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from noise_aware_compilation.analysis import evidence, seed_breakdown
from noise_aware_compilation.stats import (
    Z95,
    Interval,
    detection_rate_interval,
    difference_from_mean,
    mean_of_proportions,
    newcombe_difference,
    ranks,
    ratio_interval,
    relative_from_mean,
    spearman,
    verdict,
    wilson,
)

METHODS = ("v1", "decoder-graph", "lowest-summed-error", "qiskit-vf2")
SCORES = ("v1", "decoder-graph")
METHOD_NAMES = {
    "v1": "v1 score",
    "decoder-graph": "decoder-graph score",
    "lowest-summed-error": "lowest summed error",
    "qiskit-vf2": "Qiskit VF2Layout",
    "fixed": "fixed",
    "average": "all-placement average",
}
NAN = float("nan")
NAN_INTERVAL = Interval(NAN, NAN, NAN)


@dataclass(frozen=True)
class Placement:
    mapping: tuple[int, ...]
    failures: int
    shots: int
    per_seed: tuple[tuple[int, int], ...]
    p_l: Interval
    detection: Interval

    @property
    def estimate(self) -> float:
        return self.p_l.estimate


@dataclass(frozen=True)
class Comparison:
    """One method's choice against one reference, pooled over seeds and per seed."""

    reference: str
    verdict: str
    seed_verdicts: tuple[str, ...]
    absolute: Interval
    relative: Interval
    chosen: Interval
    reference_interval: Interval

    @property
    def breakdown(self) -> str:
        return seed_breakdown(self.verdict, self.seed_verdicts)

    @property
    def evidence(self) -> str:
        return evidence(self.verdict, self.seed_verdicts)


@dataclass(frozen=True)
class DriftMetrics:
    method: str
    delta: float
    draws: int
    failures: int  # draws where the method returned no valid placement (R-B7)
    gated: bool  # the method's delta = 0 choice is "better" than fixed (pooled)
    m6: Interval
    harm_draws: int
    m7: Interval
    distinct_choices: int


@dataclass
class CaseResult:
    case_id: str
    device: str
    role: str
    label: str
    distance: int
    raw: dict[str, Any] = field(repr=False)
    placements: list[Placement]
    fixed_index: int
    choices: dict[str, int | None]  # true-model choice index per method
    qiskit_failure: str | None
    average: Interval
    best_index: int
    worst_index: int
    comparisons: dict[str, dict[str, Comparison]]  # method -> reference -> comparison
    m4: dict[str, float]
    rank: dict[str, float]
    regret: dict[str, Interval]
    relative_regret: dict[str, float]
    drift: list[DriftMetrics]

    @property
    def headroom_overlap(self) -> bool:
        return self.placements[self.best_index].p_l.overlaps(self.placements[self.worst_index].p_l)


def _placement(raw: dict[str, Any], num_detectors: int) -> Placement:
    seeds = raw["per_seed"]
    failures = sum(int(s["logical_failures"]) for s in seeds)
    shots = sum(int(s["shots"]) for s in seeds)
    fires = sum(int(s["detector_fires"]) for s in seeds)
    fires_sq = sum(int(s["detector_fires_sq"]) for s in seeds)
    return Placement(
        mapping=tuple(raw["mapping"]),
        failures=failures,
        shots=shots,
        per_seed=tuple((int(s["logical_failures"]), int(s["shots"])) for s in seeds),
        p_l=wilson(failures, shots),
        detection=detection_rate_interval(fires, fires_sq, shots, num_detectors),
    )


def _pair(f1: int, n1: int, f2: int, n2: int) -> tuple[str, Interval, Interval, Interval, Interval]:
    a, b = wilson(f1, n1), wilson(f2, n2)
    return verdict(a, b), newcombe_difference(f1, n1, f2, n2), ratio_interval(f1, n1, f2, n2), a, b


def compare_pair(placements: Sequence[Placement], i: int, j: int, reference: str) -> Comparison:
    """Choice ``i`` against placement ``j`` (v1 rules; the same placement is indistinguishable)."""

    a, b = placements[i], placements[j]
    if i == j:
        zero = Interval(0.0, 0.0, 0.0)
        seeds = tuple("indistinguishable" for _ in a.per_seed)
        return Comparison(reference, "indistinguishable", seeds, zero, zero, a.p_l, b.p_l)
    pooled, absolute, relative, ci, ri = _pair(a.failures, a.shots, b.failures, b.shots)
    seeds = tuple(
        _pair(fa, na, fb, nb)[0] for (fa, na), (fb, nb) in zip(a.per_seed, b.per_seed, strict=True)
    )
    return Comparison(reference, pooled, seeds, absolute, relative, ci, ri)


def compare_average(placements: Sequence[Placement], i: int) -> Comparison:
    counts = [(p.failures, p.shots) for p in placements]
    average = mean_of_proportions(counts).interval
    seeds = []
    for s in range(len(placements[0].per_seed)):
        seed_counts = [p.per_seed[s] for p in placements]
        seed_avg = mean_of_proportions(seed_counts).interval
        seeds.append(verdict(wilson(*seed_counts[i]), seed_avg))
    return Comparison(
        "average",
        verdict(placements[i].p_l, average),
        tuple(seeds),
        difference_from_mean(counts, i),
        relative_from_mean(counts, i),
        placements[i].p_l,
        average,
    )


def _ratio_bootstrap(
    p_hat: np.ndarray,
    shots: np.ndarray,
    fixed: int,
    true_choice: int,
    draw_choices: Sequence[int],
    replicates: int,
    seed: int,
) -> Interval:
    """M6 estimate and percentile bootstrap over draws and binomial shot noise."""

    choices = np.asarray(draw_choices, dtype=int)
    denominator = p_hat[fixed] - p_hat[true_choice]
    estimate = float(np.mean((p_hat[fixed] - p_hat[choices]) / denominator))
    rng = np.random.Generator(np.random.PCG64(seed))
    stats = np.empty(replicates)
    for r in range(replicates):
        p_star = rng.binomial(shots, p_hat) / shots
        sample = choices[rng.integers(0, len(choices), len(choices))]
        den = p_star[fixed] - p_star[true_choice]
        stats[r] = np.mean((p_star[fixed] - p_star[sample]) / den) if den != 0 else np.nan
    low, high = np.nanpercentile(stats, [2.5, 97.5])
    return Interval(estimate, float(low), float(high))


def drift_metrics(
    raw: dict[str, Any],
    placements: Sequence[Placement],
    fixed: int,
    true_choices: dict[str, int | None],
    gate: dict[str, bool],
    bootstrap: dict[str, Any],
) -> list[DriftMetrics]:
    index = {p.mapping: i for i, p in enumerate(placements)}
    p_hat = np.array([p.failures / p.shots for p in placements])
    shots = np.array([p.shots for p in placements], dtype=np.int64)
    fixed_p = placements[fixed]
    out: list[DriftMetrics] = []
    records = raw["drift"]["records"]
    for delta in raw["drift"]["strengths"]:
        rows = [r for r in records if r["delta"] == delta]
        for m_i, method in enumerate(METHODS):
            picked: list[int] = []
            failures = 0
            for r in rows:
                choice = r["choices"][method]
                if choice.get("failure") or choice["mapping"] is None:
                    failures += 1
                    continue
                picked.append(index[tuple(choice["mapping"])])
            harm = sum(
                1
                for i in picked
                if verdict(placements[i].p_l, fixed_p.p_l) == "worse" and i != fixed
            )
            m7 = wilson(harm, len(picked)) if picked else NAN_INTERVAL
            true_choice = true_choices[method]
            if gate[method] and picked and true_choice is not None:
                m6 = _ratio_bootstrap(
                    p_hat,
                    shots,
                    fixed,
                    true_choice,
                    picked,
                    int(bootstrap["replicates"]),
                    int(bootstrap["seed"]) + 1000 * m_i + round(delta * 100),
                )
            else:
                m6 = NAN_INTERVAL
            out.append(
                DriftMetrics(
                    method=method,
                    delta=float(delta),
                    draws=len(rows),
                    failures=failures,
                    gated=gate[method],
                    m6=m6,
                    harm_draws=harm,
                    m7=m7,
                    distinct_choices=len(set(picked)),
                )
            )
    return out


def summarize(raw: dict[str, Any], bootstrap: dict[str, Any]) -> CaseResult:
    case = raw["case"]
    placements = [_placement(p, int(raw["num_detectors"])) for p in raw["placements"]]
    index = {p.mapping: i for i, p in enumerate(placements)}
    fixed = index[tuple(raw["fixed_mapping"])]
    true = raw["true_model"]["choices"]
    choices: dict[str, int | None] = {}
    for method in METHODS:
        mapping = true[method]["mapping"]
        failed = bool(true[method].get("failure"))
        choices[method] = None if failed or mapping is None else index[tuple(mapping)]
    values = [p.estimate for p in placements]
    best = min(range(len(values)), key=lambda i: (values[i], i))
    worst = max(range(len(values)), key=lambda i: (values[i], -i))
    rank_values = ranks(values)

    comparisons: dict[str, dict[str, Comparison]] = {}
    for method in METHODS:
        i = choices[method]
        if i is None:
            continue
        refs = {"fixed": compare_pair(placements, i, fixed, "fixed")}
        refs["average"] = compare_average(placements, i)
        if method in SCORES:
            for baseline in ("lowest-summed-error", "qiskit-vf2"):
                j = choices[baseline]
                if j is not None:
                    refs[baseline] = compare_pair(placements, i, j, baseline)
        comparisons[method] = refs

    m4 = {
        m: spearman([float(s) for s in true[m]["candidate_scores"]], values)
        for m in ("v1", "decoder-graph", "lowest-summed-error")
    }
    rank: dict[str, float] = {}
    regret: dict[str, Interval] = {}
    relative_regret: dict[str, float] = {}
    b = placements[best]
    for method, i in choices.items():
        if i is None:
            continue
        c = placements[i]
        rank[method] = rank_values[i]
        regret[method] = newcombe_difference(c.failures, c.shots, b.failures, b.shots)
        relative_regret[method] = (c.estimate - b.estimate) / b.estimate
    gate = {m: m in comparisons and comparisons[m]["fixed"].verdict == "better" for m in METHODS}
    drift = drift_metrics(raw, placements, fixed, choices, gate, bootstrap) if case["drift"] else []
    return CaseResult(
        case_id=case["case_id"],
        device=case["device"],
        role=case["role"],
        label=case["circuit"]["label"],
        distance=int(case["circuit"]["distance"]),
        raw=raw,
        placements=placements,
        fixed_index=fixed,
        choices=choices,
        qiskit_failure=true["qiskit-vf2"].get("failure"),
        average=mean_of_proportions([(p.failures, p.shots) for p in placements]).interval,
        best_index=best,
        worst_index=worst,
        comparisons=comparisons,
        m4=m4,
        rank=rank,
        regret=regret,
        relative_regret=relative_regret,
        drift=drift,
    )


@dataclass(frozen=True)
class RG7:
    """R-G7: mean relative-regret difference (decoder-graph minus v1) over held-out cases."""

    per_case: tuple[tuple[str, float, float, float], ...]  # case, rr v1, rr graph, difference
    difference: Interval
    decoder_graph_wins: bool

    @property
    def default_scorer(self) -> str:
        return "decoder-graph" if self.decoder_graph_wins else "v1"


def r_g7(held_out: Sequence[CaseResult]) -> RG7:
    """Delta-method shot-noise interval; cases are fixed (SPEC_V2.md R-G7, rev 1)."""

    rows = []
    diffs = []
    variance = 0.0
    for case in held_out:
        g, v = case.choices["decoder-graph"], case.choices["v1"]
        assert g is not None and v is not None
        b = case.best_index
        ps = case.placements
        p_b = ps[b].estimate
        diff = (ps[g].estimate - ps[v].estimate) / p_b
        gradient: dict[int, float] = {}
        gradient[g] = gradient.get(g, 0.0) + 1 / p_b
        gradient[v] = gradient.get(v, 0.0) - 1 / p_b
        gradient[b] = gradient.get(b, 0.0) - diff / p_b
        variance += sum(
            d * d * ps[i].estimate * (1 - ps[i].estimate) / ps[i].shots
            for i, d in gradient.items()
            if d != 0
        )
        diffs.append(diff)
        rows.append(
            (case.case_id, case.relative_regret["v1"], case.relative_regret["decoder-graph"], diff)
        )
    k = len(held_out)
    mean = sum(diffs) / k
    half = Z95 * math.sqrt(variance) / k
    interval = Interval(mean, mean - half, mean + half)
    return RG7(tuple(rows), interval, decoder_graph_wins=interval.high < 0)


def load(directory: Path, config: dict[str, Any]) -> dict[str, CaseResult]:
    out = {}
    for path in sorted(directory.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        result = summarize(raw, config["bootstrap"])
        out[result.case_id] = result
    return out


__all__ = [
    "METHODS",
    "METHOD_NAMES",
    "RG7",
    "SCORES",
    "CaseResult",
    "Comparison",
    "DriftMetrics",
    "compare_average",
    "compare_pair",
    "load",
    "r_g7",
    "summarize",
]
