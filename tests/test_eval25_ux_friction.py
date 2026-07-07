"""First-run UX friction fixes [docs/design/specs/eval25.spec.md].

The AC-mapped tests for the ux-friction story (EVAL-25), consolidated and
renamed test_ac<N>_* at spec promotion.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from harness.ledger import events
from harness.ledger.query import find_events
from harness.plan.api import plan_experiment
from harness.plan.lock import AlreadyLockedError, lock_experiment
from harness.status.aggregate import compute_status
from tests.fixtures.builders import (
    ctx_for,
    fixed_ctx,
    locked_experiment,
    valid_experiment_dict,
    write_experiment_yaml,
)

# small sim params keep the plan/lock power check fast in tests
_TWO_TASKS = {"tasks": [{"id": "t1", "prompt": "p"}, {"id": "t2", "prompt": "p"}]}
_FAST_LOCK = dict(n_sim=8, n_boot=40, deltas=[0.2, 0.4])


# --- AC-1: plan derives experiment_id from the RESOLVED experiment path -------
def _scaffold_experiment(directory: Path) -> Path:
    """A locked-ready experiment dir: a valid spec, its rubric, and two tasks."""
    directory.mkdir(parents=True, exist_ok=True)
    write_experiment_yaml(directory / "experiment.yaml")
    (directory / "tasks.yaml").write_text(yaml.safe_dump(_TWO_TASKS), encoding="utf-8")
    return directory / "experiment.yaml"


def test_ac1_experiment_id_path_independent(tmp_path, monkeypatch):
    """[ux-friction AC-1] The three invocation forms bench init itself prints
    (bare relative from inside the dir, ./-relative, absolute) yield a
    byte-identical, non-empty provenance.experiment_id on the lock event — the
    experiment directory's real name — regardless of cwd. Today the bare/./
    forms bake experiment_id='' into the permanent chain (F1)."""
    expdir = tmp_path / "my-experiment"
    _scaffold_experiment(expdir)

    def id_for(experiment_arg, ledger_name: str) -> str:
        ledger = expdir / ledger_name  # absolute: cwd changes never move the ledger
        plan_experiment(experiment_arg, ledger, actor="tester")
        lock = find_events(ledger, events.EXPERIMENT_LOCKED)[0]
        return lock["provenance"]["experiment_id"]

    monkeypatch.chdir(expdir)
    bare = id_for("experiment.yaml", "l_bare.ndjson")       # the cd-in form (F1)
    dot = id_for("./experiment.yaml", "l_dot.ndjson")       # ./-relative
    absolute = id_for(str(expdir / "experiment.yaml"), "l_abs.ndjson")

    assert bare == dot == absolute == "my-experiment"
    assert bare  # never the empty id F1 bakes into the chain


def test_ac1_empty_resolved_name_refused(tmp_path):
    """[ux-friction AC-1] A resolved parent with an empty name (a spec at the
    filesystem root) refuses with a typed error naming the offending path, rather
    than ever ledgering experiment_id=''. The refusal fires before any file read,
    so nothing is written."""
    from harness.ledger.identity import ExperimentIdResolutionError

    ledger = tmp_path / "ledger.ndjson"
    root_spec = Path("/experiment.yaml")  # resolves to root; parent has no name
    with pytest.raises(ExperimentIdResolutionError) as exc:
        plan_experiment(root_spec, ledger, actor="tester")
    # plan now derives the id through the shared seam from the spec's PARENT
    # directory, so the refusal names that resolved directory (the filesystem
    # root here — the thing that actually has no name), not the spec file.
    assert str(root_spec.parent.resolve()) in str(exc.value)  # names the offending dir
    assert not ledger.exists()  # refused before genesis: zero events appended


# --- AC-6: a successful lock removes its <ledger>.planlock flock file ----------
def test_ac6_planlock_removed_on_success(tmp_path):
    """[ux-friction AC-6] After a green plan the experiment dir carries only the
    user files and the ledger — the stray <ledger>.planlock flock file a
    successful lock leaves today (F5) is removed on success."""
    spec = write_experiment_yaml(tmp_path / "experiment.yaml")
    ledger = tmp_path / "ledger.ndjson"
    lock_experiment(spec, ledger, ctx=ctx_for(tmp_path), **_FAST_LOCK)

    planlock = Path(str(ledger) + ".planlock")
    assert not planlock.exists()  # cleaned up on success
    assert len(find_events(ledger, events.EXPERIMENT_LOCKED)) == 1  # lock still happened


def test_ac6_refused_lock_no_planlock_resurrect(tmp_path):
    """[ux-friction AC-6] Cleanup is safe: a second lock attempt is still refused
    (AlreadyLockedError) by the outer single-lock check — which fires before the
    flock guard is ever created — so it neither succeeds nor resurrects a stray
    planlock file."""
    spec = write_experiment_yaml(tmp_path / "experiment.yaml")
    ledger = tmp_path / "ledger.ndjson"
    lock_experiment(spec, ledger, ctx=ctx_for(tmp_path), **_FAST_LOCK)
    planlock = Path(str(ledger) + ".planlock")
    assert not planlock.exists()

    with pytest.raises(AlreadyLockedError):
        lock_experiment(spec, ledger, ctx=ctx_for(tmp_path), **_FAST_LOCK)

    assert not planlock.exists()  # the refused attempt left no stray guard file
    assert len(find_events(ledger, events.EXPERIMENT_LOCKED)) == 1  # still exactly one


# --- AC-5: bench status titles the experiment from the locked ledger ----------
def test_ac5_status_header_from_ledger(tmp_path, monkeypatch):
    """[ux-friction AC-5] bench status titles the experiment from the locked
    ledger's experiment_id, falling back to the directory name only when no lock
    exists. Today the header is path-derived (F8): `bench status .` renders a
    blank name and the absolute-path form renders the dir name — never the id the
    ledger actually carries, and the two invocations disagree."""
    expdir = tmp_path / "my-experiment"
    expdir.mkdir()

    # pre-lock: no lock event ⇒ the directory-name fallback holds
    assert compute_status(expdir)["experiment_id"] == "my-experiment"

    # lock with an experiment_id deliberately DISTINCT from the dir name, so a
    # header echoing the typed path is unmistakably wrong
    locked_experiment(expdir, ctx=fixed_ctx(experiment_id="ledger-name"))

    abs_snap = compute_status(expdir)
    monkeypatch.chdir(expdir)
    dot_snap = compute_status(Path("."))  # `bench status .`: dir name is "" (blank today)

    assert abs_snap["experiment_id"] == "ledger-name"  # the ledger id, not "my-experiment"
    assert dot_snap["experiment_id"] == "ledger-name"  # blank ('') today
    assert dot_snap["experiment_id"] == abs_snap["experiment_id"]  # same header both ways


# --- AC-1 broadening: one shared resolved-path seam for experiment_id ----------
def test_ac1_derive_seam_path_independent(
    tmp_path, monkeypatch
):
    """[ux-friction AC-1] The shared seam resolves before naming, so `.`, a bare
    relative name, and the absolute path to the same directory all yield the
    identical non-empty id — the experiment directory's real name — regardless of
    cwd. This is the one derivation every ledgering stage now routes through."""
    from harness.ledger.identity import derive_experiment_id

    expdir = tmp_path / "my-experiment"
    expdir.mkdir()
    monkeypatch.chdir(expdir)
    assert derive_experiment_id(Path(".")) == "my-experiment"   # the cd-in form
    assert derive_experiment_id(Path("./")) == "my-experiment"  # ./-relative
    assert derive_experiment_id(expdir) == "my-experiment"      # absolute


def test_ac1_derive_seam_empty_name_refused():
    """[ux-friction AC-1] A path that resolves to a nameless directory (the
    filesystem root) refuses with a typed error naming the offending path, rather
    than ever returning '' for a ledger to stamp."""
    from harness.ledger.identity import ExperimentIdResolutionError, derive_experiment_id

    root = Path("/")
    with pytest.raises(ExperimentIdResolutionError) as exc:
        derive_experiment_id(root)
    assert str(root.resolve()) in str(exc.value)  # names the offending path


def test_ac1_event_context_id_resolved(tmp_path, monkeypatch):
    """[ux-friction AC-1] cli_common.event_context — the shared ctx builder the
    forensics/contamination verbs use — stamps the RESOLVED directory name, so
    `bench <verb> .` no longer ledgers experiment_id='' (today Path('.').name)."""
    from harness.cli_common import event_context

    expdir = tmp_path / "my-experiment"
    expdir.mkdir()
    monkeypatch.chdir(expdir)
    ctx = event_context(Path("."), "tester")
    assert ctx.experiment_id == "my-experiment"  # '' today (unresolved Path('.').name)
    assert ctx.actor == "tester"


def _built_planned_experiment(dirpath: Path, name: str):
    """Build + lock a 2-task fake-engine experiment; return its ExperimentWorkspace."""
    from harness.sdk import Experiment, Task

    exp = (
        Experiment(name, seed=1234, cost_ceiling_usd=10.0)
        .arm("treatment", model="openai/gpt-4o-2024-08-06", platform="codex")
        .arm("control", model="anthropic/claude-haiku-4-5-20251001", platform="claude_code")
        .judge("fake/deterministic-2026-01-01")
        .task(Task("t_add", prompt="Write solution.py defining add(a, b).",
                   fake_behavior={"native_log": {"total_cost_usd": 0.01}}))
        .task(Task("t_pal", prompt="Write solution.py defining is_palindrome(s).",
                   fake_behavior={"native_log": {"total_cost_usd": 0.01}}))
    )
    ws = exp.write(dirpath)
    ws.plan(actor="tester")
    return ws


def test_ac1_run_trial_events_resolved_id(tmp_path, monkeypatch):
    """[ux-friction AC-1, broadening] From inside a locked experiment dir, a
    fake-engine run invoked the way the CLI invokes it — with the bare-relative
    Path('.') — stamps every trial event with the directory's real name. Today
    run/api.py derives exp_dir.name on the UNRESOLVED '.', baking
    experiment_id='' into the permanent chain: the F1 defect, now on trial
    events (RED today: '' != 'run-exp')."""
    from harness.run.api import run_experiment

    ws = _built_planned_experiment(tmp_path / "run-exp", "run-exp")
    ledger = ws.ledger  # absolute: chdir never moves it
    monkeypatch.chdir(ws.dir)
    run_experiment(Path("."), engine="fake", actor="tester")

    trials = find_events(ledger, events.TRIAL)
    assert trials  # the run produced trial events
    assert all(ev["provenance"]["experiment_id"] == "run-exp" for ev in trials)


def test_ac1_grade_events_resolved_id(tmp_path, monkeypatch):
    """[ux-friction AC-1, broadening] A grade pass invoked the way the CLI
    invokes it (Path('.')) stamps its events with the directory's real name. No
    holdout injection is needed: with --runner local and no holdout_results.json
    every trial lands a terminal cant_grade, whose provenance carries
    experiment_id — so the honest assertion is on the cant_grade events' id.
    Today grade/api.py derives exp_dir.name on the UNRESOLVED '.' (RED: '')."""
    from harness.grade.api import grade_experiment
    from harness.run.api import run_experiment

    ws = _built_planned_experiment(tmp_path / "grade-exp", "grade-exp")
    run_experiment(ws.dir, engine="fake", actor="tester")  # absolute: correct trials
    ledger = ws.ledger
    monkeypatch.chdir(ws.dir)
    grade_experiment(Path("."), runner="local", actor="tester")  # no injection

    cant = find_events(ledger, events.CANT_GRADE)
    assert cant  # every trial → terminal cant_grade (no holdout_results.json)
    assert all(ev["provenance"]["experiment_id"] == "grade-exp" for ev in cant)


# --- AC-4: the local runner's missing-results outcome gets an honest reason ----
def test_ac4_local_missing_results_reason(tmp_path):
    """[ux-friction AC-4] `--runner local` with no holdout_results.json is a
    missing INPUT on a path with no container — not a grader that ran and failed.
    It must ledger the terminal reason `holdout_results_missing`, its own honest
    vocabulary, rather than `container_failure` (F7: a container failure on a path
    with no container). RED today: the LocalGradeRunner raises the generic
    GradingContainerError, which grade_trial classifies as container_failure."""
    from harness.grade.deterministic import grade_trial
    from harness.grade.runners import GradingContainer, LocalGradeRunner
    from harness.grade.types import GradeTask

    ws = tmp_path / "ws"  # a real workspace, but with NO pre-placed results file
    ws.mkdir()
    (ws / "solution.txt").write_text("agent output", encoding="utf-8")
    ledger = tmp_path / "ledger.ndjson"
    outcome = grade_trial(
        "trial-1", GradeTask(id="t", task_sha="sha"), ws, ledger, ctx_for(tmp_path),
        container=GradingContainer(runner=LocalGradeRunner()),
    )

    assert outcome.graded is False
    cant = find_events(ledger, events.CANT_GRADE)
    assert len(cant) == 1
    # RED today: emits "container_failure" — a container failure on a path with no
    # container (F7). AC-4: the honest terminal reason for a missing grade INPUT.
    assert cant[0]["reason"] == "holdout_results_missing"

    # the new constant IS that literal, and is TERMINAL (re-running without the
    # file won't change it; --retry-terminal is the recovery once it is placed).
    from harness.grade.deterministic import REASON_RESULTS_MISSING, TRANSIENT_CANT_GRADE

    assert REASON_RESULTS_MISSING == "holdout_results_missing"
    assert REASON_RESULTS_MISSING not in TRANSIENT_CANT_GRADE


def test_ac4_docker_fence_still_container_failure(tmp_path):
    """[ux-friction AC-4] The docker-path fence semantics are untouched: a grader
    that ran and emitted zero fenced blocks is a real container failure and stays
    `container_failure`. AC-4 renames only the LocalGradeRunner missing-INPUT case,
    never the fence's zero-fences → container_failure path."""
    from harness.grade.fence import GradingContainerError, parse_fenced_stdout

    with pytest.raises(GradingContainerError):
        parse_fenced_stdout("no fence here", 0)  # zero fenced blocks: real failure


