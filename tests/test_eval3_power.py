"""EVAL-3 AC-4 — mde_check behavior and the injected variance seam [D007]."""

from __future__ import annotations

import numpy as np
import pytest

from harness.plan.power import (
    AssumedVariance,
    CalibrationVariance,
    calibration_variance_from_runs,
    mde_check,
    simulate_clustered_pair_deltas,
)
from harness.schema.errors import SpecError
from harness.schema.experiment import ExperimentSpec
from tests.fixtures.builders import valid_experiment_dict

FAST = dict(n_sim=40, n_boot=100, deltas=[0.05, 0.1, 0.2, 0.3, 0.5])


def _spec(**o):
    return ExperimentSpec.from_dict(valid_experiment_dict(**o))


def test_pl12_hypothesized_effect_bounded():
    """PL-12: hypothesized_effect must be a positive fraction ≤ 1; a negative or
    >1 value is refused at plan (was unbounded — always underpowered / always
    passing)."""
    for bad in (-0.1, 0.0, 1.5, 2.0):
        # a structural pydantic bound (gt=0, le=1) now surfaces through the
        # ExperimentSpec loader boundary as a SpecError-family SpecValidationError
        # rather than a raw ValidationError [refactor 13 OI-B].
        with pytest.raises(SpecError):
            _spec(hypothesized_effect=bad)
    assert _spec(hypothesized_effect=0.3).hypothesized_effect == 0.3
    assert _spec(hypothesized_effect=1.0).hypothesized_effect == 1.0


def test_pl1_mde_check_uses_real_n_override():
    """PL-1: an explicit ``n_tasks`` (the design's real cluster count) drives the
    sim, not the variance source's calibration n_tasks.

    Signature changed in Phase 5 5A: the power model now clusters by task, so the
    design's real size is the corpus's task-*cluster* count plus ``repetitions``,
    not a flat ``n`` observation count (D-P5-4)."""
    res = mde_check(_spec(), AssumedVariance(p=0.5, rho=0.3, n_tasks=999), n_tasks=8, **FAST)
    assert res.n_tasks == 8  # the real cluster count, not 999


# --- D-P5-4 / power-N: cluster by task, reps correlated within a task --------
def test_dp5_4_shared_regime_reps_add_no_information():
    """At rho=1 every task is in the shared-difficulty regime, so all reps read
    one task draw — adding reps changes nothing (correlated reps carry no extra
    information). The per-task deltas are identical for 1 vs 5 reps."""
    d1 = simulate_clustered_pair_deltas(np.random.default_rng(0), 200, 1, 0.6, 0.4, 1.0)
    d5 = simulate_clustered_pair_deltas(np.random.default_rng(0), 200, 5, 0.6, 0.4, 1.0)
    assert np.array_equal(d1, d5)


def test_dp5_4_independent_regime_reps_reduce_variance():
    """At rho=0 reps are independent, so averaging more reps shrinks a task's
    within-task noise — the model does capture rep information when it exists."""
    d1 = simulate_clustered_pair_deltas(np.random.default_rng(1), 400, 1, 0.5, 0.5, 0.0)
    d5 = simulate_clustered_pair_deltas(np.random.default_rng(1), 400, 5, 0.5, 0.5, 0.0)
    assert d5.var() < d1.var()


def test_dp5_4_reps_length_is_cluster_count():
    """The sim returns one delta per task *cluster* (the analysis unit), not one
    per (task, rep) — so the bootstrap resamples tasks, not correlated reps."""
    d = simulate_clustered_pair_deltas(np.random.default_rng(2), 17, 4, 0.5, 0.5, 0.3)
    assert d.shape == (17,)


