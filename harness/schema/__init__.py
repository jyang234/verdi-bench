"""Schema facade [refactor 02 §6].

The public surface of the schema subsystem, so SDK users import spec/task models,
the metric vocabulary, the judge config, the named error hierarchy, and the
write-path serializers from one place instead of hunting submodules. No contract
impact: the import-linter references the package, not these submodules.
"""

from __future__ import annotations

from .errors import (
    AliasJudgeIdError,
    ArmModelError,
    ArmNameError,
    AuxModelError,
    CompositePrimaryMetricError,
    DecisionRuleError,
    InfraHostsError,
    MissingCostCeilingError,
    ModelHostsError,
    SpecError,
)
from .experiment import ExperimentSpec
from .judge_config import JudgeConfig
from .metrics import PrimaryMetric
from .serialize import spec_to_yaml
from .tasks import TaskSpec, tasks_to_yaml

__all__ = [
    # models
    "ExperimentSpec",
    "TaskSpec",
    "JudgeConfig",
    "PrimaryMetric",
    # serializers (write path)
    "spec_to_yaml",
    "tasks_to_yaml",
    # error hierarchy
    "SpecError",
    "CompositePrimaryMetricError",
    "MissingCostCeilingError",
    "AliasJudgeIdError",
    "ArmModelError",
    "DecisionRuleError",
    "ArmNameError",
    "AuxModelError",
    "ModelHostsError",
    "InfraHostsError",
]