def test_ac4_unknown_reason_renders_forward_compat(tmp_path):
    """[ux-friction AC-4] The cant_grade `reason` is additive vocabulary in an
    existing string field: an unrecognized reason flows through every reader
    verbatim and breaks nothing, so the vocabulary stays forward-extensible
    without a reader change. Pinned with a synthetic FUTURE reason this version
    has never seen, through the status aggregate + drill-down (serve renders the
    same dict), an analyze render, and the retry-terminal classifier."""
    from harness.adapters.base import Outcome, Provenance, Telemetry, TrialRecord
    from harness.analyze.selfcheck import selfcheck_status
    from harness.grade.api import _completed_trials, _resolve_terminal_overrides
    from harness.ledger.events import record_trial
    from harness.status.trial import trial_detail

    expdir = tmp_path / "exp"
    _spec, _spec_path, ledger = locked_experiment(expdir)
    ctx = ctx_for(expdir)
    future = "reason_from_the_future"  # a reason string no reader enumerates

    tid = "trial-fc"
    rec = TrialRecord.assemble(
        trial_id=tid, task_id="t1", arm="control", repetition=0,
        outcome=Outcome.completed, telemetry=Telemetry(), provenance=Provenance(),
        artifacts_path=f"/tmp/{tid}/artifacts",
    )
    record_trial(ledger, ctx, trial_record=rec.model_dump(mode="json"))
    events.record_cant_grade(ledger, ctx, trial_id=tid, reason=future)

    # status aggregate: an unknown reason is bucketed terminal (the safe default —
    # a new reason blocks regrade unless explicitly made transient) and rendered
    # as a count, never a crash.
    status = compute_status(expdir)
    assert status["chain"]["ok"]  # the appended events keep the chain verifying
    assert status["stages"]["grade"]["cant_grade_terminal"] == 1

    # serve/status drill-down: the reason string is echoed VERBATIM (LedgerView →
    # trial_detail, the same dict the serve layer renders).
    detail = trial_detail(expdir, tid)
    assert [c["reason"] for c in detail["grade"]["cant_grades"]] == [future]

    # analyze render: a ledger carrying the unknown reason still classifies without
    # crashing (selfcheck reads cant_grade by KIND, never by reason string).
    assert selfcheck_status(ledger) == "missing"

    # grade + retry-terminal classifiers: an unknown reason is terminal, so the
    # trial is "done" (not re-graded every pass) yet remains --retry-terminal
    # overridable once the operator fixes the input.
    assert tid in _completed_trials(ledger)
    assert _resolve_terminal_overrides(ledger, [tid])  # resolves to a line hash


