"""Workspace scan for the home screen [EVAL-14 AC-1, D003].

One level below the root, any directory containing a ``ledger.ndjson`` is an
experiment; everything else is silently not one (a scan, not a registry —
zero configuration). Each hit is summarized through the same status seam
every other observer read uses, so a home row can never disagree with the
experiment's own screen: a broken chain yields a withheld row (``chain.ok``
false, summary ``None``), never zeros.
"""

from __future__ import annotations

from pathlib import Path

from ..status.aggregate import compute_status


def _summary_row(name: str, snap: dict) -> dict:
    """Flatten one status snapshot into the home-row shape."""
    st = snap.get("stages")
    hb = snap.get("heartbeat") or {}
    row = {
        "name": name,
        "chain": snap["chain"],
        "heartbeat_state": hb.get("state"),
        "in_flight": hb.get("in_flight"),
        "summary": None,  # withheld unless the chain verified [fail closed]
    }
    if st is not None:
        row["summary"] = {
            "locked": st["lock"]["locked"],
            "cells": st["cells"],
            "spend": st["spend"],
            "grade": st["grade"],
            "judge": st["judge"],
            "selfcheck": st["analyze"]["selfcheck"],
            "quarantines": len(st["quarantines"]),
            "last_event_ts": st["last_event_ts"],
        }
    return row


def scan_workspace(root) -> list[dict]:
    """Summaries for every experiment directory one level under ``root``,
    sorted by name (deterministic). A root that is not a directory is an
    error the caller surfaces; an empty scan is an honest empty list."""
    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(f"workspace root {root} is not a directory")
    rows: list[dict] = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "ledger.ndjson").exists():
            rows.append(_summary_row(child.name, compute_status(child)))
    return rows
