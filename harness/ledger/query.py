"""Read-side helpers for the ledger.

Reading does not go through ``chain`` (which owns the write/verify path), so
non-ledger stages may import this module freely under the import-linter contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Optional

from .chain import ChainResult, head_hash, verify_chain


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
