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
# Single-sourced from grade (the owner of the grading transport): judge -> grade
# is an allowed import direction (no contract forbids it), so the filename lives
# in exactly one place [refactor 05 §1].
from ..grade.container import HOLDOUT_RESULTS


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


# F-M-J1: a deterministic diff budget. Unbounded assembly let a gamed arm ship
# a huge junk file only on trials it would lose, forcing a TERMINAL
# CANT_JUDGE(context_overflow) that permanently excludes the comparison — a
# missing-data lever. Truncation is deterministic (sorted walk, head-of-file,
# fixed caps) so identical workspaces produce identical packets, and every cut
# is disclosed in-packet, never silent.
PER_FILE_DIFF_CAP = 64 * 1024
TOTAL_DIFF_CAP = 512 * 1024


def read_workspace_diff(artifacts_path) -> str:
    """Public alias for :func:`_read_workspace_diff` — the agent's final
    workspace as a bounded diff-from-empty.

    Control-run reuse snapshots this exact string at export so a reused control
    can be judged against a fresh contender after the source workspace is gone
    (ephemeral containers). Sharing the one implementation guarantees the
    snapshot is byte-identical to what the judge would read live."""
    return _read_workspace_diff(artifacts_path)


def _read_workspace_diff(artifacts_path) -> str:
    """The agent's final workspace as a diff-from-empty: every agent-authored
    file under the trial workspace, excluding the ``artifacts/`` subtree (logs /
    telemetry, not the solution) and the grader's holdout output, under the
    deterministic diff budget above [F-M-J1]. Redaction already ran at trial
    time, so identity canaries are scrubbed; the packet validator re-scans as
    belt-and-suspenders."""
    if not artifacts_path:
        return ""
    artifacts_dir = Path(artifacts_path)
    workspace = artifacts_dir.parent
    if not workspace.is_dir():
        return ""
    # PRA-M5: never follow a symlink out of the workspace. An agent-controlled
    # workspace can plant a symlink (a file, or a directory rglob would descend)
    # pointing at a host path; following it read arbitrary host contents verbatim
    # into the blind judge packet (and validate_identity_free only re-scans for
    # *identity*, so host secrets/other-arm files passed straight through). Skip
    # link files, and confine every read to the resolved workspace subtree so a
    # file reached through a symlinked directory is excluded too. Mirrors the
    # grade container's copytree(symlinks=True) no-follow stance.
    ws_real = workspace.resolve()
    parts: list[str] = []
    total = 0
    omitted = 0
    binary_skipped = 0
    for p in sorted(workspace.rglob("*")):
        if p.is_symlink():
            continue
        if not p.is_file():
            continue
        if not p.resolve().is_relative_to(ws_real):
            continue  # reached through a symlinked directory into another tree
        if artifacts_dir in p.parents:
            continue
        if p.name == HOLDOUT_RESULTS:
            continue
        # Generated/compiled trees and binary files are not a legible diff for a
        # human or the judge — a .pyc rendered byte-by-byte is noise, and an agent
        # that imports or builds routinely produces them. Exclude them (disclosed
        # below), keeping the diff to the agent-authored TEXT [F-M-J1 legibility].
        if "__pycache__" in p.parts:
            binary_skipped += 1
            continue
        raw = p.read_bytes()
        if b"\x00" in raw[:8192]:  # NUL byte ⇒ binary (git's heuristic)
            binary_skipped += 1
            continue
        rel = p.relative_to(workspace).as_posix()
        content = raw.decode("utf-8", errors="replace")
        if len(content) > PER_FILE_DIFF_CAP:
            content = (
                content[:PER_FILE_DIFF_CAP]
                + f"\n[verdi: truncated at {PER_FILE_DIFF_CAP} chars — diff budget F-M-J1]"
            )
        entry = f"--- {rel} ---\n{content}"
        if total + len(entry) > TOTAL_DIFF_CAP:
            omitted += 1
            continue
        total += len(entry)
        parts.append(entry)
    if binary_skipped:
        parts.append(
            f"--- [verdi: {binary_skipped} binary/generated file(s) excluded from the diff] ---"
        )
    if omitted:
        parts.append(
            f"--- [verdi: {omitted} file(s) omitted — total diff budget "
            f"{TOTAL_DIFF_CAP} chars reached, F-M-J1] ---"
        )
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