# --- AC-2: bench grade's summary discloses the scored/cant_grade split ---------
def test_ac2_grade_outcome_reports_split(tmp_path, monkeypatch):
    """[ux-friction AC-2] The F6 reproduction: a locked two-task experiment, a fake
    run, then `grade --runner local` with no injection lands every trial in
    cant_grade(holdout_results_missing) — 0 scored. GradeOutcome must report the
    split (scored / cant_grade / per-reason counts) and the summary line must
    disclose it, rather than the success-shaped bare `graded N trial(s)` that
    reads as N successes when zero were scored (F6: the ledger is honest, stdout
    is not)."""
    from harness.grade.api import grade_experiment
    from harness.grade.cli import _grade_summary_line
    from harness.run.api import run_experiment

    ws = _built_planned_experiment(tmp_path / "f6-exp", "f6-exp")
    run_experiment(ws.dir, engine="fake", actor="tester")
    outcome = grade_experiment(ws.dir, runner="local", actor="tester")  # no injection

    n = len(find_events(ws.ledger, events.CANT_GRADE))
    assert n >= 2  # the two-task suite yields multiple trials, all unscorable
    # the outcome carries the split, not just a lump `graded`
    assert outcome.scored == 0
    assert outcome.cant_grade == n
    assert outcome.graded == n  # graded = scored + cant_grade (total processed)
    assert outcome.cant_grade_reasons == {"holdout_results_missing": n}

    # the summary DISCLOSES it (per-reason counts + the pointer to bench status),
    # built by a pure function so the string is pinned without spawning a CLI.
    assert _grade_summary_line(outcome) == (
        f"graded {n} trial(s): 0 scored, {n} cant_grade "
        f"(holdout_results_missing ×{n}) — see bench status"
    )


