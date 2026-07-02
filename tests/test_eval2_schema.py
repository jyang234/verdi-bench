"""EVAL-2 AC-4 / AC-5 — verdict schema: evidence required, provenance complete."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from harness.judge.schema import Evidence, Verdict, VerdictProvenance, Winner


def _prov(**kw):
    base = dict(
        judge_model="google/gemini-1.5-pro-002",
        rubric_sha256="a" * 64,
        packet_sha256="b" * 64,
        call_ids=["c1", "c2"],
        orders="both",
        temperature=0.0,
        ts="t0",
    )
    base.update(kw)
    return VerdictProvenance(**base)


def test_ac4_evidence_required():
    # a substantive winner with no evidence is schema-rejected
    with pytest.raises(ValidationError):
        Verdict(winner=Winner.A, reason="x", evidence=[], provenance=_prov())


def test_ac4_evidence_needs_locator():
    with pytest.raises(ValidationError):
        Evidence(kind="diff", response="A")  # no hunk/ref


def test_ac4_valid_verdict():
    v = Verdict(
        winner=Winner.A,
        reason="A is better",
        evidence=[Evidence(kind="diff", response="A", hunk="@@ -1")],
        provenance=_prov(),
    )
    assert v.winner == Winner.A


def test_ac5_verdict_provenance_complete():
    for missing in ["judge_model", "rubric_sha256", "packet_sha256", "orders", "temperature", "ts"]:
        kw = dict(
            judge_model="m", rubric_sha256="a", packet_sha256="b",
            call_ids=["c1", "c2"], orders="both", temperature=0.0, ts="t",
        )
        del kw[missing]
        with pytest.raises(ValidationError):
            VerdictProvenance(**kw)


def test_ac5_both_orders_needs_two_calls():
    with pytest.raises(ValidationError):
        Verdict(
            winner=Winner.A,
            reason="x",
            evidence=[Evidence(kind="diff", response="A", hunk="h")],
            provenance=_prov(call_ids=["only-one"]),
        )


def test_cant_judge_needs_no_evidence():
    v = Verdict(winner=Winner.CANT_JUDGE, reason="timeout", provenance=_prov(call_ids=["c1"]))
    assert v.winner == Winner.CANT_JUDGE
