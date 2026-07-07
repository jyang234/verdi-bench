"""Single source of the shakedown's shared known-answer scenario (refactor 08 §1).

The golden experiment shape is the *same* known-answer control at L1
(``golden.py``), L2 (``official.py``), and L3 (``tripwires.py``) — but nothing
enforced that, so the three copies could silently drift apart. This module
single-sources that shape (constants, composable builders, and the canonical
``golden_experiment``) plus the helpers the two harbor scripts share, so L1/L2/L3
identity holds *by construction* and the harbor checks stay one implementation.

``_harness.py`` stays pure script-local plumbing; the scenario *content* lives
here. The scripts still keep their per-layer narratives inline — the stage-by-
stage pipeline is refactor 08 §1's "executable SDK documentation" and is the
point of each layer's file. Imports ``harness.*`` freely, never ``tests.*``.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from harness.corpus.manifest import build_manifest
from harness.grade.holdouts import AssertionHoldout
from harness.sdk import Experiment, Task

FAKE_JUDGE = "fake/deterministic-2026-01-01"
ESCALATION = {"kappa_threshold": 0.6, "min_human_verdicts": 1}
TREATMENT_PASS = {"t1", "t2", "t3", "t4", "t5", "t6"}
CONTROL_PASS = {"t1", "t2"}


def golden_passes(arm: str, task: str) -> bool:
    """The known-answer grade predicate: which (arm, task) pairs pass the holdout."""
    return task in (TREATMENT_PASS if arm == "treatment" else CONTROL_PASS)


def fake_experiment(name: str = "tw", *, seed: int = 1234, ceiling: float = 25.0,
                    judge: str = FAKE_JUDGE) -> Experiment:
    """A base experiment on the fake judge — vectors add arms/tasks/config."""
    return Experiment(name, seed=seed, cost_ceiling_usd=ceiling).judge(judge, escalation=ESCALATION)


def golden_arms(exp: Experiment) -> Experiment:
    return (exp.arm("treatment", model="openai/gpt-4o-2024-08-06", platform="codex")
               .arm("control", model="anthropic/claude-haiku-4-5-20251001", platform="claude_code"))


def cost_tasks(exp: Experiment, cost: float, *, task_class: str | None = None) -> Experiment:
    for i in range(1, 9):
        exp.task(Task(f"t{i}", prompt="solve", task_class=task_class,
                      fake_behavior={"native_log": {"total_cost_usd": cost}}))
    return exp


def golden_experiment(name: str, *, judge: str = FAKE_JUDGE, seed: int = 1234,
                      ceiling: float = 25.0, reps: int = 3) -> Experiment:
    """The canonical 2-arm/8-task golden shape (L1/L2/L3 share this by construction)."""
    return cost_tasks(golden_arms(
        fake_experiment(name, seed=seed, ceiling=ceiling, judge=judge)
        .corpus("shakedown-mini", "1.0.0").repetitions(reps)), 0.02)


def make_manifest(path, *, corpus_id: str = "shakedown-mini", semver: str = "1.0.0",
                  task_ids: list[str] | None = None) -> None:
    ids = task_ids or [f"t{i}" for i in range(1, 9)]
    build_manifest(corpus_id=corpus_id, semver=semver, kind="public",
                   tasks=[{"task_id": tid, "sha": hashlib.sha256(tid.encode()).hexdigest()}
                          for tid in ids]).save(path)


def advance_to_judged(ws, passes=golden_passes) -> None:
    """The shared judged-prefix of the hermetic pipelines: plan -> run -> inject
    per-arm grades -> grade -> judge. (``golden.py`` deliberately keeps this inline
    as the L1 stage-by-stage narrative.)"""
    ws.plan(actor="shakedown")
    ws.run(engine="fake")
    ws.inject_holdout_results(passes)
    ws.grade(runner="local")
    ws.judge()


# ------------------------------------------------------------------- harbor-shared
def harbor_run_config(egress_log, *, allowlist, keys_by_arm) -> dict:
    """The managed-proxy ``run.config``: metering proxy + per-arm provider keys."""
    return {"proxy": {"managed": True, "allowlist": list(allowlist), "log_path": str(egress_log)},
            "provider_key_names_by_arm": dict(keys_by_arm)}


def holdout_task(tid: str, prompt: str, expression: str, image: str) -> Task:
    """A feature task whose real solution.py is graded by executing the declared holdout."""
    return Task(tid, prompt=prompt, image=image, task_class="feature",
                holdout=AssertionHoldout(expression=expression))


def print_holdout_grades(view) -> None:
    """The per-trial holdout PASS/FAIL line the harbor scripts print after grading."""
    grades = view.latest_grade_by_trial()
    for tv in view.trials():
        rec = tv.record
        passed = bool(grades.get(rec["trial_id"], {}).get("binary_score"))
        print(f"    {rec['arm']:9s} {rec['task_id']:7s} holdout -> {'PASS' if passed else 'FAIL'}")


def check_harbor_provenance(t, view, expected_trials: int) -> None:
    """The two provenance checks both harbor scripts share: n real harbor trials
    completed, all engine=='harbor', and every image digest-pinned (sha256:)."""
    trials = [tv.record for tv in view.trials()]
    n = len(trials)
    t.check("real harbor trials completed",
            n == expected_trials and all(rec.get("provenance", {}).get("engine") == "harbor" for rec in trials),
            f"{n} harbor trials")
    digests = {str(rec.get("provenance", {}).get("image_digest", "")).startswith("sha256:") for rec in trials}
    t.check("images digest-pinned", digests == {True}, "provenance image_digest is sha256:")


def check_egress_attribution(t, egress_log, view, allowlist) -> None:
    """Per-trial egress attribution as INDEPENDENT evidence: parses the raw metering-
    proxy JSONL on purpose — the shakedown validates the instrument, so it deliberately
    does NOT trust the engine's own ``flags.egress_attempts`` attribution. Tolerant of
    partial lines (a line that fails ``json.loads`` is skipped). The tightened check:
    the set of trial ids attributed in the log must equal the ledger's trial ids, and
    every trial must have at least one record with ``decision == "allow"`` on a host
    that is exactly in the allowlist (no substring matching)."""
    allowed = set(allowlist)
    seen: set = set()
    allow_ok: dict = {}
    for line in Path(egress_log).read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        seen.add(r["trial"])
        if r["decision"] == "allow" and r["host"] in allowed:
            allow_ok[r["trial"]] = True
    ledger_ids = {tv.record["trial_id"] for tv in view.trials()}
    ok = bool(ledger_ids) and seen == ledger_ids and all(allow_ok.get(tid) for tid in ledger_ids)
    t.check("per-trial egress attributed", ok,
            f"{len(seen)} trials attributed through the metering proxy")