def test_ac2_grade_cli_discloses_split_exits_zero(tmp_path):
    """[ux-friction AC-2 + D2] The `bench grade` verb emits the disclosing line and
    still EXITS 0 when every trial landed cant_grade — a fail-closed outcome is a
    completed, ledgered operation, not a command failure (D2). RED today: stdout is
    the bare `graded N trial(s)` with no split disclosed."""
    from typer.testing import CliRunner

    from harness.cli import app
    from harness.run.api import run_experiment

    ws = _built_planned_experiment(tmp_path / "cli-exp", "cli-exp")
    run_experiment(ws.dir, engine="fake", actor="tester")

    r = CliRunner().invoke(app, ["grade", str(ws.dir), "--runner", "local"])
    assert r.exit_code == 0, r.output  # D2: cant_grade is not a command failure
    n = len(find_events(ws.ledger, events.CANT_GRADE))
    out = r.output + (r.stderr or "")
    assert f"0 scored, {n} cant_grade" in out  # the split is disclosed (absent today)
    assert "holdout_results_missing" in out and "see bench status" in out


def test_ac2_grade_summary_terse_all_scored():
    """[ux-friction AC-2] When every trial scored, the summary stays terse and
    quiet — no cant_grade clause — so the honest split appears only when there is
    friction to disclose."""
    from harness.grade.api import GradeOutcome
    from harness.grade.cli import _grade_summary_line

    outcome = GradeOutcome(graded=12, scored=12, cant_grade=0)
    assert _grade_summary_line(outcome) == "graded 12 trial(s)"


