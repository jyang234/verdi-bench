"""Shared fail-closed scorer envelope [refactor 06 §4].

Three isolated-model tiers — the forensic advisory review, the process scorer,
and (in a multi-call variant) the contamination probe — ran the same fail-closed
sequence by hand, with triplicated ``_heuristic_token_count`` + context limits +
a greedy ``_JSON_RE``:

    empty-input CANT → leak re-scan CANT → token-gate CANT →
    resolve-provider-inside-envelope CANT → complete → parse CANT

:func:`scored_completion` runs that sequence once, parameterized by the stage's
closed reason enum, a packet builder (which fail-closes a leak/over-budget input
by raising :class:`PacketRejected`), and a parser. The CANT enums' ledgered
string values never merge or change — each tier keeps its own closed set; the
envelope only *routes* to the stage's ``on_cant`` after validating the reason
belongs to that enum (``reason_enum(reason)``), which is the sync check the
"kept in sync with TRANSIENT_CANT_JUDGE" comments become [refactor 06 §4].

Provider resolution happens *inside* the envelope (the PR-3/JD-2 posture): an
unknown prefix fails closed to ``CANT(provider_error)``, never escaping with no
event. The provider-fault classification rides the one shared
:func:`provider_failure_reason` mapper so no two tiers can drift on it.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Callable, Optional, TypeVar

from pydantic import ValidationError

from .providers.base import (
    Provider,
    ProviderContextOverflow,
    ProviderError,
    get_provider,
    provider_failure_reason,
)

R = TypeVar("R")
T = TypeVar("T")

# One conservative token estimate + context limits for every tier [F-M-J4]: the
# chars/4 pre-flight, a 100k default window, and a 15% margin over the counted
# payload (assume it is bigger than we counted, then fail closed rather than
# truncate — the full-or-CANT posture).
DEFAULT_MAX_CONTEXT_TOKENS = 100_000
DEFAULT_MARGIN = 1.15

# The first complete-ish JSON object in a model reply: the greedy first-brace to
# last-brace span the review and process parsers shared [refactor 05 §7]. (The
# judge client uses a stricter brace-balanced scan of its own — envelope.py owns
# only this shared copy.)
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

# Provider-call reasons a re-run should re-attempt: the tier could not *run* (a
# transient network/provider hiccup) or the reply was call-specific garble, as
# opposed to a deterministic-for-a-fixed-packet failure a retry would only
# reproduce. Shared so the per-tier transient sets cannot drift by hand — the
# "kept in sync with TRANSIENT_CANT_JUDGE" comment becomes this derivation.
PROVIDER_TRANSIENT_REASONS: frozenset[str] = frozenset(
    {"timeout", "provider_error", "parse"}
)


def heuristic_token_count(text: str) -> int:
    """Conservative chars/4 estimate — the shared default token counter."""
    return len(text) // 4 + 1


def extract_json(text: str) -> str:
    """The shared greedy JSON-object extraction the review/process parsers use.

    Raises ``ValueError`` when there is no object, so a stage parser calling this
    fails closed to its ``parse`` reason through the envelope's parse-catch."""
    m = _JSON_RE.search(text or "")
    if not m:
        raise ValueError("no JSON object in model output")
    return m.group(0)


class PacketRejected(Exception):
    """A packet builder fail-closed *before* the provider call, carrying the
    stage's reason value — a leak re-scan hit or an over-budget input. The
    envelope maps it to ``CANT(reason)`` [refactor 06 §4]."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def scored_completion(
    input_text: str,
    *,
    reason_enum: type[Enum],
    empty_reason: str,
    build_messages: Callable[[str], list[dict]],
    parse: Callable[[str], T],
    on_cant: Callable[..., R],
    on_scored: Callable[[T], R],
    provider: Optional[Provider],
    provider_model: Optional[str],
    token_counter: Callable[[str], int] = heuristic_token_count,
    max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
    margin: float = DEFAULT_MARGIN,
) -> R:
    """Run the shared fail-closed sequence; return the stage's own result type.

    ``on_cant(reason, *, tokens=None)`` builds the stage's CANT result (``tokens``
    is passed on the two context-overflow paths and ignored by tiers that do not
    record it); ``on_scored(parsed)`` builds the success result. Both are called
    exactly once, so a caller that emits inside them still appends exactly one
    event. Every returned reason is validated to belong to ``reason_enum`` — a
    tier whose closed set lacks a reason the envelope routes fails loudly.
    """

    def _cant(reason: str, *, tokens: Optional[int] = None) -> R:
        # Validate the reason is a member of THIS tier's closed enum before
        # routing — the closed sets stay per-tier, the check is shared.
        return on_cant(reason_enum(reason).value, tokens=tokens)

    # 1. empty input — nothing to score/review; never fabricate from nothing.
    if not input_text.strip():
        return _cant(empty_reason)

    # 2. build the packet; a leak re-scan or over-budget input fails closed here.
    try:
        messages = build_messages(input_text)
    except PacketRejected as rejected:
        return _cant(rejected.reason)

    # 3. token gate [full-or-CANT, D004]: count the rendered payload, apply the
    #    margin, and fail closed rather than truncate.
    counted = token_counter("".join(m["content"] for m in messages))
    if counted * margin > max_context_tokens:
        return _cant("context_overflow", tokens=counted)

    # 4. resolve the provider inside the envelope: an unknown/absent model fails
    #    closed to provider_error instead of escaping with no event.
    if provider is None:
        if provider_model is None:
            return _cant("provider_error")
        try:
            provider = get_provider(provider_model)
        except ProviderError as e:
            return _cant(provider_failure_reason(e))

    # 5. complete — provider faults route through the one shared mapper; a
    #    provider-side context rejection is more specific than provider_error.
    try:
        completion = provider.complete(provider_model, messages, 0.0)
    except ProviderContextOverflow as e:
        return _cant("context_overflow", tokens=e.prompt_tokens)
    except ProviderError as e:
        return _cant(provider_failure_reason(e))

    # 6. parse — an unparseable/mis-shaped reply is call-specific: fail closed.
    try:
        parsed = parse(completion.text)
    except (ValueError, ValidationError):
        return _cant("parse")
    return on_scored(parsed)
