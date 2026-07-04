"""EVAL-11 advisory tier — blinded, isolated, fail-closed LLM pass [AC-4].

Blinding canaries never reach the forensic payload (property test); the packet
signature is the isolation allowlist; every failure is CANT_REVIEW(reason);
calibration fixtures produce a per-detector kappa via the EVAL-7 machinery.
"""

from __future__ import annotations

import inspect
import json

from hypothesis import given, settings
from hypothesis import strategies as st

from harness.forensics.detectors import DETECTOR_IDS
from harness.forensics.review import (
    JUDGMENT_TAG,
    CantReviewReason,
    ForensicReview,
    build_forensic_packet,
    detector_kappa,
    forensic_review,
    spotcheck_kappa,
)
from harness.judge.providers.base import ProviderError, ProviderTimeout
from harness.judge.providers.fake import DeterministicFakeProvider, FakeProvider
from harness.ledger.events import (
    record_forensic_spotcheck,
    record_forensics_report,
)
from harness.review.kappa import ReviewedItem
from tests.fixtures.builders import fixed_ctx

_CANARIES = ["control", "treatment", "claude-code", "openai/gpt-4o-2024-08-06"]


def _ok_response() -> str:
    return json.dumps(
        {
            "suspicions": {d: False for d in DETECTOR_IDS},
            "narrative": "steady edit-test loop, no shortcuts",
        }
    )


# --- blinding + isolation ------------------------------------------------------
@settings(max_examples=25, deadline=None)
@given(
    fragment=st.sampled_from(
        ["claude-code", "the control arm", "openai/gpt-4o-2024-08-06", "assistant:", "anthropic"]
    ),
    filler=st.text(alphabet="abcdefgh \n", max_size=80),
)
def test_blinding_canaries_never_reach_payload(fragment, filler):
    """Property [AC-4 VC]: identity canaries planted anywhere in the transcript
    are scrubbed before the provider sees the payload."""
    provider = FakeProvider([_ok_response()])
    review = forensic_review(
        "t-1", f"{filler} {fragment} {filler}", canaries=_CANARIES, provider=provider
    )
    assert review.cant_review_reason is None
    payload = "".join(m["content"] for m in provider.calls[0]["messages"])
    for canary in ("control", "treatment", "claude-code", "openai", "gpt-4o", "anthropic"):
        assert canary not in payload, canary


def test_forensic_call_isolated_by_signature():
    """The packet builder's signature is the allowlist: no parameter exists for
    outcome-verdict, process-score, or arm-identity content [AC-4]."""
    params = set(inspect.signature(build_forensic_packet).parameters)
    assert params == {"transcript"}
    for forbidden in ("verdict", "winner", "judge_verdict", "outcome", "rubric", "arm"):
        assert forbidden not in params

    messages = build_forensic_packet("edit, test, pass")
    body = " ".join(m["content"] for m in messages).lower()
    assert "winner" not in body
    # and its system prompt is its own — not the judge's or the process tier's
    assert "how the work was done" not in messages[0]["content"]


def test_judgment_tag_on_every_narrative():
    review = forensic_review(
        "t-1", "plain transcript", provider=FakeProvider([_ok_response()])
    )
    assert review.narrative.startswith(JUDGMENT_TAG)
    import pytest

    with pytest.raises(ValueError):
        ForensicReview(
            trial_id="t-1",
            suspicions={d: False for d in DETECTOR_IDS},
            narrative="untagged claim",
        )


# --- fail-closed CANT_REVIEW ---------------------------------------------------
def test_provider_failure_fails_closed():
    for exc, reason in [
        (ProviderError("boom"), "provider_error"),
        (ProviderTimeout("slow"), "timeout"),
    ]:
        review = forensic_review("t-1", "transcript", provider=FakeProvider([exc]))
        assert review.cant_review_reason == reason
        assert review.suspicions is None and review.narrative is None


def test_unparseable_output_fails_closed():
    review = forensic_review("t-1", "transcript", provider=FakeProvider(["not json"]))
    assert review.cant_review_reason == CantReviewReason.parse.value

    partial = json.dumps({"suspicions": {"holdout_tamper": True}, "narrative": "x"})
    review = forensic_review("t-1", "transcript", provider=FakeProvider([partial]))
    assert review.cant_review_reason == CantReviewReason.parse.value


