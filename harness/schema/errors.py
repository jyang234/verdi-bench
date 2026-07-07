"""Named, machine-recognizable schema errors [EVAL-3 AC-1].

Rejections carry a distinct type so callers and tests can assert *which* rule
fired rather than string-matching a generic ValidationError.
"""

from __future__ import annotations

from ..errors import VerdiRefusal


class SpecError(VerdiRefusal, ValueError):
    """Base for all experiment-spec rejections."""


class SpecValidationError(SpecError):
    """A purely structural pydantic rejection (an extra key, too-few arms, a
    wrong field type) that no named validator wrapped. The ``ExperimentSpec``
    loaders raise it in place of a raw pydantic ``ValidationError`` so the schema
    boundary surfaces only ``SpecError`` — never a traceback. ``str`` is the
    pydantic message verbatim, so the tripwire needles and every message-pinning
    test keep matching [refactor 13 OI-B]."""


class CompositePrimaryMetricError(SpecError):
    """primary_metric was a composite or unknown value [AC-1]."""


class MissingCostCeilingError(SpecError):
    """cost_ceiling absent — every experiment must declare one [EVAL-1-D007]."""


class AliasJudgeIdError(SpecError):
    """judge.model was an un-versioned alias id [EVAL-2 AC-5]."""


class JudgePanelUnsupportedError(SpecError):
    """judge.panel was set — a v2 placeholder not implemented in v1 [ux-friction
    AC-8, D3]. The field stays in the schema as the v2 breadcrumb, but setting it
    is refused rather than silently changing the spec hash while nothing reads it
    (F9: the exact silent no-op ``extra='forbid'`` exists to prevent)."""


class ArmModelError(SpecError):
    """arm.model was not a '<provider>/<id>' vendor-prefixed id, so judge/arm
    vendor overlap could not be defined [JD-7]."""


class DecisionRuleError(SpecError):
    """decision_rule string did not parse under DSL v1 [AC-1]."""


class ArmNameError(SpecError):
    """Two arms share a name — the run's arm map would silently collapse them,
    losing a whole arm's trials [PL-10]."""


class AuxModelError(SpecError):
    """An aux_models entry was not vendor-prefixed, or duplicated another
    declared model — the declared model set must be well-defined for blinding,
    vendor overlap, and contamination [EVAL-20 AC-1]."""


class ModelHostsError(SpecError):
    """model_hosts named a model the arm never declared, carried an empty
    host, or was declared for some arms but not all — egress attestation must
    attribute against the declared set only, and a partial declaration would
    make the derived allowlist deny the undeclared arms' model APIs
    [EVAL-20 AC-6]."""


class InfraHostsError(SpecError):
    """infra_hosts carried an empty/whitespace host — an empty entry would
    suffix-match every trailing-dot hostname in the derived allowlist
    [EVAL-20 AC-6]."""
