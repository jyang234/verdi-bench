"""Confidence-interval methods behind the ``CIMethod`` seam [EVAL-6 §M2, D004].

Three interchangeable estimators over a paired-bootstrap distribution of
per-task-cluster means:

* ``percentile`` — the trivially-available fallback (D004's ``fixed-percentile``).
* ``bca`` — bias-corrected and accelerated; jackknife acceleration over task
  clusters, normal bias correction.
* ``cluster_robust_t`` — a studentized (bootstrap-t) interval; clusters are
  tasks, so the studentizing SE is the SEM of the per-task deltas. No t-tables,
  so it stays scipy-free and deterministic.

D004 selects among these by empirical coverage under the null-sim harness
(:mod:`harness.analyze.nullsim`); if D004 resolves to ``fixed-percentile`` the
seam collapses to ``percentile`` and nothing else changes.
"""

from __future__ import annotations

import math
import warnings
from typing import Protocol

import numpy as np


# --- normal helpers (scipy-free) -------------------------------------------
def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Inverse standard-normal CDF via Acklam's rational approximation.

    Accurate to ~1e-9 on (0,1); ±inf at the open bounds so callers can detect a
    degenerate bias correction and fall back.
    """
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    p_low = 0.02425
    p_high = 1.0 - p_low
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    if p <= p_high:
        q = p - 0.5
        r = q * q
        return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
               (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)


class CIMethod(Protocol):
    name: str

    def interval(
        self,
        deltas: np.ndarray,
        boot_means: np.ndarray,
        boot_ses: np.ndarray,
        level: float,
    ) -> tuple[float, float]: ...


def _tails(level: float) -> tuple[float, float]:
    alpha = 1.0 - level
    return 100.0 * alpha / 2.0, 100.0 * (1.0 - alpha / 2.0)


class PercentileCI:
    name = "percentile"

    def interval(self, deltas, boot_means, boot_ses, level):
        lo_p, hi_p = _tails(level)
        return float(np.percentile(boot_means, lo_p)), float(np.percentile(boot_means, hi_p))


class ClusterRobustTCI:
    """Studentized bootstrap-t interval; clusters = tasks (per-task deltas)."""

    name = "cluster_robust_t"

    # Below this fraction of usable (non-zero-SE) bootstrap resamples the
    # studentized quantile is unreliable (computed over a degenerate remnant),
    # so rather than dropping the degenerate resamples in silence [AN-11] we
    # disclose the drop and fall back transparently to the SE-free percentile
    # interval. Kept low (10%) so ordinary discrete-metric bootstraps — where a
    # sizeable minority of resamples legitimately tie and carry zero SE — still
    # studentize over their ample usable remainder unchanged; only genuine
    # near-total degeneracy triggers the fallback.
    _MIN_USABLE_FRACTION = 0.1

    def interval(self, deltas, boot_means, boot_ses, level):
        n = deltas.shape[0]
        m = float(deltas.mean())
        se = float(deltas.std(ddof=1) / math.sqrt(n)) if n > 1 else 0.0
        if se == 0.0:
            return PercentileCI().interval(deltas, boot_means, boot_ses, level)
        good = boot_ses > 0
        n_boot = int(boot_ses.shape[0])
        n_good = int(good.sum())
        if n_good < max(2, self._MIN_USABLE_FRACTION * n_boot):
            if n_boot - n_good:  # some resamples were degenerate — disclose the drop
                # Constant message so Python's default per-message dedup fires: this
                # runs once per null-sim iteration, and an interpolated count would
                # defeat dedup and flood a degenerate discrete-metric sim [AN-11].
                warnings.warn(
                    "cluster_robust_t: too many zero SE bootstrap resamples; "
                    "falling back to the percentile interval instead of "
                    "studentizing over the remnant [AN-11]",
                    stacklevel=2,
                )
            return PercentileCI().interval(deltas, boot_means, boot_ses, level)
        t_star = (boot_means[good] - m) / boot_ses[good]
        lo_p, hi_p = _tails(level)
        q_lo = float(np.percentile(t_star, lo_p))
        q_hi = float(np.percentile(t_star, hi_p))
        # symmetric mapping: high t* quantile pushes the lower bound down
        return m - q_hi * se, m - q_lo * se


class BCaCI:
    """Bias-corrected and accelerated interval [Efron]."""

    name = "bca"

    def interval(self, deltas, boot_means, boot_ses, level):
        m = float(deltas.mean())
        n = deltas.shape[0]
        # Mid-p bias correction: on discrete deltas many bootstrap means tie with
        # the observed mean, and a strict ``<`` biases z0 low (understating the
        # bootstrap mass at/below m). Count ties at half weight — the standard
        # mid-p fix [AN-11].
        frac = float(np.mean(boot_means < m)) + 0.5 * float(np.mean(boot_means == m))
        z0 = _norm_ppf(frac)
        if not math.isfinite(z0) or n < 3:
            return PercentileCI().interval(deltas, boot_means, boot_ses, level)
        # acceleration via jackknife of the cluster means
        total = deltas.sum()
        jack = (total - deltas) / (n - 1)  # leave-one-out means
        jbar = jack.mean()
        d = jbar - jack
        denom = 6.0 * (float((d ** 2).sum()) ** 1.5)
        a = float((d ** 3).sum()) / denom if denom != 0 else 0.0
        alpha = 1.0 - level
        z_lo = _norm_ppf(alpha / 2.0)
        z_hi = _norm_ppf(1.0 - alpha / 2.0)

        def adj(z: float) -> float:
            denom_a = 1.0 - a * (z0 + z)
            if denom_a == 0:
                return _norm_cdf(z0)
            return _norm_cdf(z0 + (z0 + z) / denom_a)

        p_lo = adj(z_lo) * 100.0
        p_hi = adj(z_hi) * 100.0
        return float(np.percentile(boot_means, p_lo)), float(np.percentile(boot_means, p_hi))


_METHODS: dict[str, CIMethod] = {
    PercentileCI.name: PercentileCI(),
    ClusterRobustTCI.name: ClusterRobustTCI(),
    BCaCI.name: BCaCI(),
}


def resolve_ci_method(method: str | CIMethod) -> CIMethod:
    if isinstance(method, str):
        try:
            return _METHODS[method]
        except KeyError:
            raise ValueError(
                f"unknown CI method {method!r}; expected one of {sorted(_METHODS)}"
            )
    return method


def available_methods() -> list[str]:
    return sorted(_METHODS)