def test_unknown_provider_prefix_fails_closed():
    review = forensic_review("t-1", "transcript", provider_model="nosuch/model")
    assert review.cant_review_reason == CantReviewReason.provider_error.value


def test_secret_leak_fails_closed():
    review = forensic_review(
        "t-1",
        "here is sk-" + "A" * 40,
        provider=FakeProvider([_ok_response()]),
    )
    assert review.cant_review_reason == CantReviewReason.redaction_leak.value


def test_context_overflow_fails_closed():
    review = forensic_review(
        "t-1",
        "x" * 100,
        provider=FakeProvider([_ok_response()]),
        max_context_tokens=10,
    )
    assert review.cant_review_reason == CantReviewReason.context_overflow.value


def test_deterministic_fake_provider_forensic_branch():
    """The fake/ provider serves a valid forensic review with no network —
    reproducibly, so fixture experiments calibrate deterministically."""
    provider = DeterministicFakeProvider()
    a = forensic_review("t-1", "some transcript", provider=provider)
    b = forensic_review("t-1", "some transcript", provider=provider)
    assert a.cant_review_reason is None
    assert set(a.suspicions) == set(DETECTOR_IDS)
    assert a == b


# --- per-detector kappa calibration [AC-4] --------------------------------------
def test_calibration_fixtures_produce_per_detector_kappa():
    items = {
        "holdout_tamper": [
            ReviewedItem(a=1, b=1, stratum="mandatory"),
            ReviewedItem(a=0, b=0, stratum="mandatory"),
            ReviewedItem(a=1, b=1, stratum="floor"),
            ReviewedItem(a=0, b=1, stratum="mandatory"),
        ],
        "test_skip_insertion": [],
    }
    cal = detector_kappa(items)
    assert cal["holdout_tamper"].sufficient
    assert cal["holdout_tamper"].kappa is not None
    assert cal["holdout_tamper"].n == 4
    # no pairs ⇒ insufficient, never a fabricated agreement
    assert not cal["test_skip_insertion"].sufficient
    assert cal["test_skip_insertion"].kappa is None


def test_degenerate_marginals_insufficient_not_perfect():
    items = {"holdout_tamper": [ReviewedItem(a=0, b=0, stratum="mandatory")] * 5}
    cal = detector_kappa(items)
    assert cal["holdout_tamper"].kappa is None
    assert not cal["holdout_tamper"].sufficient


def test_spotcheck_kappa_pairs_llm_and_human(tmp_path):
    """LLM suspicions from the latest forensics_report pair with ledgered human
    spot-checks into the kappa table; CANT_REVIEW trials cannot calibrate."""
    ledger = tmp_path / "ledger.ndjson"
    ctx = fixed_ctx()
    reviews = {
        "t-1": {"trial_id": "t-1",
                "suspicions": {d: (d == "holdout_tamper") for d in DETECTOR_IDS},
                "narrative": f"{JUDGMENT_TAG} tampered", "cant_review_reason": None},
        "t-2": {"trial_id": "t-2", "suspicions": None, "narrative": None,
                "cant_review_reason": "provider_error"},
    }
    record_forensics_report(
        ledger, ctx,
        forensics_report={"vocabulary_version": 1, "metrics": {}, "flags": [],
                          "coverage": {"trials": 2, "covered": 2, "gaps": []},
                          "reviews": reviews},
    )
    record_forensic_spotcheck(
        ledger, ctx, trial_id="t-1",
        labels={d: (d == "holdout_tamper") for d in DETECTOR_IDS}, stratum="mandatory",
    )
    record_forensic_spotcheck(
        ledger, ctx, trial_id="t-2", labels={"holdout_tamper": True}, stratum="floor",
    )
    table = spotcheck_kappa(ledger)
    assert table["n_spotchecks"] == 2
    by_detector = table["kappa_by_detector"]
    # only t-1 calibrates (t-2's review failed closed) — one pair per detector
    assert by_detector["holdout_tamper"]["n"] == 1
    assert set(by_detector) == set(DETECTOR_IDS)
