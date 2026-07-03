"""EVAL-2 AC-4 / AC-5 — verdict schema: evidence required, provenance complete."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from harness.judge.schema import Confidence, Evidence, Verdict, VerdictProvenance, Winner


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


def test_jd12_confidence_enum_and_float_migration():
    """JD-12/D-4: confidence is the low|medium|high enum; a legacy float is coerced
    to its band by the versioned reader, so old-shape verdicts still read."""
    ev = [Evidence(kind="diff", response="A", hunk="@@ -1")]
    # the enum band passes through
    v = Verdict(winner=Winner.A, reason="x", confidence="medium", evidence=ev, provenance=_prov())
    assert v.confidence is Confidence.medium
    # a legacy float is migrated to its band
    old = Verdict(winner=Winner.A, reason="x", confidence=0.85, evidence=ev, provenance=_prov())
    assert old.confidence is Confidence.high
    # the default is low (was 0.0)
    d = Verdict(winner=Winner.A, reason="x", evidence=ev, provenance=_prov())
    assert d.confidence is Confidence.low


def test_confidence_bucket_non_finite_is_low():
    """A NaN/inf confidence is not certainty — it maps to the LEAST-confident band,
    never silently to high (`NaN < 0.4` is False), which would let a model emit
    bare NaN and have it durably recorded as the most-confident band."""
    from harness.judge.schema import confidence_bucket

    assert confidence_bucket(float("nan")) is Confidence.low
    assert confidence_bucket(float("inf")) is Confidence.low
    assert confidence_bucket(-float("inf")) is Confidence.low
    assert confidence_bucket(0.9) is Confidence.high
    assert confidence_bucket(0.5) is Confidence.medium
    assert confidence_bucket(0.1) is Confidence.low


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
