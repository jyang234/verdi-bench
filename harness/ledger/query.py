"""Read-side helpers for the ledger.

Reading does not go through ``chain`` (which owns the write/verify path), so
non-ledger stages may import this module freely under the import-linter contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Optional


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
