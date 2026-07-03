"""Assemble judge comparisons from the ledger [EVAL-2 §M5, JD-9].

``bench judge`` reads the per-trial artifacts ``run`` produced and ``grade``
scored, pairs the two arms per ``(task, repetition)``, and builds the blind
:class:`Packet` input for each comparison. A deterministic ``comparison_id`` and
the A/B → physical-arm map ride onto every verdict so the calibration join is
frame-correct [D-P4-1]. This module only *assembles* inputs — it performs no
judging and appends no events (that is ``judge_pair`` / the CLI).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..ledger import events
from ..ledger.query import find_events
from .packet import ResponseArtifacts

# The grader writes holdout results into the workspace; they are grader output,
# not agent-authored solution content, so they are excluded from the judged diff.
_GRADER_OUTPUT = "holdout_results.json"


def comparison_id_for(task_id: str, repetition: int) -> str:
    """A stable, human-readable comparison id, deterministic in (task, rep)."""
    return f"cmp-{task_id}-r{repetition}"


@dataclass
class Comparison:
    comparison_id: str
    task_id: str
    repetition: int
    task_class: str
    arm_map: dict[str, str]  # {"A": arm_a_name, "B": arm_b_name}
    response_a: ResponseArtifacts
    response_b: ResponseArtifacts


def _read_workspace_diff(artifacts_path) -> str:
    """The agent's final workspace as a diff-from-empty: every agent-authored
    file under the trial workspace, excluding the ``artifacts/`` subtree (logs /
    telemetry, not the solution) and the grader's holdout output. Redaction
    already ran at trial time, so identity canaries are scrubbed; the packet
    validator re-scans as belt-and-suspenders."""
    if not artifacts_path:
        return ""
    artifacts_dir = Path(artifacts_path)
    workspace = artifacts_dir.parent
    if not workspace.is_dir():
        return ""
    parts: list[str] = []
    for p in sorted(workspace.rglob("*")):
        if not p.is_file():
            continue
        if artifacts_dir in p.parents:
            continue
        if p.name == _GRADER_OUTPUT:
            continue
        rel = p.relative_to(workspace).as_posix()
        content = p.read_text(encoding="utf-8", errors="replace")
        parts.append(f"--- {rel} ---\n{content}")
    return "\n".join(parts)


def _holdout_results(grade_event) -> list:
    """The holdout-test assertions from a trial's grade event, in the packet's
    ``{id, result}`` shape. A trial without a grade contributes no holdout
    evidence (an empty list), never a fabricated pass."""
    if grade_event is None:
        return []
    return [
        {"id": a.get("id"), "result": a.get("result")}
        for a in grade_event.get("assertions", [])
        if a.get("source", "holdout_test") == "holdout_test"
    ]


def comparisons_from_ledger(ledger_path, spec, *, task_classes=None) -> list[Comparison]:
    """Pair the two arms per (task, repetition) into judgeable comparisons.

    ``arm_a``/``arm_b`` are ``spec.arms[0]``/``[1]`` (deterministic order), so the
    recorded ``arm_map`` is stable. A (task, repetition) with a missing arm trial
    is skipped — an unpaired trial cannot be A/B-compared.
    """
    arm_a, arm_b = spec.arms[0], spec.arms[1]
    task_classes = task_classes or {}

    grades = {g["trial_id"]: g for g in find_events(ledger_path, events.GRADE)}
    trials: dict[tuple, dict] = {}
    for e in find_events(ledger_path, events.TRIAL):
        tr = e["trial_record"]
        trials[(tr["task_id"], tr["repetition"], tr["arm"])] = tr

    keys = sorted({(t, r) for (t, r, _a) in trials})
    out: list[Comparison] = []
    for task_id, rep in keys:
        ta = trials.get((task_id, rep, arm_a.name))
        tb = trials.get((task_id, rep, arm_b.name))
        if ta is None or tb is None:
            continue
        out.append(
            Comparison(
                comparison_id=comparison_id_for(task_id, rep),
                task_id=task_id,
                repetition=rep,
                task_class=task_classes.get(task_id, "default"),
                arm_map={"A": arm_a.name, "B": arm_b.name},
                response_a=ResponseArtifacts(
                    diff=_read_workspace_diff(ta.get("artifacts_path")),
                    holdout_results=_holdout_results(grades.get(ta["trial_id"])),
                ),
                response_b=ResponseArtifacts(
                    diff=_read_workspace_diff(tb.get("artifacts_path")),
                    holdout_results=_holdout_results(grades.get(tb["trial_id"])),
                ),
            )
        )
    return out
