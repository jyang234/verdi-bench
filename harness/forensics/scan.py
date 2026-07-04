"""Forensics scan core + operator dispositions [EVAL-11 §M4, AC-6, D006, D007].

``run_forensics`` walks the ledger's trials, resolves each trajectory through
the EVAL-12 verifier (a record is never evidence unless its bytes matched the
chain), computes the vocabulary-v1 metrics, runs the gaming detectors over
assembled evidence, optionally runs the blinded advisory review, and appends
**exactly one** ``forensics_report`` event — partial coverage is disclosed in
the report with a per-trial reason, never silent [AC-6].

``quarantine_trial`` is the D007 operator path; it refuses a trial id the
ledger does not know, because a ledgered exclusion that silently matched
nothing would render as an exclusion that never happened.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..ledger.events import (
    EventContext,
    record_forensic_quarantine,
    record_forensics_report,
)
from .detectors import (
    TrialEvidence,
    detail_evaluable,
    extract_assertion_values,
    run_detectors,
)
from .metrics import FORENSICS_VOCABULARY_VERSION, trajectory_metrics

_SKIP_DIRS = {"artifacts", ".git", "__pycache__"}


class UnknownTrialError(ValueError):
    """A disposition named a trial the ledger has no record of [D007]."""


def _read_text_files(root: Optional[Path]) -> dict[str, str]:
    """Deterministic relpath→text mapping. Undecodable (binary) files are not
    text evidence and are skipped; a file that exists but cannot be READ is a
    loud OSError — swallowing it would silently drop detector evidence. A
    missing root (deleted workspace) is honest emptiness, not a crash."""
    if root is None or not root.is_dir():
        return {}
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if any(part in _SKIP_DIRS for part in rel.split("/")):
            continue
        try:
            out[rel] = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
    return out


def _resolve_holdouts_dir(experiment_dir: Path, holdouts_dir: str) -> Path:
    root = Path(holdouts_dir)
    return root if root.is_absolute() else experiment_dir / holdouts_dir


def _holdout_assertion_values(root: Path) -> tuple[str, ...]:
    values: list[str] = []
    for _, text in sorted(_read_text_files(root if root.is_dir() else None).items()):
        for v in extract_assertion_values(text):
            if v not in values:
                values.append(v)
    return tuple(values)


def _read_transcript(artifacts_path) -> str:
    """Post-redaction transcript, or "" if absent — the EVAL-9 reader's exact
    semantics (``errors="replace"``, absent ⇒ empty, a read error raises): an
    empty transcript fails closed to CANT_REVIEW(no_transcript) downstream,
    never a fabricated review."""
    if not artifacts_path:
        return ""
    p = Path(artifacts_path) / "transcript.txt"
    if not p.is_file():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def run_forensics(
    experiment_dir: Path,
    *,
    ctx: EventContext,
    review: bool = True,
    provider=None,
    provider_model: Optional[str] = None,
) -> dict:
    """The scan core: assemble → detect → (review) → one ledgered report."""
    from ..blind.core import arm_canaries
    from ..corpus.commit import load_task_dicts
    from ..ledger import events
    from ..ledger.query import find_events
    from ..plan.lock import assert_lock
    from ..run.trajectory import resolve_trajectory
    from .review import forensic_review

    experiment_dir = Path(experiment_dir)
    spec_path = experiment_dir / "experiment.yaml"
    ledger_path = experiment_dir / "ledger.ndjson"
    spec = assert_lock(spec_path, ledger_path).spec  # PRA-M1: no second spec read
    tasks = {t["id"]: t for t in load_task_dicts(experiment_dir)}
    canaries = arm_canaries(spec.arms)

    passed_by_trial: dict[str, bool] = {
        ev["trial_id"]: bool(ev["binary_score"])
        for ev in find_events(ledger_path, events.GRADE)
    }
    # assertion values depend only on the task's holdout dir — extract once
    # per distinct dir, not once per trial
    assertion_cache: dict[str, tuple[str, ...]] = {}

    metrics: dict[str, dict] = {}
    flags: list[dict] = []
    gaps: list[dict] = []
    reviews: dict[str, dict] = {}
    # EVAL-16 AC-5 (additive coverage keys, D002): where the step-content
    # detectors could and could not look, rolled up per arm — asymmetric
    # scrutiny is a disclosed measurement condition, never silent.
    detail_by_arm: dict[str, dict] = {}
    detail_gaps: list[dict] = []
    trial_events = find_events(ledger_path, events.TRIAL)
    for ev in trial_events:
        rec = ev["trial_record"]
        trial_id = rec["trial_id"]
        artifacts_path = rec.get("artifacts_path")
        status, record = resolve_trajectory(artifacts_path, ev.get("trajectory_sha"))
        if status == "verified":
            metrics[trial_id] = trajectory_metrics(record)
        else:
            # AC-6: partial coverage is data with the verifier's named reason
            gaps.append({"trial_id": trial_id, "reason": status})

        task = tasks.get(rec["task_id"], {})
        holdouts_dir = task.get("holdouts_dir") or ""
        holdout_root = (
            _resolve_holdouts_dir(experiment_dir, holdouts_dir) if holdouts_dir else None
        )
        if holdout_root is not None and holdouts_dir not in assertion_cache:
            assertion_cache[holdouts_dir] = _holdout_assertion_values(holdout_root)
        workspace_root = Path(artifacts_path).parent if artifacts_path else None
        evidence = TrialEvidence(
            trial_id=trial_id,
            task_id=rec["task_id"],
            arm=rec["arm"],
            trajectory=record,
            passed=passed_by_trial.get(trial_id),
            # resolved to a full path: detectors match by prefix, never by a
            # bare segment name a workspace dir could share
            holdout_paths=(holdout_root.as_posix(),) if holdout_root else (),
            workspace_files=_read_text_files(workspace_root),
            # tasks.yaml carries no pristine workspace content; detectors fall
            # back to trajectory-attributed edits and stay silent when neither
            # can attribute [plan §4.3]
            pristine_files={},
            holdout_assertion_values=assertion_cache.get(holdouts_dir, ()),
            # only a multi-segment relpath is content-matchable — a bare
            # segment like "tests" would flag every workspace test edit
            holdout_relpaths=(holdouts_dir,) if "/" in holdouts_dir else (),
        )
        flags.extend(run_detectors(evidence))

        # step-content detector coverage, per arm [EVAL-16 AC-5]
        bucket = detail_by_arm.setdefault(
            rec["arm"],
            {"trials": 0, "detail_evaluable": 0, "steps_total": 0, "steps_with_detail": 0},
        )
        bucket["trials"] += 1
        if record is not None:
            bucket["steps_total"] += len(record.steps)
            bucket["steps_with_detail"] += sum(
                1 for s in record.steps if s.detail is not None
            )
        if detail_evaluable(record):
            bucket["detail_evaluable"] += 1
        else:
            detail_gaps.append(
                {
                    "trial_id": trial_id,
                    "reason": "no_detail" if status == "verified" else status,
                }
            )

        if review:
            reviews[trial_id] = forensic_review(
                trial_id,
                _read_transcript(artifacts_path),
                canaries=canaries,
                provider=provider,
                provider_model=provider_model or spec.judge.model,
            ).model_dump(mode="json")

    report = {
        "vocabulary_version": FORENSICS_VOCABULARY_VERSION,
        "metrics": metrics,
        "flags": flags,
        "coverage": {
            "trials": len(trial_events),
            "covered": len(metrics),
            "gaps": gaps,
            # additive keys [EVAL-16 D002]: old readers ignore them, old
            # ledgers simply lack them, the report stays one event
            "detail_by_arm": {arm: detail_by_arm[arm] for arm in sorted(detail_by_arm)},
            "detail_gaps": detail_gaps,
        },
    }
    if review:
        report["reviews"] = reviews
    record_forensics_report(ledger_path, ctx, forensics_report=report)
    return report


def quarantine_trial(experiment_dir: Path, *, ctx: EventContext, trial_id: str, reason: str) -> dict:
    """Ledger the D007 operator disposition — refused for an unknown trial id.

    A quarantine that matches no trial would still render as '(excluded from
    comparisons)' while excluding nothing; validating here keeps the ledgered
    disclosure true [fail loudly]."""
    from ..ledger import events
    from ..ledger.query import find_events

    ledger_path = Path(experiment_dir) / "ledger.ndjson"
    known = {
        ev["trial_record"]["trial_id"] for ev in find_events(ledger_path, events.TRIAL)
    }
    if trial_id not in known:
        raise UnknownTrialError(
            f"cannot quarantine {trial_id!r}: no trial record with that id on "
            f"{ledger_path} ({len(known)} trial(s) known) — a quarantine that "
            "matches nothing would disclose an exclusion that never happened"
        )
    return record_forensic_quarantine(ledger_path, ctx, trial_id=trial_id, reason=reason)
