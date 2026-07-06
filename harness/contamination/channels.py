"""Contamination probe channels + typed payload [refactor 06 §3, EVAL-10 AC-3/AC-4].

The three detection channels the memory probe runs, each a pure function with a
**declared** evidence label (the bare ``"canary_regurgitation"`` /
``"oracle_prefix"`` / ``"solution_overlap"`` literals scattered through the
probe loop are now these module constants), plus the pydantic model for the
``contamination_probe`` event payload.

Deliberately imports no provider client [EVAL-10 AC-6, import-linter contract]:
the channels only *interpret* completions the probe already fetched, so this
module and its label/status/outcome constants are safe for the deterministic
``summary`` reader to consume. ``ContaminationProbePayload.model_dump`` is
byte-compatible with the hand-built dict it replaces — the shape is typing, not
a contract change (:mod:`tests.test_eval10_probe` pins the equality).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, model_serializer

from .overlap import solution_overlap

# --- declared evidence labels (ledgered; never change) -----------------------
CANARY_REGURGITATION = "canary_regurgitation"
ORACLE_PREFIX = "oracle_prefix"
SOLUTION_OVERLAP = "solution_overlap"

# --- per-(arm, task) outcome tri-state + probe status (ledgered) -------------
FLAGGED = "flagged"
NEGATIVE = "negative"
UNPROBED = "unprobed"
STATUS_COMPLETE = "complete"
STATUS_CANT_PROBE = "cant_probe"

# F-M-C2 (approved): the oracle-prefix channel flags only when the TRUE prefix
# outperforms a perturbed CONTROL prefix by at least this margin. Without a
# control, formulaic code a clean model can legitimately continue tripped the
# >=threshold reconstruction test — and one false positive is asymmetric,
# refusing the official render.
ORACLE_CONTROL_MARGIN = 0.2


def canary_regurgitation_channel(canary: str, completion: str) -> Optional[str]:
    """AC-3 canary channel: the model reproduced the task's canary though it was
    stripped from the prompt ⇒ training-set membership. Its declared label or
    None."""
    return CANARY_REGURGITATION if canary in completion else None


def oracle_prefix_channel(
    completion: str,
    control_completion: str,
    *,
    remainder: str,
    control_remainder: str,
    threshold: float,
    margin_threshold: float = ORACLE_CONTROL_MARGIN,
) -> tuple[Optional[str], dict[str, float]]:
    """AC-3 oracle-prefix-with-control channel [F-M-C2]. Flags only when the TRUE
    prefix's continuation reconstructs the oracle remainder at/above ``threshold``
    AND beats the identifier-perturbed CONTROL by at least ``margin_threshold`` —
    so a formulaic continuer (high in both conditions, margin ~ 0) stays silent
    and only a memorization lift flags. Returns ``(label-or-None, {true, control,
    margin})`` — the scores ride the event whether or not the channel fired."""
    continuation = solution_overlap(completion, oracle=remainder, threshold=threshold)
    control = solution_overlap(control_completion, oracle=control_remainder, threshold=threshold)
    true_score = continuation.oracle_score or 0.0
    control_score = control.oracle_score or 0.0
    margin = round(true_score - control_score, 4)
    scores = {
        "true": round(true_score, 4),
        "control": round(control_score, 4),
        "margin": margin,
    }
    label = ORACLE_PREFIX if (continuation.flagged and margin >= margin_threshold) else None
    return label, scores


def solution_overlap_channel(flagged: bool) -> Optional[str]:
    """AC-4 deterministic overlap channel: the caller's per-(arm, task) disk-scan
    flag, merged into the same event. Its declared label or None — a provider
    outage never erases evidence already computed from disk."""
    return SOLUTION_OVERLAP if flagged else None


# --- typed event payload -----------------------------------------------------
class ArmProbe(BaseModel):
    """One arm's per-task probe outcomes + channel evidence [AC-3]."""

    model_config = ConfigDict(extra="forbid")

    model: str
    outcomes: dict[str, str]
    evidence: dict[str, list[str]]
    oracle_scores: Optional[dict[str, dict[str, float]]] = None

    @model_serializer
    def _payload(self) -> dict:
        out: dict = {"model": self.model, "outcomes": self.outcomes, "evidence": self.evidence}
        if self.oracle_scores is not None:
            out["oracle_scores"] = self.oracle_scores
        return out


class ContaminationProbePayload(BaseModel):
    """The ``contamination_probe`` event payload [EVAL-10 AC-3, D002].

    ``model_dump()`` reproduces the hand-built dict byte-for-byte (typing only;
    any *shape* change would be contract-approval): ``reason`` is always emitted
    (it is ``None`` on a complete probe), while ``alarms`` / ``skipped`` /
    ``task_id`` / ``arms`` / ``canary_sha256`` are omit-if-None, so the caller
    constructs unconditionally and the omission rule lives here once."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["complete", "cant_probe"]
    reason: Optional[str] = None
    threshold: float
    overlap_flags: dict[str, dict[str, bool]]
    alarms: Optional[list[str]] = None
    skipped: Optional[list[str]] = None
    task_id: Optional[str] = None
    arms: Optional[dict[str, ArmProbe]] = None
    canary_sha256: Optional[dict[str, str]] = None

    @model_serializer
    def _payload(self) -> dict:
        out: dict = {
            "status": self.status,
            "reason": self.reason,
            "threshold": self.threshold,
            "overlap_flags": self.overlap_flags,
        }
        if self.alarms is not None:
            out["alarms"] = self.alarms
        if self.skipped is not None:
            out["skipped"] = self.skipped
        if self.task_id is not None:
            out["task_id"] = self.task_id
        if self.arms is not None:
            out["arms"] = {name: arm.model_dump() for name, arm in self.arms.items()}
        if self.canary_sha256 is not None:
            out["canary_sha256"] = self.canary_sha256
        return out
