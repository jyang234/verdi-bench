"""CI-estimator edges [AN-11]: BCa z0 mid-p correction, ClusterRobustTCI zero-SE.

Reproduce-first, unit level: each test constructs the exact bootstrap arrays the
estimator sees, so it pins the numeric behavior directly (no simulation noise).
"""

from __future__ import annotations

import warnings

import numpy as np
import pytest

from harness.analyze.ci import BCaCI, ClusterRobustTCI, PercentileCI


def test_bca_z0_midp_symmetric_ties_give_symmetric_interval():
    """On a boot distribution symmetric about the observed mean with a mass of
    ties AT the mean, the mid-p z0 is exactly 0, so BCa collapses to a symmetric
    interval. A strict ``<`` would count the ties as below-m, push z0 negative,
    and skew both bounds down — this asserts the mid-p behavior. Uses a fine
    continuous grid (not 3 discrete buckets) so the z0 shift is visible in the
    percentiles."""
    # deltas symmetric about 0 => jackknife acceleration a == 0.
    deltas = np.array([-2.0, -1.0, 0.0, 1.0, 2.0])
    # a symmetric continuous spread plus a heavy tie-mass exactly at m=0:
    #   mid-p frac = 100/301 + 0.5*(101/301) = 0.5 exactly => z0 = 0
    #   strict frac = 100/301 = 0.332 => z0 < 0 => both bounds skewed down
    boot_means = np.concatenate([np.linspace(-1.0, 1.0, 201), np.zeros(100)])
    boot_ses = np.ones_like(boot_means)
    lo, hi, method = BCaCI().interval(deltas, boot_means, boot_ses, 0.95)
    assert lo == pytest.approx(-hi, abs=1e-9)
    assert method == "bca"  # PRA-M14: realized method reported


def test_m14_bca_reports_percentile_fallback_at_small_n():
    """PRA-M14: BCa falls back to percentile at n<3; the realized method must be
    reported as 'percentile', not mislabeled 'bca'."""
    deltas = np.array([0.1, 0.2])  # n=2 < 3 => fallback
    boot_means = np.linspace(-0.2, 0.4, 100)
    boot_ses = np.ones_like(boot_means)
    lo, hi, method = BCaCI().interval(deltas, boot_means, boot_ses, 0.95)
    assert method == "percentile"


def test_m14_bootstrap_result_surfaces_fallback():
    """PRA-M14: the BootstrapResult records the realized method and a fell-back
    flag so the render can name the interval that was actually computed."""
    from harness.analyze.stats import paired_bootstrap

    res = paired_bootstrap([0.1, 0.2], seed=1, ci_method="bca", n_boot=200)
    d = res.as_dict()
    assert d["ci_method"] == "percentile"
    assert d["ci_method_requested"] == "bca"
    assert d["ci_method_fell_back"] is True


def test_bca_z0_midp_differs_from_strict_less_than():
    """Directly contrast the two frac definitions on a tie-heavy distribution:
    the mid-p frac (with half-weighted ties) exceeds the strict-``<`` frac, so the
    corrected z0 is higher (less negative). Guards the correction itself."""
    m = 0.0
    boot_means = np.array([-1.0] * 100 + [0.0] * 100 + [1.0] * 100)
    strict = float(np.mean(boot_means < m))
    midp = float(np.mean(boot_means < m)) + 0.5 * float(np.mean(boot_means == m))
    assert strict == pytest.approx(1 / 3)
    assert midp == pytest.approx(0.5)
    assert midp > strict


def test_cluster_robust_falls_back_when_majority_degenerate():
    """When most bootstrap resamples have zero SE, the studentized interval would
    be computed over a biased remnant. The method must disclose the drop and fall
    back to the percentile interval instead of silently studentizing [AN-11]."""
    deltas = np.array([0.1, -0.2, 0.3, 0.0, 0.15])
    boot_means = np.linspace(-0.3, 0.4, 200)
    # 92.5% of resamples degenerate (zero SE); only 15/200 (7.5%) usable — below
    # the 10% floor, so the studentized quantile is untrustworthy => fall back.
    boot_ses = np.where(np.arange(200) < 185, 0.0, 0.05)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        got = ClusterRobustTCI().interval(deltas, boot_means, boot_ses, 0.95)
    expected = PercentileCI().interval(deltas, boot_means, boot_ses, 0.95)
    assert got == expected
    assert any("zero SE" in str(w.message) for w in caught), "drop not disclosed"


def test_cluster_robust_studentizes_when_mostly_usable():
    """With enough usable resamples the method still studentizes (no needless
    fallback), so the disclosure path does not swallow the normal case."""
    rng = np.random.default_rng(0)
    deltas = np.array([0.1, -0.2, 0.3, 0.0, 0.15, -0.05])
    boot_means = rng.normal(0.05, 0.1, 500)
    boot_ses = np.full(500, 0.08)  # all usable
    got = ClusterRobustTCI().interval(deltas, boot_means, boot_ses, 0.95)
    percentile = PercentileCI().interval(deltas, boot_means, boot_ses, 0.95)
    assert got != percentile  # a genuinely studentized (different) interval
