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

from ..errors import VerdiRefusal
from ..judge.schema import Verdict
from ..ledger import events
from ..ledger.events import EventContext
from ..ledger.query import assert_chain, read_events


class ReviewError(VerdiRefusal, RuntimeError):
    """A review operation was refused (duplicate/post-reveal/out-of-order) [RV-1/8]."""


class RevealError(ReviewError):
    """A reveal was attempted before its verdict+integrity was captured [AC-4]."""


# The review guards each need several predicates over the ledger. Rather than
# re-read/parse the whole file once per predicate (O(N²) on a batch), the public
# functions ``read_events`` **once** and pass the parsed list to these helpers,
# which filter it in memory [carry-forward: ledger-read consolidation]. Each
# helper still accepts ``evs=None`` so external callers can use it standalone.
def _of_type(ledger_path, event_type: str, evs) -> list[dict]:
    source = evs if evs is not None else read_events(ledger_path)
    return [e for e in source if e.get("event") == event_type]


def human_verdict_exists(ledger_path, comparison_id: str, *, evs=None) -> Optional[dict]:
    """Return the human_verdict event for ``comparison_id`` (with integrity), or None."""
    for ev in _of_type(ledger_path, events.HUMAN_VERDICT, evs):
        if ev["verdict"].get("comparison_id") == comparison_id and "integrity" in ev:
            return ev
    return None


def _any_human_verdict(ledger_path, comparison_id: str, *, evs=None) -> bool:
    """True if any human verdict (integrity or not) exists for ``comparison_id``."""
    return any(
        ev["verdict"].get("comparison_id") == comparison_id
        for ev in _of_type(ledger_path, events.HUMAN_VERDICT, evs)
    )


def _reveal_exists(ledger_path, comparison_id: str, *, evs=None) -> bool:
    return any(
        ev.get("verdict_event_id") == comparison_id
        for ev in _of_type(ledger_path, events.REVEAL, evs)
    )


def events_of_batch(ledger_path, *, evs=None) -> list[dict]:
    """All ``review_batch`` events in append order [F-M-O2]."""
    return _of_type(ledger_path, events.REVIEW_BATCH, evs)


def _batch_for(ledger_path, comparison_id: str, *, evs=None) -> Optional[dict]:
    """The LATEST review batch containing ``comparison_id``, or None (legacy
    chains / unbatched comparisons keep per-item semantics) [F-M-O2]."""
    found = None
    for ev in events_of_batch(ledger_path, evs=evs):
        if comparison_id in ev.get("comparison_ids", []):
            found = ev  # append order ⇒ last wins
    return found


def _judge_verdict_exists(ledger_path, comparison_id: str, *, evs=None) -> bool:
    """True if the judge produced a verdict for ``comparison_id`` — the review
    packet is built from judge verdicts, so a comparison a human can review must
    have one (a CANT_JUDGE verdict still counts)."""
    return any(
        ev["verdict"].get("comparison_id") == comparison_id
        for ev in _of_type(ledger_path, events.JUDGE_VERDICT, evs)
    )


