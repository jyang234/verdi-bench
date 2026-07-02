"""EVAL-3 D008 — external head-hash anchoring."""

from __future__ import annotations

from harness.ledger.anchors import anchor_head, verify_against_anchor
from harness.ledger.events import EventContext, record_chain_anchor


def _ctx():
    return EventContext(experiment_id="exp", actor="t", clock=lambda: "t")


def test_anchor_head_records_height(tmp_path):
    ledger = tmp_path / "l.ndjson"
    for i in range(3):
        record_chain_anchor(ledger, _ctx(), head_hash="0" * 64, height=i)
    rec = anchor_head(ledger, tmp_path / "anchors.ndjson", ts="t0")
    assert rec["height"] == 3


def test_verify_against_anchor_ok(tmp_path):
    ledger = tmp_path / "l.ndjson"
    anchors = tmp_path / "anchors.ndjson"
    for i in range(3):
        record_chain_anchor(ledger, _ctx(), head_hash="0" * 64, height=i)
    anchor_head(ledger, anchors, ts="t0")
    for i in range(2):
        record_chain_anchor(ledger, _ctx(), head_hash="0" * 64, height=10 + i)
    assert verify_against_anchor(ledger, anchors).ok


def test_verify_against_anchor_detects_rewrite(tmp_path):
    ledger = tmp_path / "l.ndjson"
    anchors = tmp_path / "anchors.ndjson"
    for i in range(3):
        record_chain_anchor(ledger, _ctx(), head_hash="0" * 64, height=i)
    anchor_head(ledger, anchors, ts="t0")
    # rewrite anchored history
    lines = ledger.read_text().splitlines()
    lines[0] = lines[0].replace('"height":0', '"height":42')
    ledger.write_text("\n".join(lines) + "\n")
    result = verify_against_anchor(ledger, anchors)
    assert not result.ok
    assert "rewritten" in result.detail
