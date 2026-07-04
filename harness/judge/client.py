"""Judge client [EVAL-2 §M3, AC-1, AC-3, AC-8].

``judge_pair`` runs two calls per comparison with orders swapped at temperature 0
and parses strictly. Agreement ⇒ one verdict carrying both call ids; disagreement
⇒ TIE + ``order_inconsistent=True`` [D003]. Every failure mode (timeout, refusal,
provider error, unparseable output, evidence-free/malformed verdict) becomes
exactly one ``CANT_JUDGE(reason)`` — an attempted comparison without a verdict
event is unrepresentable [AC-8]. There is no vendor allow/deny list [AC-1].
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, ValidationError

from ..ledger import events
from ..ledger.events import EventContext
from .packet import (
    IdentityLeakError,
    Packet,
    SecretLeakError,
    validate_identity_free,
    validate_secret_free,
)
from .providers.base import (
    Provider,
    ProviderError,
    get_provider,
    provider_failure_reason,
)
from .schema import (
    CantJudgeReason,
    Confidence,
    Evidence,
    Verdict,
    VerdictProvenance,
    Winner,
    confidence_bucket,
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class RawEvidence(BaseModel):
    kind: Literal["diff", "holdout"]
    response: int
    hunk: Optional[str] = None
    ref: Optional[str] = None


class RawVerdict(BaseModel):
    winner: Literal["1", "2", "TIE", "CANT_JUDGE"]
    reason: str = ""
    evidence: list[RawEvidence] = []
    confidence: float = 0.0


def _parse_raw(text: str) -> RawVerdict:
    m = _JSON_RE.search(text or "")
    if not m:
        raise ValueError("no JSON object in judge output")
    return RawVerdict.model_validate(json.loads(m.group(0)))


def _pos_to_arm(order: str) -> dict[int, str]:
    # order "AB": position 1 is A, 2 is B; "BA": inverse
    return {1: "A", 2: "B"} if order == "AB" else {1: "B", 2: "A"}


def _map_call(raw: RawVerdict, order: str) -> tuple[str, list[Evidence], str]:
    """Map a raw call (Response 1/2) to an arm verdict (A/B), evidence, reason."""
    mapping = _pos_to_arm(order)
    if raw.winner == "TIE":
        winner = "TIE"
    elif raw.winner == "CANT_JUDGE":
        winner = "CANT_JUDGE"
    else:
        winner = mapping[int(raw.winner)]
    evidence = [
        Evidence(kind=e.kind, response=mapping[e.response], hunk=e.hunk, ref=e.ref)
        for e in raw.evidence
        if e.response in mapping
    ]
    return winner, evidence, raw.reason


def _call(provider: Provider, model: str, packet: Packet, order: str, temperature: float):
    call_id = f"call-{uuid.uuid4().hex[:12]}"
    text = provider.complete(model, packet.render(order), temperature)
    return _parse_raw(text), call_id


def judge_pair(
    packet: Packet,
    config,
    ledger_path,
    ctx: EventContext,
    *,
    ts: str,
    provider: Optional[Provider] = None,
    canaries: Optional[list[str]] = None,
    comparison_id: Optional[str] = None,
    task_class: Optional[str] = None,
    arm_map: Optional[dict[str, str]] = None,
    task_id: Optional[str] = None,
) -> Verdict:
    """Judge one comparison. Always appends exactly one verdict event.

    ``comparison_id``/``task_class`` ride onto the verdict so calibration
    (kappa) can join judge and human verdicts by comparison [AC-7]. ``arm_map``
    records the A/B -> physical-arm assignment so the kappa join is frame-correct
    [D-P4-1].
    """
    call_ids: list[str] = []
    single_order = config.orders == "single"

    def _provenance() -> VerdictProvenance:
        return VerdictProvenance(
            judge_model=config.model,
            rubric_sha256=packet.rubric_sha256,
            packet_sha256=packet.packet_sha256,
            call_ids=list(call_ids),
            orders=config.orders,
            temperature=config.temperature,
            ts=ts,
        )

    def _cant(reason: CantJudgeReason) -> Verdict:
        v = Verdict(
            winner=Winner.CANT_JUDGE,
            reason=reason.value,
            evidence=[],
            confidence=Confidence.low,
            provenance=_provenance(),
            comparison_id=comparison_id,
            task_class=task_class,
            arm_map=arm_map,
            single_order=single_order,
            task_id=task_id,
        )
        events.append_verdict(ledger_path, ctx, verdict=v.model_dump(mode="json"))
        return v

    # JD-2: resolve the provider *inside* the fail-closed envelope. An unknown
    # prefix (legal per D001) raises ProviderError; recording it as
    # CANT_JUDGE(provider_error) keeps "an attempted comparison without a verdict
    # event is unrepresentable" true instead of escaping with no event [AC-8].
    if provider is None:
        try:
            provider = get_provider(config.model)
        except ProviderError as e:
            return _cant(CantJudgeReason(provider_failure_reason(e)))

    # A leaking packet is never sent — but the leak is fail-closed *data*, so it
    # is recorded as CANT_JUDGE(identity_leak) rather than escaping with no event
    # (an attempted comparison without a verdict event is unrepresentable) [AC-8].
    try:
        validate_identity_free(packet, canaries)
    except IdentityLeakError:
        return _cant(CantJudgeReason.IDENTITY_LEAK)

    # PRA-L4: defense-in-depth secret re-scan (redaction ran at trial time, but a
    # miss — or a symlink escape into another tree — must not ship a key to the
    # provider). Fail closed to one CANT_JUDGE, never send.
    try:
        validate_secret_free(packet)
    except SecretLeakError:
        return _cant(CantJudgeReason.SECRET_LEAK)

    orders_to_run = ["AB", "BA"] if config.orders == "both" else ["AB"]
    mapped: list[tuple[str, list[Evidence], str]] = []
    raw_confidences: list[float] = []
    for order in orders_to_run:
        try:
            raw, call_id = _call(provider, config.model, packet, order, config.temperature)
        except ProviderError as e:
            # timeout / refusal / provider_error via one shared mapper so judge and
            # process cannot drift on the classification [carry-forward].
            return _cant(CantJudgeReason(provider_failure_reason(e)))
        except (KeyError, IndexError):
            # JD-3: an error-shaped/safety-blocked 200 can raise KeyError/IndexError
            # while a provider extracts content — a transport-shape failure, not a
            # JSON parse failure. Fail closed to provider_error, not parse.
            return _cant(CantJudgeReason.PROVIDER_ERROR)
        except (ValueError, ValidationError, json.JSONDecodeError):
            call_ids.append("unparsed")
            return _cant(CantJudgeReason.PARSE)
        call_ids.append(call_id)
        raw_confidences.append(raw.confidence)
        mapped.append(_map_call(raw, order))

    # Combine.
    winners = [w for w, _, _ in mapped]
    all_evidence: list[Evidence] = [e for _, evs, _ in mapped for e in evs]
    # preserve the judge's own rationale(s), not just the winner letters
    reasons = [r for _, _, r in mapped if r]
    if any(w == "CANT_JUDGE" for w in winners):
        return _cant(CantJudgeReason.JUDGE_CANT_JUDGE)

    if len(set(winners)) == 1:
        winner_str = winners[0]
        order_inconsistent = False
    else:
        # disagreement across orders ⇒ position bias ⇒ downgrade to TIE [D003]
        winner_str = "TIE"
        order_inconsistent = True

    # JD-12/D-4: the confidence band is the judge's PARSED confidence (bucketed),
    # not a discarded hardcode — the conservative minimum across the two orders, or
    # low when the orders disagreed (position bias ⇒ we trust the call less).
    confidence = (
        Confidence.low
        if order_inconsistent or not raw_confidences
        else confidence_bucket(min(raw_confidences))
    )

    try:
        verdict = Verdict(
            winner=Winner(winner_str),
            reason=" | ".join(reasons) if reasons else winner_str,
            evidence=all_evidence,
            confidence=confidence,
            order_inconsistent=order_inconsistent,
            provenance=_provenance(),
            comparison_id=comparison_id,
            task_class=task_class,
            arm_map=arm_map,
            single_order=single_order,
            task_id=task_id,
        )
    except ValidationError:
        # e.g. a substantive winner with no evidence ⇒ malformed
        return _cant(CantJudgeReason.MALFORMED)

    events.append_verdict(ledger_path, ctx, verdict=verdict.model_dump(mode="json"))
    return verdict


# --- one-event property registration [EVAL-3 §M7, XC-3] --------------------
def _judge_entrypoint(ctx_dir: str) -> None:
    from pathlib import Path

    from ..ledger.events import EventContext
    from ..schema.judge_config import JudgeConfig
    from .packet import ResponseArtifacts, build_packet
    from .providers.fake import FakeProvider

    d = Path(ctx_dir)
    packet = build_packet(
        ResponseArtifacts(diff="diff a", holdout_results=[{"id": "h1", "result": "pass"}]),
        ResponseArtifacts(diff="diff b", holdout_results=[{"id": "h1", "result": "fail"}]),
        task_prompt="do the task",
        rubric="judge on correctness",
    )
    config = JudgeConfig(
        model="google/gemini-1.5-pro-002", rubric="rubrics/code-task-v1.md",
        orders="both", temperature=0.0,
    )
    v1 = json.dumps({"winner": "1", "reason": "x",
                     "evidence": [{"kind": "diff", "response": 1, "hunk": "@@"}], "confidence": 0.9})
    v2 = json.dumps({"winner": "2", "reason": "x",
                     "evidence": [{"kind": "diff", "response": 2, "hunk": "@@"}], "confidence": 0.9})
    judge_pair(
        packet, config, d / "ledger.ndjson", EventContext(experiment_id="prop"),
        ts="t0", provider=FakeProvider([v1, v2]),
    )


def _register() -> None:
    from ..entrypoints import register_entrypoint

    register_entrypoint("judge", _judge_entrypoint)


_register()