def test_ac2_grade_summary_lists_reasons_sorted():
    """[ux-friction AC-2] A mixed-reason pass lists every reason with its count in
    a deterministic (sorted) order — no dict-ordering assumption leaks into the
    rendered line."""
    from harness.grade.api import GradeOutcome
    from harness.grade.cli import _grade_summary_line

    outcome = GradeOutcome(
        graded=10, scored=7, cant_grade=3,
        cant_grade_reasons={"unknown_task": 1, "holdout_results_missing": 2},
    )
    assert _grade_summary_line(outcome) == (
        "graded 10 trial(s): 7 scored, 3 cant_grade "
        "(holdout_results_missing ×2, unknown_task ×1) — see bench status"
    )


# --- AC-3: bench judge's summary discloses verdicts vs cant_judge --------------
_REAL_JUDGE = {  # a real-provider judge id (date-versioned, non-alias)
    "model": "anthropic/claude-haiku-4-5-20251001",
    "rubric": "rubric.md",
    "orders": "both",
    "temperature": 0,
}


def _graded_pairs_real_provider_judge(expdir: Path, n_tasks: int) -> Path:
    """Plan a locked 2-arm experiment with a REAL-provider judge and seed
    control+treatment graded trials for ``n_tasks`` tasks — a judgeable
    comparison set (one comparison per task)."""
    from typer.testing import CliRunner

    from harness.cli import app

    expdir.mkdir(parents=True, exist_ok=True)
    tasks = [{"id": f"t{i}", "prompt": "solve it", "task_class": "refactor"}
             for i in range(n_tasks)]
    write_experiment_yaml(expdir / "experiment.yaml", judge=dict(_REAL_JUDGE))
    (expdir / "rubric.md").write_text("Judge on correctness.", encoding="utf-8")
    (expdir / "tasks.yaml").write_text(yaml.safe_dump({"tasks": tasks}), encoding="utf-8")
    ledger = expdir / "ledger.ndjson"
    r = CliRunner().invoke(
        app, ["plan", str(expdir / "experiment.yaml"), "--ledger", str(ledger)]
    )
    assert r.exit_code == 0, r.output
    from tests.fixtures.builders import seed_trial_and_grade

    ctx = ctx_for(expdir)
    for i in range(n_tasks):
        seed_trial_and_grade(ledger, ctx, trial_id=f"tr-{i}-c", task_id=f"t{i}",
                             arm="control", passed=True)
        seed_trial_and_grade(ledger, ctx, trial_id=f"tr-{i}-t", task_id=f"t{i}",
                             arm="treatment", passed=False)
    return ledger


