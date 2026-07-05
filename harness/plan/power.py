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

[plan choice] The power sim's resampler is deliberately separate from EVAL-6's
``analyze.stats.paired_bootstrap``: this one performs a plain percentile-
bootstrap *reject* decision — the interval over resampled means excludes zero
(``_paired_bootstrap_rejects``; no null recentering, the simulated effect IS
the alternative) — while the analysis path computes a *confidence interval*
over observed deltas [F-L8: the old docstring mislabeled this as
"recentered-null"]. They are different statistics over the same clustering
model, not a duplicated CI, so they are not merged.
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
    """Real variance from a corpus calibration run [EVAL-8, PL-5]."""

    p: float
    rho: float
    n_tasks: int
    assumption_based: bool = False


def calibration_variance_from_runs(runs) -> Optional["CalibrationVariance"]:
    """Build a :class:`CalibrationVariance` from a manifest's calibration runs
    [PL-5]. Prefers the latest ``full`` run, else the latest run carrying the
    variance params; ``None`` if no run has ``p``/``rho``/``n_tasks`` (the caller
    then falls back to :class:`AssumedVariance`, flagged). This is the loader that
    replaces the old ``TODO(EVAL-8)`` — a calibrated experiment stops being
    ``assumption_based``."""
    usable = [r for r in (runs or []) if all(k in r for k in ("p", "rho", "n_tasks"))]
    if not usable:
        return None
    full = [r for r in usable if r.get("kind") == "full"]
    chosen = (full or usable)[-1]
    return CalibrationVariance(
        p=float(chosen["p"]), rho=float(chosen["rho"]), n_tasks=int(chosen["n_tasks"])
    )


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


def _simulate_clustered_pairs(
    rng: np.random.Generator,
    n_tasks: int,
    repetitions: int,
    p_a: float,
    p_b: float,
    rho: float,
) -> np.ndarray:
    """Return per-task-cluster mean differences (A − B), reps correlated within
    a task [D-P5-4].

    Two-level model. Each task draws a shared-difficulty regime with probability
    ``rho``: in that regime every rep *and* both arms read one shared task-
    difficulty draw, so the task's reps are identical (maximally correlated —
    extra reps add no information); otherwise every ``(arm, rep)`` draws
    independently, so averaging reps shrinks the within-task noise. The analysis
    unit is the **task cluster**: reps are reduced to a per-task mean and the
    caller resamples tasks. Reduces exactly to the old per-observation model at
    ``repetitions == 1``. Exact for equal marginals; a close approximation under
    a small effect.
    """
    task_shared = rng.random(n_tasks) < rho          # (n_tasks,)
    u_task = rng.random(n_tasks)                       # (n_tasks,) shared difficulty
    u_a = rng.random((n_tasks, repetitions))
    u_b = rng.random((n_tasks, repetitions))
    # in the shared regime every rep of the task reads the task draw
    eff_a = np.where(task_shared[:, None], u_task[:, None], u_a)
    eff_b = np.where(task_shared[:, None], u_task[:, None], u_b)
    a = (eff_a < p_a).astype(np.float64)
    b = (eff_b < p_b).astype(np.float64)
    return (a - b).mean(axis=1)                        # (n_tasks,) reduce reps → cluster


def simulate_clustered_pair_deltas(
    rng: np.random.Generator,
    n_tasks: int,
    repetitions: int,
    p_a: float,
    p_b: float,
    rho: float,
) -> np.ndarray:
    """Public alias of the shared clustered-pairs simulator [D-P5-4].

    EVAL-6's null-simulation harness reuses the *exact* variance model
    ``mde_check`` uses [master plan §7.7], so coverage selection and the power
    check draw from one clustering definition and cannot silently desync — the
    pre-registration power model and the realized-data analysis share one
    variance model.
    """
    return _simulate_clustered_pairs(rng, n_tasks, repetitions, p_a, p_b, rho)


def _power_at(
    rng: np.random.Generator,
    *,
    n_tasks: int,
    repetitions: int,
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
        diffs = _simulate_clustered_pairs(rng, n_tasks, repetitions, p_a, p_b, rho)
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
    n_tasks: Optional[int] = None,
    repetitions: Optional[int] = None,
) -> dict:
    """Return ``{mde, method, flags, ...}`` for ``spec`` under ``variance_source``.

    ``spec`` supplies the seed (deterministic sim) and the default ``repetitions``.
    ``n_tasks`` is the design's real **task-cluster** count (the corpus size);
    when omitted it falls back to ``variance_source.n_tasks`` (the calibration N)
    [PL-1]. The reps within a task are correlated, so the power model clusters by
    task and resamples clusters — the same variance model EVAL-6's analysis uses
    [D-P5-4]. If no swept delta reaches the power target, ``mde`` is ``None``
    (design cannot detect within the swept range at this N).
    """
    if deltas is None:
        deltas = [round(0.02 * k, 4) for k in range(1, 26)]  # 0.02 .. 0.50
    n_tasks = variance_source.n_tasks if n_tasks is None else n_tasks
    repetitions = spec.repetitions if repetitions is None else repetitions
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
            n_tasks=n_tasks,
            repetitions=repetitions,
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
        "n_tasks": n_tasks,
        "repetitions": repetitions,
        "p": p,
        "rho": rho,
        "power_target": power_target,
        "alpha": alpha,
        "power_curve": power_curve,
    }
