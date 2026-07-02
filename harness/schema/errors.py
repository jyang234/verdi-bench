"""Named, machine-recognizable schema errors [EVAL-3 AC-1].

Rejections carry a distinct type so callers and tests can assert *which* rule
fired rather than string-matching a generic ValidationError.
"""

from __future__ import annotations


class SpecError(ValueError):
    """Base for all experiment-spec rejections."""


class CompositePrimaryMetricError(SpecError):
    """primary_metric was a composite or unknown value [AC-1]."""


class MissingCostCeilingError(SpecError):
    """cost_ceiling absent — every experiment must declare one [EVAL-1-D007]."""


class AliasJudgeIdError(SpecError):
    """judge.model was an un-versioned alias id [EVAL-2 AC-5]."""


class DecisionRuleError(SpecError):
    """decision_rule string did not parse under DSL v1 [AC-1]."""
