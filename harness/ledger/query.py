"""Read-side helpers for the ledger.

Reading does not go through ``chain`` (which owns the write/verify path), so
non-ledger stages may import this module freely under the import-linter contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Optional

from .chain import ChainResult, canonical_line, hash_line, head_hash, verify_chain


def ledger_head_hash(path) -> str:
    """Read-side re-export: the current chain head hash.

    Non-ledger stages (e.g. EVAL-6 analyze) need the head hash and chain
    verdict for provenance, but the import-linter contract forbids them from
    importing ``ledger.chain`` directly. Reading is not writing, so the read
    helpers are surfaced here on the read-side module they are allowed to import.
    """
    return head_hash(Path(path))


def verify(path) -> ChainResult:
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


class ChainIntegrityError(RuntimeError):
    """The ledger's hash chain failed verification — refuse to trust its content.

    Raised by :func:`assert_chain`, which every stage that gates on ledger
    content calls before reading, so a rewritten/deleted/reordered line refuses
    the operation instead of being read as evidence [PL-6/CO-5]. This detects
    tampering of any line that has a successor; the unanchored head line is
    covered by the external chain anchor (``bench anchor``), not by this check —
    see ``chain.py``'s opacity boundary.
    """


def assert_chain(path) -> None:
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


def iter_events(path) -> Iterator[dict]:
    path = Path(path)
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            yield json.loads(line)


def read_events(path) -> list[dict]:
    return list(iter_events(path))


def find_events(path, event_type: str) -> list[dict]:
    return [e for e in iter_events(path) if e.get("event") == event_type]


def latest_event(path, event_type: str) -> Optional[dict]:
    found = find_events(path, event_type)
    return found[-1] if found else None
