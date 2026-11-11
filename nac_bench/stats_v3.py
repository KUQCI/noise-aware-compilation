"""Statistics for v3 and for the re-analysis of v1 and v2 (roadmap Step 6).

* :func:`difference_verdict`: better / worse / tie from the 95% interval of the
  difference, replacing v1's rule that called any two overlapping intervals
  indistinguishable.
* :func:`holm`: Holm's step-down correction over a named family.
* :func:`cross_fit`: the best (and worst) placement picked on two seeds and
  measured on the third, rotated over all three, so the oracle is not biased
  low by picking and measuring on the same data.
* :func:`retained_gain`: drift's retained gain in absolute terms, with the
  ratio (v2's M6) only where the original gain is stable.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from noise_aware_compilation.stats import Z95, Interval, newcombe_difference

BETTER, WORSE, TIE = "better", "worse", "tie"
VERDICT_RULE = (
    "better if the upper end of the 95% interval for P_L(choice) - P_L(reference) is below 0, "
    "worse if its lower end is above 0, otherwise tie (Newcombe hybrid score interval for two "
    "proportions; the interval of the difference from the all-placement average for that "
    "reference)"
)


def difference_verdict(absolute: Interval) -> str:
    """Verdict from the interval of ``choice - reference`` (Step 6.1)."""

    if absolute.high < 0:
        return BETTER
    if absolute.low > 0:
        return WORSE
    return TIE


def two_proportion_p(s1: int, n1: int, s2: int, n2: int) -> float:
    """Two-sided p-value of the pooled two-proportion z-test (1.0 when both rates are 0)."""

    pooled = (s1 + s2) / (n1 + n2)
    var = pooled * (1 - pooled) * (1 / n1 + 1 / n2)
    if var <= 0:
        return 1.0
    z = (s1 / n1 - s2 / n2) / math.sqrt(var)
    return math.erfc(abs(z) / math.sqrt(2))


@dataclass(frozen=True)
class HolmResult:
    adjusted: tuple[float, ...]  # Holm-adjusted p-values, in input order
    rejected: tuple[bool, ...]


def holm(pvalues: Sequence[float], alpha: float = 0.05) -> HolmResult:
    """Holm's step-down procedure; adjusted p = max over earlier steps of (m - k) p_(k)."""

    m = len(pvalues)
    order = sorted(range(m), key=lambda i: (pvalues[i], i))
    adjusted = [1.0] * m
    running = 0.0
    for k, i in enumerate(order):
        running = max(running, min(1.0, (m - k) * pvalues[i]))
        adjusted[i] = running
    return HolmResult(tuple(adjusted), tuple(a <= alpha for a in adjusted))


# --- cross-fitted oracle ---------------------------------------------------------------------


@dataclass(frozen=True)
class Fold:
    held_out_seed: int
    best: int  # placement picked as best on the other seeds
    worst: int


@dataclass(frozen=True)
class CrossFit:
    """Best and worst placements picked on two seeds and measured on the third."""

    folds: tuple[Fold, ...]
    best: Interval  # mean held-out P_L of the fold-best placements
    worst: Interval
    in_sample_best: float  # min over placements of pooled P_L (biased low)
    in_sample_worst: float
    headroom: Interval  # worst - best, cross-fitted

    @property
    def separated(self) -> bool:
        """Whether the cross-fitted worst placement is clearly worse than the best."""

        return self.headroom.low > 0

    @property
    def oracle_bias(self) -> float:
        """How much lower the in-sample best is than the cross-fitted best, relative; NaN
        when the cross-fitted best saw no failure (revision 6: no division by zero)."""

        if self.best.estimate == 0:
            return math.nan
        return (self.in_sample_best - self.best.estimate) / self.best.estimate


def _rate(counts: tuple[int, int]) -> float:
    return counts[0] / counts[1]


def _var(counts: tuple[int, int]) -> float:
    p = _rate(counts)
    return p * (1 - p) / counts[1]


