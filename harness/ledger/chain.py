"""Hash-chained ndjson ledger [EVAL-3 AC-3, AC-7, D002, D003].

Canonicalization — pinned, everything depends on it:

* Serialize each event with
  ``json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)``.
* One event per line, ``\\n``-terminated.
* ``prev_hash`` = sha256 hex of the **previous line's exact bytes, excluding the
  trailing newline**. Genesis uses ``prev_hash = "0" * 64``.

``append_event`` and ``verify_chain`` share :func:`canonical_line` and
:func:`hash_line` so the two code paths cannot drift.

**Opacity boundary [D002].** This is tamper-**evident**, not tamper-proof. A
same-user writer can rewrite the file and recompute the chain; ``verify_chain``
detects rewrites/deletions/reorders against an *unmodified* file or an external
anchor. Dedicated-UID ownership is deferred to the TRUSTED tier — do not read
this module as providing it.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

GENESIS_PREV_HASH = "0" * 64


class TruncatedLedgerError(RuntimeError):
    """The ledger's final line is unterminated — appending would concatenate.

    A well-formed ledger ends every line, including the last, in ``\\n``. A
    file whose final byte is not a newline is truncated (an interrupted write,
    a hand edit). Appending onto it would splice the new event onto the tail of
    the partial line, silently corrupting both. ``append_event`` refuses
    instead, so the truncation is repaired deliberately rather than buried
    [PL-13].
    """


def canonical_line(obj: dict) -> str:
    """The canonical single-line JSON serialization (no trailing newline)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_line(line: str) -> str:
    """sha256 hex of a canonical line's exact bytes (newline excluded)."""
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def _last_line(path: Path) -> Optional[str]:
    """Return the last event line (without newline), or None for empty/absent.

    Reads only the tail of the file (a bounded seek-from-end), not the whole
    ledger — so appending to an N-line ledger stays O(1) instead of O(N), and a
    full run of many trials avoids O(N^2) total reads through this hot path.
    """
    if not path.exists():
        return None
    chunk = 4096
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        if size == 0:
            return None
        data = b""
        pos = size
        while pos > 0:
            step = min(chunk, pos)
            pos -= step
            fh.seek(pos)
            data = fh.read(step) + data
            # a well-formed ledger ends in \n; strip a single trailing newline
            # then find the newline that precedes the final line
            stripped = data[:-1] if data.endswith(b"\n") else data
            idx = stripped.rfind(b"\n")
            if idx != -1:
                return stripped[idx + 1 :].decode("utf-8")
            if pos == 0:
                return stripped.decode("utf-8") if stripped else None
    return None


def _refuse_truncated_final_line(path: Path) -> None:
    """Raise :class:`TruncatedLedgerError` if the file's last byte is not ``\\n``.

    Called under the append lock before any hashing. A non-empty ledger whose
    final byte is a newline is well-formed and returns silently — the common
    path stays O(1). Only the (rare) truncated case reads the whole file, to
    name the offending line count in the refusal.
    """
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        if size == 0:
            return  # empty ledger: genesis append, nothing to concatenate onto
        fh.seek(size - 1)
        if fh.read(1) == b"\n":
            return
        fh.seek(0)
        n_lines = fh.read().count(b"\n") + 1  # unterminated tail is a line too
    raise TruncatedLedgerError(
        f"ledger {path} ends without a trailing newline; its final line "
        f"(line {n_lines}) is truncated — refusing to append onto a partial "
        "line. Repair or truncate the ledger deliberately first."
    )


def head_hash(path: Path) -> str:
    """Hash of the current last line — the value a new event's prev_hash takes."""
    last = _last_line(path)
    return GENESIS_PREV_HASH if last is None else hash_line(last)


def append_event(
    path: str | Path,
    event: dict,
    *,
    writer: Callable[[int, bytes], int] = os.write,
) -> dict:
    """Append ``event`` to the ledger, chaining it to the current head.

    The whole line is written in a single ``os.write`` under an exclusive
    ``flock`` (serializing same-host writers — sufficient for tamper-evident v1
    per D002). Any exception propagates and, because the write is one syscall of
    the entire line, no partial line survives normal failure paths [AC-7].

    ``writer`` is injectable for fault-injection tests only.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    stored = dict(event)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        _refuse_truncated_final_line(path)
        # compute prev_hash under the lock so concurrent appends can't race
        stored["prev_hash"] = head_hash(path)
        line = (canonical_line(stored) + "\n").encode("utf-8")
        written = writer(fd, line)
        if written != len(line):  # pragma: no cover - defensive; single write
            raise OSError(
                f"short write to ledger ({written} of {len(line)} bytes)"
            )
        os.fsync(fd)
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
    return stored


@dataclass
class ChainResult:
    ok: bool
    line_number: Optional[int] = None  # 1-indexed first broken link
    detail: Optional[str] = None

    def __bool__(self) -> bool:
        return self.ok


def verify_chain(path: str | Path) -> ChainResult:
    """Walk the ledger, recompute each link, report the first broken one.

    Distinguishes, where determinable: a rewritten/tampered line (its successor's
    prev_hash won't match), a bad genesis prev_hash, and malformed json. A clean
    file returns ``ok=True``.
    """
    path = Path(path)
    if not path.exists():
        return ChainResult(False, None, f"ledger not found: {path}")

    data = path.read_bytes()
    if not data:
        return ChainResult(True)  # empty ledger is trivially consistent

    raw_lines = data.split(b"\n")
    if raw_lines and raw_lines[-1] == b"":
        raw_lines = raw_lines[:-1]
    else:
        # file does not end in a newline: last line is truncated/partial
        return ChainResult(
            False,
            len(raw_lines),
            "ledger does not end in a newline; final line is truncated",
        )

    prev = GENESIS_PREV_HASH
    for i, raw in enumerate(raw_lines, start=1):
        try:
            line = raw.decode("utf-8")
            obj = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            return ChainResult(False, i, f"line {i} is not valid JSON: {e}")
        recorded = obj.get("prev_hash")
        if recorded != prev:
            hint = (
                "genesis prev_hash must be all zeros"
                if i == 1
                else "prev_hash does not match the previous line's hash — a line "
                "was rewritten, deleted, or reordered at or before here"
            )
            return ChainResult(
                False,
                i,
                f"broken link at line {i}: recorded prev_hash={recorded} "
                f"computed={prev} ({hint})",
            )
        prev = hash_line(line)

    return ChainResult(True)
