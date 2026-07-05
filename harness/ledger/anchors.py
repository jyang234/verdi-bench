"""External head-hash anchoring [EVAL-3-D008, assumption: rec `anchor-plus-attestation-v1`].

An optional subsystem, on by default but cleanly severable if D008 resolves to
defer. ``anchor_head`` records the current chain head to an external location (a
sibling file outside the experiment dir for v1; the destination is a parameter
so "external" can later mean git-notes/remote). ``verify_against_anchor``
cross-checks that already-anchored history has not been rewritten — the property
a same-user rewrite (tamper-evident-only, D002) cannot forge without also
controlling the anchor store.
"""

from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass
from pathlib import Path

from .chain import hash_line, split_ledger_lines, verify_chain


def _ledger_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    data = path.read_bytes()
    if not data:
        return []
    return [p.decode("utf-8") for p in split_ledger_lines(data)]


class AnchorIntegrityError(RuntimeError):
    """Refused to anchor a ledger whose own hash chain does not verify.

    Anchoring pins the current head as an authentic checkpoint; doing so over a
    rewritten/deleted/reordered ledger would launder tampered history into the
    anchor store. ``anchor_head`` verifies the chain first and raises this
    instead of writing anything [7A-2].
    """


def anchor_record(ledger_path: Path | str, *, ts: str) -> dict:
    """Compute the ``{head_hash, height, ts}`` checkpoint for the current head —
    a pure read, no external write. ``ts`` is injected (no wall-clock here).

    Refuses (``AnchorIntegrityError``) if the ledger's hash chain does not verify;
    an anchor must checkpoint authentic history. Split out from the external write
    so a caller can order the two writes deliberately [PRA-L5].
    """
    ledger_path = Path(ledger_path)
    result = verify_chain(ledger_path)
    if not result.ok:
        at = f" at line {result.line_number}" if result.line_number else ""
        raise AnchorIntegrityError(
            f"ledger chain verification failed{at}: {result.detail} "
            "— refusing to anchor tampered history"
        )
    lines = _ledger_lines(ledger_path)
    height = len(lines)
    head = hash_line(lines[-1]) if lines else "0" * 64
    return {"head_hash": head, "height": height, "ts": ts}


def write_anchor(anchor_path: Path | str, record: dict) -> None:
    """Append one checkpoint ``record`` to the external anchor store.

    Exclusive-locked and fsync'd [F-M-O8], matching the ledger append
    discipline: the anchor store is the tamper-evidence backstop, so a torn or
    interleaved line from an unlocked buffered append would be a corruption in
    exactly the artifact meant to survive corruption elsewhere."""
    anchor_path = Path(anchor_path)
    anchor_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
    with open(anchor_path, "a", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def anchor_head(ledger_path: Path | str, anchor_path: Path | str, *, ts: str) -> dict:
    """Compute and externally record the current head checkpoint in one call.

    Convenience composition of :func:`anchor_record` + :func:`write_anchor`; the
    CLI drives them separately so the ``chain_anchor`` ledger event lands *before*
    the external file, leaving no un-ledgered external checkpoint on a crash
    between the two [PRA-L5].
    """
    record = anchor_record(ledger_path, ts=ts)
    write_anchor(anchor_path, record)
    return record


@dataclass
class AnchorResult:
    ok: bool
    detail: str | None = None

    def __bool__(self) -> bool:
        return self.ok


def verify_against_anchor(ledger_path: Path | str, anchor_path: Path | str) -> AnchorResult:
    """Every recorded anchor must still match the ledger line at its height."""
    ledger_path = Path(ledger_path)
    anchor_path = Path(anchor_path)
    if not anchor_path.exists():
        return AnchorResult(False, f"anchor store not found: {anchor_path}")
    # An anchor pins the head hash of a checkpoint; detecting a rewrite of
    # *earlier* anchored history requires the chain itself to still verify (a
    # rewritten interior line breaks its successor's back-pointer). Check both.
    chain_result = verify_chain(ledger_path)
    if not chain_result.ok:
        return AnchorResult(
            False,
            f"chain broken before anchor cross-check: {chain_result.detail} "
            "— anchored history was rewritten",
        )
    lines = _ledger_lines(ledger_path)
    checked = 0
    for lineno, raw in enumerate(anchor_path.read_text(encoding="utf-8").splitlines(), 1):
        raw = raw.strip()
        if not raw:
            continue
        # F-M-O8: a corrupt/torn anchor line is a VERDICT, never a crash — the
        # audit verb must always answer, and a store an attacker can corrupt
        # into a traceback is a store they can silence.
        try:
            rec = json.loads(raw)
            height, expected = rec["height"], rec["head_hash"]
        except (ValueError, TypeError, KeyError):
            return AnchorResult(
                False,
                f"anchor store corrupt at line {lineno}: not a well-formed "
                "anchor record — cross-check impossible",
            )
        if height == 0:
            continue
        if height > len(lines):
            return AnchorResult(
                False,
                f"anchor claims height {height} but ledger has {len(lines)} lines "
                "— anchored history was deleted/truncated",
            )
        actual = hash_line(lines[height - 1])
        if actual != expected:
            return AnchorResult(
                False,
                f"line {height} hash {actual} != anchored {expected} "
                "— anchored history was rewritten",
            )
        checked += 1
    if checked == 0:
        # F-M-O8: an existing-but-empty store previously returned ok=True with
        # "0 anchor(s) verified" — truncating the anchor file converted the
        # cross-check into a pass. Zero checked anchors is fail-closed.
        return AnchorResult(
            False,
            f"anchor store {anchor_path} exists but contains no checkable "
            "anchors — cross-check impossible (truncated store?)",
        )
    return AnchorResult(True, f"{checked} anchor(s) verified")


# --- one-event property coverage [PRA-L5] ----------------------------------
# `bench anchor` appends exactly one `chain_anchor` event (plus an external,
# non-ledger file). Register it so the AC-7 one-event sweep covers it like every
# other ledgering verb. It needs a non-empty, verifying ledger, so the prepare
# hook seeds a genesis anchor before the sweep snapshots the event count.
def _anchor_entrypoint(ctx_dir: str) -> None:
    from .events import EventContext, record_chain_anchor

    d = Path(ctx_dir)
    ledger, out = d / "ledger.ndjson", d / "anchors.ndjson"
    seq = iter(range(100000))
    ctx = EventContext(
        experiment_id=d.name, actor="sweep", clock=lambda: f"t{next(seq)}"
    )
    rec = anchor_record(ledger, ts=ctx.clock())
    record_chain_anchor(ledger, ctx, head_hash=rec["head_hash"], height=rec["height"])
    write_anchor(out, rec)


def _anchor_prepare(ctx_dir: str) -> None:
    from .events import EventContext, record_chain_anchor

    d = Path(ctx_dir)
    ctx = EventContext(experiment_id=d.name, actor="seed", clock=lambda: "t-seed")
    record_chain_anchor(d / "ledger.ndjson", ctx, head_hash="0" * 64, height=0)


def _register(*_args) -> None:  # tolerate register(app)-style callers
    from ..entrypoints import register_entrypoint

    register_entrypoint("anchor", _anchor_entrypoint, prepare=_anchor_prepare)


_register()
