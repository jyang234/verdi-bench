"""Closed primary-metric vocabulary [EVAL-3-D006, master plan §7.3].

Defined in exactly one place so that both EVAL-3 schema validation and EVAL-9's
negative test (process dimensions are schema-ineligible as primaries) import the
same enum. Composites are unrepresentable: the field type is this enum, so a
composite string cannot validate.
"""

from __future__ import annotations

from enum import Enum


class PrimaryMetric(str, Enum):
    holdout_pass_rate = "holdout_pass_rate"
    judge_preference = "judge_preference"
    cost_per_task = "cost_per_task"
    wall_time = "wall_time"

    @classmethod
    def values(cls) -> list[str]:
        return [m.value for m in cls]
