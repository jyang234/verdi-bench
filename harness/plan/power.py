"""Power / minimum-detectable-effect check [EVAL-3 AC-4, D007].

``mde_check`` runs a seeded simulation under a paired-binary model and returns
the smallest effect detectable at 80% power / α=0.05 two-sided under the same
paired-bootstrap decision procedure EVAL-6 will use. The variance source is
**injected** [D007]:

* :class:`AssumedVariance` — pre-calibration; the result is flagged
  ``assumption_based_mde`` and that flag rides into the lock event and later
  into findings (do not quietly drop it).
* :class:`CalibrationVariance` — reads real calibration-run variance once
  EVAL-8 slice A has produced one.

[plan choice] The paired-bootstrap resampler is a local copy with a TODO to
unify with EVAL-6's once it lands.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

import numpy as np

from .seeds import sub_seed


class VarianceSource(Protocol):
    p: float
    rho: float
    n_tasks: int
    assumption_based: bool


@dataclass
class AssumedVariance:
    """Assumed per-arm success prob ``p`` and within-task correlation ``rho``.

    Wrong until calibration data exists — hence ``assumption_based=True``.
    """

    p: float = 0.5
    rho: float = 0.3
    n_tasks: int = 50
    assumption_based: bool = True


@dataclass
class CalibrationVariance:
    """Reads real variance from a calibration run [EVAL-8 slice A].

    Until slice A lands this is a thin holder; ``p``/``rho``/``n_tasks`` are
    populated from the calibration ledger by the loader (TODO(EVAL-8)).
    """

    p: float
    rho: float
    n_tasks: int
    assumption_based: bool = False


# TODO(EVAL-6): replace with the shared paired-bootstrap resampler.
def _paired_bootstrap_rejects(
    diffs: np.ndarray, rng: np.random.Generator, n_boot: int, alpha: float
) -> bool:
    """Two-sided paired bootstrap on per-task differences; reject H0: mean=0."""
    n = diffs.shape[0]
    if n == 0:
        return False
    idx = rng.integers(0, n, size=(n_boot, n))
    means = diffs[idx].mean(axis=1)
    lo, hi = np.percentile(means, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return lo > 0 or hi < 0


def _simulate_correlated_pairs(
    rng: np.random.Generator, n: int, p_a: float, p_b: float, rho: float
) -> np.ndarray:
    """Return per-task differences (A - B) for correlated Bernoulli outcomes.

    Correlation is induced by a shared-latent mixture: with prob ``rho`` both
    arms read the same task-difficulty draw, otherwise they draw independently.
    Exact for equal marginals; a close approximation under a small effect.
    """
    shared_mask = rng.random(n) < rho
    u_shared = rng.random(n)
    u_a = np.where(shared_mask, u_shared, rng.random(n))
    u_b = np.where(shared_mask, u_shared, rng.random(n))
    a = (u_a < p_a).astype(np.int8)
    b = (u_b < p_b).astype(np.int8)
    return (a - b).astype(np.float64)


def _power_at(
    rng: np.random.Generator,
    *,
    n: int,
    p: float,
    rho: float,
    delta: float,
    n_sim: int,
    n_boot: int,
    alpha: float,
) -> float:
    p_a = min(1.0, max(0.0, p + delta / 2))
    p_b = min(1.0, max(0.0, p - delta / 2))
    rejects = 0
    for _ in range(n_sim):
        diffs = _simulate_correlated_pairs(rng, n, p_a, p_b, rho)
        if _paired_bootstrap_rejects(diffs, rng, n_boot, alpha):
            rejects += 1
    return rejects / n_sim


def mde_check(
    spec,
    variance_source: VarianceSource,
    *,
    power_target: float = 0.80,
    alpha: float = 0.05,
    deltas: Optional[list[float]] = None,
    n_sim: int = 120,
    n_boot: int = 300,
) -> dict:
    """Return ``{mde, method, flags, ...}`` for ``spec`` under ``variance_source``.

    ``spec`` supplies the seed (deterministic sim). If no swept delta reaches the
    power target, ``mde`` is ``None`` (design cannot detect within the swept
    range at this N).
    """
    if deltas is None:
        deltas = [round(0.02 * k, 4) for k in range(1, 26)]  # 0.02 .. 0.50
    n = variance_source.n_tasks
    p = variance_source.p
    rho = variance_source.rho

    mde: Optional[float] = None
    power_curve: list[dict] = []
    for delta in sorted(deltas):
        # Common random numbers across deltas: reseed to the SAME base each
        # delta so the underlying task-difficulty draws are shared and only the
        # effect size varies. This makes the power curve monotone and prevents a
        # noise-driven early crossing from understating the MDE. Deterministic in
        # spec.seed.
        rng = np.random.default_rng(sub_seed(spec.seed, "mde"))
        power = _power_at(
            rng,
            n=n,
            p=p,
            rho=rho,
            delta=delta,
            n_sim=n_sim,
            n_boot=n_boot,
            alpha=alpha,
        )
        power_curve.append({"delta": delta, "power": round(power, 3)})
        if mde is None and power >= power_target:
            mde = delta

    flags: list[str] = []
    if getattr(variance_source, "assumption_based", False):
        flags.append("assumption_based_mde")

    return {
        "mde": mde,
        "method": "paired_binary_bootstrap_sim",
        "flags": flags,
        "n_tasks": n,
        "p": p,
        "rho": rho,
        "power_target": power_target,
        "alpha": alpha,
        "power_curve": power_curve,
    }
