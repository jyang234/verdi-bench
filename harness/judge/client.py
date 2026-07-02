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
from .packet import Packet, validate_identity_free
from .providers.base import (
    Provider,
    ProviderError,
    ProviderRefusal,
    ProviderTimeout,
    get_provider,
)
from .schema import Evidence, Verdict, VerdictProvenance, Winner

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


def _map_call(raw: RawVerdict, order: str) -> tuple[str, list[Evidence]]:
    """Map a raw call (Response 1/2) to an arm verdict (A/B) and evidence."""
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
    return winner, evidence


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
) -> Verdict:
    """Judge one comparison. Always appends exactly one verdict event."""
    # A leaking packet is never sent — the leak itself is fail-closed data.
    validate_identity_free(packet, canaries)

    provider = provider or get_provider(config.model)
    call_ids: list[str] = []

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

    def _cant(reason: str) -> Verdict:
        v = Verdict(
            winner=Winner.CANT_JUDGE,
            reason=reason,
            evidence=[],
            confidence=0.0,
            provenance=_provenance(),
        )
        events.append_verdict(ledger_path, ctx, verdict=v.model_dump(mode="json"))
        return v

    orders_to_run = ["AB", "BA"] if config.orders == "both" else ["AB"]
    mapped: list[tuple[str, list[Evidence]]] = []
    for order in orders_to_run:
        try:
            raw, call_id = _call(provider, config.model, packet, order, config.temperature)
        except ProviderTimeout:
            return _cant("timeout")
        except ProviderRefusal:
            return _cant("refusal")
        except ProviderError:
            return _cant("provider_error")
        except (ValueError, ValidationError, json.JSONDecodeError):
            call_ids.append("unparsed")
            return _cant("parse")
        call_ids.append(call_id)
        mapped.append(_map_call(raw, order))

    # Combine.
    winners = [w for w, _ in mapped]
    all_evidence: list[Evidence] = [e for _, evs in mapped for e in evs]
    if any(w == "CANT_JUDGE" for w in winners):
        return _cant("judge_cant_judge")

    if len(set(winners)) == 1:
        winner_str = winners[0]
        order_inconsistent = False
    else:
        # disagreement across orders ⇒ position bias ⇒ downgrade to TIE [D003]
        winner_str = "TIE"
        order_inconsistent = True

    try:
        verdict = Verdict(
            winner=Winner(winner_str),
            reason="; ".join(m[0] for m in mapped) or winner_str,
            evidence=all_evidence,
            confidence=0.5 if order_inconsistent else 0.8,
            order_inconsistent=order_inconsistent,
            provenance=_provenance(),
        )
    except ValidationError:
        # e.g. a substantive winner with no evidence ⇒ malformed
        return _cant("malformed")

    events.append_verdict(ledger_path, ctx, verdict=verdict.model_dump(mode="json"))
    return verdict
