"""Ledger+disk → :class:`TrialEvidence` + coverage notes [refactor 06 §5].

The deterministic first phase of ``run_forensics``. ``TrialEvidenceAssembler``
walks one :class:`~harness.ledger.view.LedgerView`'s trials and, for each,
resolves the trajectory through the EVAL-12 verifier (a record is never evidence
unless its bytes matched the chain), verifies the end-state workspace against the
grade-time commitment (F-H3), extracts holdout assertion values (cached per
distinct dir), and assembles the frozen :class:`TrialEvidence` the detectors read
— rolling up per-arm step-content coverage and every kind of coverage gap as it
goes. Partial coverage is disclosed with a per-trial reason, never silent [AC-6].

This module is deterministic by construction: it reads and hashes persisted
artifacts, never a provider. It imports **no LLM client** [EVAL-11 AC-3,
import-linter contract] — the flight-recorder *splicing* it performs only
prepares the transcript the advisory pass (the sole provider-touching phase)
will feed downstream; the review byte-budget is decided here [EVAL-24-D003] but
no completion is requested here.

``TrialEvidence``'s frozen-dataclass allowlist signature is a capability boundary
[refactor 06 §3]: this assembler fills exactly the fields the detectors are
allowed to see and widens nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..ledger.view import LedgerView
from ..run.artifacts import read_transcript
from ..run.flight_recorder import DEFAULT_REASONING_BUDGET_BYTES, resolve_flight_recorder
from ..run.trajectory import resolve_trajectory
from ..run.workspace import ABSENT, VERIFIED, resolve_workspace
from .detectors import TrialEvidence, detail_evaluable, extract_assertion_values
from .metrics import trajectory_metrics

_SKIP_DIRS = {"artifacts", ".git", "__pycache__"}


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


@dataclass(frozen=True)
class AssembledTrial:
    """One trial's deterministic evidence plus the (review-only) spliced
    transcript + byte budget the advisory pass will feed to the provider. When
    the scan runs ``review=False`` the transcript is empty and unused — no
    artifact is read for a review that will not happen."""

    evidence: TrialEvidence
    review_transcript: str
    max_reasoning_bytes: Optional[int]

    @property
    def trial_id(self) -> str:
        return self.evidence.trial_id


@dataclass(frozen=True)
class AssembledEvidence:
    """The whole scan's assembled evidence and coverage bookkeeping — everything
    the detector/advisory passes and the report builder need, and nothing a
    provider touched. The lists are in ledger (trial) order so the emitted report
    is deterministic [refactor 06 §9]."""

    trials: list[AssembledTrial]
    metrics: dict[str, dict]
    gaps: list[dict]
    detail_by_arm: dict[str, dict]
    detail_gaps: list[dict]
    workspace_gaps: list[dict]

    @property
    def n_trials(self) -> int:
        return len(self.trials)


class TrialEvidenceAssembler:
    """Assemble every trial's evidence + coverage from a :class:`LedgerView` and
    the on-disk artifacts [refactor 06 §5].

    Owns grade indexing, holdout extraction+caching, trajectory resolution,
    workspace verification, evidence assembly, per-arm coverage bookkeeping, and
    flight-recorder splicing. ``review`` gates only the splicing: the evidence
    and coverage are identical either way, so a ``--no-review`` scan does exactly
    the deterministic work and no more.
    """

    def __init__(
        self, view: LedgerView, experiment_dir: Path, tasks: dict, *, review: bool
    ) -> None:
        self._view = view
        self._experiment_dir = Path(experiment_dir)
        self._tasks = tasks
        self._review = review

    def assemble(self) -> AssembledEvidence:
        # Latest grade wins per trial — one GRADE scan feeds both the pass map and
        # the F-H3 grade-time workspace commitment (legacy grades lack the field →
        # None → ABSENT below), replacing the former double iteration [refactor 06 §1].
        latest_grades = self._view.latest_grade_by_trial()
        passed_by_trial: dict[str, bool] = {
            trial_id: bool(g["binary_score"]) for trial_id, g in latest_grades.items()
        }
        workspace_sha_by_trial: dict[str, Optional[str]] = {
            trial_id: g.get("workspace_sha256") for trial_id, g in latest_grades.items()
        }
        # assertion values depend only on the task's holdout dir — extract once
        # per distinct dir, not once per trial
        assertion_cache: dict[str, tuple[str, ...]] = {}

        metrics: dict[str, dict] = {}
        gaps: list[dict] = []
        # EVAL-16 AC-5 (additive coverage keys, D002): where the step-content
        # detectors could and could not look, rolled up per arm — asymmetric
        # scrutiny is a disclosed measurement condition, never silent.
        detail_by_arm: dict[str, dict] = {}
        detail_gaps: list[dict] = []
        # F-H3 (additive): trials whose end-state evidence could not be verified
        # against the grade-time workspace commitment.
        workspace_gaps: list[dict] = []
        assembled: list[AssembledTrial] = []

        for tv in self._view.trials():
            rec = tv.record
            trial_id = rec["trial_id"]
            artifacts_path = rec.get("artifacts_path")
            status, record = resolve_trajectory(artifacts_path, tv.trajectory_sha)
            if status == "verified":
                metrics[trial_id] = trajectory_metrics(record)
            else:
                # AC-6: partial coverage is data with the verifier's named reason
                gaps.append({"trial_id": trial_id, "reason": status})

            task = self._tasks.get(rec["task_id"], {})
            holdouts_dir = task.get("holdouts_dir") or ""
            holdout_root = (
                _resolve_holdouts_dir(self._experiment_dir, holdouts_dir)
                if holdouts_dir
                else None
            )
            if holdout_root is not None and holdouts_dir not in assertion_cache:
                assertion_cache[holdouts_dir] = _holdout_assertion_values(holdout_root)
            workspace_root = Path(artifacts_path).parent if artifacts_path else None
            # F-H3: end-state evidence is chain-verified before it is trusted.
            # verified → read, full authority. ABSENT (legacy chain / fabricated
            # grade) → read for legacy utility, but disclosed as an unverified-
            # evidence gap. Any mismatch/missing → WITHHELD: tampered bytes must
            # neither produce flags (framing) nor clean claims (laundering).
            ws_status = resolve_workspace(
                workspace_root,
                workspace_sha_by_trial.get(trial_id),
                artifacts_dir=Path(artifacts_path) if artifacts_path else None,
            )
            if ws_status not in (VERIFIED,):
                workspace_gaps.append({"trial_id": trial_id, "reason": ws_status})
            workspace_files = (
                _read_text_files(workspace_root) if ws_status in (VERIFIED, ABSENT) else {}
            )
            evidence = TrialEvidence(
                trial_id=trial_id,
                task_id=rec["task_id"],
                arm=rec["arm"],
                trajectory=record,
                passed=passed_by_trial.get(trial_id),
                # resolved to a full path: detectors match by prefix, never by a
                # bare segment name a workspace dir could share
                holdout_paths=(holdout_root.as_posix(),) if holdout_root else (),
                workspace_files=workspace_files,
                # tasks.yaml carries no pristine workspace content; detectors fall
                # back to trajectory-attributed edits and stay silent when neither
                # can attribute [plan §4.3]
                pristine_files={},
                holdout_assertion_values=assertion_cache.get(holdouts_dir, ()),
                # only a multi-segment relpath is content-matchable — a bare
                # segment like "tests" would flag every workspace test edit
                holdout_relpaths=(holdouts_dir,) if "/" in holdouts_dir else (),
            )

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

            review_transcript, max_reasoning_bytes = self._splice_review_transcript(
                artifacts_path, tv.flight_recorder_sha
            )
            assembled.append(
                AssembledTrial(
                    evidence=evidence,
                    review_transcript=review_transcript,
                    max_reasoning_bytes=max_reasoning_bytes,
                )
            )

        return AssembledEvidence(
            trials=assembled,
            metrics=metrics,
            gaps=gaps,
            detail_by_arm=detail_by_arm,
            detail_gaps=detail_gaps,
            workspace_gaps=workspace_gaps,
        )

    def _splice_review_transcript(
        self, artifacts_path, flight_recorder_sha
    ) -> tuple[str, Optional[int]]:
        """Prepare the advisory pass's input: the flight recorder (verified,
        richest pathology signal) spliced ahead of the transcript [EVAL-24 AC-3].
        Only when ``review`` — a ``--no-review`` scan reads no review artifact.

        ``resolve_flight_recorder`` verifies the sha, so unverified reasoning is
        never fed, exactly like the trajectory. Returns ``("", None)`` when there
        is nothing (or no review) to splice. The byte budget is set only when
        reasoning is present, so non-reasoning trials keep their existing review
        behavior exactly [EVAL-24-D003]; no completion is requested here."""
        if not self._review:
            return "", None
        transcript = read_transcript(artifacts_path)
        _fr_status, fr_record = resolve_flight_recorder(artifacts_path, flight_recorder_sha)
        if fr_record is None:
            return transcript, None
        reasoning_text = fr_record.as_transcript()
        spliced = (
            f"{reasoning_text}\n\n{transcript}" if transcript.strip() else reasoning_text
        )
        # D003: bound only when reasoning is present, so non-reasoning trials keep
        # their existing review behavior exactly.
        return spliced, DEFAULT_REASONING_BUDGET_BYTES
