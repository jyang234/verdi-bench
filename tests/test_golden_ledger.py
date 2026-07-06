"""Golden ledger + anchor serialization guards [refactor 01 §1 items 1, 3].

Pins the hash-chained ledger's canonical bytes and the anchor store's
deliberately-different serialization against the committed fixtures in
``tests/fixtures/data/``. The chain writer and verifier share one
``canonical_line``, so a serialization drift stays self-consistent — every
live test keeps passing while every pre-existing on-disk ledger becomes
unverifiable. These guards make that drift loud:

* ``verify_chain`` must pass on the committed bytes and the head hash must
  equal a pinned constant;
* every committed line must round-trip through ``canonical_line`` unchanged
  (the canonical-form check a monkeypatched drift visibly breaks);
* regenerating the entire scenario must reproduce every fixture byte —
  commit-independent by construction (pinned instrument identity);
* the anchor store must match recomputed anchor records byte-for-byte, pass
  ``verify_against_anchor``, and fail it on rewritten/truncated history.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.ledger.anchors import (
    anchor_record,
    verify_against_anchor,
    write_anchor,
)
from harness.ledger.chain import (
    GENESIS_PREV_HASH,
    canonical_line,
    hash_line,
    head_hash,
    split_ledger_lines,
    verify_chain,
)
from tests.fixtures import goldens

_DATA = Path(__file__).parent / "fixtures" / "data"
_LEDGER = _DATA / "golden_ledger.ndjson"
_ANCHORS = _DATA / "golden_anchor.ndjson"
_CONSTRUCTORS = _DATA / "golden_constructors.ndjson"


def _ledger_lines() -> list[bytes]:
    return split_ledger_lines(_LEDGER.read_bytes())


# --- item 1: committed chain verifies, head hash pinned ----------------------
def test_committed_golden_chain_verifies():
    result = verify_chain(_LEDGER)
    assert result.ok, result.detail


def test_committed_head_hash_equals_pinned_constant():
    assert head_hash(_LEDGER) == goldens.GOLDEN_HEAD_HASH


def test_committed_ledger_tells_the_complete_story():
    """lock → trials → grades → judge verdicts → findings_rendered, in order."""
    kinds = [json.loads(line)["event"] for line in _ledger_lines()]
    assert kinds[0] == "experiment_locked"
    for required in ("trial", "grade", "judge_verdict", "calibration_run", "selfcheck"):
        assert required in kinds
    assert kinds[-2:] == ["findings_rendered", "findings_rendered"]
    modes = [
        json.loads(line)["mode"]
        for line in _ledger_lines()
        if json.loads(line)["event"] == "findings_rendered"
    ]
    assert modes == ["exploratory", "official"]


def test_committed_lines_are_in_canonical_form():
    """Every committed line round-trips through ``canonical_line`` unchanged.

    This is the committed-file check a canonicalization drift breaks directly:
    under a drifted serialization the same parsed events no longer re-serialize
    to the committed bytes (see the drift tests below)."""
    for line in _ledger_lines():
        text = line.decode("utf-8")
        assert canonical_line(json.loads(text)) == text


def test_scenario_regenerates_byte_identical(tmp_path):
    """The master guard: rebuilding the whole scenario — lock, trials, grades,
    verdicts, selfcheck, renders, card, anchors — reproduces every committed
    fixture byte-for-byte, on any machine and any git HEAD [refactor 01 §1].
    """
    scenario = goldens.build_golden_scenario(tmp_path / "exp")
    assert scenario.selfcheck_passed
    assert scenario.official_refusal is None
    assert scenario.head_hash == goldens.GOLDEN_HEAD_HASH
    for name, produced in scenario.artifacts.items():
        committed = _DATA / name
        assert committed.exists(), f"missing committed fixture {name}"
        assert produced.read_bytes() == committed.read_bytes(), (
            f"{name} drifted from the committed golden — a serialization "
            "contract changed (see tests/fixtures/data/regen_goldens.py)"
        )


# --- item 1: drift sensitivity — prove the guard guards ----------------------
_DRIFTS = {
    "sort_keys_false": dict(separators=(",", ":"), ensure_ascii=False),
    "spaced_separators": dict(sort_keys=True, separators=(", ", ": "), ensure_ascii=False),
    "ensure_ascii_true": dict(sort_keys=True, separators=(",", ":"), ensure_ascii=True),
}


@pytest.mark.parametrize("drift", ["ensure_ascii_true", "spaced_separators"])
def test_drift_breaks_canonical_form_of_committed_file(drift, monkeypatch):
    """A canonicalization drift makes verification of the committed bytes fail:
    the committed lines stop being canonical under the drifted serializer.

    ``sort_keys=False`` is deliberately absent here: ``json.loads`` of a
    committed line yields keys in stored (already sorted) order, so a
    loads→re-serialize round-trip cannot see a sort-keys drift — that drift is
    caught by the replay-vs-committed-fixture guard below instead."""
    import harness.ledger.chain as chain

    kwargs = _DRIFTS[drift]
    monkeypatch.setattr(
        chain, "canonical_line", lambda obj: json.dumps(obj, allow_nan=False, **kwargs)
    )
    drifted = [
        line for line in _ledger_lines()
        if chain.canonical_line(json.loads(line.decode("utf-8"))) != line.decode("utf-8")
    ]
    assert drifted, f"drift {drift} was not detected against the committed file"


@pytest.mark.parametrize("drift", sorted(_DRIFTS))
def test_drift_breaks_replay_byte_equality_but_not_verify_chain(drift, monkeypatch, tmp_path):
    """Under a drifted writer the constructor replay stops matching the
    committed fixture — while ``verify_chain`` still passes on the drifted
    output, which is exactly why the byte-golden (not the verifier) is the
    guard: writer and verifier share one implementation and drift together."""
    import harness.ledger.chain as chain

    kwargs = _DRIFTS[drift]
    monkeypatch.setattr(
        chain, "canonical_line", lambda obj: json.dumps(obj, allow_nan=False, **kwargs)
    )
    replay = tmp_path / "drifted.ndjson"
    goldens.build_constructor_replay(replay)
    assert replay.read_bytes() != _CONSTRUCTORS.read_bytes()
    assert verify_chain(replay).ok  # self-consistent drift: verifier is blind to it


# --- item 1: tamper evidence on the committed bytes --------------------------
def test_interior_rewrite_breaks_the_chain(tmp_path):
    lines = _ledger_lines()
    idx = next(i for i, line in enumerate(lines) if b'"binary_score":true' in line)
    assert idx < len(lines) - 1  # interior: a successor exists to catch it
    lines[idx] = lines[idx].replace(b'"binary_score":true', b'"binary_score":false')
    tampered = tmp_path / "tampered.ndjson"
    tampered.write_bytes(b"\n".join(lines) + b"\n")
    result = verify_chain(tampered)
    assert not result.ok
    assert result.line_number == idx + 2  # the successor's prev_hash mismatches


def test_deleted_line_breaks_the_chain(tmp_path):
    lines = _ledger_lines()
    del lines[4]
    tampered = tmp_path / "deleted.ndjson"
    tampered.write_bytes(b"\n".join(lines) + b"\n")
    assert not verify_chain(tampered).ok


def test_reordered_lines_break_the_chain(tmp_path):
    lines = _ledger_lines()
    lines[2], lines[3] = lines[3], lines[2]
    tampered = tmp_path / "reordered.ndjson"
    tampered.write_bytes(b"\n".join(lines) + b"\n")
    assert not verify_chain(tampered).ok


def test_truncated_final_newline_is_detected(tmp_path):
    tampered = tmp_path / "truncated.ndjson"
    tampered.write_bytes(_LEDGER.read_bytes()[:-1])
    result = verify_chain(tampered)
    assert not result.ok
    assert "truncated" in (result.detail or "")


def test_last_line_rewrite_passes_verify_but_fails_the_head_pin(tmp_path):
    """No successor re-hashes the final line, so ``verify_chain`` alone cannot
    see a head rewrite — the pinned head-hash constant is what catches it."""
    lines = _ledger_lines()
    lines[-1] = lines[-1].replace(b'"mode":"official"', b'"mode":"exploratory"')
    tampered = tmp_path / "head-rewrite.ndjson"
    tampered.write_bytes(b"\n".join(lines) + b"\n")
    assert verify_chain(tampered).ok
    assert head_hash(tampered) != goldens.GOLDEN_HEAD_HASH


# --- item 3: anchor-record bytes + verify_against_anchor pass/fail -----------
def test_committed_anchor_store_matches_recomputed_records(tmp_path):
    """Both committed anchor records byte-match ``anchor_record`` +
    ``write_anchor`` recomputed over the committed ledger (and its lock-only
    prefix), pinning the anchor's own serialization — ``ensure_ascii`` default,
    sorted keys, tight separators [harness/ledger/anchors.py:73]."""
    lines = _ledger_lines()
    lock_prefix = tmp_path / "prefix1.ndjson"
    lock_prefix.write_bytes(lines[0] + b"\n")
    store = tmp_path / "anchors.ndjson"
    write_anchor(store, anchor_record(lock_prefix, ts=goldens.GOLDEN_ANCHOR_TS_LOCK))
    write_anchor(store, anchor_record(_LEDGER, ts=goldens.GOLDEN_ANCHOR_TS_HEAD))
    assert store.read_bytes() == _ANCHORS.read_bytes()

    head_rec = anchor_record(_LEDGER, ts=goldens.GOLDEN_ANCHOR_TS_HEAD)
    assert head_rec["head_hash"] == goldens.GOLDEN_HEAD_HASH
    assert head_rec["height"] == len(lines)


def test_anchor_serialization_is_ascii_escaped(tmp_path):
    """Anchors deliberately serialize with ``ensure_ascii=True`` (the json
    default), unlike the chain's ``ensure_ascii=False`` — pinned here with a
    synthetic non-ASCII record so a 'harmonizing' change is byte-visible."""
    store = tmp_path / "ascii.ndjson"
    write_anchor(store, {"ts": "2026-Δ", "height": 3, "head_hash": "ab" * 32})
    expected = (
        b'{"head_hash":"' + b"ab" * 32 + b'","height":3,"ts":"2026-\\u0394"}\n'
    )
    assert store.read_bytes() == expected


def test_verify_against_anchor_passes_on_committed_pair():
    result = verify_against_anchor(_LEDGER, _ANCHORS)
    assert result.ok, result.detail
    assert "2 anchor(s) verified" in (result.detail or "")


def test_verify_against_anchor_fails_on_truncated_history(tmp_path):
    lines = _ledger_lines()
    truncated = tmp_path / "truncated.ndjson"
    truncated.write_bytes(b"\n".join(lines[:-1]) + b"\n")
    result = verify_against_anchor(truncated, _ANCHORS)
    assert not result.ok
    assert "deleted/truncated" in (result.detail or "")


def test_verify_against_anchor_fails_on_rechained_rewrite(tmp_path):
    """A same-user rewrite that recomputes the whole chain defeats
    ``verify_chain`` (tamper-evident, not tamper-proof [D002]) — the external
    anchor is what catches it. This is the anchor 'fail' half of the pair."""
    objs = [json.loads(line) for line in _ledger_lines()]
    grade_idx = next(
        i for i, o in enumerate(objs)
        if o["event"] == "grade" and o["binary_score"] is False
    )
    objs[grade_idx]["binary_score"] = True  # flip a failing grade to passing
    prev = GENESIS_PREV_HASH
    rebuilt: list[str] = []
    for obj in objs:
        obj["prev_hash"] = prev
        line = canonical_line(obj)
        rebuilt.append(line)
        prev = hash_line(line)
    rewritten = tmp_path / "rewritten.ndjson"
    rewritten.write_text("\n".join(rebuilt) + "\n", encoding="utf-8")

    assert verify_chain(rewritten).ok  # the chain alone cannot see this
    result = verify_against_anchor(rewritten, _ANCHORS)
    assert not result.ok
    assert "rewritten" in (result.detail or "")
