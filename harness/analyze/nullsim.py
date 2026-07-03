"""Null-simulation harness [EVAL-6 §M5, D004; master plan §7.7].

Build once, serve twice: this module both selects the CI method by empirical
coverage (D004) and is the substrate for EVAL-1-D008's A/A + coverage selfcheck.

``select_ci_method`` simulates null paired experiments at the experiment's N
(same variance model ``mde_check`` uses — imported, not re-derived), runs each
candidate ``CIMethod``, measures the share of intervals that cover the true null
effect (0), and picks the method whose coverage is closest to nominal. The
findings record which method was selected and its measured coverage.

BCa on clustered small-N can be unstable; that instability is exactly what this
harness surfaces — it is reported, never papered over [risks §9].
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.random import PCG64, Generator

from ..plan.power import simulate_clustered_pair_deltas
from ..plan.seeds import sub_seed
from .ci import available_methods, resolve_ci_method

DEFAULT_N_SIM = 200
DEFAULT_N_BOOT = 10_000


@dataclass
class VarianceParams:
    """The null variance model — mirrors ``power.VarianceSource`` fields.

    ``repetitions`` is the design's within-task rep count, so the null clusters
    by task exactly as the power sim does [D-P5-4]."""

    p: float
    rho: float
    n_tasks: int
    repetitions: int = 1


@dataclass(frozen=True)
class CoverageSelection:
    selected_method: str
    nominal: float
    coverage: dict[str, float]
    n_sim: int
    n_boot: int
    n_tasks: int

    def as_dict(self) -> dict:
        return {
            "selected_method": self.selected_method,
            "nominal": self.nominal,
            "coverage": dict(sorted(self.coverage.items())),
            "n_sim": self.n_sim,
            "n_boot": self.n_boot,
            "n_tasks": self.n_tasks,
        }


def _null_deltas(rng: Generator, params: VarianceParams) -> np.ndarray:
    """Per-task-cluster deltas under H0 (equal marginals ⇒ true effect 0)."""
    return simulate_clustered_pair_deltas(
        rng, params.n_tasks, params.repetitions, params.p, params.p, params.rho
    )


def measure_coverage(
    params: VarianceParams,
    seed: int,
    *,
    ci_level: float = 0.95,
    n_sim: int = DEFAULT_N_SIM,
    n_boot: int = DEFAULT_N_BOOT,
    methods: list[str] | None = None,
) -> dict[str, float]:
    """Empirical CI coverage of the true null (0) per method, over ``n_sim`` sims.

    Deterministic in ``seed``: one namespaced generator drives the whole sweep,
    and every method sees the *same* simulated datasets and the *same*
    bootstrap resamples, so coverage differences are the methods', not noise.
    """
    methods = methods or available_methods()
    hits = {m: 0 for m in methods}
    sim_rng = Generator(PCG64(sub_seed(seed, "nullsim_data")))
    for s in range(n_sim):
        deltas = _null_deltas(sim_rng, params)
        # Shared bootstrap resamples across methods (fairness + determinism).
        boot_rng = Generator(PCG64(sub_seed(seed, f"nullsim_boot_{s}")))
        n = deltas.shape[0]
        idx = boot_rng.integers(0, n, size=(n_boot, n))
        samples = deltas[idx]
        boot_means = samples.mean(axis=1)
        boot_ses = (
            samples.std(axis=1, ddof=1) / np.sqrt(n) if n > 1 else np.zeros(n_boot)
        )
        for m in methods:
            lo, hi = resolve_ci_method(m).interval(deltas, boot_means, boot_ses, ci_level)
            if lo <= 0.0 <= hi:
                hits[m] += 1
    return {m: hits[m] / n_sim for m in methods}


def select_ci_method(
    params: VarianceParams,
    seed: int,
    *,
    ci_level: float = 0.95,
    n_sim: int = DEFAULT_N_SIM,
    n_boot: int = DEFAULT_N_BOOT,
    methods: list[str] | None = None,
) -> CoverageSelection:
    """Pick the CI method whose empirical coverage is closest to nominal at N.

    Ties break deterministically by method name so the choice is reproducible.
    """
    coverage = measure_coverage(
        params, seed, ci_level=ci_level, n_sim=n_sim, n_boot=n_boot, methods=methods
    )
    nominal = ci_level
    selected = min(coverage, key=lambda m: (abs(coverage[m] - nominal), m))
    return CoverageSelection(
        selected_method=selected,
        nominal=nominal,
        coverage=coverage,
        n_sim=n_sim,
        n_boot=n_boot,
        n_tasks=params.n_tasks,
    )
