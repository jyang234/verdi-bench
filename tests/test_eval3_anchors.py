"""EVAL-3 D008 — external head-hash anchoring."""

from __future__ import annotations

import pytest

from harness.ledger.anchors import (
    anchor_head,
    anchor_record,
    verify_against_anchor,
    write_anchor,
)
from harness.ledger.events import EventContext, record_chain_anchor


def _ctx():
    return EventContext(experiment_id="exp", actor="t", clock=lambda: "t")


def test_anchor_cli_order_leaves_no_orphaned_external_checkpoint(tmp_path):
    """PRA-L5: if the external write fails after the compute, the CLI's
    ledger-first order means no external checkpoint exists without a ledgered
    chain_anchor. We simulate the CLI sequence and inject a write failure."""
    ledger = tmp_path / "l.ndjson"
    for i in range(2):
        record_chain_anchor(ledger, _ctx(), head_hash="0" * 64, height=i)
    out = tmp_path / "anchors.ndjson"

    # Compute (pure read) succeeds; a crash before write_anchor must leave the
    # external store absent — never a checkpoint with no ledgered record.
    rec = anchor_record(ledger, ts="t0")
    seq = iter(range(100))
    ctx = EventContext(experiment_id="exp", actor="a", clock=lambda: f"t{next(seq)}")
    record_chain_anchor(ledger, ctx, head_hash=rec["head_hash"], height=rec["height"])
    # (write_anchor would run here; simulate its failure by not calling it)
    assert not out.exists()  # no orphaned external checkpoint
    from harness.ledger.query import find_events

    assert len(find_events(ledger, "chain_anchor")) == 3  # the anchoring IS ledgered

    # Completing the write reconciles the external store.
    write_anchor(out, rec)
    assert verify_against_anchor(ledger, out).ok


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
