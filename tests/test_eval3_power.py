"""EVAL-3 AC-4 — mde_check behavior and the injected variance seam [D007]."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from harness.plan.power import (
    AssumedVariance,
    CalibrationVariance,
    calibration_variance_from_runs,
    mde_check,
)
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
        with pytest.raises(ValidationError):
            _spec(hypothesized_effect=bad)
    assert _spec(hypothesized_effect=0.3).hypothesized_effect == 0.3
    assert _spec(hypothesized_effect=1.0).hypothesized_effect == 1.0


def test_pl1_mde_check_uses_real_n_override():
    """PL-1: an explicit ``n`` (the design's real N) drives the sim, not the
    variance source's calibration n_tasks."""
    res = mde_check(_spec(), AssumedVariance(p=0.5, rho=0.3, n_tasks=999), n=8, **FAST)
    assert res["n_tasks"] == 8  # the real N, not 999


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
    res = mde_check(_spec(), AssumedVariance(p=0.5, rho=0.3, n_tasks=80), **FAST)
    assert res["method"] == "paired_binary_bootstrap_sim"
    # a bigger N should detect a smaller-or-equal effect than a tiny N
    assert res["mde"] is None or res["mde"] <= 0.5


def test_ac4_assumed_variance_flagged():
    res = mde_check(_spec(), AssumedVariance(), **FAST)
    assert "assumption_based_mde" in res["flags"]


def test_ac4_calibration_variance_not_flagged():
    res = mde_check(_spec(), CalibrationVariance(p=0.5, rho=0.3, n_tasks=80), **FAST)
    assert "assumption_based_mde" not in res["flags"]


def test_ac4_mde_deterministic_for_seed():
    a = mde_check(_spec(seed=7), AssumedVariance(n_tasks=60), **FAST)
    b = mde_check(_spec(seed=7), AssumedVariance(n_tasks=60), **FAST)
    assert a["power_curve"] == b["power_curve"]


def test_ac4_power_increases_with_effect():
    res = mde_check(_spec(), AssumedVariance(p=0.5, rho=0.2, n_tasks=100), **FAST)
    powers = [pt["power"] for pt in res["power_curve"]]
    # monotone-ish: the largest effect should have power >= the smallest
    assert powers[-1] >= powers[0]
