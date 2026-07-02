"""Grading data contracts [EVAL-5 §4.1].

The assertion vector is the substance of a Layer-0 verdict. This layer contains
**no LLM calls** — determinism is its entire authority [import-linter contract].
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict


class AssertionResult(str, Enum):
    passed = "pass"
    failed = "fail"
    abstain = "abstain"


class Assertion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    source: str  # "holdout_test" or "plugin:<id>"
    result: AssertionResult
    detail: Optional[str] = None

    @property
    def is_holdout(self) -> bool:
        return self.source == "holdout_test"


@dataclass
class GradeTask:
    """Task definition as seen by the grader (distinct from the run-time Task)."""

    id: str
    task_sha: str
    holdouts_dir: str = ""              # bind-mounted read-only into the container
    plugin_ids: list[str] = field(default_factory=list)
    # FAKE/TEST ONLY: scripted holdout output + plugin behavior.
    fake_holdout_output: Optional[dict] = None
    fake_plugin_output: dict = field(default_factory=dict)
