"""Back-compat facade for the grading-container subsystem [refactor 05 §2].

``grade/container.py`` was a 555-line file with four tangled concerns. It is now
split into focused modules — the fenced-stdout transport (:mod:`fence`), the
throwaway-copy + exit-classification discipline (:mod:`isolation`), the runner
family + orchestrator (:mod:`runners`), and the containerized plugin recipe
(:mod:`plugins.launch`). This module survives only as a **compatibility facade**
that re-exports every name external importers still reach through
``harness.grade.container`` — ``judge/assemble``'s ``HOLDOUT_RESULTS``,
``corpus``'s runner + error names, and the eval5 / e2e test suites — so nothing
outside ``grade/`` changed import path. New code should import from the owning
module directly.
"""

from __future__ import annotations

from .fence import (
    NONCE_ENV,
    PLUGIN_FENCE_BEGIN,
    PLUGIN_FENCE_END,
    RESULTS_FENCE_BEGIN,
    RESULTS_FENCE_END,
    GraderUnavailableError,
    GradingContainerError,
    HoldoutResultsMissingError,
    HoldoutRun,
    holdout_fence,
    parse_fenced_stdout,
    plugin_fence,
)
from .runners import (
    DEFAULT_GRADER_IMAGE,
    HOLDOUT_RESULTS,
    DockerGradeRunner,
    GradeRunner,
    GradingContainer,
    LocalExecutingGradeRunner,
    LocalGradeRunner,
)

__all__ = [
    # transport [fence]
    "NONCE_ENV",
    "RESULTS_FENCE_BEGIN",
    "RESULTS_FENCE_END",
    "PLUGIN_FENCE_BEGIN",
    "PLUGIN_FENCE_END",
    "holdout_fence",
    "plugin_fence",
    "parse_fenced_stdout",
    "HoldoutRun",
    "GradingContainerError",
    "GraderUnavailableError",
    "HoldoutResultsMissingError",
    # runners + orchestrator [runners]
    "DEFAULT_GRADER_IMAGE",
    "HOLDOUT_RESULTS",
    "GradeRunner",
    "DockerGradeRunner",
    "LocalGradeRunner",
    "LocalExecutingGradeRunner",
    "GradingContainer",
]
