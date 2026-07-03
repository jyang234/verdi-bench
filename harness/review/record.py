"""Capture-then-reveal state machine [EVAL-7 §M4, AC-4].

The ordering is enforced by the tool, not by discipline:

* ``record_human_verdict`` captures the verdict **and** the two integrity
  questions ("could you identify the arm? guess?") — strictly before any reveal.
* ``reveal_comparison`` discloses the judge verdict + arm identities as a
  ledgered ``reveal`` event, and **refuses** unless a human verdict already
  exists for that comparison. The reveal is the unlock EVAL-9's human process
  scoring keys off [EVAL-9 AC-3].

Only ``human_verdict`` events close comparisons [D004]; a ``reveal`` never does.
"""

from __future__ import annotations

from typing import Optional

from ..judge.schema import Verdict
from ..ledger import events
from ..ledger.events import EventContext
from ..ledger.query import assert_chain, find_events


class ReviewError(RuntimeError):
    """A review operation was refused (duplicate/post-reveal/out-of-order) [RV-1/8]."""


class RevealError(ReviewError):
    """A reveal was attempted before its verdict+integrity was captured [AC-4]."""


def human_verdict_exists(ledger_path, comparison_id: str) -> Optional[dict]:
    """Return the human_verdict event for ``comparison_id`` (with integrity), or None."""
    for ev in find_events(ledger_path, events.HUMAN_VERDICT):
        if ev["verdict"].get("comparison_id") == comparison_id and "integrity" in ev:
            return ev
    return None


def _any_human_verdict(ledger_path, comparison_id: str) -> bool:
    """True if any human verdict (integrity or not) exists for ``comparison_id``."""
    return any(
        ev["verdict"].get("comparison_id") == comparison_id
        for ev in find_events(ledger_path, events.HUMAN_VERDICT)
    )


def _reveal_exists(ledger_path, comparison_id: str) -> bool:
    return any(
        ev.get("verdict_event_id") == comparison_id
        for ev in find_events(ledger_path, events.REVEAL)
    )


def _judge_verdict_exists(ledger_path, comparison_id: str) -> bool:
    """True if the judge produced a verdict for ``comparison_id`` — the review
    packet is built from judge verdicts, so a comparison a human can review must
    have one (a CANT_JUDGE verdict still counts)."""
    return any(
        ev["verdict"].get("comparison_id") == comparison_id
        for ev in find_events(ledger_path, events.JUDGE_VERDICT)
    )


def record_human_verdict(
    ledger_path,
    ctx: EventContext,
    *,
    verdict: Verdict,
    arm_recognized: bool,
    arm_guess: Optional[str],
    actual_arm: Optional[str] = None,
) -> dict:
    """Capture a blinded human verdict + integrity answers (pre-reveal) [AC-4].

    The integrity questions are captured in the **same** event as the verdict, so
    a verdict can never be recorded without them through this path.
    """
    if verdict.source != "human":
        raise ValueError("record_human_verdict requires a human-sourced verdict")
    # RV-1: the capture side reads the ledger to enforce single-verdict,
    # pre-reveal ordering; verify the chain first so a forged reveal/verdict line
    # cannot fool these gates [PL-6].
    assert_chain(ledger_path)
    cid = verdict.comparison_id
    # RV-9: refuse a verdict for a comparison the judge never produced — a mistyped
    # comparison_id would otherwise record cleanly and silently drop from kappa
    # (which joins on judge↔human comparison_id).
    if not _judge_verdict_exists(ledger_path, cid):
        raise ReviewError(
            f"comparison {cid!r} has no judge verdict; a human verdict for a "
            "comparison that was never judged is a mistyped comparison_id [RV-9]"
        )
    # RV-1: a verdict recorded after the comparison is unblinded is no longer
    # blinded — refuse it rather than let an unblinded verdict poison kappa.
    if _reveal_exists(ledger_path, cid):
        raise ReviewError(
            f"comparison {cid!r} is already revealed; a post-reveal verdict is "
            "unblinded and cannot be recorded [RV-1]"
        )
    # RV-1: exactly one human verdict per comparison; a duplicate double-counts in
    # kappa and the integrity rate.
    if _any_human_verdict(ledger_path, cid):
        raise ReviewError(
            f"comparison {cid!r} already has a human verdict; a second verdict is "
            "refused (it would double-count in kappa/integrity) [RV-1]"
        )
    return events.append_human_verdict(
        ledger_path,
        ctx,
        verdict=verdict.model_dump(mode="json"),
        arm_recognized=arm_recognized,
        arm_guess=arm_guess,
        actual_arm=actual_arm,
    )


def reveal_comparison(
    ledger_path,
    ctx: EventContext,
    *,
    comparison_id: str,
    arm_identities: dict,
) -> dict:
    """Disclose judge verdict + arm identities — only after the human verdict [AC-4].

    Refuses if no verdict+integrity event exists for the comparison; the reveal
    references that human verdict and names the judge verdict it unblinds.
    """
    # The reveal gate reads the ledger to check a human verdict exists; verify
    # the chain first so a forged human_verdict cannot enable a premature
    # unblinding, defeating capture-then-reveal [PL-6/AC-4].
    assert_chain(ledger_path)
    # RV-8: a comparison is unblinded once; a duplicate reveal would append a
    # second (potentially divergent) disclosure. Refuse it.
    if _reveal_exists(ledger_path, comparison_id):
        raise RevealError(
            f"comparison {comparison_id!r} is already revealed; a duplicate reveal "
            "is refused [RV-8]"
        )
    hv = human_verdict_exists(ledger_path, comparison_id)
    if hv is None:
        raise RevealError(
            f"cannot reveal comparison {comparison_id!r}: no human verdict + "
            "integrity recorded yet; capture the verdict before unblinding [AC-4]"
        )
    # locate the advisory judge verdict for the same comparison (may be absent)
    judge_id = None
    for ev in find_events(ledger_path, events.JUDGE_VERDICT):
        if ev["verdict"].get("comparison_id") == comparison_id:
            judge_id = ev["verdict"]["provenance"].get("call_ids") or ["judge"]
            judge_id = judge_id[0]
            break
    return events.record_reveal(
        ledger_path,
        ctx,
        verdict_event_id=comparison_id,
        revealed={"judge_verdict_id": judge_id, "arm_identities": arm_identities},
    )
