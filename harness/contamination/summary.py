"""Per-arm contamination summary + asymmetry detection [EVAL-10 AC-5, D001].

Joins the three channels into the summary every render discloses: dating
(manifest ``created_at`` × arm ``training_cutoff``) overlaid with the flags of
the latest ``contamination_probe`` event. LLM outcomes count only from a
``complete`` probe, but the deterministic AC-4 ``overlap_flags`` on the event
count regardless of status — a provider outage must not erase evidence
computed from disk. Deterministic and LLM-free — this module reads the ledger
and computes; it never talks to a provider [AC-6]. Disclosure over
suppression: symmetric and unknown states are caveats; only *asymmetric
flagged* contamination (one arm flagged on a task, another not) reaches the
official fence — the one case that invalidates the pairing itself [D001].
Flags never delete trials [D004].
"""

from __future__ import annotations

from typing import Optional

from ..ledger import events
from ..ledger.query import find_events, latest_event
from ..schema.experiment import ExperimentSpec
from .dating import ContaminationStatus, cutoff_status, effective_cutoff


def latest_probe(ledger_path) -> Optional[dict]:
    """The most recent ``contamination_probe`` payload on the ledger, or None.

    Latest wins: a re-run supersedes its predecessor, the same way re-baselines
    do. A ``cant_probe`` record supersedes an earlier ``complete`` one — a
    failed re-probe must not silently resurrect stale outcomes."""
    ev = latest_event(ledger_path, events.CONTAMINATION_PROBE)
    return ev["probe"] if ev is not None else None


def _flagged_in_probe(probe: Optional[dict], arm_name: str, task_id: str) -> bool:
    """Whether the probe payload flags (arm, task) on any channel.

    LLM outcomes require a complete probe; the deterministic overlap flags
    ride every event and count under ``cant_probe`` too."""
    if probe is None:
        return False
    if probe.get("status") == "complete":
        outcomes = probe.get("arms", {}).get(arm_name, {}).get("outcomes", {})
        if outcomes.get(task_id) == "flagged":
            return True
    return bool(probe.get("overlap_flags", {}).get(arm_name, {}).get(task_id))


def probe_asymmetries(probe: Optional[dict]) -> list[dict]:
    """Asymmetric flagged tasks recomputed from the chain-anchored probe
    payload alone [AC-5, D001].

    The official fence calls this against the *ledgered* event rather than
    trusting the hand-editable findings field: a task flagged for at least one
    of the event's arms and not all of them breaks the pairing. Dating cannot
    flag (only detections flag), so the probe payload is the complete flag
    source.
    """
    if probe is None:
        return []
    arm_names = sorted(set(probe.get("arms", {})) | set(probe.get("overlap_flags", {})))
    if len(arm_names) < 2:
        return []
    task_ids: set[str] = set()
    for payload in probe.get("arms", {}).values():
        task_ids |= set(payload.get("outcomes", {}))
    for per_task in probe.get("overlap_flags", {}).values():
        task_ids |= set(per_task)
    return _asymmetries(
        sorted(task_ids),
        arm_names,
        lambda arm, tid: _flagged_in_probe(probe, arm, tid),
    )


def _asymmetries(task_ids, arm_names, is_flagged) -> list[dict]:
    out = []
    for task_id in task_ids:
        flagged = sorted(a for a in arm_names if is_flagged(a, task_id))
        unflagged = sorted(set(arm_names) - set(flagged))
        if flagged and unflagged:
            out.append(
                {
                    "task_id": task_id,
                    "flagged_arms": flagged,
                    "unflagged_arms": unflagged,
                }
            )
    return out


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
    created_at_by_id = (
        {t.task_id: t.created_at for t in manifest.tasks} if manifest is not None else {}
    )

    per_arm: dict[str, dict] = {}
    flagged_by_arm: dict[str, set[str]] = {}
    for arm in spec.arms:
        # EVAL-13 AC-4 [D002]: the arm dates on its effective cutoff — the
        # latest across every declared model (clean requires postdating them
        # all); any absent cutoff makes the arm undatable (unknown).
        cutoffs_by_model = {arm.model: arm.training_cutoff}
        cutoffs_by_model.update({a.model: a.training_cutoff for a in arm.aux_models})
        arm_cutoff = effective_cutoff(list(cutoffs_by_model.values()))
        counts = {s.value: 0 for s in ContaminationStatus}
        per_model: dict[str, dict[str, int]] = {
            m: {s.value: 0 for s in ContaminationStatus} for m in cutoffs_by_model
        }
        flagged_ids: list[str] = []
        for task_id in task_ids:
            flagged = _flagged_in_probe(probe, arm.name, task_id)
            status = cutoff_status(
                created_at_by_id.get(task_id), arm_cutoff, flagged=flagged
            )
            counts[status.value] += 1
            if status is ContaminationStatus.FLAGGED:
                flagged_ids.append(task_id)
            # per-model breakdown so the aggregation is auditable — which model
            # dragged the arm to unknown is visible, not a black box [AC-4].
            # Probe flags overlay every model row: detection is arm-scoped.
            for m, cutoff in cutoffs_by_model.items():
                per_model[m][
                    cutoff_status(
                        created_at_by_id.get(task_id), cutoff, flagged=flagged
                    ).value
                ] += 1
        per_arm[arm.name] = {**counts, "flagged_task_ids": flagged_ids}
        if arm.aux_models:
            per_arm[arm.name]["per_model"] = per_model
        flagged_by_arm[arm.name] = set(flagged_ids)

    asymmetric = _asymmetries(
        task_ids,
        [arm.name for arm in spec.arms],
        lambda arm, tid: tid in flagged_by_arm[arm],
    )
    return {"probe_status": probe_status, "per_arm": per_arm, "asymmetric": asymmetric}
