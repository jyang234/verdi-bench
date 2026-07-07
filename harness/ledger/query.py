"""Read-side helpers for the ledger.

Reading does not go through ``chain`` (which owns the write/verify path), so
non-ledger stages may import this module freely under the import-linter contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Optional

from ..errors import VerdiRefusal
from .chain import (
    ChainResult,
    canonical_line,
    hash_line,
    head_hash,
    split_ledger_lines,
    verify_chain,
)


def ledger_head_hash(path: Path | str) -> str:
    """Read-side re-export: the current chain head hash.

    Non-ledger stages (e.g. EVAL-6 analyze) need the head hash and chain
    verdict for provenance, but the import-linter contract forbids them from
    importing ``ledger.chain`` directly. Reading is not writing, so the read
    helpers are surfaced here on the read-side module they are allowed to import.
    """
    return head_hash(Path(path))


def verify(path: Path | str) -> ChainResult:
    """Read-side re-export of :func:`harness.ledger.chain.verify_chain`."""
    return verify_chain(path)


def event_line_hash(event: dict) -> str:
    """The sha256 line hash of a ledgered ``event`` — its ledger-native id.

    Re-canonicalizes the parsed event (as :func:`iter_events` yields it,
    ``prev_hash`` included) and hashes it exactly as ``append_event`` hashed
    the line it wrote, so the value equals the chain's back-pointer to that
    line. Used to reference a specific prior event (e.g. the ``cant_grade`` a
    ``--retry-terminal`` grade overrides) without a separate id field [D-P7-2].
    """
    return hash_line(canonical_line(event))


class ChainIntegrityError(VerdiRefusal, RuntimeError):
    """The ledger's hash chain failed verification — refuse to trust its content.

    Raised by :func:`assert_chain`, which every stage that gates on ledger
    content calls before reading, so a rewritten/deleted/reordered line refuses
    the operation instead of being read as evidence [PL-6/CO-5]. This detects
    tampering of any line that has a successor; the unanchored head line is
    covered by the external chain anchor (``bench anchor``), not by this check —
    see ``chain.py``'s opacity boundary.
    """


def assert_chain(path: Path | str) -> None:
    """Fail closed unless the ledger's hash chain verifies.

    The chain is tamper-*evident* only if something actually consults it; the
    stage entrypoints (``assert_lock``, corpus admission) call this first so a
    hand-edited ledger cannot pass a downstream gate on unverified content.

    An absent or empty ledger is "nothing recorded yet", not tampering — there is
    no content to be fooled by, and the caller's own precondition check (no lock
    event, no curation approval, …) fails the operation closed. Deleting the
    ledger therefore cannot slip past a gate here; it fails at the precondition.
    """
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return
    result = verify(path)
    if not result:
        at = f" at line {result.line_number}" if result.line_number else ""
        raise ChainIntegrityError(
            f"ledger chain verification failed{at}: {result.detail}"
        )


def iter_events(path: Path | str) -> Iterator[dict]:
    path = Path(path)
    if not path.exists():
        return
    # Split on b"\n" ONLY, exactly as verify_chain does — never str.splitlines(),
    # which also breaks on U+0085/U+2028/U+2029. Those are legal, unescaped, inside
    # a JSON string, so a single legitimate event carrying one (an infra-failure
    # reason, a judge reason, agent output in a trial record) would otherwise be
    # torn into fragments here while verify_chain still reports a clean chain — a
    # reader/verifier split that bricks every downstream read gate [PRA-H1].
    for line in split_ledger_lines(path.read_bytes()):
        if line:
            yield json.loads(line)


def read_events(path: Path | str) -> list[dict]:
    return list(iter_events(path))


def find_events(path: Path | str, event_type: str) -> list[dict]:
    return [e for e in iter_events(path) if e.get("event") == event_type]


def latest_event(path: Path | str, event_type: str) -> Optional[dict]:
    found = find_events(path, event_type)
    return found[-1] if found else None


class TailOffsetError(ValueError):
    """The tail cursor points past the end of the ledger — the file shrank.

    ``append_event`` only ever grows the file, so a cursor beyond EOF means the
    ledger was rewritten or truncated underneath the observer. That is rewrite
    evidence, refused loudly — never silently treated as "no new events"
    [EVAL-13 AC-2]."""


def tail_events(path: Path | str, offset: int = 0) -> tuple[list[dict], int]:
    """Incremental read: complete events from byte ``offset``, plus the next cursor.

    Because ``append_event`` writes each event as one newline-terminated line in
    a single syscall, a byte-offset poller never sees a torn line — this cursor
    makes that contract explicit at the read seam [EVAL-13 AC-2]. Only lines
    ending in ``\\n`` are consumed; a partial tail (a foreign writer, a
    mid-crash artifact) is left for a later call and the returned offset does
    not advance past it. Malformed JSON in a *complete* line raises — a corrupt
    consumed line must fail loud, exactly as ``iter_events`` would.

    Returns ``(events, next_offset)``; an absent file is ``([], 0)`` (nothing
    recorded yet, not an error). Poll by passing each returned ``next_offset``
    back in; every appended event is yielded exactly once.
    """
    if offset < 0:
        raise TailOffsetError(f"tail offset must be >= 0, got {offset}")
    path = Path(path)
    if not path.exists():
        if offset:
            raise TailOffsetError(
                f"tail offset {offset} but ledger {path} is absent — the ledger "
                "shrank underneath the observer (rewrite evidence)"
            )
        return [], 0
    with open(path, "rb") as fh:
        fh.seek(0, 2)  # os.SEEK_END
        size = fh.tell()
        if offset > size:
            raise TailOffsetError(
                f"tail offset {offset} exceeds ledger size {size} ({path}) — the "
                "ledger shrank underneath the observer (rewrite evidence)"
            )
        fh.seek(offset)
        data = fh.read(size - offset)
    end = data.rfind(b"\n")
    if end == -1:
        return [], offset  # no complete line yet; leave the partial tail alone
    complete = data[: end + 1]
    # b"\n"-only split, parity with iter_events/verify_chain [PRA-H1].
    events = [
        json.loads(line) for line in split_ledger_lines(complete) if line
    ]
    return events, offset + len(complete)
