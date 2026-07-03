"""``experiment.yaml`` → :class:`ExperimentSpec` [EVAL-3 AC-1].

A locked experiment is a cryptographic commitment; this schema is its shape.
``extra="forbid"`` everywhere so an unrecognized key is a rejection, not a
silent no-op. Named errors (:mod:`harness.schema.errors`) fire for the three
spec-level rejections the AC calls out: composite primary metric, missing cost
ceiling, alias judge id.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import (
    AliasJudgeIdError,
    ArmModelError,
    CompositePrimaryMetricError,
    DecisionRuleError,
    MissingCostCeilingError,
    SpecError,
)
from .judge_config import JudgeConfig, model_vendor
from .metrics import PrimaryMetric


class Arm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    platform: str  # agent stack, e.g. "claude_code" / "codex"
    model: str
    payload: dict = Field(default_factory=dict)

    @field_validator("model")
    @classmethod
    def _require_vendor_prefix(cls, v: str) -> str:
        # JD-7: a bare model id has no vendor to compare, so judge/arm vendor
        # overlap is silently wrong. Require '<provider>/<id>' at the schema, via
        # the one shared vendor-prefix definition.
        if model_vendor(v) is None:
            raise ArmModelError(
                f"arm.model {v!r} must be '<provider>/<id>' (e.g. "
                "'anthropic/claude-3-5-sonnet-20241022') so the judge/arm vendor "
                "overlap is well-defined [JD-7]"
            )
        return v


class CorpusRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    version: str


class CostCeiling(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: float = Field(gt=0)
    currency: str = "USD"


_RULE_RE = re.compile(
    r"^\s*delta_(?P<metric>[a-z_]+)\s*(?P<op>>=|<=|>|<|==)\s*(?P<num>-?\d+(?:\.\d+)?)\s*$"
)
_OPS = {
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
}


class DecisionRule(BaseModel):
    """Parsed decision rule — DSL v1: ``delta_<metric> <op> <threshold>``.

    [plan choice] a validated string, no expression engine. The metric must be
    the experiment's primary metric; direction/threshold are fixed at lock.
    """

    model_config = ConfigDict(extra="forbid")
    raw: str
    metric: str
    op: str
    threshold: float

    @classmethod
    def parse(cls, raw: str, primary: PrimaryMetric) -> "DecisionRule":
        m = _RULE_RE.match(raw or "")
        if not m:
            raise DecisionRuleError(
                f"decision_rule {raw!r} does not parse; expected "
                "'delta_<primary_metric> <op> <threshold>', e.g. "
                "'delta_holdout_pass_rate > 0'"
            )
        metric = m.group("metric")
        if metric != primary.value:
            raise DecisionRuleError(
                f"decision_rule references delta_{metric} but the primary metric "
                f"is {primary.value}; the rule must be on the primary metric"
            )
        return cls(
            raw=raw,
            metric=metric,
            op=m.group("op"),
            threshold=float(m.group("num")),
        )

    def decides_positive(self, observed_delta: float) -> bool:
        return _OPS[self.op](observed_delta, self.threshold)


class ExperimentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arms: list[Arm] = Field(min_length=2)
    corpus: CorpusRef
    repetitions: int = Field(gt=0)
    primary_metric: PrimaryMetric
    decision_rule: str
    judge: JudgeConfig
    seed: int
    cost_ceiling: CostCeiling
    # PL-12: a detectable effect is a positive fraction ≤ 1. A negative value is
    # always "underpowered" and a value > 1 always passes the gate — both are
    # nonsense. Enforced at the schema, so an out-of-range value is rejected on
    # every spec load (a nonsense effect is invalid everywhere, not just at plan);
    # verdi-bench has no pre-existing locked specs, so this cannot brick an
    # in-flight experiment.
    hypothesized_effect: Optional[float] = Field(default=None, gt=0, le=1)
    fractional_scoring: bool = False

    # Parsed form of decision_rule; populated post-validation.
    parsed_rule: Optional[DecisionRule] = Field(default=None, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _require_ceiling(cls, data):
        if isinstance(data, dict) and "cost_ceiling" not in data:
            raise MissingCostCeilingError(
                "experiment must declare a cost_ceiling [EVAL-1-D007]; none found"
            )
        return data

    @field_validator("primary_metric", mode="before")
    @classmethod
    def _reject_composite_metric(cls, v):
        if isinstance(v, PrimaryMetric):
            return v
        if v not in PrimaryMetric.values():
            raise CompositePrimaryMetricError(
                f"primary_metric {v!r} is not one of {PrimaryMetric.values()}; "
                "composite and unknown metrics are banned [EVAL-3-D006]"
            )
        return v

    @model_validator(mode="after")
    def _parse_rule(self) -> "ExperimentSpec":
        object.__setattr__(
            self, "parsed_rule", DecisionRule.parse(self.decision_rule, self.primary_metric)
        )
        return self

    # --- loaders -----------------------------------------------------------
    @staticmethod
    def _prevalidate(data: dict) -> None:
        """Surface the three AC-1 named rejections before pydantic wraps them.

        pydantic re-raises validator ValueErrors as ``ValidationError``; callers
        and tests want the distinct named type, so we check these cases up front.
        """
        from .judge_config import is_alias_model_id

        if "cost_ceiling" not in data:
            raise MissingCostCeilingError(
                "experiment must declare a cost_ceiling [EVAL-1-D007]; none found"
            )
        pm = data.get("primary_metric")
        if pm is not None and not isinstance(pm, PrimaryMetric):
            if pm not in PrimaryMetric.values():
                raise CompositePrimaryMetricError(
                    f"primary_metric {pm!r} is not one of {PrimaryMetric.values()}; "
                    "composite and unknown metrics are banned [EVAL-3-D006]"
                )
        judge = data.get("judge")
        if isinstance(judge, dict) and "model" in judge:
            if is_alias_model_id(judge["model"]):
                raise AliasJudgeIdError(
                    f"judge.model {judge['model']!r} is not a fully-versioned id; "
                    "alias ids are rejected at plan time [EVAL-2 AC-5]"
                )
        # JD-7: surface the named ArmModelError before pydantic wraps it, so a bare
        # arm model id (no vendor prefix) is a distinct, assertable rejection.
        arms = data.get("arms")
        if isinstance(arms, list):
            for arm in arms:
                if isinstance(arm, dict) and "model" in arm:
                    if model_vendor(str(arm["model"])) is None:
                        raise ArmModelError(
                            f"arm.model {arm['model']!r} must be '<provider>/<id>' so "
                            "the judge/arm vendor overlap is well-defined [JD-7]"
                        )
        rule = data.get("decision_rule")
        if pm in PrimaryMetric.values() and isinstance(rule, str):
            DecisionRule.parse(rule, PrimaryMetric(pm))

    @classmethod
    def from_dict(cls, data: dict) -> "ExperimentSpec":
        if isinstance(data, dict):
            cls._prevalidate(data)
        return cls.model_validate(data)

    @classmethod
    def from_yaml_text(cls, text: str, *, source: str = "<text>") -> "ExperimentSpec":
        """Parse an already-read yaml document. Separated from :meth:`from_yaml`
        so a caller that must hash the exact bytes it validates (the plan lock)
        can read the file once and parse *those* bytes — no re-read race [PL-2].
        """
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise SpecError(f"{source}: top-level YAML must be a mapping")
        return cls.from_dict(data)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ExperimentSpec":
        text = Path(path).read_text(encoding="utf-8")
        return cls.from_yaml_text(text, source=str(path))
