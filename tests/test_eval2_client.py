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


def test_ac1_no_vendor_denylist_in_code():
    """The client and provider dispatch carry no vendor allow/deny list [AC-1]."""
    import pathlib

    for name in ["client.py", "providers/base.py"]:
        text = (pathlib.Path("harness/judge") / name).read_text().lower()
        assert "denylist" not in text
        assert "deny_list" not in text
        assert "banned" not in text
