"""EVAL-3 AC-1 — experiment schema validation."""

from __future__ import annotations

import pytest

import yaml

from harness.schema.errors import (
    AliasJudgeIdError,
    ArmModelError,
    ArmNameError,
    CompositePrimaryMetricError,
    DecisionRuleError,
    MissingCostCeilingError,
)
from harness.schema.experiment import ExperimentSpec
from tests.fixtures.builders import valid_experiment_dict


def _mutate_missing_ceiling(d):
    del d["cost_ceiling"]


def _mutate_composite(d):
    d["primary_metric"] = "holdout_pass_rate+cost_per_task"


def _mutate_alias(d):
    d["judge"]["model"] = "google/gemini-pro"


def _mutate_arm_model(d):
    d["arms"][0]["model"] = "barecodemodel"  # no vendor prefix


def _mutate_bad_rule(d):
    d["primary_metric"] = "cost_per_task"
    d["decision_rule"] = "delta_holdout_pass_rate > 0"


@pytest.mark.parametrize(
    "mutate,error",
    [
        (_mutate_missing_ceiling, MissingCostCeilingError),
        (_mutate_composite, CompositePrimaryMetricError),
        (_mutate_alias, AliasJudgeIdError),
        (_mutate_arm_model, ArmModelError),
        (_mutate_bad_rule, DecisionRuleError),
    ],
)
@pytest.mark.parametrize("path", ["from_dict", "from_yaml_text"])
def test_ac1_named_errors_on_both_loader_paths(mutate, error, path):
    """PL-9: the pydantic validators are the single validation source; both
    loader paths surface the same distinct named SpecError (proving the collapse
    of the old _prevalidate duplicate is behavior-preserving)."""
    data = valid_experiment_dict()
    mutate(data)
    with pytest.raises(error):
        if path == "from_dict":
            ExperimentSpec.from_dict(data)
        else:
            ExperimentSpec.from_yaml_text(yaml.safe_dump(data))


def test_pl10_duplicate_arm_names_refused():
    """PL-10/D-P7-1: duplicate arm names are refused — run's arm_map would
    otherwise silently collapse two arms into one."""
    data = valid_experiment_dict()
    data["arms"][1]["name"] = data["arms"][0]["name"]  # collide the two arm names
    with pytest.raises(ArmNameError):
        ExperimentSpec.from_dict(data)


def test_pl11_equality_operator_refused_named():
    """PL-11: '==' in the decision rule is refused, naming the operator —
    equality on a bootstrap float is never decidable."""
    data = valid_experiment_dict(decision_rule="delta_holdout_pass_rate == 0")
    with pytest.raises(DecisionRuleError) as exc:
        ExperimentSpec.from_dict(data)
    assert "==" in str(exc.value)


def test_ac1_schema_valid():
    spec = ExperimentSpec.from_dict(valid_experiment_dict())
    assert spec.repetitions == 3
    assert spec.primary_metric.value == "holdout_pass_rate"
    assert spec.parsed_rule.op == ">"
    assert spec.parsed_rule.threshold == 0.0
    assert spec.cost_ceiling.amount == 25.0


def test_ac1_composite_metric_rejected():
    data = valid_experiment_dict(primary_metric="holdout_pass_rate+cost_per_task")
    with pytest.raises(CompositePrimaryMetricError):
        ExperimentSpec.from_dict(data)


def test_ac1_unknown_metric_rejected():
    data = valid_experiment_dict(primary_metric="vibes")
    with pytest.raises(CompositePrimaryMetricError):
        ExperimentSpec.from_dict(data)


def test_ac1_missing_cost_ceiling_rejected():
    data = valid_experiment_dict()
    del data["cost_ceiling"]
    with pytest.raises(MissingCostCeilingError):
        ExperimentSpec.from_dict(data)


def test_ac1_alias_judge_rejected():
    data = valid_experiment_dict()
    data["judge"]["model"] = "google/gemini-pro"  # no version segment
    with pytest.raises(AliasJudgeIdError):
        ExperimentSpec.from_dict(data)


def test_ac1_decision_rule_must_match_primary():
    data = valid_experiment_dict(
        primary_metric="cost_per_task", decision_rule="delta_holdout_pass_rate > 0"
    )
    with pytest.raises(DecisionRuleError):
        ExperimentSpec.from_dict(data)


def test_ac1_extra_key_forbidden():
    data = valid_experiment_dict(surprise="nope")
    with pytest.raises(Exception):
        ExperimentSpec.from_dict(data)


@pytest.mark.parametrize(
    "model,is_alias",
    [
        ("google/gemini-1.5-pro-002", False),
        ("anthropic/claude-3-5-sonnet-20241022", False),
        ("openai/gpt-4o-2024-08-06", False),
        ("google/gemini-pro", True),
        ("anthropic/claude-sonnet", True),
        ("openai/gpt-5", True),
        ("gemini-1.5-pro-002", True),  # missing provider
        # JD-6: a bare dotted version names a mutable family, not a pinned build —
        # it must be rejected (a date / build stamp / -NNN suffix is required).
        ("google/gemini-1.5-pro", True),
        ("openai/gpt-4.1", True),
        # ...but the same family with a pinned build suffix is accepted
        ("openai/gpt-4.1-2025-04-14", False),
    ],
)
def test_ac1_alias_detection(model, is_alias):
    from harness.schema.judge_config import is_alias_model_id

    assert is_alias_model_id(model) is is_alias