def test_ac3_judge_cli_discloses_cant_judge_exits_zero(tmp_path, monkeypatch):
    """[ux-friction AC-3 + D2] The keyless real-provider reproduction: with the
    provider key ABSENT every comparison lands CANT_JUDGE(provider_error), and
    the `bench judge` summary must disclose the split rather than the
    success-shaped bare `judged N comparison(s)` (F6). Exit stays 0 (D2).

    No network is touched: the harness never auto-loads .env, and the absent key
    makes require_key (judge/providers/_http.py) raise ProviderError as the
    request headers are built — before urllib is ever reached — so the failure is
    the missing key, not a live call."""
    from typer.testing import CliRunner

    from harness.cli import app
    from harness.judge.providers._http import require_key
    from harness.judge.providers.base import ProviderError

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # keyless first-timer
    # the provider fails on the MISSING KEY before any request (pinned at the seam)
    with pytest.raises(ProviderError):
        require_key("ANTHROPIC_API_KEY")

    expdir = tmp_path / "judge-exp"
    ledger = _graded_pairs_real_provider_judge(expdir, n_tasks=3)

    r = CliRunner().invoke(app, ["judge", str(expdir)])
    assert r.exit_code == 0, r.output  # D2: all-cant_judge is not a command failure

    verdicts = find_events(ledger, events.JUDGE_VERDICT)
    assert len(verdicts) == 3
    assert all(v["verdict"]["winner"] == "CANT_JUDGE" for v in verdicts)
    assert all(v["verdict"]["reason"] == "provider_error" for v in verdicts)

    out = r.output + (r.stderr or "")
    # RED today: the bare "judged 3 comparison(s)" with no split disclosed.
    assert "judged 3 comparison(s): 0 verdicts, 3 cant_judge (provider_error ×3)" in out


def test_ac3_judge_summary_discloses_split():
    """[ux-friction AC-3] The disclosing line names the substantive/cant_judge
    split with per-reason counts, visibly consistent with grade's line."""
    from harness.judge.api import JudgeOutcome
    from harness.judge.cli import _judge_summary_line

    outcome = JudgeOutcome(
        judged=3, stopped_ceiling=False, accumulated=0, ceiling=None,
        n_reused=0, rubric_warning=False, calibration={},
        verdicts=0, cant_judge=3, cant_judge_reasons={"provider_error": 3},
    )
    assert _judge_summary_line(outcome) == (
        "judged 3 comparison(s): 0 verdicts, 3 cant_judge (provider_error ×3)"
    )


def test_ac3_judge_summary_terse_all_substantive():
    """[ux-friction AC-3] When every comparison produced a substantive verdict the
    line stays terse and quiet — the split appears only when cant_judge > 0."""
    from harness.judge.api import JudgeOutcome
    from harness.judge.cli import _judge_summary_line

    outcome = JudgeOutcome(
        judged=5, stopped_ceiling=False, accumulated=0, ceiling=None,
        n_reused=0, rubric_warning=False, calibration={},
        verdicts=5, cant_judge=0,
    )
    assert _judge_summary_line(outcome) == "judged 5 comparison(s)"


# --- AC-8: judge.panel is refused when set (v2 breadcrumb, D3) -----------------
def test_ac8_panel_set_refused_named_error():
    """[ux-friction AC-8] judge.panel is a v2 placeholder read by nothing (F9):
    setting it silently changes the spec hash and does nothing else — the exact
    silent no-op extra='forbid' exists to prevent. It must be refused at spec
    load with a typed SpecError that names the field and the fix, before a lock
    is ever written (before spend). RED today: the panel-set spec VALIDATES,
    returning a spec whose judge.panel is the ignored dict."""
    from harness.schema.errors import JudgePanelUnsupportedError, SpecError
    from harness.schema.experiment import ExperimentSpec

    spec_dict = valid_experiment_dict()
    spec_dict["judge"] = {**spec_dict["judge"], "panel": {"size": 3}}
    with pytest.raises(JudgePanelUnsupportedError) as exc:
        ExperimentSpec.from_dict(spec_dict)
    # the schema boundary surfaces the TYPED error, never a raw pydantic
    # ValidationError (mirrors how AliasJudgeIdError flows through from_dict).
    assert isinstance(exc.value, SpecError)
    msg = str(exc.value)
    assert "judge.panel" in msg  # names the field
    assert "v2" in msg  # says it is a v2 placeholder not implemented in v1
    assert "remove judge.panel from the spec" in msg  # names the fix


