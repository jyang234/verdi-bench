"""Lifecycle snapshot assembly [EVAL-13 AC-3].

``compute_status`` is a pure read over ``(experiment dir, ledger, heartbeat)``:
it appends no event, writes no file, and returns one versioned dict an observer
(CLI verb, HTTP endpoint) can render. Counters mirror the semantics the stages
themselves use — planned cells come from the same ``enumerate_trials`` run
executes, grade progress uses the same terminal-vs-transient ``cant_grade``
vocabulary ``bench grade`` skips by, spend uses the RN-2 enforcement figure
(self-reported cost, else proxy-metered) the cost guard enforces — so status
never invents a second definition of progress.

Fail-closed on tamper [PL-6/CO-5 posture]: a broken hash chain yields
``chain.ok=false`` with the verifier's detail and ``stages=None`` — ledger
content that failed verification is withheld, never summarized. The heartbeat,
being operational telemetry outside the chain, is surfaced regardless.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from ..corpus.commit import TaskCommitmentError, load_task_dicts
from ..grade.deterministic import TRANSIENT_CANT_GRADE
from ..ledger import events
from ..ledger.query import ledger_head_hash, read_events, verify
from ..plan.interleave import enumerate_trials
from ..run.budget import enforcement_cost
from ..run.heartbeat import HEARTBEAT_FILENAME, read_heartbeat
from ..schema.errors import SpecError
from ..schema.experiment import ExperimentSpec

STATUS_SCHEMA_VERSION = 1


def compute_status(experiment_dir) -> dict:
    """Assemble the lifecycle snapshot for one experiment directory."""
    experiment_dir = Path(experiment_dir)
    ledger_path = experiment_dir / "ledger.ndjson"

    doc: dict = {
        "schema_version": STATUS_SCHEMA_VERSION,
        "experiment_id": experiment_dir.name,
        "chain": None,
        # Withheld (None) unless the chain verifies — content from an
        # unverified ledger is never summarized [AC-3, fail closed].
        "stages": None,
        # Operational liveness, outside the chain — shown regardless.
        "heartbeat": read_heartbeat(experiment_dir / HEARTBEAT_FILENAME),
    }

    if not ledger_path.exists() or ledger_path.stat().st_size == 0:
        # Nothing recorded yet — a state, not tampering (assert_chain precedent).
        doc["chain"] = {"ok": True, "detail": "empty", "head_hash": None, "events": 0}
        doc["stages"] = _stages(experiment_dir, [])
        return doc

    result = verify(ledger_path)
    if not result.ok:
        doc["chain"] = {
            "ok": False,
            "detail": result.detail,
            "head_hash": None,
            "events": None,
        }
        return doc

    evs = read_events(ledger_path)
    doc["chain"] = {
        "ok": True,
        "detail": None,
        "head_hash": ledger_head_hash(ledger_path),
        "events": len(evs),
    }
    doc["stages"] = _stages(experiment_dir, evs, ledger_path=ledger_path)
    return doc


def _stages(
    experiment_dir: Path, evs: list[dict], *, ledger_path: Optional[Path] = None
) -> dict:
    by_kind: dict[str, list[dict]] = {}
    for ev in evs:
        by_kind.setdefault(ev.get("event", ""), []).append(ev)

    spec, spec_error = _load_spec(experiment_dir / "experiment.yaml")
    trials = by_kind.get(events.TRIAL, [])
    cells = _cells(experiment_dir, spec, trials, by_kind)
    judge = _judge(spec, trials, by_kind)
    # planned cells = tasks × arms × reps, so planned pairs = planned / |arms|.
    if cells["planned"] is not None and spec is not None and spec.arms:
        judge["pairs_expected"] = cells["planned"] // len(spec.arms)

    return {
        "lock": _lock(by_kind),
        "spec": _spec_summary(spec),
        "spec_error": spec_error,
        # provenance ts of the newest event — the "updated" signal for a
        # workspace home row [EVAL-14 AC-1]. Ledger-derived, so it lives in the
        # withheld-on-tamper block with everything else.
        "last_event_ts": (
            ((evs[-1].get("provenance") or {}).get("ts")) if evs else None
        ),
        "cells": cells,
        "per_arm": _per_arm(trials, by_kind),
        "spend": _spend(spec, trials, by_kind),
        "grade": _grade(trials, by_kind),
        "judge": judge,
        "review": {
            "packets": len(by_kind.get(events.REVIEW_PACKET_BUILT, [])),
            "human_verdicts": len(by_kind.get(events.HUMAN_VERDICT, [])),
            "reveals": len(by_kind.get(events.REVEAL, [])),
        },
        "process_scores": len(by_kind.get(events.PROCESS_SCORE, [])),
        "forensics": _forensics(by_kind),
        "quarantines": [
            ev["forensic_quarantine"]
            for ev in by_kind.get(events.FORENSIC_QUARANTINE, [])
        ],
        "contamination_probes": len(by_kind.get(events.CONTAMINATION_PROBE, [])),
        "analyze": _analyze(by_kind, ledger_path),
    }


def _load_spec(spec_path: Path):
    """Parse the spec if present; a malformed or absent spec is a *describable
    state* for an observer (reported as ``spec_error``), not a crash — status
    must be able to say "not yet planned" or "spec unreadable" out loud."""
    if not spec_path.exists():
        return None, "experiment.yaml not found"
    try:
        return ExperimentSpec.from_yaml(spec_path), None
    except (SpecError, yaml.YAMLError, OSError) as e:
        return None, f"{type(e).__name__}: {e}"


def _lock(by_kind: dict) -> dict:
    locked = by_kind.get(events.EXPERIMENT_LOCKED, [])
    if not locked:
        return {"locked": False}
    ev = locked[0]  # the genesis event; one lock per ledger
    return {
        "locked": True,
        "spec_sha256": ev.get("spec_sha256"),
        "seed": ev.get("seed"),
        "attested_by": (ev.get("attestation") or {}).get("attested_by"),
        "ts": (ev.get("provenance") or {}).get("ts"),
    }


def _spec_summary(spec: Optional[ExperimentSpec]) -> Optional[dict]:
    if spec is None:
        return None
    return {
        "arms": [a.name for a in spec.arms],
        "repetitions": spec.repetitions,
        "primary_metric": spec.primary_metric.value,
        "decision_rule": spec.decision_rule,
        "cost_ceiling": {
            "amount": spec.cost_ceiling.amount,
            "currency": spec.cost_ceiling.currency,
        },
    }


def _trial_cells(trials: list[dict]) -> set[tuple]:
    return {
        (
            (ev.get("trial_record") or {}).get("task_id"),
            (ev.get("trial_record") or {}).get("arm"),
            (ev.get("trial_record") or {}).get("repetition"),
        )
        for ev in trials
    }


def _cells(
    experiment_dir: Path, spec: Optional[ExperimentSpec], trials: list[dict], by_kind: dict
) -> dict:
    """Planned vs done, using run's own cell enumeration so "planned" is what
    ``bench run`` will actually execute, not a re-guess. ``planned`` is None
    when the spec or task source cannot define a plan (still an honest state)."""
    done_cells = _trial_cells(trials)
    planned: Optional[int] = None
    done = len(done_cells)
    if spec is not None:
        try:
            task_ids = [t["id"] for t in load_task_dicts(experiment_dir)]
        except TaskCommitmentError:
            task_ids = []
        if task_ids:
            plan = enumerate_trials(
                task_ids, [a.name for a in spec.arms], spec.repetitions
            )
            planned_set = {(t.task_id, t.arm, t.repetition) for t in plan}
            planned = len(planned_set)
            done = len(done_cells & planned_set)
    return {
        "planned": planned,
        "done": done,
        "infra_failures": len(by_kind.get(events.TRIAL_INFRA_FAILED, [])),
    }


def _enforcement_cost(rec: dict) -> Optional[float]:
    """The RN-2 figure — the same rule the cost guard enforces with [F-H4]."""
    return enforcement_cost(
        (rec.get("telemetry") or {}).get("cost"),
        (rec.get("flags") or {}).get("proxy_metered_cost"),
    )


def _per_arm(trials: list[dict], by_kind: dict) -> dict:
    acc: dict[str, dict] = {}

    def bucket(arm: Optional[str]) -> dict:
        return acc.setdefault(
            arm or "?",
            {"trials": 0, "completed": 0, "timeout": 0, "infra_failed": 0, "cost": 0.0},
        )

    for ev in trials:
        rec = ev.get("trial_record") or {}
        b = bucket(rec.get("arm"))
        b["trials"] += 1
        outcome = rec.get("outcome")
        if outcome in ("completed", "timeout"):
            b[outcome] += 1
        cost = _enforcement_cost(rec)
        if cost is not None:
            b["cost"] += cost
    for ev in by_kind.get(events.TRIAL_INFRA_FAILED, []):
        bucket(ev.get("arm"))["infra_failed"] += 1
    return {arm: acc[arm] for arm in sorted(acc)}


def _spend(spec: Optional[ExperimentSpec], trials: list[dict], by_kind: dict) -> dict:
    """The RN-2 enforcement figure per trial (self-reported cost, else
    proxy-metered), floored by any ceiling-stop snapshot — the same arithmetic
    ``schedule`` resumes with, so status and the guard cannot disagree."""
    accumulated = 0.0
    for ev in trials:
        cost = _enforcement_cost(ev.get("trial_record") or {})
        if cost is not None:
            accumulated += cost
    stops = by_kind.get(events.RUN_STOPPED_COST_CEILING, [])
    for ev in stops:
        accumulated = max(accumulated, ev.get("accumulated_cost", 0.0) or 0.0)
    return {
        "accumulated": accumulated,
        "ceiling": spec.cost_ceiling.amount if spec is not None else None,
        "currency": spec.cost_ceiling.currency if spec is not None else None,
        "stopped_cost_ceiling": bool(stops),
    }


def _grade(trials: list[dict], by_kind: dict) -> dict:
    """Mirrors ``bench grade``'s skip set [GR-11]: graded or terminally
    cant_grade trials are settled; transient cant_grade stays pending."""
    trial_ids = {
        (ev.get("trial_record") or {}).get("trial_id") for ev in trials
    } - {None}
    graded = {ev.get("trial_id") for ev in by_kind.get(events.GRADE, [])}
    terminal_cant = {
        ev.get("trial_id")
        for ev in by_kind.get(events.CANT_GRADE, [])
        if ev.get("reason") not in TRANSIENT_CANT_GRADE
    }
    return {
        "graded": len(graded),
        "cant_grade_terminal": len(terminal_cant - graded),
        "pending": len(trial_ids - graded - terminal_cant),
    }


def _judge(spec: Optional[ExperimentSpec], trials: list[dict], by_kind: dict) -> dict:
    """Verdict counts plus honest denominators: ``pairs_ready`` counts
    (task, repetition) cells with BOTH locked arms' trials present (what judge
    can compare today); ``pairs_expected`` is the planned total when the spec
    and task source define one (None otherwise, never guessed)."""
    verdicts = by_kind.get(events.JUDGE_VERDICT, [])
    cant = sum(
        1 for ev in verdicts if (ev.get("verdict") or {}).get("winner") == "CANT_JUDGE"
    )
    pairs_ready = 0
    pairs_expected: Optional[int] = None
    if spec is not None and len(spec.arms) >= 2:
        arm_a, arm_b = spec.arms[0].name, spec.arms[1].name
        seen: dict[tuple, set] = {}
        for ev in trials:
            rec = ev.get("trial_record") or {}
            seen.setdefault((rec.get("task_id"), rec.get("repetition")), set()).add(
                rec.get("arm")
            )
        pairs_ready = sum(1 for arms in seen.values() if {arm_a, arm_b} <= arms)
    return {
        "verdicts": len(verdicts),
        "cant_judge": cant,
        "pairs_ready": pairs_ready,
        "pairs_expected": pairs_expected,
    }


def _forensics(by_kind: dict) -> dict:
    reports = by_kind.get(events.FORENSICS_REPORT, [])
    latest = None
    if reports:
        fr = reports[-1].get("forensics_report") or {}
        latest = {
            "flags": len(fr.get("flags") or []),
            "coverage": fr.get("coverage"),
            "vocabulary_version": fr.get("vocabulary_version"),
        }
    return {"reports": len(reports), "latest": latest}


def _analyze(by_kind: dict, ledger_path: Optional[Path]) -> dict:
    renders = by_kind.get(events.FINDINGS_RENDERED, [])
    last = None
    if renders:
        ev = renders[-1]
        last = {"mode": ev.get("mode"), "ts": (ev.get("provenance") or {}).get("ts")}
    if ledger_path is None:
        selfcheck = "missing"  # empty ledger: nothing selfchecked yet
    else:
        # Local import: reuse the EVAL-6 gate classifier (one definition of
        # staleness), without paying its module's analyze-wide import graph at
        # status import time.
        from ..analyze.selfcheck import selfcheck_status

        selfcheck = selfcheck_status(ledger_path)
    return {
        "selfcheck": selfcheck,
        "renders": {
            "official": sum(1 for ev in renders if ev.get("mode") == "official"),
            "exploratory": sum(1 for ev in renders if ev.get("mode") == "exploratory"),
        },
        "cant_analyze": len(by_kind.get(events.CANT_ANALYZE, [])),
        "last_render": last,
    }
