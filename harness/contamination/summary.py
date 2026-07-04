"""Per-arm contamination summary + asymmetry detection [EVAL-10 AC-5, D001].

Joins the three channels into the summary every render discloses: dating
(manifest ``created_at`` × arm ``training_cutoff``) overlaid with the flags of
the latest **complete** ``contamination_probe`` event (which already merges the
AC-3 probe channels and the AC-4 overlap scan). Deterministic and LLM-free —
this module reads the ledger and computes; it never talks to a provider
[AC-6]. Disclosure over suppression: symmetric and unknown states are caveats;
only *asymmetric flagged* contamination (one arm flagged on a task, another
not) reaches the official fence — the one case that invalidates the pairing
itself [D001]. Flags never delete trials [D004].
"""

from __future__ import annotations

from typing import Optional

from ..ledger import events
from ..ledger.query import find_events
from ..schema.experiment import ExperimentSpec
from .dating import ContaminationStatus, cutoff_status


def latest_probe(ledger_path) -> Optional[dict]:
    """The most recent ``contamination_probe`` payload on the ledger, or None.

    Latest wins: a re-run supersedes its predecessor, the same way re-baselines
    do. A ``cant_probe`` record supersedes an earlier ``complete`` one — a
    failed re-probe must not silently resurrect stale outcomes."""
    found = None
    for ev in find_events(ledger_path, events.CONTAMINATION_PROBE):
        found = ev
    return found["probe"] if found is not None else None


def contamination_summary(ledger_path, spec: ExperimentSpec, manifest=None) -> dict:
    """The per-arm contamination summary both renders carry [AC-5].

    Per (task, arm) over the tasks the experiment actually ran: the AC-1
    tri-state with probe flags overlaid (detection outranks dating). ``manifest``
    supplies ``created_at``; absent manifest or entry means the dating channel
    honestly reports ``unknown``. The ``asymmetric`` list names every task
    flagged for at least one arm but not all — flagged-vs-unknown breaks the
    pairing exactly as flagged-vs-clean does, so it is asymmetric too
    [fail-closed].
    """
    task_ids = sorted(
        {ev["trial_record"]["task_id"] for ev in find_events(ledger_path, events.TRIAL)}
    )
    probe = latest_probe(ledger_path)
    if probe is None:
        probe_status = "not_run"
    elif probe["status"] == "complete":
        probe_status = "complete"
    else:
        probe_status = f"cant_probe({probe['reason']})"
    probe_arms = probe.get("arms", {}) if probe_status == "complete" else {}

    per_arm: dict[str, dict] = {}
    status_by_task: dict[str, dict[str, ContaminationStatus]] = {t: {} for t in task_ids}
    for arm in spec.arms:
        counts = {s.value: 0 for s in ContaminationStatus}
        flagged_ids: list[str] = []
        outcomes = probe_arms.get(arm.name, {}).get("outcomes", {})
        for task_id in task_ids:
            entry = manifest.task(task_id) if manifest is not None else None
            created_at = entry.created_at if entry is not None else None
            status = cutoff_status(
                created_at,
                arm.training_cutoff,
                flagged=outcomes.get(task_id) == "flagged",
            )
            status_by_task[task_id][arm.name] = status
            counts[status.value] += 1
            if status is ContaminationStatus.FLAGGED:
                flagged_ids.append(task_id)
        per_arm[arm.name] = {**counts, "flagged_task_ids": flagged_ids}

    asymmetric = []
    for task_id in task_ids:
        by_arm = status_by_task[task_id]
        flagged_arms = sorted(
            a for a, s in by_arm.items() if s is ContaminationStatus.FLAGGED
        )
        unflagged_arms = sorted(
            a for a, s in by_arm.items() if s is not ContaminationStatus.FLAGGED
        )
        if flagged_arms and unflagged_arms:
            asymmetric.append(
                {
                    "task_id": task_id,
                    "flagged_arms": flagged_arms,
                    "unflagged_arms": unflagged_arms,
                }
            )
    return {"probe_status": probe_status, "per_arm": per_arm, "asymmetric": asymmetric}
