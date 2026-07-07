"""EVAL-2 AC-1 / AC-3 / AC-8 — client debiasing, vendor-unrestricted, fail-closed."""

from __future__ import annotations

from harness.judge.client import judge_pair
from harness.judge.providers.base import ProviderError, ProviderRefusal, ProviderTimeout
from harness.judge.providers.fake import FakeProvider, FakeProviderExhausted
from harness.judge.schema import Confidence, Winner
from harness.ledger.query import find_events
from tests.fixtures.builders import ctx_for
from tests.fixtures.judge_fakes import make_config, make_packet, verdict_json


def test_jd13_response_labels_deterministic_both_orders(tmp_path):
    """JD-13 / EVAL-2-D-P6-3: response labels follow the fixed both-orders scheme
    (AB then BA), a pure function of order — not a random per-call assignment.
    Both orders always run, so position bias cancels by construction; this pins
    the mapping and the two-order behavior so a switch to a single order or to a
    random labeling is caught."""
    from harness.judge.client import _pos_to_arm

    # the label->arm mapping is deterministic in the order, both directions.
    assert _pos_to_arm("AB") == {1: "A", 2: "B"}
    assert _pos_to_arm("BA") == {1: "B", 2: "A"}

    prov = FakeProvider([verdict_json("1"), verdict_json("1")])
    v, _ = _run(tmp_path, prov)
    # both orders were judged (two provider calls)...
    assert len(prov.calls) == 2
    # ...in distinct rendered orders (AB vs BA), not the same order twice.
    assert prov.calls[0]["messages"] != prov.calls[1]["messages"]
    # and the verdict is reproducible: a re-run yields the same winner.
    v2, _ = _run(tmp_path, FakeProvider([verdict_json("1"), verdict_json("1")]))
    assert v.winner == v2.winner


def test_fake_provider_raises_on_exhaustion():
    """RN-18: a scripted FakeProvider raises when called past its script instead
    of silently replaying the last response (which could hide a miscounted test)."""
    import pytest

    prov = FakeProvider([verdict_json("1")])
    assert prov.complete("m", [{"content": "x"}], 0.0)  # first call: scripted
    with pytest.raises(FakeProviderExhausted):
        prov.complete("m", [{"content": "x"}], 0.0)      # second call: no script


def _run(tmp_path, provider, config=None):
    ledger = tmp_path / "l.ndjson"
    v = judge_pair(make_packet(), config or make_config(), ledger, ctx_for(tmp_path),
                   ts="t0", provider=provider)
    return v, ledger


# --- JD-12 / D-4: confidence is the low|medium|high enum, from the parsed value --
def test_jd12_confidence_enum_from_parsed_value(tmp_path):
    """JD-12/D-4: an order-consistent verdict's confidence is the enum band bucketed
    from the judge's PARSED confidence (0.9 → high), not the discarded 0.8 hardcode."""
    v, _ = _run(tmp_path, FakeProvider([verdict_json("1", confidence=0.9),
                                        verdict_json("2", confidence=0.9)]))
    assert v.winner is Winner.A
    assert v.confidence is Confidence.high


def test_jd12_confidence_low_when_order_inconsistent(tmp_path):
    """An order-inconsistent (position-biased ⇒ TIE) verdict is low confidence —
    we trust a call the two orders disagreed on less."""
    v, _ = _run(tmp_path, FakeProvider([verdict_json("1"), verdict_json("1")]))
    assert v.winner is Winner.TIE
    assert v.confidence is Confidence.low


def test_jd12_confidence_medium_bucket(tmp_path):
    """A mid parsed confidence buckets to medium."""
    v, _ = _run(tmp_path, FakeProvider([verdict_json("1", confidence=0.5),
                                        verdict_json("2", confidence=0.6)]))
    assert v.confidence is Confidence.medium  # min(0.5, 0.6)=0.5 → medium


def test_ac3_both_orders_invoked(tmp_path):
    prov = FakeProvider([verdict_json("1"), verdict_json("2")])
    _run(tmp_path, prov)
    assert len(prov.calls) == 2  # both orders exercised


