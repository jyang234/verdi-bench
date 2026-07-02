"""EVAL-2 AC-6 — judge/arm vendor overlap disclosure."""

from __future__ import annotations

from harness.analyze.confounds import judge_vendor_overlap
from harness.schema.experiment import ExperimentSpec
from tests.fixtures.builders import valid_experiment_dict


def _spec(**judge_and_arms):
    return ExperimentSpec.from_dict(valid_experiment_dict(**judge_and_arms))


def test_ac6_cross_vendor_clean():
    # judge google; arms anthropic + openai ⇒ no overlap
    ov = judge_vendor_overlap(_spec())
    assert ov.overlap is False
    assert ov.overlapping_arms == []
    assert ov.judge_vendor == "google"


def test_ac6_vendor_overlap_flagged():
    data = valid_experiment_dict()
    data["judge"]["model"] = "anthropic/claude-3-5-sonnet-20241022"  # same as control arm
    ov = judge_vendor_overlap(ExperimentSpec.from_dict(data))
    assert ov.overlap is True
    assert "control" in ov.overlapping_arms
    flag = ov.as_flag()
    assert flag["flag"] == "judge_vendor_overlap" and flag["overlap"] is True
