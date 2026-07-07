"""EVAL-2 AC-6 — judge/arm vendor overlap disclosure."""

from __future__ import annotations

import pytest

from harness.analyze.confounds import _vendor, judge_vendor_overlap
from harness.schema.errors import ArmModelError
from harness.schema.experiment import ExperimentSpec
from tests.fixtures.builders import valid_experiment_dict


def _spec(**judge_and_arms):
    return ExperimentSpec.from_dict(valid_experiment_dict(**judge_and_arms))


# --- JD-7: vendor is well-defined only for prefixed model ids ----------------
def test_jd7_vendor_rejects_prefixless_model():
    """JD-7: a prefix-less id has no vendor to compare — _vendor must fail loudly,
    not return the whole string (which made vendor-overlap silently wrong)."""
    assert _vendor("anthropic/claude-3-5-sonnet-20241022") == "anthropic"
    with pytest.raises(ValueError):
        _vendor("claude-3-5-sonnet")  # was silently returned whole


def test_jd7_arm_model_requires_vendor_prefix():
    """JD-7: the arm model must be '<provider>/<id>' so vendor overlap is defined —
    a bare arm model is a distinct ArmModelError on the spec-load path."""
    _spec()  # the default (prefixed) arms load cleanly
    bad = valid_experiment_dict()
    bad["arms"][0]["model"] = "claude-3-5-sonnet"  # no vendor prefix
    with pytest.raises(ArmModelError):
        ExperimentSpec.from_dict(bad)


def test_ac6_cross_vendor_clean():
    # judge fake (the template default); arms anthropic + openai ⇒ no overlap
    ov = judge_vendor_overlap(_spec())
    assert ov.overlap is False
    assert ov.overlapping_arms == []
    assert ov.judge_vendor == "fake"


def test_ac6_vendor_overlap_flagged():
    data = valid_experiment_dict()
    data["judge"]["model"] = "anthropic/claude-haiku-4-5-20251001"  # same as control arm
    ov = judge_vendor_overlap(ExperimentSpec.from_dict(data))
    assert ov.overlap is True
    assert "control" in ov.overlapping_arms
    flag = ov.as_flag()
    assert flag["flag"] == "judge_vendor_overlap" and flag["overlap"] is True
