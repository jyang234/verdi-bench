"""``spec_to_yaml`` round-trips through the existing validators [refactor 02 §2].

The write path adds no second validation source: a serialized spec must reload,
under the SAME pydantic model, to a spec equal to the original — for every
optional field. This is the property that lets the SDK author experiment.yaml
without a divergent hand-maintained template.
"""

from __future__ import annotations

import pytest
import yaml

from harness.schema.experiment import ExperimentSpec
from harness.schema.serialize import spec_to_yaml
from tests.fixtures.builders import valid_experiment_dict

# --- representative specs, exercising every optional field -------------------

_MINIMAL = valid_experiment_dict()

# Every optional field populated at once: per-arm training_cutoff / aux_models /
# model_hosts, spec-level infra_hosts / contamination / multi_arm_correction /
# hypothesized_effect / fractional_scoring, and a fully-specified judge block
# (non-default orders/temperature/escalation/panel/token_ceiling).
_FULL = valid_experiment_dict(
    arms=[
        {
            "name": "control",
            "platform": "claude_code",
            "model": "anthropic/claude-haiku-4-5-20251001",
            "payload": {"skill": "refactor-v2"},
            "training_cutoff": "2024-01-01T00:00:00Z",
            "aux_models": [
                {
                    "model": "anthropic/claude-opus-4-1-20250805",
                    "training_cutoff": "2023-06-01T00:00:00Z",
                }
            ],
            "model_hosts": {
                "anthropic/claude-haiku-4-5-20251001": ["api.anthropic.com"],
                "anthropic/claude-opus-4-1-20250805": ["api.anthropic.com"],
            },
        },
        {
            "name": "treatment",
            "platform": "codex",
            "model": "openai/gpt-4o-2024-08-06",
            "payload": {},
            "training_cutoff": "2024-08-01T00:00:00Z",
            "model_hosts": {"openai/gpt-4o-2024-08-06": ["api.openai.com"]},
        },
    ],
    infra_hosts=["pypi.org", "files.pythonhosted.org"],
    contamination={"overlap_threshold": 0.25},
    multi_arm_correction="holm",
    hypothesized_effect=0.15,
    fractional_scoring=True,
    repetitions=5,
    primary_metric="judge_preference",
    decision_rule="delta_judge_preference >= 0.1",
    judge={
        "model": "google/gemini-1.5-pro-002",
        "rubric": "rubrics/code-task-v1.md",
        "orders": "single",
        "temperature": 0.5,
        "escalation": {"kappa_threshold": 0.7, "min_human_verdicts": 30},
        "token_ceiling": 100_000,
        "panel": {"size": 3},
    },
    cost_ceiling={"amount": 50.0, "currency": "EUR"},
)

# A cost-metric spec with an aux model but NO egress declaration (aux_models is
# exercised independently of model_hosts).
_COST = valid_experiment_dict(
    primary_metric="cost_per_task",
    decision_rule="delta_cost_per_task < 0",
    fractional_scoring=True,
    hypothesized_effect=0.9,
    arms=[
        {
            "name": "control",
            "platform": "generic",
            "model": "anthropic/claude-haiku-4-5-20251001",
            "aux_models": [{"model": "openai/gpt-4o-mini-2024-07-18"}],
        },
        {
            "name": "treatment",
            "platform": "generic",
            "model": "openai/gpt-4o-2024-08-06",
        },
    ],
)

_WALL = valid_experiment_dict(
    primary_metric="wall_time",
    decision_rule="delta_wall_time <= 0",
    multi_arm_correction="none",
)

_REPRESENTATIVE = {
    "minimal": _MINIMAL,
    "full": _FULL,
    "cost_per_task": _COST,
    "wall_time": _WALL,
}


@pytest.mark.parametrize("name", sorted(_REPRESENTATIVE))
def test_spec_to_yaml_round_trips_equal(name):
    """from_yaml_text(spec_to_yaml(s)) == s for representative specs."""
    spec = ExperimentSpec.from_dict(_REPRESENTATIVE[name])
    reloaded = ExperimentSpec.from_yaml_text(spec_to_yaml(spec))
    assert reloaded == spec


def test_full_spec_covers_every_optional_field():
    """Guard the fixture itself: the 'full' spec must actually set every optional
    field, or the round-trip proof is vacuous for the ones it silently omits."""
    spec = ExperimentSpec.from_dict(_FULL)
    # spec-level optionals
    assert spec.hypothesized_effect is not None
    assert spec.fractional_scoring is True
    assert spec.contamination is not None
    assert spec.multi_arm_correction == "holm"
    assert spec.infra_hosts
    # arm-level optionals
    assert any(a.training_cutoff for a in spec.arms)
    assert any(a.aux_models for a in spec.arms)
    assert all(a.model_hosts for a in spec.arms)
    # judge-level optionals (non-default)
    assert spec.judge.orders == "single"
    assert spec.judge.temperature == 0.5
    assert spec.judge.token_ceiling is not None
    assert spec.judge.panel is not None
    assert spec.judge.escalation.kappa_threshold == 0.7


def test_parsed_rule_is_never_serialized():
    """parsed_rule is exclude=True; it must not leak into the YAML (it is derived
    from decision_rule on load, not a stored field)."""
    spec = ExperimentSpec.from_dict(_FULL)
    text = spec_to_yaml(spec)
    assert "parsed_rule" not in text
    data = yaml.safe_load(text)
    assert "parsed_rule" not in data


def test_spec_to_yaml_is_deterministic():
    """Same spec ⇒ same bytes (no wall-clock, no set-ordering) — determinism
    directive; the emitted file is what the lock will hash."""
    spec = ExperimentSpec.from_dict(_FULL)
    assert spec_to_yaml(spec) == spec_to_yaml(spec)


def test_output_is_loadable_yaml_mapping():
    """The emitted text is a YAML mapping (what from_yaml_text/the lock read)."""
    spec = ExperimentSpec.from_dict(_MINIMAL)
    assert isinstance(yaml.safe_load(spec_to_yaml(spec)), dict)
