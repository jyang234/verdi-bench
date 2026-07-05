"""Null-simulation harness [EVAL-6 §M5, D004; master plan §7.7].

``coverage_from_deltas`` selects the CI method by empirical coverage under the
null (D004). At analyze time the realized per-task-cluster deltas are available,
so the null population is exactly those deltas **recentered to mean 0** (H0: no
effect), preserving the realized N, the realized variance, and the within-task
clustering [AN-4]. Each simulated null experiment resamples ``n`` clusters from
that population; the interval method whose empirical coverage is closest to
nominal is selected, and the findings record which method was selected plus its
measured coverage.

This is the analyze-time face of the one clustering model the power sim also
uses [D-P5-4]: the unit of analysis is the **task cluster**. At plan time there
is no data, so the power sim draws clusters parametrically
(``simulate_clustered_pair_deltas``); at analyze time there is data, so coverage
selection resamples the realized clusters. Both cluster by task, so the
pre-registration power model and the realized-data analysis cannot silently
desync. The null is **metric-appropriate by construction** — binary deltas
resample to binary deltas, continuous to continuous — and the ``null_model`` used
is recorded, so a continuous primary is never silently scored under a binary null
and no assumed ``0.5/0.3/50`` parameters leak in [AN-4].

BCa on clustered small-N can be unstable; that instability is exactly what this
harness surfaces — it is reported, never papered over [risks §9].
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.random import PCG64, Generator

from ..plan.seeds import sub_seed
from .ci import available_methods, resolve_ci_method

DEFAULT_N_SIM = 200
DEFAULT_N_BOOT = 10_000

# ``null_model`` labels by primary-metric family [AN-4].
NULL_BINARY = "paired_binary"          # holdout_pass_rate, judge_preference (bounded)
NULL_CONTINUOUS = "paired_continuous"  # cost_per_task, wall_time
NULL_INSUFFICIENT = "insufficient_data"  # <2 realized clusters — no selection ran


@dataclass(frozen=True)
class CoverageSelection:
    selected_method: str
    nominal: float
    coverage: dict[str, float]
    n_sim: int
    n_boot: int
    n_tasks: int
    null_model: str

    def as_dict(self) -> dict:
        return {
            "selected_method": self.selected_method,
            "nominal": self.nominal,
            "coverage": dict(sorted(self.coverage.items())),
            "n_sim": self.n_sim,
            "n_boot": self.n_boot,
            "n_tasks": self.n_tasks,
            "null_model": self.null_model,
        }


def _insufficient(n_tasks: int, ci_level: float) -> CoverageSelection:
    """Too few realized clusters to measure coverage.

    Fall back to the documented ``percentile`` method [D004 fallback] and
    **disclose** that no coverage selection ran (``null_model`` is always
    ``insufficient_data`` here) — no fabricated coverage, and no assumed-N binary
    null in place of realized data [AN-4]. ``nominal`` echoes the requested
    ``ci_level`` so it agrees with the level the fallback interval deploys."""
    return CoverageSelection(
        selected_method="percentile",
        nominal=ci_level,
        coverage={},
        n_sim=0,
        n_boot=0,
        n_tasks=n_tasks,
        null_model=NULL_INSUFFICIENT,
    )


def coverage_of_method(
    realized_deltas,
    seed: int,
    *,
    method: str,
    ci_level: float = 0.95,
    n_sim: int = DEFAULT_N_SIM,
    n_boot: int = DEFAULT_N_BOOT,
) -> Optional[float]:
    """Empirical coverage of ONE method under the recentered null [F-M-S1].

    The selfcheck's validation pass: selection (:func:`coverage_from_deltas`)
    and validation previously shared the same draws, so the winner's-curse on
    the selected method's coverage biased the gate toward passing. Run this
    with an INDEPENDENT seed (e.g. ``sub_seed(spec.seed, "selfcheck_validate")``)
    to score the already-selected method on fresh draws. ``None`` when fewer
    than two realized clusters exist.
    """
    deltas = np.asarray(list(realized_deltas), dtype=np.float64)
    n = deltas.shape[0]
    if n < 2:
        return None
    centered = deltas - deltas.mean()
    hits = 0
    data_rng = Generator(PCG64(sub_seed(seed, "nullsim_data")))
    for s_i in range(n_sim):
        sample = centered[data_rng.integers(0, n, size=n)]
        boot_rng = Generator(PCG64(sub_seed(seed, f"nullsim_boot_{s_i}")))
        idx = boot_rng.integers(0, n, size=(n_boot, n))
        samples = sample[idx]
        boot_means = samples.mean(axis=1)
        boot_ses = samples.std(axis=1, ddof=1) / np.sqrt(n)
        lo, hi, _ = resolve_ci_method(method).interval(sample, boot_means, boot_ses, ci_level)
        if lo <= 0.0 <= hi:
            hits += 1
    return hits / n_sim


def coverage_from_deltas(
    realized_deltas,
    seed: int,
    *,
    null_model: str,
    ci_level: float = 0.95,
    n_sim: int = DEFAULT_N_SIM,
    n_boot: int = DEFAULT_N_BOOT,
    methods: list[str] | None = None,
) -> CoverageSelection:
    """Pick the CI method whose empirical coverage is closest to nominal, under a
    realized recentered null at the realized N [AN-4, D004].

    ``realized_deltas`` are the primary comparison's per-task-cluster deltas. They
    are recentered to mean 0 to impose H0, then each of ``n_sim`` simulated null
    experiments resamples ``n`` clusters and every method sees the *same*
    simulated datasets and bootstrap resamples (fairness + determinism in
    ``seed``). Fewer than two realized clusters ⇒ no selection
    (``insufficient_data``, percentile fallback) — never a fabricated coverage.
    Ties break deterministically by method name.
    """
    deltas = np.asarray(list(realized_deltas), dtype=np.float64)
    n = deltas.shape[0]
    if n < 2:
        return _insufficient(n, ci_level)
    centered = deltas - deltas.mean()  # impose H0: true effect 0
    methods = methods or available_methods()
    hits = {m: 0 for m in methods}
    data_rng = Generator(PCG64(sub_seed(seed, "nullsim_data")))
    for s in range(n_sim):
        # a fresh null experiment: resample n clusters from the recentered pop
        sample = centered[data_rng.integers(0, n, size=n)]
        boot_rng = Generator(PCG64(sub_seed(seed, f"nullsim_boot_{s}")))
        idx = boot_rng.integers(0, n, size=(n_boot, n))
        samples = sample[idx]
        boot_means = samples.mean(axis=1)
        boot_ses = (
            samples.std(axis=1, ddof=1) / np.sqrt(n) if n > 1 else np.zeros(n_boot)
        )
        for m in methods:
            lo, hi, _ = resolve_ci_method(m).interval(sample, boot_means, boot_ses, ci_level)
            if lo <= 0.0 <= hi:
                hits[m] += 1
    coverage = {m: hits[m] / n_sim for m in methods}
    selected = min(coverage, key=lambda m: (abs(coverage[m] - ci_level), m))
    return CoverageSelection(
        selected_method=selected,
        nominal=ci_level,
        coverage=coverage,
        n_sim=n_sim,
        n_boot=n_boot,
        n_tasks=n,
        null_model=null_model,
    )