def test_ac8_panel_absent_unchanged():
    """[ux-friction AC-8] The refusal is scoped to a SET panel: the field stays in
    the schema as the v2 breadcrumb (D3) and default None is untouched. A fixture
    spec that omits panel validates unchanged, and an explicit panel=None (the
    default, spelled out) stays valid too — no green path regresses."""
    from harness.schema.experiment import ExperimentSpec

    # the canonical starter/template fixture omits panel: still valid, panel None
    spec = ExperimentSpec.from_dict(valid_experiment_dict())
    assert spec.judge.panel is None
    # explicit None is the default written out — still valid, still None
    d = valid_experiment_dict()
    d["judge"] = {**d["judge"], "panel": None}
    assert ExperimentSpec.from_dict(d).judge.panel is None


# --- AC-9: plan warns (never gates) when the suite can't support a decision ----
_WARNING_LINE = (
    "a decision needs ≥2 task clusters [F-H7]; "
    "this design will render findings but no decision"
)


def _plan_via_cli(expdir: Path, n_tasks: int):
    """Scaffold experiment.yaml + its rubric + a tasks.yaml carrying ``n_tasks``
    tasks, then lock through the real `bench plan` path (plan_experiment loads
    tasks.yaml via corpus.commit.load_task_dicts). Returns (CliRunner result,
    ledger path)."""
    from typer.testing import CliRunner

    from harness.cli import app

    expdir.mkdir(parents=True, exist_ok=True)
    write_experiment_yaml(expdir / "experiment.yaml")  # also materializes the rubric
    tasks = [{"id": f"t{i}", "prompt": "solve it"} for i in range(n_tasks)]
    (expdir / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": tasks}), encoding="utf-8"
    )
    ledger = expdir / "ledger.ndjson"
    r = CliRunner().invoke(
        app, ["plan", str(expdir / "experiment.yaml"), "--ledger", str(ledger)]
    )
    return r, ledger


def test_ac9_single_task_warns_and_flags(tmp_path):
    """[ux-friction AC-9 + D4] A one-task suite can never yield a paired decision
    (the bootstrap clusters on tasks — F3/F-H7), and today the user learns this
    only at analyze time. plan now WARNS at lock: an
    `insufficient_tasks_for_decision` string joins the lock event's existing mde
    flags vector (beside power_gate_skipped) and the CLI echoes one line naming
    the consequence. It is never a gate — the lock succeeds and exit stays 0 (D4).
    RED today: the one-task plan locks with NO such flag and NO warning line."""
    r, ledger = _plan_via_cli(tmp_path / "one-task", n_tasks=1)

    assert r.exit_code == 0, r.output  # D4: a warning is never a gate
    locked = find_events(ledger, events.EXPERIMENT_LOCKED)
    assert len(locked) == 1  # the lock still happened
    assert "insufficient_tasks_for_decision" in locked[0]["mde"]["flags"]
    out = r.output + (r.stderr or "")
    assert _WARNING_LINE in out  # disclosed at the moment the design is created


def test_ac9_two_tasks_no_warning_no_flag(tmp_path):
    """[ux-friction AC-9] Two or more task clusters CAN support a decision, so the
    warning and its flag are both absent — the disclosure appears only when there
    is friction to disclose. (A single-task exploratory design stays legitimate
    and lockable per D4; this pins that the two-task design locks silently.)"""
    r, ledger = _plan_via_cli(tmp_path / "two-task", n_tasks=2)

    assert r.exit_code == 0, r.output
    locked = find_events(ledger, events.EXPERIMENT_LOCKED)
    assert len(locked) == 1
    assert "insufficient_tasks_for_decision" not in locked[0]["mde"]["flags"]
    out = r.output + (r.stderr or "")
    assert "a decision needs" not in out  # no warning line, not even paraphrased


# --- AC-10: bench init's closing message teaches the two hard-won lessons -------
def test_ac10_init_next_steps_message(tmp_path):
    """[ux-friction AC-10] bench init's closing message teaches the two things a
    first-timer otherwise learns the hard way: the keyless fake-path next steps
    (run → inject each trial's holdout_results.json → grade --runner local) and
    `bench status <dir>` as the always-safe read-only triage view. RED today: the
    message names NEITHER — only the scaffold list and the plan hint."""
    from typer.testing import CliRunner

    from harness.cli import app

    r = CliRunner().invoke(app, ["init", str(tmp_path / "myexp")])
    assert r.exit_code == 0, r.output
    out = r.output

    # the existing lines are kept: the scaffold list, and the plan hint whose
    # cd-in form is safe since Batch A's identity seam (so its shape is unchanged).
    assert "experiment.yaml, tasks.yaml" in out
    assert "bench plan experiment.yaml --ledger ledger.ndjson" in out

    # the fake-path next steps: run, the operator injection step, grade --runner local
    assert "bench run" in out
    assert "holdout_results.json" in out  # the file the arm-blind fake engine needs
    assert "inject_holdout_results" in out  # the named public SDK one-liner
    assert "§1.5" in out  # its documented home in the usage guide
    assert "bench grade" in out and "--runner local" in out

    # bench status as the standing read-only triage view
    assert "bench status" in out

    # the whole message stays compact — under the ~6-line budget (AC-10 vc)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) <= 6, out


