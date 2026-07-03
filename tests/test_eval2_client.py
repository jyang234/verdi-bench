"""EVAL-2 AC-1 / AC-3 / AC-8 — client debiasing, vendor-unrestricted, fail-closed."""

from __future__ import annotations

from harness.judge.client import judge_pair
from harness.judge.providers.base import ProviderError, ProviderRefusal, ProviderTimeout
from harness.judge.providers.fake import FakeProvider
from harness.judge.schema import Winner
from harness.ledger.query import find_events
from tests.fixtures.builders import fixed_ctx
from tests.fixtures.judge_fakes import make_config, make_packet, verdict_json


def _run(tmp_path, provider, config=None):
    ledger = tmp_path / "l.ndjson"
    v = judge_pair(make_packet(), config or make_config(), ledger, fixed_ctx(),
                   ts="t0", provider=provider)
    return v, ledger


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
        judge_pair(make_packet(), make_config(), ledger, fixed_ctx(), ts="t0", provider=prov)
        assert len(find_events(ledger, "judge_verdict")) == 1


def test_ac7_comparison_id_propagated(tmp_path):
    # regression: judge_pair must stamp comparison_id/task_class so calibration
    # can join judge and human verdicts (previously always None)
    from harness.judge.calibrate import pairs_from_ledger
    from harness.ledger.events import append_human_verdict
    from harness.judge.schema import Verdict, VerdictProvenance, Winner

    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
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
    v = judge_pair(pkt, make_config(), ledger, fixed_ctx(), ts="t0",
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
                   ledger, fixed_ctx(), ts="t0")  # provider=None -> real get_provider
    assert v.winner == Winner.CANT_JUDGE and v.reason == "provider_error"
    assert len(find_events(ledger, "judge_verdict")) == 1


def test_ac8_provider_shape_error_is_provider_error(tmp_path):
    # JD-3: an error-shaped/safety-blocked 200 makes a provider raise KeyError/
    # IndexError while extracting content; the client must fail closed to
    # CANT_JUDGE(provider_error), not let the exception escape with no event.
    for i, exc in enumerate((KeyError("choices"), IndexError("candidates"))):
        ledger = tmp_path / f"l{i}.ndjson"
        v = judge_pair(make_packet(), make_config(), ledger, fixed_ctx(), ts="t0",
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
