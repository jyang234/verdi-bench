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


class RevealError(RuntimeError):
    """A reveal was attempted before its verdict+integrity was captured [AC-4]."""


def human_verdict_exists(ledger_path, comparison_id: str) -> Optional[dict]:
    """Return the human_verdict event for ``comparison_id`` (with integrity), or None."""
    for ev in find_events(ledger_path, events.HUMAN_VERDICT):
        if ev["verdict"].get("comparison_id") == comparison_id and "integrity" in ev:
            return ev
    return None


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
