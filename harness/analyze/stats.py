"""Paired bootstrap over task clusters [EVAL-6 §M1, D001, AC-1].

``paired_bootstrap`` resamples **task indices** with replacement (clusters — the
unit of analysis is the task, per D004's framing), 10k resamples by default, all
randomness from ``numpy.random.Generator(PCG64(sub_seed(seed, ...)))`` with a
namespaced sub-seed [master plan §7.5]. The result is byte-identical for a given
``(deltas, seed, method)`` — numpy is pinned, there is no parallel nondeterminism,
and full-precision floats are stored (rendered at fixed decimals downstream).

The CI estimator is injected through the ``CIMethod`` seam (:mod:`.ci`); D004
selects it by coverage (:mod:`.nullsim`), with ``percentile`` the fallback.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.random import PCG64, Generator

from ..plan.seeds import sub_seed
from .ci import CIMethod, resolve_ci_method

DEFAULT_N_BOOT = 10_000
DEFAULT_CI_LEVEL = 0.95


@dataclass(frozen=True)
class BootstrapResult:
    mean_delta: float
    ci_low: float
    ci_high: float
    ci_method: str
    ci_level: float
    n_boot: int
    n_tasks: int

    def as_dict(self) -> dict:
        """Full-precision serialization — the findings doc renders at fixed dp."""
        return {
            "mean_delta": self.mean_delta,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "ci_method": self.ci_method,
            "ci_level": self.ci_level,
            "n_boot": self.n_boot,
            "n_tasks": self.n_tasks,
        }

    def excludes_zero(self) -> bool:
        """Whether the CI lies strictly to one side of zero (a detected effect)."""
        return self.ci_low > 0.0 or self.ci_high < 0.0


def paired_bootstrap(
    per_task_deltas,
    seed: int,
    ci_method: str | CIMethod = "percentile",
    *,
    n_boot: int = DEFAULT_N_BOOT,
    ci_level: float = DEFAULT_CI_LEVEL,
) -> BootstrapResult:
    """Bootstrap the mean per-task delta with a CI from ``ci_method``.

    ``per_task_deltas`` is one delta per task (already reduced over repetitions).
    """
    deltas = np.asarray(list(per_task_deltas), dtype=np.float64)
    n = deltas.shape[0]
    if n == 0:
        raise ValueError("paired_bootstrap needs at least one per-task delta")

    rng = Generator(PCG64(sub_seed(seed, "paired_bootstrap")))
    idx = rng.integers(0, n, size=(n_boot, n))
    samples = deltas[idx]
    boot_means = samples.mean(axis=1)
    if n > 1:
        boot_ses = samples.std(axis=1, ddof=1) / math.sqrt(n)
    else:
        boot_ses = np.zeros(n_boot, dtype=np.float64)

    method = resolve_ci_method(ci_method)
    lo, hi = method.interval(deltas, boot_means, boot_ses, ci_level)
    return BootstrapResult(
        mean_delta=float(deltas.mean()),
        ci_low=float(lo),
        ci_high=float(hi),
        ci_method=method.name,
        ci_level=ci_level,
        n_boot=n_boot,
        n_tasks=n,
    )
