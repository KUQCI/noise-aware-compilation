"""Cross-checks of the in-house statistics against SciPy (dev dependency)."""

from __future__ import annotations

import math

import pytest
from scipy import stats as sps

from noise_aware_compilation.stats import (
    Interval,
    chi2_sf,
    epsilon_per_round,
    homogeneity_test,
    mean_of_proportions,
    newcombe_difference,
    spearman,
    verdict,
    wilson,
)


def test_wilson_matches_scipy() -> None:
    for k, n in [(0, 100), (3, 100), (500, 1000), (41571, 600000)]:
        ours = wilson(k, n)
        ref = sps.binomtest(k, n).proportion_ci(method="wilson")
        assert ours.low == pytest.approx(ref.low, abs=1e-12)
        assert ours.high == pytest.approx(ref.high, abs=1e-12)


def test_chi2_and_homogeneity_match_scipy() -> None:
    for stat, dof in [(0.5, 1), (3.13, 7), (120.0, 99), (250.0, 177), (30.0, 4)]:
        assert chi2_sf(stat, dof) == pytest.approx(sps.chi2.sf(stat, dof), rel=1e-9)
    counts = [(410, 10000), (395, 10000), (450, 10000), (380, 10000)]
    table = [[s, n - s] for s, n in counts]
    ref = sps.chi2_contingency(table, correction=False)
    ours = homogeneity_test(counts)
    assert ours.statistic == pytest.approx(ref.statistic)
    assert ours.p_value == pytest.approx(ref.pvalue)


def test_spearman_matches_scipy() -> None:
    x = [0.3, 0.1, 0.2, 0.2, 0.5, 0.9]
    y = [3.0, 1.0, 2.5, 2.0, 4.0, 3.5]
    assert spearman(x, y) == pytest.approx(sps.spearmanr(x, y).statistic)
    assert math.isnan(spearman([1.0, 1.0], [2.0, 3.0]))


def test_epsilon_formula() -> None:
    assert epsilon_per_round(0.1, 1) == pytest.approx(0.1)
    p3 = 0.5 * (1 - (1 - 2 * 0.05) ** 3)
    assert epsilon_per_round(p3, 3) == pytest.approx(0.05)


def test_verdict_rule_r_r2() -> None:
    assert verdict(Interval(0.1, 0.09, 0.11), Interval(0.2, 0.19, 0.21)) == "better"
    assert verdict(Interval(0.2, 0.19, 0.21), Interval(0.1, 0.09, 0.11)) == "worse"
    assert verdict(Interval(0.1, 0.09, 0.2), Interval(0.2, 0.19, 0.21)) == "indistinguishable"


def test_newcombe_and_mean() -> None:
    d = newcombe_difference(100, 1000, 100, 1000)
    assert d.estimate == 0 and d.low < 0 < d.high
    m = mean_of_proportions([(10, 100), (30, 100)])
    assert m.interval.estimate == pytest.approx(0.2)
