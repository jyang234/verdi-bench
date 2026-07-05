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
from typing import Literal, Optional

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .errors import (
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
from .judge_config import JudgeConfig, model_vendor
from .metrics import PrimaryMetric


def _validate_cutoff(v: Optional[str], *, field: str) -> Optional[str]:
    """RFC 3339 or absent. Validated at the schema so a malformed cutoff is
    refused on every spec load, not first discovered mid-analysis; the same
    parser the dating channel runs, so load-time acceptance is analysis-time
    acceptance [EVAL-10 AC-1]. Absent stays legal (unknown)."""
    if v is None:
        return v
    from .dates import parse_rfc3339

    parse_rfc3339(v, field=field)
    return v


class AuxModel(BaseModel):
    """One additional model the arm's stack invokes beyond the primary
    [EVAL-20 AC-1]. Same fields as the primary declaration because an aux
    model is subject to the same honesty machinery (blinding, vendor overlap,
    contamination)."""

    model_config = ConfigDict(extra="forbid")
    model: str
    training_cutoff: Optional[str] = None

    @field_validator("training_cutoff")
    @classmethod
    def _cutoff_parses(cls, v: Optional[str]) -> Optional[str]:
        return _validate_cutoff(v, field="aux_model.training_cutoff")

    @field_validator("model")
    @classmethod
    def _require_vendor_prefix(cls, v: str) -> str:
        # JD-7 applies to every declared model: a prefix-less aux id would make
        # the arm's vendor set — and everything computed from it — undefined.
        if model_vendor(v) is None:
            raise AuxModelError(
                f"aux_models entry {v!r} must be '<provider>/<id>' so the arm's "
                "vendor set is well-defined [JD-7, EVAL-20 AC-1]"
            )
        return v


class Arm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    platform: str  # agent stack, e.g. "claude_code" / "codex"
    model: str
    payload: dict = Field(default_factory=dict)
    # EVAL-10 AC-1: the arm model's training-data cutoff (RFC 3339), feeding the
    # contamination tri-state. Optional — absent yields an honest `unknown`,
    # never `clean` (the cross-vendor honesty rule, §7.8).
    training_cutoff: Optional[str] = None
    # EVAL-20 AC-1: every additional model the arm's stack invokes, pre-registered
    # so blinding, vendor overlap, contamination, and comparability see the whole
    # stack — a sub-model cannot be quietly swapped post-lock.
    aux_models: list[AuxModel] = Field(default_factory=list)
    # EVAL-20 AC-6 [D003: declared-hosts-per-model]: egress hosts per declared
    # model. Keys must name declared models; feeds the spec-derived proxy
    # allowlist and per-trial egress attestation.
    model_hosts: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("training_cutoff")
    @classmethod
    def _cutoff_parses(cls, v: Optional[str]) -> Optional[str]:
        return _validate_cutoff(v, field="arm.training_cutoff")

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

    def declared_models(self) -> list[str]:
        """Every model id the arm pre-registered: primary first, then aux in
        declaration order — the single source for blinding canaries, vendor
        sets, and attestation [EVAL-20]."""
        return [self.model, *(a.model for a in self.aux_models)]

    @model_validator(mode="after")
    def _declared_set_well_defined(self) -> "Arm":
        # A duplicated declared id would double-count in vendor sets and make
        # per-model contamination breakdowns ambiguous — refuse loudly.
        models = self.declared_models()
        dupes = sorted({m for m in models if models.count(m) > 1})
        if dupes:
            raise AuxModelError(
                f"arm {self.name!r} declares duplicate model id(s) {dupes}; the "
                "declared model set must be unique [EVAL-20 AC-1]"
            )
        undeclared = sorted(set(self.model_hosts) - set(models))
        if undeclared:
            raise ModelHostsError(
                f"arm {self.name!r} model_hosts names undeclared model(s) "
                f"{undeclared}; declared: {models}. Egress attestation attributes "
                "against declared models only [EVAL-20 AC-6]"
            )
        empty = sorted(
            m for m, hosts in self.model_hosts.items()
            if not hosts or any(not h.strip() for h in hosts)
        )
        if empty:
            raise ModelHostsError(
                f"arm {self.name!r} model_hosts entries {empty} carry an empty "
                "host; declare real endpoints or omit the key [EVAL-20 AC-6]"
            )
        return self


class CorpusRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    version: str


class CostCeiling(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: float = Field(gt=0)
    currency: str = "USD"


class ContaminationConfig(BaseModel):
    """Pre-registered contamination parameters [EVAL-10, D003].

    Living inside the locked spec bytes makes the overlap threshold part of the
    cryptographic commitment — locked at plan, never tuned post-hoc against
    observed trials. A threshold outside (0, 1] is nonsense (0 flags everything,
    >1 flags nothing) and is refused at the schema.
    """

    model_config = ConfigDict(extra="forbid")
    overlap_threshold: float = Field(gt=0, le=1)


_RULE_RE = re.compile(
    r"^\s*delta_(?P<metric>[a-z_]+)\s*(?P<op>>=|<=|>|<|==)\s*(?P<num>-?\d+(?:\.\d+)?)\s*$"
)
# PL-11: `==` stays in _RULE_RE so a rule that uses it is *named* in the refusal,
# but it is deliberately absent from _OPS — equality on a bootstrap point
# estimate is never decidable, so it is rejected at parse rather than evaluated.
_OPS = {
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
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
        op = m.group("op")
        if op == "==":
            raise DecisionRuleError(
                f"decision_rule {raw!r} uses '=='; equality on a bootstrap float "
                "is never decidable — use >= or <="
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
            op=op,
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
    # EVAL-10 D003: contamination parameters ride the locked spec so they are
    # pre-registered by construction. Absent block ⇒ the module default applies
    # (itself a fixed constant, still not post-hoc tunable).
    contamination: Optional[ContaminationConfig] = None
    # F-H7 [REVIEW-D-P8-1]: the >2-arm decision policy is a decision-rule
    # component, so it rides the locked spec bytes — pre-registered, never an
    # analyze-time knob. Absent field ⇒ "none" (only the primary pair carries a
    # decision), the pre-existing default, so legacy locked specs parse and
    # analyze unchanged. Inert for 2-arm designs (n_pairs == 1).
    multi_arm_correction: Literal["none", "holm"] = "none"
    # EVAL-20 AC-6 [D005: experiment-level-shared]: non-model egress hosts
    # (package registries, mirrors), declared once for ALL arms so both face
    # identical infrastructure — per-arm infra could masquerade as a treatment
    # effect. Feeds the spec-derived proxy allowlist with the arms' model_hosts.
    infra_hosts: list[str] = Field(default_factory=list)

    @field_validator("infra_hosts")
    @classmethod
    def _infra_hosts_wellformed(cls, v: list[str]) -> list[str]:
        # An empty entry would suffix-match every trailing-dot hostname
        # (host_matches: host.endswith("." + "")) — a silent wildcard in the
        # locked allowlist. Refuse at the schema, like model_hosts does.
        bad = [h for h in v if not h or not h.strip()]
        if bad:
            raise InfraHostsError(
                "infra_hosts contains empty/whitespace host(s); an empty entry "
                "would suffix-match every trailing-dot hostname in the derived "
                "allowlist [EVAL-20 AC-6]"
            )
        return v

    @model_validator(mode="after")
    def _hosts_fully_declared(self) -> "ExperimentSpec":
        """A partial egress declaration is refused [EVAL-20 AC-6]: the derived
        allowlist REPLACES the runtime allowlist, so an arm that declares no
        model_hosts while the spec declares any hosts would have its model-API
        calls denied on every trial — a systematic per-arm bias, silently."""
        declaring = [a.name for a in self.arms if a.model_hosts]
        if not self.infra_hosts and not declaring:
            return self  # nothing declared: pre-EVAL-20 semantics, runtime allowlist
        missing = [a.name for a in self.arms if not a.model_hosts]
        if missing:
            source = (
                "infra_hosts is set" if not declaring
                else f"arm(s) {declaring} declare model_hosts"
            )
            raise ModelHostsError(
                f"egress hosts are partially declared: {source} but arm(s) "
                f"{missing} declare no model_hosts. The derived allowlist would "
                "deny the undeclared arm(s)' model APIs on every trial — declare "
                "model_hosts for every arm, or for none [EVAL-20 AC-6]"
            )
        return self

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

    @field_validator("arms")
    @classmethod
    def _unique_arm_names(cls, arms):
        # PL-10: duplicate arm names are a live bug — run's arm_map is keyed by
        # name and would silently collapse two arms into one, losing a whole
        # arm's trials. Refuse at the schema (D-P7-1: unique-names-required).
        names = [a.name for a in arms]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ArmNameError(
                f"arm names must be unique; duplicated: {dupes}. Each arm's trials "
                "are keyed by name, so duplicates would silently collapse [PL-10]"
            )
        return arms

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
    @classmethod
    def from_dict(cls, data: dict) -> "ExperimentSpec":
        """Validate a spec dict, surfacing the distinct named ``SpecError``.

        The pydantic validators are the single source of every spec rejection;
        pydantic wraps a validator's ValueError in a ``ValidationError`` but
        preserves the original in ``errors()[i]["ctx"]["error"]``. Re-raise the
        first wrapped ``SpecError`` so callers and tests still see the named type
        (PL-9: one validation source, no parallel prevalidation to drift)."""
        try:
            return cls.model_validate(data)
        except ValidationError as e:
            for err in e.errors():
                wrapped = err.get("ctx", {}).get("error")
                if isinstance(wrapped, SpecError):
                    raise wrapped from e
            raise

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