def test_dp5_4_correlated_reps_do_not_beat_independent_tasks():
    """power-N reproduce: 10 task clusters × 3 correlated reps carry no more
    information than 30 correlated reps would — and *less* than 30 independent
    single-rep tasks. Under the old flat-N model both were N=30 and gave the same
    (optimistic) MDE; under clustering the correlated design needs a larger-or-
    equal MDE. Also records ``repetitions`` and the cluster count."""
    clustered = mde_check(
        _spec(), AssumedVariance(p=0.5, rho=0.6, n_tasks=999), n_tasks=10, repetitions=3, **FAST
    )
    flat = mde_check(
        _spec(repetitions=1), AssumedVariance(p=0.5, rho=0.6, n_tasks=999),
        n_tasks=30, repetitions=1, **FAST,
    )
    assert clustered.repetitions == 3 and clustered.n_tasks == 10
    # 10 correlated clusters detect a larger-or-equal minimum effect than 30
    # independent tasks (a None MDE = "cannot detect in range" is the largest).
    if clustered.mde is None:
        assert True
    else:
        assert flat.mde is not None and clustered.mde >= flat.mde


def test_pl5_calibration_variance_from_runs():
    """PL-5: the loader builds a CalibrationVariance from ledgered runs (prefers
    the latest full run); None when no run carries the variance params."""
    assert calibration_variance_from_runs([]) is None
    assert calibration_variance_from_runs([{"kind": "full"}]) is None
    cv = calibration_variance_from_runs([
        {"p": 0.5, "rho": 0.3, "n_tasks": 40, "kind": "subset"},
        {"p": 0.62, "rho": 0.25, "n_tasks": 80, "kind": "full"},
    ])
    assert cv is not None and cv.p == 0.62 and cv.n_tasks == 80
    assert cv.assumption_based is False


def test_ac4_mde_computed():
    # More task clusters => more power => a strictly smaller detectable effect.
    # (The old assertion `mde is None or mde <= 0.5` was a tautology: the swept
    # deltas top out at 0.5, so it held regardless of what the sim computed [XC-4].)
    small = mde_check(_spec(), AssumedVariance(p=0.5, rho=0.3, n_tasks=8), **FAST)
    large = mde_check(_spec(), AssumedVariance(p=0.5, rho=0.3, n_tasks=200), **FAST)
    assert small.method == "paired_binary_bootstrap_sim"
    # the small design must actually resolve an MDE in range (not None)...
    assert small.mde is not None
    # ...and the larger design must detect a strictly smaller effect. Fails if
    # the power model stops responding to N.
    assert large.mde is not None and large.mde < small.mde


def test_ac4_assumed_variance_flagged():
    res = mde_check(_spec(), AssumedVariance(), **FAST)
    assert "assumption_based_mde" in res.flags


def test_ac4_calibration_variance_not_flagged():
    res = mde_check(_spec(), CalibrationVariance(p=0.5, rho=0.3, n_tasks=80), **FAST)
    assert "assumption_based_mde" not in res.flags


def test_ac4_mde_deterministic_for_seed():
    a = mde_check(_spec(seed=7), AssumedVariance(n_tasks=60), **FAST)
    b = mde_check(_spec(seed=7), AssumedVariance(n_tasks=60), **FAST)
    assert a.power_curve == b.power_curve


def test_ac4_power_increases_with_effect():
    res = mde_check(_spec(), AssumedVariance(p=0.5, rho=0.2, n_tasks=100), **FAST)
    powers = [pt["power"] for pt in res.power_curve]
    # monotone-ish: the largest effect should have power >= the smallest
    assert powers[-1] >= powers[0]


def test_l9_schedule_order_pinned_across_shuffle_refactor():
    """F-L9: derive_schedule now delegates to the shared seeded_shuffle
    primitive — the realized order is seed-visible, so this pins the exact
    pre-refactor order for a fixed (seed, trials): any drift in the shuffle
    draw is a schedule change, not a cleanup."""
    from harness.plan.interleave import derive_schedule, enumerate_trials

    trials = enumerate_trials(["t1", "t2", "t3"], ["control", "treatment"], 2)
    out = [t.key() for t in derive_schedule(1234, trials)]
    assert out == [
        "t3|control|0", "t1|control|1", "t1|treatment|0", "t2|treatment|0",
        "t3|treatment|1", "t2|treatment|1", "t3|control|1", "t2|control|1",
        "t2|control|0", "t1|treatment|1", "t1|control|0", "t3|treatment|0",
    ]
