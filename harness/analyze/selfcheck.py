"""Harness self-validation [EVAL-1-D008; master plan §7.7].

The coverage self-check the D008 gate requires before the first official finding:
extract the experiment's realized primary-comparison per-task deltas (the *same*
task-cluster model :mod:`harness.analyze.report` uses), estimate the selected CI
method's empirical coverage under the recentered null at the realized N, and
**pass iff the nominal CI level lies within the Wilson 95% interval** of that
estimated coverage. The pass band self-scales with ``n_sim`` — there is no magic
tolerance constant.

The selfcheck seed is derived from the *locked* experiment seed
(``sub_seed(spec.seed, "selfcheck")``), so the check is deterministic and cannot
be re-rolled until it passes. A failing selfcheck makes the experiment
exploratory-only (the official fence refuses); nothing else is blocked. An
experiment too small to selfcheck (``< 2`` realized clusters) fails closed with
``null_model = "insufficient_data"``.
"""

from __future__ import annotations

import math
from typing import Optional

from ..plan.seeds import sub_seed
from ..schema.metrics import PrimaryMetric
from .nullsim import NULL_INSUFFICIENT, coverage_from_deltas
from .report import (
    _METRIC_TELEMETRY_FIELD,
    _comparison_series,
    _holdout_values,
    _null_model_for_metric,
    _telemetry_values,
)

# 95% two-sided normal quantile — the Monte-Carlo interval level [D008 (c)].
_Z95 = 1.959963984540054


def wilson_interval(p_hat: float, n: int, *, z: float = _Z95) -> tuple[float, float]:
    """Wilson score interval for a proportion ``p_hat`` observed over ``n`` trials.

    Self-scaling: the band tightens as ``n_sim`` grows, so the selfcheck's
    strictness is set by how many Monte-Carlo replicates ran, not a fixed
    tolerance [D008 (c)]."""
    if n <= 0:
        return (0.0, 1.0)
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p_hat + z2 / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p_hat * (1.0 - p_hat) / n + z2 / (4.0 * n * n))
    return (center - half, center + half)


def _primary_comparison_deltas(ledger_path, spec):
    """The realized per-task-cluster deltas of the primary (first) comparison and
    the metric-appropriate null model — the same extraction analyze uses."""
    primary = spec.primary_metric.value
    if primary == PrimaryMetric.holdout_pass_rate.value:
        per_task = _holdout_values(ledger_path)
    elif primary in _METRIC_TELEMETRY_FIELD:
        per_task = _telemetry_values(ledger_path, _METRIC_TELEMETRY_FIELD[primary])
    elif primary == PrimaryMetric.judge_preference.value:
        per_task = None
    else:  # pragma: no cover - the metric enum is closed
        raise ValueError(f"unsupported primary metric {primary!r}")
    arm_a, arm_b = spec.arms[0].name, spec.arms[1].name
    _, _, deltas = _comparison_series(primary, per_task, ledger_path, arm_a, arm_b)
    return deltas, _null_model_for_metric(primary)


def run_selfcheck(
    ledger_path, spec, *, n_sim: int = 200, n_boot: int = 10_000, ci_level: float = 0.95
) -> dict:
    """Compute the selfcheck result dict (the ``selfcheck`` event payload).

    Deterministic in ``spec.seed``: same ledger ⇒ byte-identical payload."""
    deltas, null_model = _primary_comparison_deltas(ledger_path, spec)
    seed = sub_seed(spec.seed, "selfcheck")
    sel = coverage_from_deltas(
        deltas, seed, null_model=null_model, ci_level=ci_level,
        n_sim=n_sim, n_boot=n_boot,
    )
    if sel.null_model == NULL_INSUFFICIENT:
        # too small to selfcheck: cannot render official [D008 (b)].
        return _result(sel.selected_method, ci_level, None, None, sel.n_sim,
                       sel.n_boot, sel.n_tasks, NULL_INSUFFICIENT, passed=False)
    coverage = sel.coverage[sel.selected_method]
    lo, hi = wilson_interval(coverage, sel.n_sim)
    passed = lo <= ci_level <= hi
    return _result(sel.selected_method, ci_level, coverage, [lo, hi], sel.n_sim,
                   sel.n_boot, sel.n_tasks, null_model, passed=passed)


def _result(selected_method, nominal, coverage, mc_interval, n_sim, n_boot,
            n_tasks, null_model, *, passed) -> dict:
    return {
        "selected_method": selected_method,
        "nominal": nominal,
        "coverage": coverage,
        "mc_interval": mc_interval,
        "n_sim": n_sim,
        "n_boot": n_boot,
        "n_tasks": n_tasks,
        "null_model": null_model,
        "passed": passed,
    }


def selfcheck_passed(ledger_path) -> Optional[bool]:
    """The latest ledgered selfcheck's ``passed`` (latest wins), or None if none.

    Read-side helper the official fence uses [D008]."""
    from ..ledger import events
    from ..ledger.query import find_events

    found = find_events(ledger_path, events.SELFCHECK)
    return found[-1]["passed"] if found else None
