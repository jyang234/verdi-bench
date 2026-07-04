"""EVAL-3 AC-3 / AC-7 — hash-chained ledger: append, verify, tamper, atomicity."""

from __future__ import annotations

import os

import pytest

from harness.ledger import chain
from harness.ledger.events import EventContext, record_chain_anchor


def _ctx():
    seq = iter(range(1000))
    return EventContext(experiment_id="exp", actor="t", clock=lambda: f"t{next(seq)}")


def _append_n(path, n, ctx):
    for i in range(n):
        record_chain_anchor(path, ctx, head_hash="0" * 64, height=i)


def test_ac3_chain_append(tmp_path):
    ledger = tmp_path / "l.ndjson"
    ctx = _ctx()
    _append_n(ledger, 3, ctx)
    lines = ledger.read_text().splitlines()
    assert len(lines) == 3
    # first event is genesis-chained
    import json

    first = json.loads(lines[0])
    assert first["prev_hash"] == chain.GENESIS_PREV_HASH
    assert chain.verify_chain(ledger).ok


def test_ac3_tamper_detected_rewrite(tmp_path):
    ledger = tmp_path / "l.ndjson"
    _append_n(ledger, 4, _ctx())
    lines = ledger.read_text().splitlines()
    # mutate a middle line's payload
    lines[1] = lines[1].replace('"height":1', '"height":99')
    ledger.write_text("\n".join(lines) + "\n")
    result = chain.verify_chain(ledger)
    assert not result.ok
    # the break manifests at the successor of the rewritten line
    assert result.line_number == 3


def test_ac3_tamper_detected_deletion(tmp_path):
    ledger = tmp_path / "l.ndjson"
    _append_n(ledger, 4, _ctx())
    lines = ledger.read_text().splitlines()
    del lines[1]
    ledger.write_text("\n".join(lines) + "\n")
    assert not chain.verify_chain(ledger).ok


def test_ac3_tamper_detected_reorder(tmp_path):
    ledger = tmp_path / "l.ndjson"
    _append_n(ledger, 4, _ctx())
    lines = ledger.read_text().splitlines()
    lines[1], lines[2] = lines[2], lines[1]
    ledger.write_text("\n".join(lines) + "\n")
    assert not chain.verify_chain(ledger).ok


def test_ac3_clean_file_ok(tmp_path):
    ledger = tmp_path / "l.ndjson"
    _append_n(ledger, 5, _ctx())
    assert chain.verify_chain(ledger).ok


def test_ac7_append_atomic(tmp_path):
    """Fault-inject a failing writer: exception ⇒ no partial line."""
    ledger = tmp_path / "l.ndjson"
    _append_n(ledger, 2, _ctx())
    before = ledger.read_bytes()

    def boom(fd, data):  # noqa: ANN001
        raise OSError("disk full")

    with pytest.raises(OSError):
        chain.append_event(
            ledger, {"event": "chain_anchor", "provenance": {}}, writer=boom
        )
    after = ledger.read_bytes()
    assert after == before  # nothing partial survived
    assert chain.verify_chain(ledger).ok


def test_ac7_append_refuses_truncated_final_line(tmp_path):
    """A ledger whose final line lost its newline must not be appended onto.

    Detection previously lived only in ``verify_chain``; ``append_event``
    happily chained a new line onto the unterminated fragment. Refuse loudly
    (PL-13) and leave the file byte-identical.
    """
    ledger = tmp_path / "l.ndjson"
    _append_n(ledger, 2, _ctx())
    full = ledger.read_bytes()
    assert full.endswith(b"\n")
    truncated = full[:-1]  # strip the trailing newline: final line is now partial
    ledger.write_bytes(truncated)

    with pytest.raises(chain.TruncatedLedgerError) as exc:
        record_chain_anchor(ledger, _ctx(), head_hash="0" * 64, height=99)
    assert "2" in str(exc.value)  # names the (unterminated) line count
    assert ledger.read_bytes() == truncated  # nothing appended, no concatenation


def test_ac7_concurrent_appends_chain(tmp_path):
    """Appends under flock stay consistent even when interleaved."""
    import threading

    ledger = tmp_path / "l.ndjson"

    def worker(n):
        ctx = EventContext(experiment_id="exp", actor=f"w{n}", clock=lambda: "t")
        for i in range(10):
            record_chain_anchor(ledger, ctx, head_hash="0" * 64, height=i)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ledger.read_text().splitlines()) == 40
    assert chain.verify_chain(ledger).ok
