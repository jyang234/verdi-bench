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
from ..judge.providers.base import (
    Provider,
    ProviderContextOverflow,
    ProviderError,
    get_provider,
    provider_failure_reason,
)
from ..ledger import events
from ..ledger.events import EventContext
from ..ledger.query import assert_chain, find_events
from .packet import ProcessPacket, RedactionLeakError, build_process_packet
from .rubric import ProcessRubric, SCALE_MAX, SCALE_MIN

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
DEFAULT_MAX_CONTEXT_TOKENS = 100_000
DEFAULT_MARGIN = 1.15  # conservative: assume the payload is 15% larger than counted


class TranscriptPolicy(str, Enum):
    full_or_cant_score = "full_or_cant_score"
    # recorded_truncation would add a branch here [D004]; v1 does not truncate.


class CantScoreReason(str, Enum):
    """Enumerated per-dimension fail-closed reasons [EVAL-9 §7.2].

    A closed set, mirroring judge's ``CantJudgeReason`` and grade's
    ``cant_grade`` taxonomy — no more ad-hoc "parse" vs "unparsed" strings.
    Stored on ``DimensionScore.cant_score_reason`` (str), so additive."""

    redaction_leak = "redaction_leak"
    context_overflow = "context_overflow"
    provider_error = "provider_error"
    timeout = "timeout"
    refusal = "refusal"
    parse = "parse"
    judge_declared = "judge_declared"  # the judge replied the instructed "CANT_SCORE"
    out_of_range = "out_of_range"
    human_cant = "human_cant"
    missing_transcript = "missing_transcript"  # F-M-O3: absent/empty, never scored


# PRA-M13: reasons a re-run should re-attempt — the scorer could not *run*
# (transient network/provider hiccup), mirroring judge's TRANSIENT_CANT_JUDGE
# and grade's TRANSIENT_CANT_GRADE. context_overflow/parse/etc. are
# deterministic for a fixed transcript, so retrying reproduces them — terminal.
TRANSIENT_CANT_SCORE = frozenset(
    {CantScoreReason.timeout.value, CantScoreReason.provider_error.value}
)


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

    @model_validator(mode="after")
    def _no_duplicate_dims(self) -> "ProcessScore":
        # PR-8: a duplicate dimension double-counts in kappa/correlation; refuse it
        # at the schema so no path (judge or human) can ledger one.
        ids = [s.dim_id for s in self.scores]
        if len(ids) != len(set(ids)):
            raise ValueError("process score has duplicate dimension ids")
        return self


# --- token counting seam ---------------------------------------------------
def _heuristic_token_count(text: str) -> int:
    """Conservative chars/4 estimate; a real impl uses the provider's counter."""
    return len(text) // 4 + 1


def _emit(ledger_path, ctx, score: ProcessScore) -> ProcessScore:
    events.record_process_score(ledger_path, ctx, process_score=score.model_dump(mode="json"))
    return score


def _all_cant(
    rubric: ProcessRubric, reason: CantScoreReason, tokens: Optional[int] = None
) -> list[DimensionScore]:
    return [
        DimensionScore(dim_id=d.id, cant_score_reason=reason.value, tokens=tokens)
        for d in rubric.dimensions
    ]


def _parse_judge_scores(text: str, rubric: ProcessRubric) -> list[DimensionScore]:
    m = _JSON_RE.search(text or "")
    if not m:
        raise ValueError("no JSON object in judge process output")
    raw = json.loads(m.group(0)).get("scores", {})
    # PR-1: a non-object ``scores`` (e.g. a list) must fail closed to parse, not
    # raise AttributeError on ``raw.get`` and escape with no event.
    if not isinstance(raw, dict):
        raise ValueError(f"judge process 'scores' must be an object, got {type(raw).__name__}")
    out: list[DimensionScore] = []
    for d in rubric.dimensions:
        v = raw.get(d.id)
        if v == "CANT_SCORE":
            # PR-4: the judge used the instructed sentinel ⇒ a first-class,
            # judge-declared CANT_SCORE, not an "unparsed" mishap.
            out.append(DimensionScore(dim_id=d.id, cant_score_reason=CantScoreReason.judge_declared.value))
        elif isinstance(v, bool) or not isinstance(v, int):
            # missing or non-integer ⇒ per-dimension CANT_SCORE(parse)
            out.append(DimensionScore(dim_id=d.id, cant_score_reason=CantScoreReason.parse.value))
        elif not d.is_valid_score(v):
            out.append(DimensionScore(dim_id=d.id, cant_score_reason=CantScoreReason.out_of_range.value))
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
    spec,
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
    # PR-9: spec is required (production always passes it), so judge/arm vendor
    # overlap is honest again — it no longer silently degrades to False when the
    # spec is unknown, which would under-report a real overlap confound.
    overlap = bool(judge_vendor_overlap(spec).overlap)
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

    # F-M-O3: a missing/empty transcript is fail-closed, exactly as the CLI
    # docstring promises — the judge must never fabricate dimension scores from
    # nothing. One event, no provider call.
    if not transcript.strip():
        return _score(_all_cant(rubric, CantScoreReason.missing_transcript))

    # PR-2: the redaction re-scan in build_process_packet raises RedactionLeakError;
    # it must fail closed to CANT_SCORE(redaction_leak) (mirroring judge's
    # identity_leak), never escape with no event.
    try:
        packet: ProcessPacket = build_process_packet(transcript, rubric, telemetry=telemetry)
    except RedactionLeakError:
        return _score(_all_cant(rubric, CantScoreReason.redaction_leak))
    messages = packet.render_judge()

    # Full-or-CANT_SCORE token gate [D004]: count the *rendered payload*, apply a
    # conservative margin, and fail closed rather than truncate.
    payload_text = "".join(m["content"] for m in messages)
    counted = token_counter(payload_text)
    if counted * margin > max_context_tokens:
        return _score(_all_cant(rubric, CantScoreReason.context_overflow, tokens=counted))

    # PR-3: resolve the provider inside the fail-closed envelope so an unknown
    # prefix records CANT_SCORE(provider_error) instead of escaping.
    if provider is None:
        try:
            provider = get_provider(provider_model)
        except ProviderError as e:
            return _score(_all_cant(rubric, CantScoreReason(provider_failure_reason(e))))
    # PR-4: timeout / refusal / provider_error via the one shared mapper the judge
    # uses, so the two stages cannot drift on the classification [carry-forward].
    try:
        text = provider.complete(provider_model, messages, 0.0)
    except ProviderContextOverflow as e:
        # PR-9: a provider-side context rejection is more specific than a generic
        # provider_error — record context_overflow with the provider's token count
        # when it reported one (else the pre-flight count is unavailable here).
        return _score(_all_cant(rubric, CantScoreReason.context_overflow, tokens=e.prompt_tokens))
    except ProviderError as e:
        return _score(_all_cant(rubric, CantScoreReason(provider_failure_reason(e))))
    try:
        scores = _parse_judge_scores(text, rubric)
    except (ValueError, json.JSONDecodeError):
        # _parse_judge_scores type-checks a non-dict ``scores`` up front (raising
        # ValueError), so a stray AttributeError/TypeError here would signal a real
        # bug — let it crash loudly rather than masquerade as a parse failure.
        return _score(_all_cant(rubric, CantScoreReason.parse))
    return _score(scores)


