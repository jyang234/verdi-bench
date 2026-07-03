"""Process scoring [EVAL-9 §M2, §M3, AC-2, AC-4].

The ``process_score`` event and the two scoring paths:

* **judge path** (:func:`score_trial_process`) — isolated model call over the
  post-redaction full transcript. Policy is **full-or-CANT_SCORE** [D004]: if the
  transcript does not fit the judge context, every dimension fails closed to
  ``CANT_SCORE(context_overflow)`` with token counts recorded — never silent
  truncation. Provider/parse failures likewise become CANT_SCORE(reason).
* **human path** (:func:`record_human_process_score`) — reachable **only after
  the EVAL-7 reveal** for the comparison exists; refused otherwise [AC-3].

A ``process_score`` is unrepresentable without unblinded provenance: the
provenance model pins ``unblinded=True`` and is schema-required [AC-2].
"""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, model_validator

from ..analyze.confounds import judge_vendor_overlap
from ..judge.providers.base import Provider, ProviderError, get_provider
from ..ledger import events
from ..ledger.events import EventContext
from ..ledger.query import assert_chain, find_events
from .packet import ProcessPacket, build_process_packet
from .rubric import ProcessRubric, SCALE_MAX, SCALE_MIN

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
DEFAULT_MAX_CONTEXT_TOKENS = 100_000
DEFAULT_MARGIN = 1.15  # conservative: assume the payload is 15% larger than counted


class TranscriptPolicy(str, Enum):
    full_or_cant_score = "full_or_cant_score"
    # recorded_truncation would add a branch here [D004]; v1 does not truncate.


# --- schema ----------------------------------------------------------------
class Scorer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["judge", "human"]
    id: str


class ProcessScoreProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # unblinded is pinned True ⇒ a score without unblinded provenance is
    # unrepresentable [AC-2].
    unblinded: Literal[True]
    scorer: Scorer
    judge_vendor_overlap: bool
    ts: str


class DimensionScore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dim_id: str
    score: Optional[int] = None
    cant_score_reason: Optional[str] = None
    tokens: Optional[int] = None  # recorded when the reason is context overflow

    @model_validator(mode="after")
    def _score_xor_cant(self) -> "DimensionScore":
        if (self.score is None) == (self.cant_score_reason is None):
            raise ValueError(
                f"dimension {self.dim_id!r}: exactly one of score / cant_score_reason"
            )
        if self.score is not None and not (SCALE_MIN <= self.score <= SCALE_MAX):
            raise ValueError(f"score {self.score} out of range [{SCALE_MIN},{SCALE_MAX}]")
        return self

    @property
    def is_cant_score(self) -> bool:
        return self.score is None


class ProcessScore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trial_id: str
    rubric_version: str
    comparison_id: Optional[str] = None
    scores: list[DimensionScore]
    provenance: ProcessScoreProvenance


# --- token counting seam ---------------------------------------------------
def _heuristic_token_count(text: str) -> int:
    """Conservative chars/4 estimate; a real impl uses the provider's counter."""
    return len(text) // 4 + 1


def _emit(ledger_path, ctx, score: ProcessScore) -> ProcessScore:
    events.record_process_score(ledger_path, ctx, process_score=score.model_dump(mode="json"))
    return score


def _all_cant(rubric: ProcessRubric, reason: str, tokens: Optional[int] = None) -> list[DimensionScore]:
    return [
        DimensionScore(dim_id=d.id, cant_score_reason=reason, tokens=tokens)
        for d in rubric.dimensions
    ]


def _parse_judge_scores(text: str, rubric: ProcessRubric) -> list[DimensionScore]:
    m = _JSON_RE.search(text or "")
    if not m:
        raise ValueError("no JSON object in judge process output")
    raw = json.loads(m.group(0)).get("scores", {})
    out: list[DimensionScore] = []
    for d in rubric.dimensions:
        v = raw.get(d.id)
        if isinstance(v, bool) or not isinstance(v, int):
            # missing or non-integer ⇒ per-dimension CANT_SCORE(unparsed)
            out.append(DimensionScore(dim_id=d.id, cant_score_reason="unparsed"))
        elif not d.is_valid_score(v):
            out.append(DimensionScore(dim_id=d.id, cant_score_reason="out_of_range"))
        else:
            out.append(DimensionScore(dim_id=d.id, score=v))
    return out


