"""Metrics M1-M5, comparisons and verdicts computed from results files."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from noise_aware_compilation.stats import (
    Homogeneity,
    Interval,
    detection_rate_interval,
    difference_from_mean,
    epsilon_interval,
    homogeneity_test,
    mean_of_proportions,
    newcombe_difference,
    ranks,
    ratio_interval,
    relative_from_mean,
    spearman,
    verdict,
    wilson,
)


@dataclass(frozen=True)
class PlacementSummary:
    mapping: tuple[int, ...]
    score: float
    is_chosen: bool
    is_fixed: bool
    contains_fault: bool | None
    shots: int
    failures: int
    p_l: Interval
    epsilon: Interval
    detection: Interval
    per_seed: tuple[tuple[int, int], ...]  # (failures, shots) per base seed
    per_seed_detection: tuple[tuple[int, int, int], ...]  # (fires, fires_sq, shots)


@dataclass(frozen=True)
class Comparison:
    reference: str
    absolute: Interval
    relative: Interval
    verdict: str
    chosen: Interval
    reference_interval: Interval


@dataclass
class CaseSummary:
    case: dict[str, Any]
    metric: str  # "P_L" or "M3" (distance 2)
    placements: list[PlacementSummary]
    chosen_index: int
    fixed_index: int
    average: Interval
    vs_fixed: Comparison
    vs_average: Comparison
    best_index: int
    worst_index: int
    headroom_overlap: bool
    spearman: float
    chosen_rank: float
    regret: Interval
    homogeneity: Homogeneity
    per_seed_verdicts: list[dict[str, str]]
    wall_clock_seconds: float
    shots_per_seed: int
    raw: dict[str, Any] = field(repr=False)

    @property
    def case_id(self) -> str:
        return str(self.case["case_id"])

    @property
    def num_detectors(self) -> int:
        return int(self.raw["num_detectors"])


def _metric_interval(p: PlacementSummary, metric: str) -> Interval:
    return p.p_l if metric == "P_L" else p.detection


def _compare_fixed(chosen: PlacementSummary, fixed: PlacementSummary, metric: str) -> Comparison:
    if metric == "P_L":
        absolute = newcombe_difference(chosen.failures, chosen.shots, fixed.failures, fixed.shots)
        relative = ratio_interval(chosen.failures, chosen.shots, fixed.failures, fixed.shots)
    else:
        a, b = chosen.detection, fixed.detection
        half = math.hypot((a.high - a.low) / 2, (b.high - b.low) / 2)
        d = a.estimate - b.estimate
        absolute = Interval(d, d - half, d + half)
        rel = d / b.estimate if b.estimate else float("nan")
        rhalf = half / b.estimate if b.estimate else float("nan")
        relative = Interval(rel, rel - rhalf, rel + rhalf)
    ci, fi = _metric_interval(chosen, metric), _metric_interval(fixed, metric)
    return Comparison("fixed", absolute, relative, verdict(ci, fi), ci, fi)


def _average(placements: Sequence[PlacementSummary], metric: str) -> tuple[Interval, float]:
    if metric == "P_L":
        est = mean_of_proportions([(p.failures, p.shots) for p in placements])
        return est.interval, est.variance
    k = len(placements)
    mean = sum(p.detection.estimate for p in placements) / k
    var = (
        sum(
            ((p.detection.high - p.detection.low) / (2 * 1.959963984540054)) ** 2
            for p in placements
        )
        / k**2
    )
    half = 1.959963984540054 * math.sqrt(var)
    return Interval(mean, mean - half, mean + half), var


def _compare_average(
    placements: Sequence[PlacementSummary], index: int, metric: str, average: Interval
) -> Comparison:
    chosen = placements[index]
    if metric == "P_L":
        counts = [(p.failures, p.shots) for p in placements]
        absolute = difference_from_mean(counts, index)
        relative = relative_from_mean(counts, index)
    else:
        k = len(placements)
        ses = [(p.detection.high - p.detection.low) / (2 * 1.959963984540054) for p in placements]
        d = chosen.detection.estimate - average.estimate
        var = (1 - 1 / k) ** 2 * ses[index] ** 2 + sum(
            s * s for i, s in enumerate(ses) if i != index
        ) / k**2
        half = 1.959963984540054 * math.sqrt(var)
        absolute = Interval(d, d - half, d + half)
        m = average.estimate
        relative = (
            Interval(d / m, (d - half) / m, (d + half) / m)
            if m
            else Interval(float("nan"), float("nan"), float("nan"))
        )
    ci = _metric_interval(chosen, metric)
    return Comparison("average", absolute, relative, verdict(ci, average), ci, average)


def summarize_placement(raw: dict[str, Any], rounds: int, num_detectors: int) -> PlacementSummary:
    seeds = raw["per_seed"]
    shots = sum(int(s["shots"]) for s in seeds)
    failures = sum(int(s["logical_failures"]) for s in seeds)
    fires = sum(int(s["detector_fires"]) for s in seeds)
    fires_sq = sum(int(s["detector_fires_sq"]) for s in seeds)
    p = wilson(failures, shots)
    return PlacementSummary(
        mapping=tuple(raw["mapping"]),
        score=float(raw["score"]),
        is_chosen=bool(raw["is_chosen"]),
        is_fixed=bool(raw["is_fixed"]),
        contains_fault=raw.get("contains_fault"),
        shots=shots,
        failures=failures,
        p_l=p,
        epsilon=epsilon_interval(p, rounds),
        detection=detection_rate_interval(fires, fires_sq, shots, num_detectors),
        per_seed=tuple((int(s["logical_failures"]), int(s["shots"])) for s in seeds),
        per_seed_detection=tuple(
            (int(s["detector_fires"]), int(s["detector_fires_sq"]), int(s["shots"])) for s in seeds
        ),
    )


def _seed_view(p: PlacementSummary, seed: int, rounds: int, num_detectors: int) -> PlacementSummary:
    failures, shots = p.per_seed[seed]
    fires, fires_sq, _ = p.per_seed_detection[seed]
    w = wilson(failures, shots)
    return PlacementSummary(
        mapping=p.mapping,
        score=p.score,
        is_chosen=p.is_chosen,
        is_fixed=p.is_fixed,
        contains_fault=p.contains_fault,
        shots=shots,
        failures=failures,
        p_l=w,
        epsilon=epsilon_interval(w, rounds),
        detection=detection_rate_interval(fires, fires_sq, shots, num_detectors),
        per_seed=(p.per_seed[seed],),
        per_seed_detection=(p.per_seed_detection[seed],),
    )


def summarize_case(raw: dict[str, Any]) -> CaseSummary:
    case = raw["case"]
    rounds = int(case["circuit"]["rounds"])
    num_detectors = int(raw["num_detectors"])
    metric = "M3" if int(case["circuit"]["distance"]) == 2 else "P_L"
    placements = [summarize_placement(p, rounds, num_detectors) for p in raw["placements"]]
    chosen_index = next(i for i, p in enumerate(placements) if p.is_chosen)
    fixed_index = next(i for i, p in enumerate(placements) if p.is_fixed)

    def analyse(ps: list[PlacementSummary]) -> tuple[Interval, Comparison, Comparison]:
        avg, _ = _average(ps, metric)
        return (
            avg,
            _compare_fixed(ps[chosen_index], ps[fixed_index], metric),
            _compare_average(ps, chosen_index, metric, avg),
        )

    average, vs_fixed, vs_average = analyse(placements)
    values = [_metric_interval(p, metric).estimate for p in placements]
    best_index = min(range(len(values)), key=lambda i: (values[i], i))
    worst_index = max(range(len(values)), key=lambda i: (values[i], -i))
    best_i = _metric_interval(placements[best_index], metric)
    worst_i = _metric_interval(placements[worst_index], metric)
    rank_values = ranks(values)
    if metric == "P_L":
        b, c = placements[best_index], placements[chosen_index]
        regret = newcombe_difference(c.failures, c.shots, b.failures, b.shots)
    else:
        b_i, c_i = best_i, _metric_interval(placements[chosen_index], metric)
        d = c_i.estimate - b_i.estimate
        half = math.hypot((c_i.high - c_i.low) / 2, (b_i.high - b_i.low) / 2)
        regret = Interval(d, d - half, d + half)
    # T-F2 homogeneity is always on P_L counts (also for distance 2, where P_L is not reported).
    homogeneity = homogeneity_test([(p.failures, p.shots) for p in placements])

    per_seed_verdicts = []
    num_seeds = len(placements[0].per_seed)
    for s in range(num_seeds):
        view = [_seed_view(p, s, rounds, num_detectors) for p in placements]
        _, f, a = analyse(view)
        per_seed_verdicts.append({"vs_fixed": f.verdict, "vs_average": a.verdict})

    return CaseSummary(
        case=case,
        metric=metric,
        placements=placements,
        chosen_index=chosen_index,
        fixed_index=fixed_index,
        average=average,
        vs_fixed=vs_fixed,
        vs_average=vs_average,
        best_index=best_index,
        worst_index=worst_index,
        headroom_overlap=best_i.overlaps(worst_i),
        spearman=spearman([p.score for p in placements], values),
        chosen_rank=rank_values[chosen_index],
        regret=regret,
        homogeneity=homogeneity,
        per_seed_verdicts=per_seed_verdicts,
        wall_clock_seconds=float(raw["wall_clock_seconds"]),
        shots_per_seed=int(raw["shots"]["shots_per_seed"]),
        raw=raw,
    )


def load_runs(directory: str | Path) -> dict[str, dict[str, Any]]:
    runs: dict[str, dict[str, Any]] = {}
    for path in sorted(Path(directory).glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        runs[str(data["case"]["case_id"])] = data
    return runs


def load_summaries(directory: str | Path) -> dict[str, CaseSummary]:
    return {cid: summarize_case(raw) for cid, raw in load_runs(directory).items()}


def precision_ok(p: PlacementSummary, target: float = 0.10) -> bool:
    """T-P4 for one placement: Wilson 95% half-width <= target * P_L."""

    if p.failures == 0:
        return False
    half = max(p.p_l.high - p.p_l.estimate, p.p_l.estimate - p.p_l.low)
    return half <= target * p.p_l.estimate


def trend(diffs: Sequence[Interval]) -> str:
    """T-P6 trend of chosen-vs-average differences over increasing code distance.

    "flat" when every pair of 95% intervals overlaps; otherwise "grows" when
    the magnitude of the difference at the largest distance exceeds that at
    the smallest, else "shrinks" (DECISIONS.md D10).
    """

    if all(a.overlaps(b) for a in diffs for b in diffs):
        return "flat"
    return "grows" if abs(diffs[-1].estimate) > abs(diffs[0].estimate) else "shrinks"


__all__ = [
    "CaseSummary",
    "Comparison",
    "PlacementSummary",
    "load_runs",
    "load_summaries",
    "precision_ok",
    "summarize_case",
    "trend",
]