def _pick(per_seed: Sequence[Sequence[tuple[int, int]]], seeds: Sequence[int], worst: bool) -> int:
    def pooled(i: int) -> float:
        f = sum(per_seed[i][s][0] for s in seeds)
        n = sum(per_seed[i][s][1] for s in seeds)
        return f / n

    values = [pooled(i) for i in range(len(per_seed))]
    if worst:
        return max(range(len(values)), key=lambda i: (values[i], -i))
    return min(range(len(values)), key=lambda i: (values[i], i))


def _mean_interval(rates: Sequence[float], variances: Sequence[float]) -> Interval:
    k = len(rates)
    mean = sum(rates) / k
    half = Z95 * math.sqrt(sum(variances)) / k
    return Interval(mean, mean - half, mean + half)


def cross_fit(per_seed: Sequence[Sequence[tuple[int, int]]]) -> CrossFit:
    """``per_seed[i][s]`` = (failures, shots) of placement ``i`` on base seed ``s``.

    Fold ``s`` picks the best and worst placement by pooled P_L on the other seeds and
    measures them on seed ``s``. Conditional on the picks, each fold's measurement is
    independent of the data that made the pick, so the intervals are Wald intervals of
    the fold means (variance summed over folds).
    """

    num_seeds = len(per_seed[0])
    if num_seeds < 2:
        raise ValueError("cross-fitting needs at least two seeds")
    folds = []
    best_r, best_v, worst_r, worst_v, diff_v = [], [], [], [], []
    for s in range(num_seeds):
        others = [t for t in range(num_seeds) if t != s]
        b, w = _pick(per_seed, others, False), _pick(per_seed, others, True)
        folds.append(Fold(s, b, w))
        best_r.append(_rate(per_seed[b][s]))
        best_v.append(_var(per_seed[b][s]))
        worst_r.append(_rate(per_seed[w][s]))
        worst_v.append(_var(per_seed[w][s]))
        diff_v.append(0.0 if b == w else _var(per_seed[b][s]) + _var(per_seed[w][s]))
    best = _mean_interval(best_r, best_v)
    worst = _mean_interval(worst_r, worst_v)
    headroom = _mean_interval([w - b for w, b in zip(worst_r, best_r, strict=True)], diff_v)
    pooled = [sum(c[0] for c in placement) / sum(c[1] for c in placement) for placement in per_seed]
    return CrossFit(
        folds=tuple(folds),
        best=best,
        worst=worst,
        in_sample_best=min(pooled),
        in_sample_worst=max(pooled),
        headroom=headroom,
    )


@dataclass(frozen=True)
class Regret:
    """A choice's P_L against the cross-fitted best, on the same held-out folds."""

    absolute: Interval  # mean over folds of P_L(choice, s) - P_L(best_s, s)
    relative: Interval  # absolute / cross-fitted best (delta method)
    verdict: str  # tie when the choice is not separated from the cross-fitted best


def cross_fit_regret(
    per_seed: Sequence[Sequence[tuple[int, int]]], fit: CrossFit, choice: int
) -> Regret:
    """Regret of placement ``choice`` (picked without the counts) against ``fit``'s best."""

    k = len(fit.folds)
    c_r = [_rate(per_seed[choice][f.held_out_seed]) for f in fit.folds]
    c_v = [_var(per_seed[choice][f.held_out_seed]) for f in fit.folds]
    b_r = [_rate(per_seed[f.best][f.held_out_seed]) for f in fit.folds]
    b_v = [_var(per_seed[f.best][f.held_out_seed]) for f in fit.folds]
    same = [f.best == choice for f in fit.folds]
    d_r = [c - b for c, b in zip(c_r, b_r, strict=True)]
    d_v = [0.0 if sm else cv + bv for sm, cv, bv in zip(same, c_v, b_v, strict=True)]
    absolute = _mean_interval(d_r, d_v)
    big_c, big_b = sum(c_r) / k, sum(b_r) / k
    if big_b == 0:
        # The cross-fitted best saw no failure on any held-out fold: the relative regret is
        # undefined (revision 6); the absolute regret above still stands.
        nan = math.nan
        return Regret(absolute, Interval(nan, nan, nan), difference_verdict(absolute))
    var_c, var_b = sum(c_v) / k**2, sum(b_v) / k**2
    cov = sum(cv for sm, cv in zip(same, c_v, strict=True) if sm) / k**2
    ratio = big_c / big_b
    var_ratio = (var_c - 2 * ratio * cov + ratio * ratio * var_b) / (big_b * big_b)
    half = Z95 * math.sqrt(max(var_ratio, 0.0))
    relative = Interval(ratio - 1, ratio - 1 - half, ratio - 1 + half)
    return Regret(absolute, relative, difference_verdict(absolute))


