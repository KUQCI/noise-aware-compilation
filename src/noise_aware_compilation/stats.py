"""Statistics for the benchmark: intervals, verdicts, rank correlation, homogeneity.

Self-contained (no SciPy at runtime); the test suite cross-checks against SciPy.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

Z95 = 1.959963984540054
Z99 = 2.5758293035489004


@dataclass(frozen=True)
class Interval:
    estimate: float
    low: float
    high: float

    def overlaps(self, other: Interval) -> bool:
        return self.low <= other.high and other.low <= self.high

    def to_dict(self) -> dict[str, float]:
        return {"estimate": self.estimate, "low": self.low, "high": self.high}


def wilson(successes: int, trials: int, z: float = Z95) -> Interval:
    """Wilson score interval for a binomial proportion."""

    if trials <= 0:
        raise ValueError("trials must be positive")
    p = successes / trials
    denom = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denom
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denom
    return Interval(p, max(0.0, centre - half), min(1.0, centre + half))


def epsilon_per_round(p_l: float, rounds: int) -> float:
    """M2: per-round logical error from per-shot P_L (requires P_L < 0.5)."""

    if not 0 <= p_l < 0.5:
        return float("nan")
    return 0.5 * (1 - (1 - 2 * p_l) ** (1 / rounds))


def epsilon_interval(p: Interval, rounds: int) -> Interval:
    """Map a P_L interval through the monotone M2 formula."""

    def f(x: float) -> float:
        return epsilon_per_round(min(x, 0.5 - 1e-15), rounds)

    return Interval(f(p.estimate), f(p.low), f(p.high))


def newcombe_difference(
    s1: int, n1: int, s2: int, n2: int, z: float = Z95
) -> Interval:
    """Newcombe hybrid score interval for p1 - p2 (independent samples)."""

    a = wilson(s1, n1, z)
    b = wilson(s2, n2, z)
    d = a.estimate - b.estimate
    low = d - math.sqrt((a.estimate - a.low) ** 2 + (b.high - b.estimate) ** 2)
    high = d + math.sqrt((a.high - a.estimate) ** 2 + (b.estimate - b.low) ** 2)
    return Interval(d, low, high)


def ratio_interval(s1: int, n1: int, s2: int, n2: int, z: float = Z95) -> Interval:
    """Katz log interval for p1/p2, returned as the relative difference p1/p2 - 1."""

    if s1 == 0 or s2 == 0:
        p1, p2 = s1 / n1, s2 / n2
        est = (p1 / p2 - 1) if p2 > 0 else float("nan")
        return Interval(est, float("nan"), float("nan"))
    r = (s1 / n1) / (s2 / n2)
    se = math.sqrt(1 / s1 - 1 / n1 + 1 / s2 - 1 / n2)
    return Interval(r - 1, r * math.exp(-z * se) - 1, r * math.exp(z * se) - 1)


@dataclass(frozen=True)
class MeanEstimate:
    """Mean of several independent proportions with a normal 95% interval."""

    interval: Interval
    variance: float


def mean_of_proportions(
    counts: Sequence[tuple[int, int]], z: float = Z95
) -> MeanEstimate:
    """R-B2: mean P_L over placements; Var = sum p(1-p)/n / N^2."""

    k = len(counts)
    ps = [s / n for s, n in counts]
    mean = sum(ps) / k
    var = sum(p * (1 - p) / n for p, (_, n) in zip(ps, counts, strict=True)) / (k * k)
    half = z * math.sqrt(var)
    return MeanEstimate(Interval(mean, max(0.0, mean - half), mean + half), var)


def difference_from_mean(
    counts: Sequence[tuple[int, int]], index: int, z: float = Z95
) -> Interval:
    """p_index - mean(p), with the exact variance of that linear combination."""

    k = len(counts)
    ps = [s / n for s, n in counts]
    vs = [p * (1 - p) / n for p, (_, n) in zip(ps, counts, strict=True)]
    mean = sum(ps) / k
    d = ps[index] - mean
    var = (1 - 1 / k) ** 2 * vs[index] + sum(v for i, v in enumerate(vs) if i != index) / k**2
    half = z * math.sqrt(var)
    return Interval(d, d - half, d + half)


def relative_from_mean(
    counts: Sequence[tuple[int, int]], index: int, z: float = Z95
) -> Interval:
    """(p_index - mean) / mean with a delta-method interval."""

    k = len(counts)
    ps = [s / n for s, n in counts]
    vs = [p * (1 - p) / n for p, (_, n) in zip(ps, counts, strict=True)]
    mean = sum(ps) / k
    if mean == 0:
        return Interval(float("nan"), float("nan"), float("nan"))
    pc = ps[index]
    r = pc / mean
    # gradient of pc/mean w.r.t. each p_i
    grads = [(-pc / (k * mean * mean)) for _ in range(k)]
    grads[index] += 1 / mean
    var = sum(g * g * v for g, v in zip(grads, vs, strict=True))
    half = z * math.sqrt(var)
    return Interval(r - 1, r - 1 - half, r - 1 + half)


def verdict(chosen: Interval, reference: Interval) -> str:
    """R-R2: better / worse only when the 95% intervals do not overlap."""

    if chosen.high < reference.low:
        return "better"
    if chosen.low > reference.high:
        return "worse"
    return "indistinguishable"


def ranks(values: Sequence[float]) -> list[float]:
    """Average ranks (1-based), ties sharing their mean rank."""

    order = sorted(range(len(values)), key=lambda i: values[i])
    result = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            result[order[k]] = avg
        i = j + 1
    return result


def spearman(x: Sequence[float], y: Sequence[float]) -> float:
    """M4: Spearman rank correlation (Pearson on average ranks); NaN if undefined."""

    if len(x) != len(y) or len(x) < 2:
        return float("nan")
    rx, ry = ranks(x), ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx == 0 or syy == 0:
        return float("nan")
    return sxy / math.sqrt(sxx * syy)


def _gammaincc(a: float, x: float) -> float:
    """Regularized upper incomplete gamma Q(a, x) (Numerical Recipes gser/gcf)."""

    if x <= 0:
        return 1.0
    gln = math.lgamma(a)
    if x < a + 1:
        ap, total, delta = a, 1.0 / a, 1.0 / a
        for _ in range(10_000):
            ap += 1
            delta *= x / ap
            total += delta
            if abs(delta) < abs(total) * 1e-15:
                break
        return 1.0 - total * math.exp(-x + a * math.log(x) - gln)
    tiny = 1e-300
    b = x + 1 - a
    c = 1 / tiny
    d = 1 / b
    h = d
    for i in range(1, 10_000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-15:
            break
    return math.exp(-x + a * math.log(x) - gln) * h


def chi2_sf(statistic: float, dof: int) -> float:
    return _gammaincc(dof / 2, statistic / 2)


@dataclass(frozen=True)
class Homogeneity:
    statistic: float
    dof: int
    p_value: float


def homogeneity_test(counts: Sequence[tuple[int, int]]) -> Homogeneity:
    """Pearson chi-square test that all placements share one P_L (k x 2 table)."""

    k = len(counts)
    total_s = sum(s for s, _ in counts)
    total_n = sum(n for _, n in counts)
    if k < 2 or total_s == 0 or total_s == total_n:
        return Homogeneity(0.0, max(k - 1, 0), 1.0)
    p = total_s / total_n
    stat = 0.0
    for s, n in counts:
        e1, e0 = n * p, n * (1 - p)
        stat += (s - e1) ** 2 / e1 + ((n - s) - e0) ** 2 / e0
    return Homogeneity(stat, k - 1, chi2_sf(stat, k - 1))


def detection_rate_interval(
    fires: int, fires_sq: int, shots: int, num_detectors: int, z: float = Z95
) -> Interval:
    """M3 with a shot-clustered normal interval (detectors within a shot are correlated)."""

    if num_detectors == 0 or shots == 0:
        return Interval(float("nan"), float("nan"), float("nan"))
    mean_per_shot = fires / shots
    var_per_shot = max(fires_sq / shots - mean_per_shot**2, 0.0)
    se = math.sqrt(var_per_shot / shots) / num_detectors
    rate = mean_per_shot / num_detectors
    return Interval(rate, max(0.0, rate - z * se), rate + z * se)


__all__ = [
    "Z95",
    "Z99",
    "Homogeneity",
    "Interval",
    "MeanEstimate",
    "chi2_sf",
    "detection_rate_interval",
    "difference_from_mean",
    "epsilon_interval",
    "epsilon_per_round",
    "homogeneity_test",
    "mean_of_proportions",
    "newcombe_difference",
    "ranks",
    "ratio_interval",
    "relative_from_mean",
    "spearman",
    "verdict",
    "wilson",
]