def test_ac3_consistent_agreement(tmp_path):
    # content-consistent judge: picks Response1 in AB then Response2 in BA -> both A
    prov = FakeProvider([verdict_json("1"), verdict_json("2")])
    v, ledger = _run(tmp_path, prov)
    assert v.winner == Winner.A
    assert v.order_inconsistent is False
    assert len(v.provenance.call_ids) == 2


def test_ac3_order_debias_inconsistent_ties(tmp_path):
    # position-biased judge always picks Response 1 -> AB=A, BA=B -> TIE
    prov = FakeProvider([verdict_json("1"), verdict_json("1")])
    v, ledger = _run(tmp_path, prov)
    assert v.winner == Winner.TIE
    assert v.order_inconsistent is True


def test_ac1_judge_vendor_unrestricted(tmp_path):
    # a judge whose vendor matches an arm still validates, runs, produces a verdict
    prov = FakeProvider([verdict_json("1"), verdict_json("2")])
    cfg = make_config(model="anthropic/claude-3-5-sonnet-20241022")
    v, _ = _run(tmp_path, prov, cfg)
    assert v.winner == Winner.A  # no vendor allow/deny list anywhere


def test_ac8_fail_closed_timeout(tmp_path):
    v, ledger = _run(tmp_path, FakeProvider([ProviderTimeout("slow")]))
    assert v.winner == Winner.CANT_JUDGE and v.reason == "timeout"
    assert len(find_events(ledger, "judge_verdict")) == 1


def test_ac8_fail_closed_refusal(tmp_path):
    v, _ = _run(tmp_path, FakeProvider([ProviderRefusal("no")]))
    assert v.winner == Winner.CANT_JUDGE and v.reason == "refusal"


def test_ac8_fail_closed_provider_error(tmp_path):
    v, _ = _run(tmp_path, FakeProvider([ProviderError("500")]))
    assert v.winner == Winner.CANT_JUDGE and v.reason == "provider_error"


def test_ac8_fail_closed_context_overflow(tmp_path):
    """PRA-H3: a ProviderContextOverflow (OpenAI context_length_exceeded on a
    large packet) must record exactly one CANT_JUDGE(context_overflow), not
    crash judge_pair with a ValueError (missing enum member) and write no event
    — the regression that voided AC-8 for OpenAI judges."""
    from harness.judge.providers.base import ProviderContextOverflow

    v, ledger = _run(tmp_path, FakeProvider([ProviderContextOverflow("too big")]))
    assert v.winner == Winner.CANT_JUDGE and v.reason == "context_overflow"
    assert len(find_events(ledger, "judge_verdict")) == 1


def test_ac8_fail_closed_parse(tmp_path):
    v, _ = _run(tmp_path, FakeProvider(["this is not json"]))
    assert v.winner == Winner.CANT_JUDGE and v.reason == "parse"


def test_ac4_malformed_becomes_cant_judge(tmp_path):
    # judge agrees on a substantive winner (A) but cites no evidence in either
    # order -> the assembled A-verdict is evidence-free -> malformed -> CANT_JUDGE
    prov = FakeProvider([verdict_json("1", with_evidence=False),
                         verdict_json("2", with_evidence=False)])
    v, ledger = _run(tmp_path, prov)
    assert v.winner == Winner.CANT_JUDGE and v.reason == "malformed"
    # every comparison yields exactly one verdict event
    assert len(find_events(ledger, "judge_verdict")) == 1


def test_ac8_every_comparison_has_one_event(tmp_path):
    for prov in [
        FakeProvider([verdict_json("1"), verdict_json("2")]),
        FakeProvider([ProviderTimeout("x")]),
        FakeProvider(["garbage"]),
    ]:
        ledger = tmp_path / f"l{id(prov)}.ndjson"
        judge_pair(make_packet(), make_config(), ledger, ctx_for(tmp_path), ts="t0", provider=prov)
        assert len(find_events(ledger, "judge_verdict")) == 1


