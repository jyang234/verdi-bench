"""The shared fail-closed scorer envelope [refactor 06 §4].

Drives :func:`scored_completion` directly with a throwaway reason enum so the
common sequence — empty → leak → token-gate → provider → parse — and the
per-tier reason validation are pinned once, independently of the forensic/process
adoptions that also exercise them.
"""

from __future__ import annotations

import json
from enum import Enum

import pytest

from harness.judge.envelope import (
    PROVIDER_TRANSIENT_REASONS,
    PacketRejected,
    extract_json,
    heuristic_token_count,
    scored_completion,
)
from harness.judge.providers.base import ProviderTimeout
from harness.judge.providers.fake import FakeProvider


class _Reason(str, Enum):
    empty = "empty"
    identity_leak = "identity_leak"
    context_overflow = "context_overflow"
    provider_error = "provider_error"
    timeout = "timeout"
    refusal = "refusal"
    parse = "parse"


def _run(text, *, provider=None, provider_model="fake/m", build=None, parse=None, **kw):
    return scored_completion(
        text,
        reason_enum=_Reason,
        empty_reason=_Reason.empty.value,
        build_messages=build or (lambda t: [{"role": "user", "content": t}]),
        parse=parse or (lambda t: json.loads(extract_json(t))),
        on_cant=lambda reason, *, tokens=None: ("cant", reason, tokens),
        on_scored=lambda parsed: ("ok", parsed),
        provider=provider,
        provider_model=provider_model,
        **kw,
    )


def test_empty_input_fails_closed_with_the_stage_reason():
    assert _run("   ") == ("cant", "empty", None)


def test_packet_rejection_maps_to_its_reason():
    def build(_):
        raise PacketRejected(_Reason.identity_leak.value)

    assert _run("x", build=build) == ("cant", "identity_leak", None)


def test_token_gate_records_the_counted_tokens():
    kind, reason, tokens = _run("x" * 100, max_context_tokens=1)
    assert (kind, reason) == ("cant", "context_overflow")
    assert isinstance(tokens, int) and tokens > 1


def test_unknown_provider_prefix_fails_closed_inside_the_envelope():
    # provider is None → resolved inside the envelope → unknown prefix → cant
    assert _run('{"x": 1}', provider=None, provider_model="nosuch/x") == (
        "cant", "provider_error", None,
    )


def test_provider_fault_routes_through_the_shared_mapper():
    assert _run('{"x": 1}', provider=FakeProvider([ProviderTimeout("deadline")])) == (
        "cant", "timeout", None,
    )


def test_unparseable_reply_fails_closed_to_parse():
    assert _run("x", provider=FakeProvider(["not json at all"])) == ("cant", "parse", None)


def test_success_routes_to_on_scored():
    assert _run("x", provider=FakeProvider(['{"x": 1}'])) == ("ok", {"x": 1})


def test_a_reason_outside_the_enum_fails_loudly():
    """The sync check as code: routing a reason the tier's closed set lacks is a
    loud ValueError, never a silent mis-tag [refactor 06 §4]."""

    def build(_):
        raise PacketRejected("not_a_member")

    with pytest.raises(ValueError):
        _run("x", build=build)


def test_shared_transient_reasons_are_the_provider_run_failures():
    assert PROVIDER_TRANSIENT_REASONS == frozenset({"timeout", "provider_error", "parse"})


def test_extract_json_and_token_count_helpers():
    assert extract_json('prefix {"a": 1} suffix') == '{"a": 1}'
    with pytest.raises(ValueError):
        extract_json("no object here")
    assert heuristic_token_count("") == 1 and heuristic_token_count("abcd") == 2
