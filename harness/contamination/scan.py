"""Deterministic trial-artifact overlap scan [EVAL-10 AC-4].

Walks the ledgered trial records and fingerprints each trial's *solution*
against the task's oracle and holdout references. The solution is defined
exactly as the judge packet defines it (``harness.judge.assemble``): the trial
workspace — the parent of the recorded ``artifacts_path`` — excluding the
``artifacts/`` subtree (logs/telemetry, not the solution) and the grader's
holdout-output file (instrument-written, would self-trigger the insulation
alarm). A trial that cannot be read is *disclosed as unscanned*, never scored
against an empty or wrong tree. Pure file reads + fingerprints — no LLM, no
network [AC-6].
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

from ..ledger import events
from ..ledger.query import find_events
from ..run.seam import HoldoutLeakError
from ..run.workspace import VERIFIED, resolve_workspace
from .overlap import solution_overlap

# The grader writes holdout results into the workspace; scanning them would
# compare holdout-derived content against the holdouts themselves. Same
# exclusion the judge packet applies (judge/assemble._GRADER_OUTPUT).
_GRADER_OUTPUT = "holdout_results.json"


@dataclass(frozen=True)
class TaskReferences:
    """What a task's trials are compared against: the oracle solution (when
    the corpus carries one) and the holdout content."""

    oracle: Optional[str] = None
    holdouts: tuple[str, ...] = ()

    def measurable(self) -> bool:
        return self.oracle is not None or bool(self.holdouts)


@dataclass
class ScanReport:
    """One scan pass over the ledger's trials.

    ``overlap_flags`` is per (arm, task): True = overlap at/above threshold on
    some trial, False = scanned clean — shaped for ``run_memory_probe``'s
    ``overlap_flags`` input. ``alarms`` are holdout-leak insulation breaches
    [EVAL-4 AC-9] and ``skipped`` are disclosed unscanned trials — both must be
    surfaced by the caller, never dropped."""

    overlap_flags: dict[str, dict[str, bool]] = field(default_factory=dict)
    alarms: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def read_solution(artifacts_path: Optional[str]) -> Optional[str]:
    """The trial's workspace text under the judge's solution definition.

    None when there is nothing to read — an absent/empty ``artifacts_path`` or
    a vanished workspace must surface as *unscanned*, not as an empty (or
    worse, cwd-relative) solution scored against real references. Undecodable
    bytes are replaced, not dropped: a skipped file could hide a leak.
    """
    if not artifacts_path:
        return None
    artifacts_dir = Path(artifacts_path)
    workspace = artifacts_dir.parent
    if not workspace.is_dir():
        return None
    parts: list[str] = []
    for p in sorted(workspace.rglob("*")):
        if not p.is_file():
            continue
        if p == artifacts_dir or artifacts_dir in p.parents:
            continue
        if p.name == _GRADER_OUTPUT:
            continue
        parts.append(p.read_text(encoding="utf-8", errors="replace"))
    if not parts:
        return None
    return "\n".join(parts)


def scan_trials(
    ledger_path,
    references: Mapping[str, TaskReferences],
    *,
    threshold: Optional[float] = None,
) -> ScanReport:
    """Fingerprint every ledgered trial's solution against its task's
    references [AC-4].

    A task with no references is unmeasurable by this channel and contributes
    nothing (that is a property of the corpus, not a failure). A trial whose
    workspace cannot be read is recorded in ``skipped`` — disclosed, never
    silently treated as clean. A holdout overlap is recorded as both a flag
    and an ``alarms`` entry (the detector's :class:`HoldoutLeakError` is the
    EVAL-4 insulation channel; the scan preserves it as evidence rather than
    letting one trial's alarm abort the sweep). Multiple trials of the same
    (arm, task) OR-merge: one leaking repetition flags the pair.
    """
    report = ScanReport()
    # F-H3: the grade-time workspace commitment (latest grade wins). A trial
    # whose live bytes no longer match it is UNSCANNED — a post-grade edit
    # could otherwise scrub a leak and launder a "clean" probe onto the chain.
    # ABSENT commitments (legacy chains) scan with legacy semantics; the
    # forensics report carries that disclosure.
    workspace_sha_by_trial = {
        gev["trial_id"]: gev.get("workspace_sha256")
        for gev in find_events(ledger_path, events.GRADE)
    }
    # F-M-C3: a forensically-quarantined trial is excluded from ANALYSIS by a
    # ledgered human decision — the scan honors the same decision (disclosed,
    # never silent), so quarantining an intentional/false-positive leak and
    # re-running scan+probe is a real resolution path for the insulation fence.
    quarantined = {
        qev["forensic_quarantine"]["trial_id"]
        for qev in find_events(ledger_path, events.FORENSIC_QUARANTINE)
    }
    for ev in find_events(ledger_path, events.TRIAL):
        rec = ev["trial_record"]
        task_id, arm, trial_id = rec["task_id"], rec["arm"], rec["trial_id"]
        refs = references.get(task_id)
        if refs is None or not refs.measurable():
            continue  # nothing the agent could not have produced — unmeasurable
        if trial_id in quarantined:
            report.skipped.append(
                f"trial {trial_id} (task {task_id}, arm {arm}): forensically "
                "quarantined (ledgered) — excluded from the scan [F-M-C3]"
            )
            continue
        artifacts_path = rec.get("artifacts_path")
        ledgered_sha = workspace_sha_by_trial.get(trial_id)
        if ledgered_sha is not None and artifacts_path:
            status = resolve_workspace(
                Path(artifacts_path).parent, ledgered_sha, artifacts_dir=artifacts_path
            )
            if status != VERIFIED:
                report.skipped.append(
                    f"trial {trial_id} (task {task_id}, arm {arm}): workspace "
                    f"failed chain verification ({status}) — UNSCANNED [F-H3]"
                )
                continue
        solution = read_solution(artifacts_path)
        if solution is None:
            report.skipped.append(
                f"trial {trial_id} (task {task_id}, arm {arm}): no readable "
                "workspace at the recorded artifacts_path — UNSCANNED"
            )
            continue
        try:
            flagged = solution_overlap(
                solution,
                oracle=refs.oracle,
                holdouts=refs.holdouts,
                threshold=threshold,
            ).flagged
        except HoldoutLeakError as e:
            report.alarms.append(
                f"trial {trial_id} (task {task_id}, arm {arm}): {e}"
            )
            flagged = True
        per_arm = report.overlap_flags.setdefault(arm, {})
        per_arm[task_id] = per_arm.get(task_id, False) or flagged
    return report