def human_scores_from_mapping(
    raw: dict, rubric: ProcessRubric
) -> list[DimensionScore]:
    """Parse a ``{dim_id: 1-5 | "CANT_SCORE"}`` mapping against the rubric [PR-7].

    Every rubric dimension must appear exactly once; an unknown or missing key is
    a loud error, never a silent ``CANT_SCORE("human_cant")`` that degrades a real
    score. ``"CANT_SCORE"`` is the only accepted non-numeric value.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"human scores must be an object, got {type(raw).__name__}")
    valid = set(rubric.dimension_ids)
    unknown = sorted(set(raw) - valid)
    if unknown:
        raise ValueError(f"unknown dimension id(s) {unknown}; rubric dims are {rubric.dimension_ids}")
    missing = sorted(valid - set(raw))
    if missing:
        raise ValueError(f"missing score(s) for dimension(s) {missing}; every dimension must be scored")
    out: list[DimensionScore] = []
    for d in rubric.dimensions:
        v = raw[d.id]
        if v == "CANT_SCORE":
            out.append(DimensionScore(dim_id=d.id, cant_score_reason=CantScoreReason.human_cant.value))
        elif isinstance(v, bool) or not isinstance(v, int):
            # reject a non-integer value loudly rather than truncating a float
            # (3.7 -> 3) or emitting an opaque int() error [fail loudly]
            raise ValueError(
                f"dimension {d.id!r}: score must be an integer 1..5 or \"CANT_SCORE\", got {v!r}"
            )
        else:
            out.append(DimensionScore(dim_id=d.id, score=v))
    return out


def _assert_scores_cover_rubric(rubric: ProcessRubric, scores: list[DimensionScore]) -> None:
    """PR-8: refuse a human score set whose dims do not match the rubric exactly."""
    ids = [s.dim_id for s in scores]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate dimension ids in process score")
    valid = set(rubric.dimension_ids)
    unknown = sorted(set(ids) - valid)
    if unknown:
        raise ValueError(f"unknown dimension id(s) {unknown}; rubric {rubric.rubric_version} dims are {rubric.dimension_ids}")
    missing = sorted(valid - set(ids))
    if missing:
        raise ValueError(f"missing score(s) for dimension(s) {missing}; every rubric dimension must be scored")


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
    # PR-8: refuse a score set that does not cover the rubric exactly (unknown,
    # missing, or duplicate dims) before touching the ledger.
    _assert_scores_cover_rubric(rubric, dimension_scores)
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


# --- one-event property registration [EVAL-3 §M7, XC-3] --------------------
def _process_entrypoint(ctx_dir: str) -> None:
    from pathlib import Path

    from ..judge.providers.fake import FakeProvider
    from ..schema.experiment import ExperimentSpec
    from .rubric import default_rubric

    d = Path(ctx_dir)
    r = default_rubric()
    fp = FakeProvider([json.dumps({"scores": {dim: 3 for dim in r.dimension_ids}})])
    spec = ExperimentSpec.from_dict({
        "arms": [
            {"name": "control", "platform": "claude_code", "model": "anthropic/claude-3-5-sonnet-20241022", "payload": {}},
            {"name": "treatment", "platform": "codex", "model": "openai/gpt-4o-2024-08-06", "payload": {}},
        ],
        "corpus": {"id": "public-mini", "version": "1.0.0"},
        "repetitions": 1,
        "primary_metric": "holdout_pass_rate",
        "decision_rule": "delta_holdout_pass_rate > 0",
        "judge": {"model": "google/gemini-1.5-pro-002", "rubric": "r.md", "orders": "both", "temperature": 0},
        "seed": 1,
        "cost_ceiling": {"amount": 1.0, "currency": "USD"},
    })
    score_trial_process(
        "trial-x", "clean transcript", r, ledger_path=d / "ledger.ndjson",
        ctx=EventContext(experiment_id="prop"), ts="t0", scorer_id="judge", provider=fp,
        spec=spec,
    )


def _register() -> None:
    from ..entrypoints import register_entrypoint

    register_entrypoint("process", _process_entrypoint)


_register()
