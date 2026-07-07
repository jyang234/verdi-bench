"""``review`` stage API [refactor 02 §M4, §M6].

The importable entry points behind ``bench review build|record|reveal`` [EVAL-7]:
``review_build`` renders the offline blinded packet; ``review_record`` captures a
verdict + the two integrity questions strictly before any reveal; ``review_reveal``
unblinds only after a verdict exists. The ordering is enforced by the tool
(reveal refuses early), not by discipline. The typer verbs are thin shells that
map the refusals to exit codes and echo. (``review serve`` stays a server
entrypoint in the CLI.)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReviewBuildOutcome:
    """What ``bench review build`` wrote: the comparison count + packet path."""

    n_comparisons: int
    out_path: Path


def review_build(exp_dir, *, out=None, actor=None) -> ReviewBuildOutcome:
    """Sample + render the blinded review packet; record the Response↔arm map.

    Raises ``TaskCommitmentError`` (task swapped post-lock) / ``ActorResolutionError``
    (the CLI maps to exit 2), resolving the actor after the task commitment so the
    refusal order is unchanged."""
    from ..corpus.commit import assert_task_commitment, load_task_dicts
    from ..ledger.actor import resolve_actor
    from ..ledger.events import EventContext
    from ..ledger.identity import derive_experiment_id
    from ..plan.lock import assert_lock
    from .build import build_review

    exp_dir = Path(exp_dir)
    spec_path = exp_dir / "experiment.yaml"
    ledger_path = exp_dir / "ledger.ndjson"
    _lock = assert_lock(spec_path, ledger_path)
    lock_event, spec = _lock.event, _lock.spec  # PRA-M1: no second spec read
    task_dicts = load_task_dicts(exp_dir)
    assert_task_commitment(
        lock_event, task_dicts,
        corpus_id=spec.corpus.id, semver=spec.corpus.version,
    )

    # [ux-friction AC-1] one shared seam: resolve exp_dir before naming.
    ctx = EventContext(experiment_id=derive_experiment_id(exp_dir), actor=resolve_actor(actor))
    html, n = build_review(ledger_path, spec, task_dicts, ctx, seed=spec.seed)
    out_path = out or (exp_dir / "review_packet.html")
    out_path.write_text(html, encoding="utf-8")
    return ReviewBuildOutcome(n_comparisons=n, out_path=out_path)


def review_record(
    exp_dir, *, comparison_id: str, winner: str, reason: str = "",
    arm_recognized: bool = False, arm_guess=None, actor=None,
) -> None:
    """Record a human verdict + integrity answers (strictly pre-reveal).

    The human picks a **response** (1/2) as shown in the packet; the recorded
    winner is translated to the judge's A/B (arm) frame via the comparison's
    ``review_packet_built`` map, so the kappa join is frame-correct (RV-6/RV-9).
    Raises ``ActorResolutionError``/``ReviewError`` (invalid winner, missing
    packet, or sequencing) — all mapped to exit 2 by the CLI."""
    from ..judge.schema import Evidence, Verdict, VerdictProvenance, Winner
    from ..ledger.actor import resolve_actor
    from ..ledger.events import EventContext
    from ..ledger.identity import derive_experiment_id
    from ..schema.experiment import ExperimentSpec
    from .record import ReviewError, record_human_verdict, review_packet_built_for

    exp_dir = Path(exp_dir)
    ledger_path = exp_dir / "ledger.ndjson"
    # [ux-friction AC-1] one shared seam: resolve exp_dir before naming.
    ctx = EventContext(experiment_id=derive_experiment_id(exp_dir), actor=resolve_actor(actor))

    if winner not in ("1", "2", "TIE", "CANT_JUDGE"):
        raise ReviewError("--winner must be one of: 1 | 2 | TIE | CANT_JUDGE")

    built = review_packet_built_for(ledger_path, comparison_id)
    if built is None:
        raise ReviewError(
            f"comparison {comparison_id!r} has no review_packet_built event; "
            "run `review build` before recording a verdict [RV-6]"
        )
    response_map = built["response_map"]
    task_class = built.get("task_class")

    # Translate the response the human picked into the judge's A/B arm frame: A is
    # spec.arms[0]. actual_arm is the true arm shown as Response 1, which the
    # reviewer's guess is checked against for guess accuracy [RV-6].
    spec = ExperimentSpec.from_yaml(exp_dir / "experiment.yaml")
    arm_a_name = spec.arms[0].name
    evidence = []
    if winner in ("1", "2"):
        chosen_arm = response_map[winner]
        letter = "A" if chosen_arm == arm_a_name else "B"
        evidence = [Evidence(kind="diff", response=letter, hunk="reviewer-cited")]
    else:
        letter = winner
    actual_arm = response_map["1"]

    prov = VerdictProvenance(
        judge_model="human", rubric_sha256="human", packet_sha256="human",
        call_ids=["human"], orders="single", temperature=0.0, ts=ctx.clock(),
    )
    verdict = Verdict(
        winner=Winner(letter), reason=reason or winner, evidence=evidence,
        provenance=prov, source="human", comparison_id=comparison_id,
        task_class=task_class,
    )
    record_human_verdict(
        ledger_path, ctx, verdict=verdict, arm_recognized=arm_recognized,
        arm_guess=arm_guess, actual_arm=actual_arm,
    )


def review_reveal(exp_dir, *, comparison_id: str, actor=None) -> dict:
    """Unblind a comparison — refuses (``RevealError``, exit 2) before a verdict
    exists. Returns the revealed ``arm_identities`` map for the CLI to echo."""
    from ..ledger.actor import resolve_actor
    from ..ledger.events import EventContext
    from ..ledger.identity import derive_experiment_id
    from .record import reveal_comparison

    exp_dir = Path(exp_dir)
    ledger_path = exp_dir / "ledger.ndjson"
    # [ux-friction AC-1] one shared seam: resolve exp_dir before naming.
    ctx = EventContext(experiment_id=derive_experiment_id(exp_dir), actor=resolve_actor(actor))
    rec = reveal_comparison(ledger_path, ctx, comparison_id=comparison_id)
    return rec["revealed"]["arm_identities"]
