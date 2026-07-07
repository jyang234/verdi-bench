"""Ledger read/anchor stage API [refactor 02 §3, 06 §1].

The importable entry points behind ``bench verify-chain`` and ``bench anchor``
[EVAL-3]. Both stay on the ledger's public seams: verification reads through
``ledger.query.verify`` / ``ledger.anchors`` (never ``ledger.chain`` directly),
and anchoring appends its ``chain_anchor`` event only through the typed
constructor ``record_chain_anchor``. The typer verbs are thin shells that map the
verdicts to exit codes and echo.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ChainVerdict:
    """The chain (and optional anchor) verification result the CLI renders."""

    chain_ok: bool
    chain_detail: str
    anchor_checked: bool = False
    anchor_ok: bool | None = None
    anchor_detail: str | None = None


@dataclass(frozen=True)
class AnchorOutcome:
    """A recorded anchor: the chain head hash + height that were checkpointed."""

    head_hash: str
    height: int


def verify_chain(ledger, *, against_anchor=None) -> ChainVerdict:
    """Verify the hash chain, and optionally cross-check an external anchor.

    Read-only. The anchor is checked **only** when the chain itself verifies (a
    broken chain short-circuits, exactly as the verb did)."""
    from .query import verify  # read-side seam; never import ledger.chain directly

    result = verify(ledger)
    if not result.ok:
        return ChainVerdict(chain_ok=False, chain_detail=result.detail)
    if against_anchor is None:
        return ChainVerdict(chain_ok=True, chain_detail=result.detail)
    from .anchors import verify_against_anchor

    ar = verify_against_anchor(ledger, against_anchor)
    return ChainVerdict(
        chain_ok=True, chain_detail=result.detail,
        anchor_checked=True, anchor_ok=ar.ok, anchor_detail=ar.detail,
    )


def anchor(ledger, *, out, actor=None) -> AnchorOutcome:
    """Record the current chain head to an external anchor store [D008, PL-4].

    Also appends a ``chain_anchor`` event so the act of anchoring is itself an
    auditable, chained record. Raises ``ActorResolutionError`` (exit 2) and
    ``AnchorIntegrityError`` (tampered history, exit 1); the ledger append lands
    *before* the external file write so a crash between leaves an in-chain record,
    never an orphaned checkpoint [PRA-L5]."""
    from .actor import resolve_actor
    from .anchors import anchor_record, write_anchor
    from .events import EventContext, record_chain_anchor
    from .identity import derive_experiment_id

    ledger = Path(ledger)
    # Route the timestamp through the EventContext clock seam rather than a bare
    # wall-clock read [PL-4 / determinism].
    # [ux-friction AC-1] one shared seam: resolve the ledger's parent directory
    # before naming, so `bench anchor ledger.ndjson` from inside the dir stamps
    # the experiment's real name rather than '' (unresolved `.parent` of a bare
    # relative ledger path is `.`).
    ctx = EventContext(
        experiment_id=derive_experiment_id(ledger.parent), actor=resolve_actor(actor)
    )
    # 7A-2: anchor_record chain-verifies first and refuses tampered history before
    # writing anything.
    rec = anchor_record(ledger, ts=ctx.clock())
    # Ledger the anchoring FIRST (durable, fsync'd), then write the external
    # checkpoint [PRA-L5].
    record_chain_anchor(ledger, ctx, head_hash=rec["head_hash"], height=rec["height"])
    write_anchor(out, rec)
    return AnchorOutcome(head_hash=rec["head_hash"], height=rec["height"])
