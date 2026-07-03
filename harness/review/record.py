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


def review_packet_built_for(ledger_path, comparison_id: str) -> Optional[dict]:
    """The ``review_packet_built`` event for ``comparison_id`` (its recorded
    Response-1/2 ↔ arm map + task class), or None if the comparison was never
    built into a packet [D-P4-1]."""
    for ev in find_events(ledger_path, events.REVIEW_PACKET_BUILT):
        if ev.get("comparison_id") == comparison_id:
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
) -> dict:
    """Disclose judge verdict + **real** arm identities — only after the human
    verdict [AC-4, RV-2].

    The arm identities are read from the comparison's recorded
    ``review_packet_built`` map, not fabricated by the caller — so the ledgered
    unblinding is the truth the human was shown, not a hardcoded convention.
    Refuses if no human verdict + integrity exists, or if the comparison was
    never built into a packet (no map to disclose).
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
    # RV-2: the reveal discloses the *recorded* Response↔arm map, not a hardcoded
    # {"1":"arm_a","2":"arm_b"}. A comparison with no packet has no truthful map.
    built = review_packet_built_for(ledger_path, comparison_id)
    if built is None:
        raise RevealError(
            f"comparison {comparison_id!r} has no review_packet_built event; its "
            "Response↔arm map was never recorded, so it cannot be truthfully "
            "unblinded — run `review build` first [RV-2]"
        )
    arm_identities = built["response_map"]
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


# --- one-event property registration [EVAL-3 §M7, XC-3] --------------------
_PROP_CID = "cmp-prop"


def _seed_judge_verdict(ctx_dir: str) -> None:
    from pathlib import Path

    from ..judge.schema import Evidence, Verdict, VerdictProvenance, Winner
    from ..ledger.events import EventContext

    d = Path(ctx_dir)
    jv = Verdict(
        winner=Winner.A, reason="x",
        evidence=[Evidence(kind="diff", response="A", hunk="h")],
        provenance=VerdictProvenance(
            judge_model="m", rubric_sha256="a", packet_sha256="b",
            call_ids=["c1", "c2"], orders="both", temperature=0.0, ts="t"),
        comparison_id=_PROP_CID, task_class="cls",
    )
    events.append_verdict(d / "ledger.ndjson", EventContext(experiment_id="prop"),
                          verdict=jv.model_dump(mode="json"))


def _human_verdict(cid: str) -> Verdict:
    from ..judge.schema import Evidence, Verdict, VerdictProvenance, Winner

    return Verdict(
        winner=Winner.A, reason="r",
        evidence=[Evidence(kind="diff", response="A", hunk="h")],
        provenance=VerdictProvenance(
            judge_model="human", rubric_sha256="human", packet_sha256="human",
            call_ids=["human"], orders="single", temperature=0.0, ts="t"),
        source="human", comparison_id=cid, task_class="cls",
    )


def _review_record_entrypoint(ctx_dir: str) -> None:
    from pathlib import Path

    from ..ledger.events import EventContext

    d = Path(ctx_dir)
    record_human_verdict(
        d / "ledger.ndjson", EventContext(experiment_id="prop"),
        verdict=_human_verdict(_PROP_CID), arm_recognized=False, arm_guess=None,
    )


def _prepare_reveal(ctx_dir: str) -> None:
    # a reveal presupposes a judge verdict, a human verdict, and the recorded
    # Response↔arm map — the events its gates read before disclosing.
    from pathlib import Path

    from ..ledger.events import EventContext

    _seed_judge_verdict(ctx_dir)
    _review_record_entrypoint(ctx_dir)
    events.record_review_packet_built(
        Path(ctx_dir) / "ledger.ndjson", EventContext(experiment_id="prop"),
        comparison_id=_PROP_CID, task_id="t", task_class="cls",
        response_map={"1": "arm_a", "2": "arm_b"}, seed=1,
    )


def _review_reveal_entrypoint(ctx_dir: str) -> None:
    from pathlib import Path

    from ..ledger.events import EventContext

    d = Path(ctx_dir)
    reveal_comparison(
        d / "ledger.ndjson", EventContext(experiment_id="prop"),
        comparison_id=_PROP_CID,
    )


def _register() -> None:
    from ..entrypoints import register_entrypoint

    register_entrypoint("review-record", _review_record_entrypoint, prepare=_seed_judge_verdict)
    register_entrypoint("review-reveal", _review_reveal_entrypoint, prepare=_prepare_reveal)


_register()
