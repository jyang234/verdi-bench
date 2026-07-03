"""Verdict schema [EVAL-2 AC-4, AC-5].

Human verdicts (EVAL-7) share this schema family so kappa is directly computable.
Evidence is structurally required for a substantive winner: an evidence-free
A/B verdict is schema-rejected and re-recorded as ``CANT_JUDGE(malformed)`` —
never an exception trace. Any missing provenance field also fails schema.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class Winner(str, Enum):
    A = "A"
    B = "B"
    TIE = "TIE"
    CANT_JUDGE = "CANT_JUDGE"


class Confidence(str, Enum):
    """Verdict confidence, per the pre-registered event schema [EVAL-2, JD-12/D-4].

    Replaces the earlier bare ``float`` that the client hardcoded (0.5/0.8) while
    discarding the model's parsed value. A ``low|medium|high`` band is what the
    spec's event schema declares (``eval2.spec.md``)."""

    low = "low"
    medium = "medium"
    high = "high"


def confidence_bucket(x: float) -> Confidence:
    """Bucket a model's parsed 0..1 confidence into the pre-registered band [D-4].

    Thresholds are fixed so the mapping is reproducible; the bands, not the raw
    float, are what a downstream reader sees. A non-finite value (NaN/inf — a
    model can emit bare ``NaN`` in JSON and pydantic keeps it) is not a valid
    confidence and must not read as certainty: it maps to the LEAST-confident
    band, never silently to ``high`` (``NaN < 0.4`` is False) [fail loudly]."""
    if not math.isfinite(x) or x < 0.4:
        return Confidence.low
    if x < 0.75:
        return Confidence.medium
    return Confidence.high


class CantJudgeReason(str, Enum):
    """Enumerated fail-closed reasons [EVAL-2 §7.2].

    Mirrors grade's ``cant_grade`` reason taxonomy so a CANT_JUDGE verdict's
    ``reason`` field is a closed set, not an ad-hoc string. Stored on the
    verdict's free-form ``reason`` (str), so this is additive — no event-schema
    change.
    """

    IDENTITY_LEAK = "identity_leak"
    TIMEOUT = "timeout"
    REFUSAL = "refusal"
    PROVIDER_ERROR = "provider_error"
    PARSE = "parse"
    JUDGE_CANT_JUDGE = "judge_cant_judge"
    MALFORMED = "malformed"


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["diff", "holdout"]
    response: Literal["A", "B"]
    hunk: Optional[str] = None
    ref: Optional[str] = None

    @model_validator(mode="after")
    def _needs_locator(self) -> "Evidence":
        if self.hunk is None and self.ref is None:
            raise ValueError("evidence needs a locator: hunk (diff) or ref (holdout)")
        return self


class VerdictProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    judge_model: str
    rubric_sha256: str
    packet_sha256: str
    call_ids: list[str]
    orders: str  # "both" | "single"
    temperature: float
    ts: str


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    winner: Winner
    reason: str
    evidence: list[Evidence] = []
    confidence: Confidence = Confidence.low
    order_inconsistent: bool = False
    provenance: VerdictProvenance
    # for human verdicts (EVAL-7): distinguishes the verdict source
    source: Literal["judge", "human"] = "judge"
    # comparison identity + task class, so kappa groups and the ledger state
    # machine can tell whether a comparison is closed [AC-7]
    comparison_id: Optional[str] = None
    task_class: Optional[str] = None
    # the task a comparison belongs to — lets the review sampler resolve a
    # comparison's per-arm holdout rates (its deterministic winner) without
    # assuming comparison_id == task_id [RV-3/RV-4].
    task_id: Optional[str] = None
    # D-P4-1 (a slice of AN-1): the A/B -> physical-arm map the judge scored in,
    # recorded as per-verdict provenance so Phase-5 judge-preference analysis (AN-1)
    # can attribute a delta to the right arm without assuming convention. (The
    # Phase-4 kappa join is already frame-correct because review record translates
    # the human's response pick into this same A=arms[0] frame — arm_map is the
    # durable record of that frame, read by analyze.) None on legacy verdicts.
    arm_map: Optional[dict[str, str]] = None
    # JD-11: True when orders='single' — D003 order-debiasing was skipped, so a
    # full experiment cannot silently omit it. Rides visibly on the verdict.
    single_order: bool = False

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, v):
        # Migration [D-4]: a legacy verdict recorded confidence as a float; the
        # versioned reader maps it to the enum band so old-shape verdicts still
        # read. A low|medium|high value passes through unchanged. verdi-bench has
        # no production ledgers, so this is a documented compatibility note, not a
        # live migration.
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return confidence_bucket(float(v)).value
        return v

    @model_validator(mode="after")
    def _substantive_needs_evidence(self) -> "Verdict":
        if self.winner in (Winner.A, Winner.B) and not self.evidence:
            raise ValueError(
                "a substantive verdict (winner A/B) must cite evidence; "
                "evidence-free verdicts are recorded as CANT_JUDGE(malformed)"
            )
        # a completed both-orders comparison carries both call ids; a fail-closed
        # CANT_JUDGE may carry fewer (a call never completed)
        if (
            self.winner != Winner.CANT_JUDGE
            and self.provenance.orders == "both"
            and len(self.provenance.call_ids) != 2
        ):
            raise ValueError("orders='both' requires exactly two call_ids")
        return self