def test_ac7_comparison_id_propagated(tmp_path):
    # regression: judge_pair must stamp comparison_id/task_class so calibration
    # can join judge and human verdicts (previously always None)
    from harness.judge.calibrate import pairs_from_ledger
    from harness.ledger.events import append_human_verdict
    from harness.judge.schema import Verdict, VerdictProvenance, Winner

    ledger = tmp_path / "l.ndjson"
    ctx = ctx_for(tmp_path)
    v = judge_pair(make_packet(), make_config(), ledger, ctx, ts="t0",
                   provider=FakeProvider([verdict_json("1"), verdict_json("2")]),
                   comparison_id="cmp-9", task_class="refactor")
    assert v.comparison_id == "cmp-9" and v.task_class == "refactor"
    hv = Verdict(winner=Winner.A, reason="agree",
                 evidence=[{"kind": "diff", "response": "A", "hunk": "h"}],
                 provenance=VerdictProvenance(judge_model="m", rubric_sha256="a",
                     packet_sha256="b", call_ids=["c1", "c2"], orders="both",
                     temperature=0.0, ts="t"),
                 source="human", comparison_id="cmp-9", task_class="refactor")
    append_human_verdict(ledger, ctx, verdict=hv.model_dump(mode="json"))
    pairs = pairs_from_ledger(ledger)
    assert len(pairs) == 1 and pairs[0]["task_class"] == "refactor"


def test_ac8_identity_leak_records_cant_judge(tmp_path):
    # regression: a leaking packet must fail closed WITH a ledger event, not
    # escape with none
    ledger = tmp_path / "l.ndjson"
    pkt = make_packet(diff_a="leaked arm-control identity")
    v = judge_pair(pkt, make_config(), ledger, ctx_for(tmp_path), ts="t0",
                   provider=FakeProvider([verdict_json("1"), verdict_json("2")]),
                   canaries=["arm-control"])
    assert v.winner == Winner.CANT_JUDGE and v.reason == "identity_leak"
    assert len(find_events(ledger, "judge_verdict")) == 1


def test_judge_reason_preserves_rationale(tmp_path):
    # regression: verdict reason carries the judge's rationale, not winner letters
    import json as _json
    raw = _json.dumps({"winner": "1", "reason": "Response 1 fixed the holdout",
                       "evidence": [{"kind": "diff", "response": 1, "hunk": "@@"}],
                       "confidence": 0.9})
    raw2 = _json.dumps({"winner": "2", "reason": "Response 2 broke the build",
                        "evidence": [{"kind": "diff", "response": 2, "hunk": "@@"}],
                        "confidence": 0.9})
    v, _ = _run(tmp_path, FakeProvider([raw, raw2]))
    assert "fixed the holdout" in v.reason


def test_ac8_unknown_provider_records_cant_judge(tmp_path):
    # JD-2: provider resolution runs inside the fail-closed envelope, so an
    # unknown prefix (legal per D001) records exactly one CANT_JUDGE(provider_error)
    # rather than escaping judge_pair with no event.
    ledger = tmp_path / "l.ndjson"
    # a versioned id (passes plan-time alias check) with an unknown provider prefix
    v = judge_pair(make_packet(), make_config(model="mystery/model-2024-01-01"),
                   ledger, ctx_for(tmp_path), ts="t0")  # provider=None -> real get_provider
    assert v.winner == Winner.CANT_JUDGE and v.reason == "provider_error"
    assert len(find_events(ledger, "judge_verdict")) == 1


def test_ac8_provider_shape_error_is_provider_error(tmp_path):
    # JD-3: an error-shaped/safety-blocked 200 makes a provider raise KeyError/
    # IndexError while extracting content; the client must fail closed to
    # CANT_JUDGE(provider_error), not let the exception escape with no event.
    for i, exc in enumerate((KeyError("choices"), IndexError("candidates"))):
        ledger = tmp_path / f"l{i}.ndjson"
        v = judge_pair(make_packet(), make_config(), ledger, ctx_for(tmp_path), ts="t0",
                       provider=FakeProvider([exc]))
        assert v.winner == Winner.CANT_JUDGE and v.reason == "provider_error"
        assert len(find_events(ledger, "judge_verdict")) == 1


def test_ac1_no_vendor_denylist_in_code():
    """The client and provider dispatch carry no vendor allow/deny list [AC-1]."""
    import pathlib

    for name in ["client.py", "providers/base.py"]:
        text = (pathlib.Path("harness/judge") / name).read_text().lower()
        assert "denylist" not in text
        assert "deny_list" not in text
        assert "banned" not in text


