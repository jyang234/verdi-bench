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

import json
from dataclasses import dataclass
from pathlib import Path

from .chain import hash_line, verify_chain


def _ledger_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    data = path.read_bytes()
    if not data:
        return []
    parts = data.split(b"\n")
    if parts and parts[-1] == b"":
        parts = parts[:-1]
    return [p.decode("utf-8") for p in parts]


class AnchorIntegrityError(RuntimeError):
    """Refused to anchor a ledger whose own hash chain does not verify.

    Anchoring pins the current head as an authentic checkpoint; doing so over a
    rewritten/deleted/reordered ledger would launder tampered history into the
    anchor store. ``anchor_head`` verifies the chain first and raises this
    instead of writing anything [7A-2].
    """


def anchor_head(ledger_path, anchor_path, *, ts: str) -> dict:
    """Append ``{head_hash, height, ts}`` for the current ledger head to
    ``anchor_path``. ``ts`` is injected (no wall-clock reads here).

    Refuses (``AnchorIntegrityError``) before writing anything if the ledger's
    hash chain does not verify — an anchor must checkpoint authentic history.
    """
    ledger_path = Path(ledger_path)
    anchor_path = Path(anchor_path)
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
    record = {"head_hash": head, "height": height, "ts": ts}
    anchor_path.parent.mkdir(parents=True, exist_ok=True)
    with open(anchor_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    return record


@dataclass
class AnchorResult:
    ok: bool
    detail: str | None = None

    def __bool__(self) -> bool:
        return self.ok


def verify_against_anchor(ledger_path, anchor_path) -> AnchorResult:
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
    for raw in anchor_path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        rec = json.loads(raw)
        height, expected = rec["height"], rec["head_hash"]
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
    return AnchorResult(True, f"{checked} anchor(s) verified")
