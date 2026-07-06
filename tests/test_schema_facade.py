"""The schema facade re-exports the public surface [refactor 02 §6].

One import site for SDK users; the names must resolve to the real submodule
objects (not shadow copies) so isinstance/except across the codebase still works.
"""

from __future__ import annotations

import harness.schema as facade
from harness.schema import (
    AliasJudgeIdError,
    ExperimentSpec,
    JudgeConfig,
    PrimaryMetric,
    SpecError,
    TaskSpec,
    spec_to_yaml,
    tasks_to_yaml,
)


def test_facade_reexports_are_the_canonical_objects():
    from harness.schema import experiment, judge_config, metrics, serialize, tasks

    assert ExperimentSpec is experiment.ExperimentSpec
    assert TaskSpec is tasks.TaskSpec
    assert JudgeConfig is judge_config.JudgeConfig
    assert PrimaryMetric is metrics.PrimaryMetric
    assert spec_to_yaml is serialize.spec_to_yaml
    assert tasks_to_yaml is tasks.tasks_to_yaml


def test_spec_error_hierarchy_is_exported():
    from harness.schema import errors

    for name in (
        "SpecError", "CompositePrimaryMetricError", "MissingCostCeilingError",
        "AliasJudgeIdError", "ArmModelError", "DecisionRuleError", "ArmNameError",
        "AuxModelError", "ModelHostsError", "InfraHostsError",
    ):
        assert getattr(facade, name) is getattr(errors, name)
    # the base is a real supertype of the leaves (except-clauses across the code
    # rely on this)
    assert issubclass(AliasJudgeIdError, SpecError)


def test_all_names_resolve():
    for name in facade.__all__:
        assert hasattr(facade, name), name