# --- drift: retained gain ----------------------------------------------------------------------


@dataclass(frozen=True)
class RetainedGain:
    """Drift's effect on a method's gain over the naive placement (Step 6.4)."""

    gain: Interval  # P_L(fixed) - P_L(true-model choice), Newcombe
    stable: bool  # gain's lower end >= half its estimate: the ratio is reported
    retained: Interval  # mean over draws of P_L(fixed) - P_L(drifted choice), bootstrap
    choice_rate: Interval  # mean over draws of P_L(drifted choice), bootstrap
    ratio: Interval | None  # retained / gain (v2's M6), only when stable


STABILITY_RULE = (
    "M6 (retained / original gain) is reported only when the 95% interval of the original gain "
    "P_L(fixed) - P_L(true-model choice) has its lower end at or above half its estimate; the "
    "absolute retained gain is always reported"
)


def retained_gain(
    failures: Sequence[int],
    shots: Sequence[int],
    fixed: int,
    true_choice: int,
    draw_choices: Sequence[int],
    replicates: int,
    seed: int,
) -> RetainedGain:
    """Percentile bootstrap over draws and binomial shot noise, as v2's M6 interval."""

    f = np.asarray(failures, dtype=np.int64)
    n = np.asarray(shots, dtype=np.int64)
    p_hat = f / n
    gain_ci = newcombe_difference(
        int(f[fixed]), int(n[fixed]), int(f[true_choice]), int(n[true_choice])
    )
    gain = Interval(gain_ci.estimate, gain_ci.low, gain_ci.high)
    stable = gain.estimate > 0 and gain.low >= 0.5 * gain.estimate
    choices = np.asarray(draw_choices, dtype=int)
    rng = np.random.Generator(np.random.PCG64(seed))
    kept = np.empty(replicates)
    rate = np.empty(replicates)
    ratio = np.empty(replicates)
    for r in range(replicates):
        p_star = rng.binomial(n, p_hat) / n
        sample = choices[rng.integers(0, len(choices), len(choices))]
        kept[r] = np.mean(p_star[fixed] - p_star[sample])
        rate[r] = np.mean(p_star[sample])
        den = p_star[fixed] - p_star[true_choice]
        ratio[r] = kept[r] / den if den != 0 else np.nan

    def pct(values: np.ndarray, estimate: float) -> Interval:
        low, high = np.nanpercentile(values, [2.5, 97.5])
        return Interval(estimate, float(low), float(high))

    kept_est = float(np.mean(p_hat[fixed] - p_hat[choices]))
    rate_est = float(np.mean(p_hat[choices]))
    return RetainedGain(
        gain=gain,
        stable=stable,
        retained=pct(kept, kept_est),
        choice_rate=pct(rate, rate_est),
        ratio=pct(ratio, kept_est / gain.estimate) if stable else None,
    )


__all__ = [
    "BETTER",
    "STABILITY_RULE",
    "TIE",
    "VERDICT_RULE",
    "WORSE",
    "CrossFit",
    "Fold",
    "HolmResult",
    "Regret",
    "RetainedGain",
    "cross_fit",
    "cross_fit_regret",
    "difference_verdict",
    "holm",
    "retained_gain",
    "two_proportion_p",
]