def score_trial_process(
    trial_id: str,
    transcript: str,
    rubric: ProcessRubric,
    *,
    ledger_path,
    ctx: EventContext,
    ts: str,
    scorer_id: str,
    provider: Optional[Provider] = None,
    provider_model: str = "anthropic/claude-3-5-sonnet-20241022",
    spec=None,
    telemetry: Optional[dict] = None,
    token_counter: Callable[[str], int] = _heuristic_token_count,
    max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
    margin: float = DEFAULT_MARGIN,
    policy: TranscriptPolicy = TranscriptPolicy.full_or_cant_score,
    comparison_id: Optional[str] = None,
) -> ProcessScore:
    """Judge-score one trial's process. Always appends exactly one event [AC-4].

    Full-or-CANT_SCORE: no silent truncation; an over-context transcript fails
    closed to CANT_SCORE(context_overflow) with token counts recorded.
    """
    overlap = bool(spec is not None and judge_vendor_overlap(spec).overlap)
    prov = ProcessScoreProvenance(
        unblinded=True,
        scorer=Scorer(kind="judge", id=scorer_id),
        judge_vendor_overlap=overlap,
        ts=ts,
    )

    def _score(scores) -> ProcessScore:
        return _emit(
            ledger_path, ctx,
            ProcessScore(trial_id=trial_id, rubric_version=rubric.rubric_version,
                         comparison_id=comparison_id, scores=scores, provenance=prov),
        )

    # Redaction check happens in build_process_packet (fail closed on secrets).
    packet: ProcessPacket = build_process_packet(transcript, rubric, telemetry=telemetry)
    messages = packet.render_judge()

    # Full-or-CANT_SCORE token gate [D004]: count the *rendered payload*, apply a
    # conservative margin, and fail closed rather than truncate.
    payload_text = "".join(m["content"] for m in messages)
    counted = token_counter(payload_text)
    if counted * margin > max_context_tokens:
        return _score(_all_cant(rubric, "context_overflow", tokens=counted))

    provider = provider or get_provider(provider_model)
    try:
        text = provider.complete(provider_model, messages, 0.0)
    except ProviderError:
        return _score(_all_cant(rubric, "provider_error"))
    try:
        scores = _parse_judge_scores(text, rubric)
    except (ValueError, json.JSONDecodeError):
        return _score(_all_cant(rubric, "parse"))
    return _score(scores)


class ProcessSequencingError(RuntimeError):
    """Human process scoring attempted before the comparison's reveal [AC-3]."""


def _reveal_exists(ledger_path, comparison_id: str) -> bool:
    for ev in find_events(ledger_path, events.REVEAL):
        if ev.get("verdict_event_id") == comparison_id:
            return True
    return False


def record_human_process_score(
    trial_id: str,
    rubric: ProcessRubric,
    dimension_scores: list[DimensionScore],
    *,
    ledger_path,
    ctx: EventContext,
    ts: str,
    scorer_id: str,
    comparison_id: str,
) -> ProcessScore:
    """Record a human process score — only after the EVAL-7 reveal [AC-3].

    The firewall direction: trajectory impressions must not contaminate outcome
    verdicts, so process comes strictly after verdict + reveal. Refused if no
    reveal event references ``comparison_id``.
    """
    # The firewall reads the ledger to check the reveal happened; verify the
    # chain first so a forged reveal cannot let trajectory scoring run before the
    # genuine outcome verdict [PL-6/AC-3].
    assert_chain(ledger_path)
    if not _reveal_exists(ledger_path, comparison_id):
        raise ProcessSequencingError(
            f"human process scoring for comparison {comparison_id!r} requires its "
            "EVAL-7 reveal event first; process follows verdict + reveal [AC-3]"
        )
    prov = ProcessScoreProvenance(
        unblinded=True,
        scorer=Scorer(kind="human", id=scorer_id),
        judge_vendor_overlap=False,
        ts=ts,
    )
    score = ProcessScore(
        trial_id=trial_id, rubric_version=rubric.rubric_version,
        comparison_id=comparison_id, scores=dimension_scores, provenance=prov,
    )
    return _emit(ledger_path, ctx, score)
