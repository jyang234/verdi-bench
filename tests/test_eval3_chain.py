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
    assert len(ledger.read_bytes().split(b"\n")) - 1 == 40
    assert chain.verify_chain(ledger).ok


# --- PRA-H1: reader/verifier line-splitting parity -------------------------
# A chain-valid event whose payload string carries a Unicode line separator
# (U+0085 NEL, U+2028 LS, U+2029 PS — all legal, unescaped, inside a JSON
# string) must round-trip through the read helpers, not tear into fragments.
# Before the fix, query.iter_events/tail_events used str.splitlines() (which
# splits on those code points) while verify_chain used b"\n" only, so such an
# event verified clean yet crashed every read gate — a poison-event DoS.
from hypothesis import example, given, settings
from hypothesis import strategies as st

from harness.ledger import query

_LINE_SEPS = "\x85  \x0b\x0c\x1c\x1d\x1e"


@settings(max_examples=50, deadline=None)
@given(payload=st.text())
@example(payload="before\x85after")
@example(payload="line sep")
@example(payload="para sep")
@example(payload="all" + _LINE_SEPS + "seps")
def test_reader_verifier_parity_over_unicode_payloads(tmp_path_factory, payload):
    ledger = tmp_path_factory.mktemp("chain") / "l.ndjson"
    stored = chain.append_event(
        ledger, {"event": "cant_grade", "experiment_id": "exp", "reason": payload}
    )
    # the chain says the ledger is clean...
    assert chain.verify_chain(ledger).ok
    # ...and the read helpers agree, yielding the event intact (not torn).
    events = query.read_events(ledger)
    assert len(events) == 1
    assert events[0]["reason"] == payload
    assert events[0]["prev_hash"] == stored["prev_hash"]
    # tail_events must consume it identically.
    tail, offset = query.tail_events(ledger, 0)
    assert len(tail) == 1 and tail[0]["reason"] == payload
    assert offset == ledger.stat().st_size


def test_append_refuses_nonfinite_floats(tmp_path):
    """PRA-L1: NaN/Infinity are not RFC 8259 JSON; refuse them at append so the
    ledger stays independently verifiable (jq, other languages)."""
    ledger = tmp_path / "l.ndjson"
    with pytest.raises(ValueError):
        chain.append_event(ledger, {"event": "x", "cost": float("nan")})
    with pytest.raises(ValueError):
        chain.append_event(ledger, {"event": "x", "ceiling": float("inf")})
    # nothing was written
    assert not ledger.exists() or ledger.read_bytes() == b""