def review_packet_built_for(ledger_path, comparison_id: str, *, evs=None) -> Optional[dict]:
    """The ``review_packet_built`` event for ``comparison_id`` (its recorded
    Response-1/2 ↔ arm map + task class), or None if the comparison was never
    built into a packet [D-P4-1]."""
    for ev in _of_type(ledger_path, events.REVIEW_PACKET_BUILT, evs):
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
    # cannot fool these gates [PL-6]. Read the parsed events ONCE and filter for
    # every predicate [carry-forward: ledger-read consolidation].
    assert_chain(ledger_path)
    evs = read_events(ledger_path)
    cid = verdict.comparison_id
    # RV-9: refuse a verdict for a comparison the judge never produced — a mistyped
    # comparison_id would otherwise record cleanly and silently drop from kappa
    # (which joins on judge↔human comparison_id).
    if not _judge_verdict_exists(ledger_path, cid, evs=evs):
        raise ReviewError(
            f"comparison {cid!r} has no judge verdict; a human verdict for a "
            "comparison that was never judged is a mistyped comparison_id [RV-9]"
        )
    # RV-1: a verdict recorded after the comparison is unblinded is no longer
    # blinded — refuse it rather than let an unblinded verdict poison kappa.
    if _reveal_exists(ledger_path, cid, evs=evs):
        raise ReviewError(
            f"comparison {cid!r} is already revealed; a post-reveal verdict is "
            "unblinded and cannot be recorded [RV-1]"
        )
    # RV-1: exactly one human verdict per comparison; a duplicate double-counts in
    # kappa and the integrity rate.
    if _any_human_verdict(ledger_path, cid, evs=evs):
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
    # unblinding, defeating capture-then-reveal [PL-6/AC-4]. Read once, filter for
    # every predicate [carry-forward: ledger-read consolidation].
    assert_chain(ledger_path)
    evs = read_events(ledger_path)
    # RV-8: a comparison is unblinded once; a duplicate reveal would append a
    # second (potentially divergent) disclosure. Refuse it.
    if _reveal_exists(ledger_path, comparison_id, evs=evs):
        raise RevealError(
            f"comparison {comparison_id!r} is already revealed; a duplicate reveal "
            "is refused [RV-8]"
        )
    hv = human_verdict_exists(ledger_path, comparison_id, evs=evs)
    if hv is None:
        raise RevealError(
            f"cannot reveal comparison {comparison_id!r}: no human verdict + "
            "integrity recorded yet; capture the verdict before unblinding [AC-4]"
        )
    # RV-2: the reveal discloses the *recorded* Response↔arm map, not a hardcoded
    # {"1":"arm_a","2":"arm_b"}. A comparison with no packet has no truthful map.
    built = review_packet_built_for(ledger_path, comparison_id, evs=evs)
    if built is None:
        raise RevealError(
            f"comparison {comparison_id!r} has no review_packet_built event; its "
            "Response↔arm map was never recorded, so it cannot be truthfully "
            "unblinded — run `review build` first [RV-2]"
        )
    # F-M-O2: capture-then-reveal is enforced per QUEUE — revealing any item
    # (arm identities + judge verdict) unblinds the reviewer for the rest of
    # the batch, so every batched comparison must carry its verdict first.
    batch = _batch_for(ledger_path, comparison_id, evs=evs)
    if batch is not None:
        missing = [
            c for c in batch["comparison_ids"]
            if human_verdict_exists(ledger_path, c, evs=evs) is None
        ]
        if missing:
            raise RevealError(
                f"cannot reveal comparison {comparison_id!r}: review batch "
                f"{batch['batch_id']} still has {len(missing)} un-verdicted "
                f"comparison(s) {missing} — a reveal unblinds the whole queue, "
                "so record every verdict in the batch first [F-M-O2]"
            )
    arm_identities = built["response_map"]
    # locate the advisory judge verdict for the same comparison (may be absent).
    # RV-9: last-wins on a duplicated ledger, matching both kappa joins
    # (sample.py) — first-wins here would disclose one verdict while kappa scored
    # another. With 7A-4 idempotency, duplicates can only be legacy.
    judge_id = None
    for ev in _of_type(ledger_path, events.JUDGE_VERDICT, evs):
        if ev["verdict"].get("comparison_id") == comparison_id:
            call_ids = ev["verdict"]["provenance"].get("call_ids") or ["judge"]
            judge_id = call_ids[0]
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


def _review_batch_entrypoint(ctx_dir: str) -> None:
    from pathlib import Path

    from ..ledger.events import EventContext

    events.record_review_batch(
        Path(ctx_dir) / "ledger.ndjson", EventContext(experiment_id="prop"),
        batch_id="prop-batch", comparison_ids=[_PROP_CID], seed=1,
    )


def _register() -> None:
    from ..entrypoints import register_entrypoint

    register_entrypoint("review-record", _review_record_entrypoint, prepare=_seed_judge_verdict)
    register_entrypoint("review-reveal", _review_reveal_entrypoint, prepare=_prepare_reveal)
    register_entrypoint("review-batch", _review_batch_entrypoint)


_register()