# --- balanced-brace verdict extraction [refactor 05 §7] ---------------------
def test_first_json_object_leading_prose():
    """Leading prose before the object is skipped to the first brace."""
    from harness.judge.client import _first_json_object

    obj = '{"winner": "1", "confidence": 0.9}'
    assert _first_json_object(f"Here is my verdict: {obj}") == obj


def test_first_json_object_trailing_prose_with_stray_brace():
    """The greedy \\{.*\\} regression: trailing prose that itself contains a '}'
    was swallowed into an unparseable blob. Balanced extraction stops at the first
    complete object."""
    from harness.judge.client import _first_json_object

    obj = '{"winner": "1", "confidence": 0.9}'
    assert _first_json_object(f"{obj} — note: mind the }} brace.") == obj


def test_first_json_object_nested_and_string_aware():
    """A nested object (evidence carries objects) extracts whole, and a '}' inside
    a JSON string value never closes the object early (string-aware counter)."""
    from harness.judge.client import _first_json_object

    obj = '{"winner": "1", "evidence": [{"kind": "diff", "response": 1}], "note": "close }"}'
    assert _first_json_object(f"prose {obj} trailing }}") == obj


def test_first_json_object_missing_or_unterminated_fails_closed():
    """No brace, or an unterminated object, raises ValueError → the client maps it
    to CANT_JUDGE(parse); fail-closed behavior is preserved."""
    import pytest

    from harness.judge.client import _first_json_object

    with pytest.raises(ValueError):
        _first_json_object("no json here")
    with pytest.raises(ValueError):
        _first_json_object('{"winner": "1"')  # unterminated


def test_trailing_prose_verdict_recovers_not_cant_judge(tmp_path):
    """End to end: a judge that wraps its JSON in prose (and a stray '}') yields a
    real verdict now, where the greedy extractor forced CANT_JUDGE(parse) — fewer
    spurious parse failures, same fail-closed floor for genuine garbage [05 §7]."""
    import json as _json

    def chatty(winner, response):
        body = _json.dumps({
            "winner": winner, "reason": "ok",
            "evidence": [{"kind": "diff", "response": response, "hunk": "@@"}],
            "confidence": 0.9,
        })
        return f"Sure, here is my call: {body}\nThanks! (mind the }} brace)"

    v, ledger = _run(tmp_path, FakeProvider([chatty("1", 1), chatty("2", 2)]))
    assert v.winner == Winner.A  # recovered, not CANT_JUDGE(parse)
    assert len(find_events(ledger, "judge_verdict")) == 1


# --- JD-5 / JD-11 / D-P4-1 (arm_map) additions ------------------------------
def _judge_verdict(comparison_id, winner):
    from harness.judge.schema import Verdict, VerdictProvenance, Winner

    ev = [{"kind": "diff", "response": winner, "hunk": "h"}] if winner in ("A", "B") else []
    return Verdict(
        winner=Winner(winner), reason=winner, evidence=ev,
        provenance=VerdictProvenance(judge_model="m", rubric_sha256="a",
            packet_sha256="b", call_ids=["c1", "c2"], orders="both",
            temperature=0.0, ts="t"),
        comparison_id=comparison_id, task_class="refactor",
    )


def _human_verdict(comparison_id, winner):
    from harness.judge.schema import Verdict, VerdictProvenance, Winner

    ev = [{"kind": "diff", "response": winner, "hunk": "h"}] if winner in ("A", "B") else []
    return Verdict(
        winner=Winner(winner), reason=winner, evidence=ev,
        provenance=VerdictProvenance(judge_model="human", rubric_sha256="human",
            packet_sha256="human", call_ids=["human"], orders="single",
            temperature=0.0, ts="t"),
        source="human", comparison_id=comparison_id, task_class="refactor",
    )


def test_jd5_cant_judge_excluded_from_kappa(tmp_path):
    """JD-5: a CANT_JUDGE verdict is a fail-closed non-answer, not a kappa
    category. A judge CANT_JUDGE paired with a human verdict must NOT enter the
    kappa pairs (today it pools in as an ordinary label)."""
    from harness.judge.calibrate import pairs_from_ledger
    from harness.ledger.events import append_human_verdict, append_verdict

    ledger = tmp_path / "l.ndjson"
    ctx = ctx_for(tmp_path)
    append_verdict(ledger, ctx, verdict=_judge_verdict("c1", "CANT_JUDGE").model_dump(mode="json"))
    append_human_verdict(ledger, ctx, verdict=_human_verdict("c1", "A").model_dump(mode="json"))
    assert pairs_from_ledger(ledger) == []


