"""``harness.sdk`` — the experiment SDK facade [refactor 02, master plan §3].

The one place a library user imports from: the fluent :class:`Experiment`
builder, its :class:`Task` value object, the :class:`ExperimentWorkspace` stage
facade, the ``require_env_keys`` gate, the starter/rubric template loaders, and
re-exports of the read facade (:class:`LedgerView`) and the schema types.

``harness.sdk`` is a **leaf consumer**: it composes and re-exports subsystems and
is imported by *no* subsystem in turn (the ``sdk-is-a-leaf`` import contract).
The facade adds no second implementation of anything — it delegates (master
plan §3, single-responsibility directive).
"""

from __future__ import annotations

# Read facade (re-export) ------------------------------------------------------
from ..ledger.view import LedgerView, TrialEventView, TrialStory

# Schema facade (re-export) ----------------------------------------------------
from ..schema import (
    ExperimentSpec,
    JudgeConfig,
    PrimaryMetric,
    SpecError,
    TaskSpec,
    spec_to_yaml,
    tasks_to_yaml,
)
from .env import MissingEnvKeysError, require_env_keys
from .experiment import Experiment, Task
from .templates import (
    judge_rubric_text,
    starter_spec_text,
    starter_tasks_text,
)
from .workspace import (
    ContaminationProbeRefusal,
    CorpusAdmitPersistError,
    ExperimentWorkspace,
    RunQuarantineRefusal,
    WorkspaceError,
    write_holdout_results,
)

__all__ = [
    # builders
    "Experiment",
    "Task",
    # workspace facade
    "ExperimentWorkspace",
    "write_holdout_results",
    "WorkspaceError",
    "RunQuarantineRefusal",
    "ContaminationProbeRefusal",
    "CorpusAdmitPersistError",
    # env gating
    "require_env_keys",
    "MissingEnvKeysError",
    # templates (single-source starter + rubric)
    "starter_spec_text",
    "starter_tasks_text",
    "judge_rubric_text",
    # read facade
    "LedgerView",
    "TrialEventView",
    "TrialStory",
    # schema facade
    "ExperimentSpec",
    "TaskSpec",
    "JudgeConfig",
    "PrimaryMetric",
    "SpecError",
    "spec_to_yaml",
    "tasks_to_yaml",
]