# --- AC-7: a contender-first, keyless, two-task scaffold that reaches a decision -
def test_ac7_template_contender_first_fake_judge():
    """[ux-friction AC-7 + D1-A] The single-source starter template now (a) declares
    the CONTENDER arm FIRST, so the scaffolded `delta_holdout_pass_rate > 0` rule
    pre-registers 'treatment beats control' (the paired delta is arms[0] − arms[1])
    instead of F2's backwards 'control beats treatment'; (b) ships the keyless
    fake/deterministic-2026-01-01 judge (D1-A) so the default path runs end to end
    with no API key; and (c) starter-tasks.yaml ships TWO placeholder tasks so the
    scaffold can reach a decision (n_tasks=1 renders 'no decision possible', F3).
    RED today: arms[0] is control, the judge is google/gemini-1.5-pro-002, and the
    tasks template ships a single task."""
    from harness.sdk import starter_spec_text, starter_tasks_text

    spec = yaml.safe_load(starter_spec_text())
    assert [a["name"] for a in spec["arms"]] == ["treatment", "control"]  # contender first (F2)
    assert spec["judge"]["model"] == "fake/deterministic-2026-01-01"  # keyless judge (D1-A)

    tasks = yaml.safe_load(starter_tasks_text())["tasks"]
    assert len(tasks) == 2  # two clusters: enough to support a decision (F3)
    assert len({t["id"] for t in tasks}) == 2  # unique ids


def test_ac7_scaffold_zero_edit_pipeline_met(tmp_path):
    """[ux-friction AC-7] The north star: `bench init` scaffolds the starter files,
    and WITHOUT editing a single file — no judge swap, no second task, no API key,
    no Docker — the keyless fake pipeline (plan → run → inject treatment-passes →
    grade --runner local → judge → analyze --exploratory) reaches a MET decision on
    a verifying chain.

    The scaffold declares the contender first, so injecting treatment-passes drives
    the paired delta (arms[0] − arms[1]) to +1.0, and the `> 0` rule reads MET. The
    experiment is scaffolded via the exact bytes `bench init` writes (the init CLI,
    not a hand-rolled spec), then driven through the public ExperimentWorkspace SDK.

    RED today: the scaffold's google/gemini-1.5-pro-002 judge lands every comparison
    as a keyless CANT_JUDGE (F4), its single task renders 'no decision possible' (F3),
    and control-first makes the injected treatment win read as delta -1.0000 (F2) —
    never a MET verdict on the untouched scaffold."""
    from typer.testing import CliRunner

    from harness.cli import app
    from harness.sdk import ExperimentWorkspace

    expdir = tmp_path / "quickstart"
    r = CliRunner().invoke(app, ["init", str(expdir)])  # the exact bytes bench init writes
    assert r.exit_code == 0, r.output

    ws = ExperimentWorkspace(expdir)
    ws.plan(actor="tester")
    ws.run(engine="fake")  # 2 arms × 2 tasks × 3 reps = 12 trials
    ws.inject_holdout_results(lambda arm, task: arm == "treatment")  # contender wins
    ws.grade(runner="local")
    judged = ws.judge()
    findings = ws.analyze(exploratory=True)

    md = findings.read_text(encoding="utf-8")
    assert "mean paired delta: 1.0000" in md  # RED today: -1.0000 (control-first, F2)
    # the pre-registered rule reads MET (RED today: 'no decision possible', F3)
    assert "Decision rule `delta_holdout_pass_rate > 0` ⇒ MET." in md

    # the fake judge ran keyless: every comparison a substantive verdict, zero CANT_JUDGE
    assert judged.cant_judge == 0  # RED today: keyless google → all CANT_JUDGE (F4)
    verdicts = find_events(ws.ledger, events.JUDGE_VERDICT)
    assert verdicts and all(v["verdict"]["winner"] != "CANT_JUDGE" for v in verdicts)

    # two tasks: the lock carries no insufficient-tasks warning flag (RED: present, F3)
    lock = find_events(ws.ledger, events.EXPERIMENT_LOCKED)[0]
    assert "insufficient_tasks_for_decision" not in lock["mde"]["flags"]

    assert ws.verify_chain().chain_ok  # the whole zero-edit run verifies end to end