def test_jd5_null_comparison_id_not_joined(tmp_path):
    """JD-5: verdicts with no comparison_id must not join on ``None`` — two
    unrelated verdicts previously paired with each other via the shared None
    key. With no reliable id, they are skipped, not falsely joined."""
    from harness.judge.calibrate import pairs_from_ledger
    from harness.ledger.events import append_human_verdict, append_verdict

    ledger = tmp_path / "l.ndjson"
    ctx = ctx_for(tmp_path)
    append_verdict(ledger, ctx, verdict=_judge_verdict(None, "A").model_dump(mode="json"))
    append_verdict(ledger, ctx, verdict=_judge_verdict(None, "B").model_dump(mode="json"))
    append_human_verdict(ledger, ctx, verdict=_human_verdict(None, "A").model_dump(mode="json"))
    append_human_verdict(ledger, ctx, verdict=_human_verdict(None, "B").model_dump(mode="json"))
    assert pairs_from_ledger(ledger) == []


def test_jd5_last_judge_verdict_wins_dedup(tmp_path):
    """JD-5: duplicate judge verdicts for one comparison dedupe to a single pair
    (the last verdict), not one pair per duplicate."""
    from harness.judge.calibrate import pairs_from_ledger
    from harness.ledger.events import append_human_verdict, append_verdict

    ledger = tmp_path / "l.ndjson"
    ctx = ctx_for(tmp_path)
    append_verdict(ledger, ctx, verdict=_judge_verdict("c1", "A").model_dump(mode="json"))
    append_verdict(ledger, ctx, verdict=_judge_verdict("c1", "B").model_dump(mode="json"))
    append_human_verdict(ledger, ctx, verdict=_human_verdict("c1", "B").model_dump(mode="json"))
    pairs = pairs_from_ledger(ledger)
    assert len(pairs) == 1
    assert pairs[0]["judge_winner"] == "B"  # the LAST judge verdict


def test_jd11_single_order_flagged(tmp_path):
    """JD-11: orders='single' skips D003 debiasing; the verdict must carry a
    ``single_order`` flag so a full experiment cannot silently skip it."""
    prov = FakeProvider([verdict_json("1")])
    v, ledger = _run(tmp_path, prov, make_config(orders="single"))
    assert v.single_order is True
    ev = find_events(ledger, "judge_verdict")[0]
    assert ev["verdict"]["single_order"] is True


def test_both_orders_not_flagged_single(tmp_path):
    v, _ = _run(tmp_path, FakeProvider([verdict_json("1"), verdict_json("2")]))
    assert v.single_order is False


def test_dp4_1_arm_map_recorded_on_verdict(tmp_path):
    """D-P4-1: the judge records its A/B -> physical-arm map so the kappa join is
    frame-correct (a slice of AN-1). The map rides onto the ledgered verdict."""
    arm_map = {"A": "control", "B": "treatment"}
    v = judge_pair(make_packet(), make_config(), tmp_path / "l.ndjson", ctx_for(tmp_path),
                   ts="t0", provider=FakeProvider([verdict_json("1"), verdict_json("2")]),
                   arm_map=arm_map)
    assert v.arm_map == arm_map
    ev = find_events(tmp_path / "l.ndjson", "judge_verdict")[0]
    assert ev["verdict"]["arm_map"] == arm_map


def test_dp4_1_arm_map_recorded_on_cant_judge(tmp_path):
    """Even a fail-closed CANT_JUDGE verdict carries the arm_map (the frame is
    known before the judge call fails)."""
    arm_map = {"A": "control", "B": "treatment"}
    v = judge_pair(make_packet(), make_config(), tmp_path / "l.ndjson", ctx_for(tmp_path),
                   ts="t0", provider=FakeProvider([ProviderTimeout("x")]), arm_map=arm_map)
    assert v.winner == Winner.CANT_JUDGE and v.arm_map == arm_map
