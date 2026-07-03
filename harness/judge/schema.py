"""Verdict schema [EVAL-2 AC-4, AC-5].

Human verdicts (EVAL-7) share this schema family so kappa is directly computable.
Evidence is structurally required for a substantive winner: an evidence-free
A/B verdict is schema-rejected and re-recorded as ``CANT_JUDGE(malformed)`` —
never an exception trace. Any missing provenance field also fails schema.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, model_validator


class Winner(str, Enum):
    A = "A"
    B = "B"
    TIE = "TIE"
    CANT_JUDGE = "CANT_JUDGE"


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
    confidence: float = 0.0
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
    # D-P4-1 (a slice of AN-1): the A/B -> physical-arm map, so the judge-vs-human
    # kappa join resolves both winners to the same arm frame instead of assuming
    # convention. None on legacy verdicts (analyze falls back to the assumed frame).
    arm_map: Optional[dict[str, str]] = None
    # JD-11: True when orders='single' — D003 order-debiasing was skipped, so a
    # full experiment cannot silently omit it. Rides visibly on the verdict.
    single_order: bool = False

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
