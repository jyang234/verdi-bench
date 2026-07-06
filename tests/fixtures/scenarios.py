"""Shared experiment scenarios for the observability/analysis suites [refactor 01 §2].

Pure moves of the fixtures the tests audit found welded across test files:
``rich_experiment`` (from test_eval14_observability_ui), ``reasoning_experiment``
(from test_eval14_page_drive), ``linked_experiment`` (from
test_flight_recorder_v3), and the analyze-scenario helpers
(from test_eval6_analyze) that test_eval10_findings imported cross-file.
Staged for reimplementation on the SDK builders in Phase 2. Test-utils only —
never the public SDK.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from harness.corpus.manifest import build_manifest
from harness.corpus.registry import CorpusManifest
from harness.judge.assemble import comparison_id_for
from harness.ledger import events as ledger_events
from harness.ledger.events import record_calibration_run
from harness.ledger.query import find_events, read_events
from harness.plan.interleave import derive_schedule, enumerate_trials
from harness.run.engines.fake import FakeEngine
from harness.run.heartbeat import HEARTBEAT_FILENAME
from harness.run.interleave import schedule
from harness.run.types import RunConfig, Task
from tests.fixtures.builders import fixed_ctx, locked_experiment, seed_trial_and_grade

# a claude-code native log whose message stream yields a real trajectory
_NATIVE_LOG = {
    "usage": {"input_tokens": 900, "output_tokens": 120},
    "total_cost_usd": 0.05,
    "messages": [
        {"content": [{"type": "text", "text": "reading the task"}]},
        {"content": [{"type": "tool_use", "name": "Bash",
                      "input": {"command": "pytest -q"}}]},
        {"content": [{"type": "tool_use", "name": "Edit",
                      "input": {"file_path": "solution.py"}}]},
    ],
}


def rich_experiment(tmp_path: Path) -> dict:
    """A locked experiment with a real fake-engine run (verified trajectories,
    heartbeat), per-arm workspace content, grades that disagree on t1, one
    advisory verdict, a forensics flag, and a quarantine."""
    # repetitions=1 so the fixture RUN completes the pre-registered plan
    # (status reports planned cells from the locked spec, not from what ran)
    spec, spec_path, ledger = locked_experiment(tmp_path, repetitions=1)
    (tmp_path / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": [{"id": "t1", "prompt": "p"}, {"id": "t2", "prompt": "p"}]}),
        encoding="utf-8",
    )
    ctx = fixed_ctx(experiment_id=tmp_path.name)
    arms = {a.name: a for a in spec.arms}
    tasks = {
        tid: Task(id=tid, prompt="p", fake_behavior={"native_log": _NATIVE_LOG})
        for tid in ["t1", "t2"]
    }
    order = derive_schedule(spec.seed, enumerate_trials(["t1", "t2"], list(arms), 1))
    schedule(
        order, tasks=tasks, arms=arms, workspace_root=tmp_path / "workspaces",
        ledger_path=ledger, ctx=ctx, config=RunConfig(engine=FakeEngine()),
        cost_ceiling=spec.cost_ceiling.amount,
        heartbeat_path=tmp_path / HEARTBEAT_FILENAME,
    )

    trial_ids: dict[tuple, str] = {}
    for ev in read_events(ledger):
        if ev.get("event") == "trial":
            rec = ev["trial_record"]
            trial_ids[(rec["task_id"], rec["arm"])] = rec["trial_id"]
            # plant per-arm solution content so compare has a real diff
            ws = Path(rec["artifacts_path"]).parent
            ws.mkdir(parents=True, exist_ok=True)
            (ws / "solution.py").write_text(
                f"def solve():\n    return {rec['arm']!r}  # {rec['task_id']}\n",
                encoding="utf-8",
            )

    rubric_sha = hashlib.sha256(
        (tmp_path / "rubrics" / "code-task-v1.md").read_text("utf-8").encode("utf-8")
    ).hexdigest()

    def grade(tid: str, passed: bool) -> None:
        ledger_events.record_grade(
            ledger, ctx, trial_id=tid, task_sha="sha-x",
            assertions=[{"id": "h1", "source": "holdout_test",
                         "result": "pass" if passed else "fail"}],
            binary_score=passed,
        )

    grade(trial_ids[("t1", "control")], False)
    grade(trial_ids[("t1", "treatment")], True)   # t1: arms disagree
    grade(trial_ids[("t2", "control")], True)
    grade(trial_ids[("t2", "treatment")], True)   # t2: arms agree

    ledger_events.append_verdict(
        ledger, ctx,
        verdict={
            "comparison_id": comparison_id_for("t1", 0), "winner": "B",
            "reason": "treatment handles the holdout case",
            "provenance": {"judge_model": "google/gemini-1.5-pro-002",
                           "rubric_sha256": rubric_sha},
        },
    )
    flagged = trial_ids[("t1", "control")]
    ledger_events.record_forensics_report(
        ledger, ctx,
        forensics_report={
            "vocabulary_version": 1,
            "metrics": {flagged: {"steps": 3}},
            "flags": [{"trial_id": flagged, "task_id": "t1", "arm": "control",
                       "detector": "suspicious_single_step",
                       "reason": "planted for fixture"}],
            "coverage": {"trials": 4, "covered": 4, "gaps": []},
        },
    )
    ledger_events.record_forensic_quarantine(
        ledger, ctx, trial_id=trial_ids[("t2", "treatment")], reason="fixture quarantine"
    )
    return {
        "dir": tmp_path, "ledger": ledger, "spec": spec, "ctx": ctx,
        "trial_ids": trial_ids, "flagged": flagged, "rubric_sha": rubric_sha,
    }


def reasoning_experiment(exp_dir: Path) -> Path:
    """A 2-arm generic experiment whose native log carries agent-attributed
    reasoning — for driving the compare screen's flight-recorder panel [EVAL-24]."""
    arms_cfg = [
        {"name": "control", "platform": "generic",
         "model": "anthropic/claude-haiku-4-5-20251001", "payload": {}},
        {"name": "treatment", "platform": "generic",
         "model": "openai/gpt-4.1-mini-2025-04-14", "payload": {}},
    ]
    spec, _sp, ledger = locked_experiment(exp_dir, arms=arms_cfg, repetitions=1)
    (exp_dir / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": [{"id": "t1", "prompt": "p"}]}), encoding="utf-8")
    ctx = fixed_ctx(experiment_id=exp_dir.name)
    arms = {a.name: a for a in spec.arms}
    native = {"verdi_log_version": 1, "telemetry": {"tokens_out": 40},
              "trajectory": [{"kind": "file_edit", "files_touched": ["solution.py"], "agent": "worker-1"}],
              "reasoning": [
                  # one token-less entry (a deterministic/unmeasured turn) and one
                  # measured model turn, so per-entry usage rendering has both states
                  {"content": "plan: decompose into add, then verify", "agent": "planner"},
                  {"content": "add(a, b) returns a + b; handled overflow", "agent": "worker-1",
                   "tokens": 412, "cost": 0.0021}]}
    tasks = {"t1": Task(id="t1", prompt="p", fake_behavior={"native_log": native})}
    order = derive_schedule(spec.seed, enumerate_trials(["t1"], list(arms), 1))
    schedule(order, tasks=tasks, arms=arms, workspace_root=exp_dir / "workspaces",
             ledger_path=ledger, ctx=ctx, config=RunConfig(engine=FakeEngine()),
             cost_ceiling=spec.cost_ceiling.amount)
    trial_ids = {}
    for ev in read_events(ledger):
        if ev.get("event") == "trial":
            rec = ev["trial_record"]
            trial_ids[rec["arm"]] = rec["trial_id"]
            ws = Path(rec["artifacts_path"]).parent
            ws.mkdir(parents=True, exist_ok=True)
            (ws / "solution.py").write_text(f"# {rec['arm']}\n", encoding="utf-8")
    for arm, passed in (("control", False), ("treatment", True)):
        ledger_events.record_grade(
            ledger, ctx, trial_id=trial_ids[arm], task_sha="s",
            assertions=[{"id": "h1", "source": "holdout_test",
                         "result": "pass" if passed else "fail"}],
            binary_score=passed)
    ledger_events.append_verdict(ledger, ctx, verdict={
        "comparison_id": comparison_id_for("t1", 0), "winner": "B", "reason": "x",
        "provenance": {"judge_model": "google/gemini-1.5-pro-002", "rubric_sha256": "s"}})
    return exp_dir


def linked_experiment(dirpath: Path) -> list[str]:
    """One generic-platform trial whose native log carries a 2-step trajectory
    and linkage-bearing reasoning (two linked turns + one unlinked note)."""
    arms = [{"name": "control", "platform": "generic",
             "model": "anthropic/claude-haiku-4-5-20251001", "payload": {}},
            {"name": "treatment", "platform": "generic",
             "model": "openai/gpt-4.1-mini-2025-04-14", "payload": {}}]
    spec, _sp, ledger = locked_experiment(dirpath, arms=arms, repetitions=1)
    (dirpath / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": [{"id": "t1", "prompt": "p"}]}), encoding="utf-8")
    native = {
        "verdi_log_version": 2,
        "telemetry": {"tokens_out": 90},
        "trajectory": [
            {"kind": "message", "agent": "planner", "relative_ts": 2.0, "detail": "the plan"},
            {"kind": "file_edit", "agent": "worker-1", "relative_ts": 9.0,
             "files_touched": ["solution.py"], "detail": "the code"},
        ],
        "reasoning": [
            {"content": "thought before planning", "agent": "planner", "relative_ts": 1.5, "turn": 0, "tokens": 30},
            {"content": "thought before editing", "agent": "worker-1", "relative_ts": 8.0, "turn": 1, "tokens": 60},
            {"content": "clock-only note", "relative_ts": 8.5},  # ts merge, no turn
            {"content": "ambient unlinked note"},
        ],
    }
    tasks = {"t1": Task(id="t1", prompt="p", fake_behavior={"native_log": native})}
    arms_by_name = {a.name: a for a in spec.arms}
    order = derive_schedule(spec.seed, enumerate_trials(["t1"], list(arms_by_name), 1))
    schedule(order, tasks=tasks, arms=arms_by_name, workspace_root=dirpath / "workspaces",
             ledger_path=ledger, ctx=fixed_ctx(experiment_id=dirpath.name),
             config=RunConfig(engine=FakeEngine()), cost_ceiling=spec.cost_ceiling.amount)
    return [ev["trial_record"]["trial_id"] for ev in find_events(ledger, "trial")]


# --- analyze scenarios (from test_eval6_analyze) -----------------------------------
FAST_STATS = dict(coverage_n_sim=40, n_boot=500)


def full_corpus() -> CorpusManifest:
    # AN-2: the fence binds the cited manifest to the pre-registered spec corpus
    # (public-mini@1.0.0) and to the tasks the experiment ran (task0..task4), so
    # the manifest must match both — the old terminal-bench@2.0.0 / one-task
    # manifest was the mismatch the shipped tests baked in.
    m = build_manifest(
        corpus_id="public-mini", semver="1.0.0", kind="public",
        tasks=[{"task_id": f"task{i}", "sha": "a" * 64} for i in range(5)],
    )
    # status is kept for provenance, but the FENCE now reads the ledgered
    # calibration_run events (CO-4), so official tests must seed_full_calibration.
    m.calibration.status = "full-run-validated"
    return m


def seed_full_calibration(ledger, ctx, *, corpus_id="public-mini", semver="1.0.0"):
    """Ledger a full-run-validated calibration_run for the corpus — the chain-
    anchored status the AN-2 fence binds to (not the mutable manifest JSON).

    Also seeds a passing selfcheck: EVAL-1-D008 makes a passed ledgered selfcheck
    an official-render prerequisite, so the official-ready fixtures need one.
    (A refusal test that trips an earlier fence check still refuses — the
    selfcheck check is the fence's last.)"""
    record_calibration_run(
        ledger, ctx, corpus_id=corpus_id, semver=semver, kind="full",
        run={"p": 0.5, "rho": 0.3, "n_tasks": 5}, status="full-run-validated",
    )


def seed_matching_selfcheck(ledger, ctx, spec, *, n_sim=40, n_boot=500):
    """Seed a passing, current selfcheck [EVAL-1-D008] whose validated CI method
    matches the method ``compute_findings`` will deploy.

    Runs the real selection (same ``spec.seed`` + params) so ``selected_method``
    aligns with the render's, then forces ``passed=True``. Call BEFORE
    ``compute_findings`` and after all data events — the findings are head-bound
    (``_assert_head_hash``), so nothing may be appended between compute and
    render, and the selfcheck event does not affect the delta selection. Pass the
    same ``n_sim``/``n_boot`` the test's ``compute_findings`` uses."""
    from harness.analyze.selfcheck import run_selfcheck
    from harness.ledger.events import record_selfcheck

    res = run_selfcheck(ledger, spec, n_sim=n_sim, n_boot=n_boot)
    res["passed"] = True  # official tests here exercise OTHER gates, not pass/fail
    record_selfcheck(ledger, ctx, **res)


def populate_paired_trials(ledger, ctx, *, control_pass, treatment_pass, tasks=5, reps=2,
                           control_tel=None, treatment_tel=None,
                           control_prov=None, treatment_prov=None):
    control_tel = control_tel if control_tel is not None else {"cost": 1.0, "wall_time_s": 10.0}
    treatment_tel = treatment_tel if treatment_tel is not None else {"cost": 1.1, "wall_time_s": 9.0}
    for i in range(tasks):
        for rep in range(reps):
            seed_trial_and_grade(
                ledger, ctx, trial_id=f"c-{i}-{rep}", task_id=f"task{i}", arm="control",
                repetition=rep, passed=control_pass(i), telemetry=control_tel,
                provenance=control_prov or {"image_digest": "digestC"},
            )
            seed_trial_and_grade(
                ledger, ctx, trial_id=f"t-{i}-{rep}", task_id=f"task{i}", arm="treatment",
                repetition=rep, passed=treatment_pass(i), telemetry=treatment_tel,
                provenance=treatment_prov or {"image_digest": "digestT"},
            )
