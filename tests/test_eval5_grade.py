"""EVAL-5 AC-3 / AC-5 — grading, scoring policy, fail-closed."""

from __future__ import annotations

from harness.grade.container import GradingContainer
from harness.grade.deterministic import (
    REASON_CONTAINER,
    REASON_MALFORMED,
    REASON_WORKSPACE_MISSING,
    grade_trial,
)
from harness.grade.types import GradeTask
from harness.ledger.query import find_events
from tests.fixtures.builders import fixed_ctx
from tests.fixtures.grade_fakes import ScriptedGradeRunner, write_workspace
from tests.fixtures.grading import write_holdout_results


def _task(**kw):
    return GradeTask(id="t1", task_sha="sha-abc", **kw)


def _grade(tmp_path, output, *, fractional=False, task=None, container_error=False):
    ws = write_workspace(tmp_path)
    container = GradingContainer(
        runner=ScriptedGradeRunner(output, container_error=container_error)
    )
    ledger = tmp_path / "l.ndjson"
    outcome = grade_trial(
        "trial-1", task or _task(), ws, ledger, fixed_ctx(),
        container=container, fractional=fractional,
    )
    return outcome, ledger


def test_ac3_per_assertion_recorded(tmp_path):
    out, ledger = _grade(tmp_path, {"assertions": [
        {"id": "h1", "result": "pass"},
        {"id": "h2", "result": "fail", "detail": "expected 3"},
    ]})
    grade = find_events(ledger, "grade")[0]
    assert len(grade["assertions"]) == 2
    ids = {a["id"]: a for a in grade["assertions"]}
    assert ids["h2"]["result"] == "fail"
    assert ids["h2"]["detail"] == "expected 3"


def test_ac3_binary_default(tmp_path):
    out, ledger = _grade(tmp_path, {"assertions": [
        {"id": "h1", "result": "pass"}, {"id": "h2", "result": "pass"},
    ]})
    assert find_events(ledger, "grade")[0]["binary_score"] is True


def test_ac3_binary_fails_on_any_holdout_fail(tmp_path):
    out, ledger = _grade(tmp_path, {"assertions": [
        {"id": "h1", "result": "pass"}, {"id": "h2", "result": "fail"},
    ]})
    assert find_events(ledger, "grade")[0]["binary_score"] is False


def test_ac3_abstain_does_not_count_as_pass(tmp_path):
    out, ledger = _grade(tmp_path, {"assertions": [
        {"id": "h1", "result": "pass"}, {"id": "h2", "result": "abstain"},
    ]})
    # abstain is not pass ⇒ binary is False
    assert find_events(ledger, "grade")[0]["binary_score"] is False


def test_ac3_fractional_requires_prereg(tmp_path):
    # without pre-registration, fractional field is absent
    out, ledger = _grade(tmp_path, {"assertions": [{"id": "h1", "result": "pass"}]},
                         fractional=False)
    grade = find_events(ledger, "grade")[0]
    assert "fractional_score" not in grade


def test_ac3_fractional_present_when_prereg(tmp_path):
    out, ledger = _grade(tmp_path, {"assertions": [
        {"id": "h1", "result": "pass"}, {"id": "h2", "result": "fail"},
    ]}, fractional=True)
    grade = find_events(ledger, "grade")[0]
    assert grade["fractional_score"] == 0.5


def test_ac5_fail_closed_container(tmp_path):
    out, ledger = _grade(tmp_path, None, container_error=True)
    assert out.graded is False
    cg = find_events(ledger, "cant_grade")
    assert len(cg) == 1 and cg[0]["reason"] == REASON_CONTAINER
    assert find_events(ledger, "grade") == []


def test_ac5_fail_closed_malformed(tmp_path):
    out, ledger = _grade(tmp_path, {"garbage": True})  # no 'assertions'
    cg = find_events(ledger, "cant_grade")
    assert len(cg) == 1 and cg[0]["reason"] == REASON_MALFORMED


def test_ac5_fail_closed_workspace_missing(tmp_path):
    container = GradingContainer(runner=ScriptedGradeRunner({"assertions": []}))
    ledger = tmp_path / "l.ndjson"
    out = grade_trial("trial-1", _task(), tmp_path / "nonexistent", ledger, fixed_ctx(),
                      container=container)
    assert out.graded is False
    assert find_events(ledger, "cant_grade")[0]["reason"] == REASON_WORKSPACE_MISSING


def test_ac5_exactly_one_event(tmp_path):
    out, ledger = _grade(tmp_path, {"assertions": [{"id": "h1", "result": "pass"}]})
    from harness.ledger.query import read_events

    assert len(read_events(ledger)) == 1


def test_ac5_fail_closed_non_iterable_assertions(tmp_path):
    # regression: {"assertions": 5} must fail closed, not crash with TypeError
    out, ledger = _grade(tmp_path, {"assertions": 5})
    assert out.graded is False
    assert find_events(ledger, "cant_grade")[0]["reason"] == REASON_MALFORMED
    from harness.ledger.query import read_events

    assert len(read_events(ledger)) == 1


def test_ac3_empty_holdout_not_vacuous_pass(tmp_path):
    # regression: an empty holdout set must NOT score binary True (would inflate
    # holdout_pass_rate for a trial that verified nothing)
    out, ledger = _grade(tmp_path, {"assertions": []})
    assert find_events(ledger, "grade")[0]["binary_score"] is False


def test_grade_loader_ignores_fake_scripting(tmp_path):
    """GR-5: the production task loader does not read fake_holdout_output /
    fake_plugin_output from the task source — they cannot script a grade."""
    from harness.grade.cli import _grade_tasks_from_dicts

    tasks = _grade_tasks_from_dicts(
        [{"id": "t1", "holdouts_dir": "h", "fake_holdout_output": {"assertions": []},
          "fake_plugin_output": {"p": 1}}]
    )
    gt = tasks["t1"]
    assert gt.fake_holdout_output is None
    assert gt.fake_plugin_output == {}
    # sha is recomputed from content, not any self-attested field
    from harness.corpus.commit import task_content_sha

    assert gt.task_sha == task_content_sha(
        {"id": "t1", "holdouts_dir": "h", "fake_holdout_output": {"assertions": []},
         "fake_plugin_output": {"p": 1}}
    )


def test_grade_hostile_workspace_dir_results_fails_closed(tmp_path):
    """SEC5: an agent that makes holdout_results.json a DIRECTORY must not crash
    grade_trial (old: unlink -> IsADirectoryError -> aborts the whole batch). The
    fresh-copy prep removes the bad entry and grading proceeds; one event lands."""
    ws = write_workspace(tmp_path)
    (ws / "holdout_results.json").mkdir()  # hostile: a directory, not a file
    runner = _FreshCopyRunner({"assertions": [{"id": "h1", "result": "pass"}]})
    container = GradingContainer(runner=runner)
    ledger = tmp_path / "l.ndjson"
    outcome = grade_trial("trial-1", _task(), ws, ledger, fixed_ctx(), container=container)
    # no crash, exactly one event, and the bad entry was removed in the copy
    assert outcome.graded is True
    assert runner.saw_stale is False
    from harness.ledger.query import read_events

    assert len(read_events(ledger)) == 1


def test_completed_trials_allows_transient_regrade(tmp_path):
    """GR-11: only a transient cant_grade (grader could not be RUN) is
    regradeable. A grade, a terminal cant_grade, OR a grader that ran and FAILED
    (container_failure) is not — a deterministically broken grader must not be
    re-attempted on every `bench grade`."""
    from harness.grade.cli import _completed_trials
    from harness.ledger import events

    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    events.record_grade(ledger, ctx, trial_id="graded", task_sha="s", assertions=[],
                        binary_score=True)
    events.record_cant_grade(ledger, ctx, trial_id="transient", reason="grader_unavailable")
    events.record_cant_grade(ledger, ctx, trial_id="terminal", reason="unknown_task")
    events.record_cant_grade(ledger, ctx, trial_id="ran_and_failed", reason="container_failure")

    done = _completed_trials(ledger)
    assert "graded" in done
    assert "terminal" in done
    assert "ran_and_failed" in done  # grader ran and exited nonzero: terminal
    assert "transient" not in done  # only "could not run the grader" regrades


def test_grade_batch_daemon_down_marks_trials_transient(tmp_path, monkeypatch):
    """7B-1/GR-8: a down daemon at batch start marks every pending trial
    cant_grade(grader_unavailable) — transient/regradeable — not terminal
    container_failure, and the verb exits nonzero naming the daemon."""
    import yaml
    from typer.testing import CliRunner

    from harness.adapters.base import Outcome, Provenance, Telemetry, TrialRecord
    from harness.cli import app
    from harness.grade.cli import _completed_trials
    from harness.grade.container import DockerGradeRunner, GraderUnavailableError
    from harness.ledger.events import record_trial
    from tests.fixtures.builders import fixed_ctx, write_experiment_yaml

    expdir = tmp_path / "exp"
    expdir.mkdir()
    write_experiment_yaml(expdir / "experiment.yaml")
    (expdir / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": [{"id": "t1", "prompt": "p", "task_class": "refactor"}]}),
        encoding="utf-8",
    )
    ledger = expdir / "ledger.ndjson"
    runner = CliRunner()
    assert runner.invoke(
        app, ["plan", str(expdir / "experiment.yaml"), "--ledger", str(ledger)]
    ).exit_code == 0

    ctx = fixed_ctx(experiment_id="exp")
    for tid in ("tr-a", "tr-b"):
        rec = TrialRecord.assemble(
            trial_id=tid, task_id="t1", arm="control", repetition=0,
            outcome=Outcome.completed, telemetry=Telemetry(), provenance=Provenance(),
            artifacts_path=f"/tmp/{tid}/artifacts",
        )
        record_trial(ledger, ctx, trial_record=rec.model_dump(mode="json"))

    def down(self):
        raise GraderUnavailableError("docker daemon unavailable (docker version exit 1)")

    monkeypatch.setattr(DockerGradeRunner, "preflight", down)

    r = runner.invoke(app, ["grade", str(expdir)])
    assert r.exit_code == 1, r.output
    assert "grader unavailable" in r.output.lower() or "grader unavailable" in (r.stderr or "").lower()

    cant = find_events(ledger, "cant_grade")
    assert {c["trial_id"] for c in cant} == {"tr-a", "tr-b"}
    assert all(c["reason"] == "grader_unavailable" for c in cant)
    assert find_events(ledger, "grade") == []
    # transient ⇒ still regradeable (no override needed)
    assert _completed_trials(ledger) == set()


def test_grade_trial_stamps_override_of_on_grade(tmp_path):
    """7B-2/D-P7-2: a --retry-terminal re-attempt stamps override_of on the grade."""
    outcome, ledger = _grade(
        tmp_path, {"assertions": [{"id": "h1", "result": "pass"}]},
    )
    # regrade path: grade_trial called with override_of
    ws = write_workspace(tmp_path, name="ws2")
    container = GradingContainer(
        runner=ScriptedGradeRunner({"assertions": [{"id": "h1", "result": "pass"}]})
    )
    ledger2 = tmp_path / "l2.ndjson"
    grade_trial("t1", _task(), ws, ledger2, fixed_ctx(),
                container=container, override_of="cafe" * 16)
    g = find_events(ledger2, "grade")[0]
    assert g["override_of"] == "cafe" * 16


def test_grade_trial_stamps_override_of_on_cant_grade(tmp_path):
    """A failed re-attempt still records the override on the resulting cant_grade,
    so every override attempt is visible (D-P7-2)."""
    ws = write_workspace(tmp_path)
    container = GradingContainer(runner=ScriptedGradeRunner(container_error=True))
    ledger = tmp_path / "l.ndjson"
    grade_trial("t1", _task(), ws, ledger, fixed_ctx(),
                container=container, override_of="beef" * 16)
    c = find_events(ledger, "cant_grade")[0]
    assert c["reason"] == REASON_CONTAINER
    assert c["override_of"] == "beef" * 16


def test_resolve_terminal_overrides_refusals_and_hash(tmp_path):
    """--retry-terminal targets are validated: a graded, a transient-only, and a
    missing trial are all refused; a terminal cant_grade resolves to its ledger
    line hash (the ledger-native override reference)."""
    import pytest

    from harness.grade.cli import RetryTerminalError, _resolve_terminal_overrides
    from harness.ledger import events
    from harness.ledger.query import ledger_head_hash

    ledger = tmp_path / "l.ndjson"
    ctx = fixed_ctx()
    events.record_grade(ledger, ctx, trial_id="graded", task_sha="s", assertions=[],
                        binary_score=True)
    events.record_cant_grade(ledger, ctx, trial_id="transient", reason="grader_unavailable")
    events.record_cant_grade(ledger, ctx, trial_id="terminal", reason="container_failure")

    with pytest.raises(RetryTerminalError):
        _resolve_terminal_overrides(ledger, ["graded"])
    with pytest.raises(RetryTerminalError):
        _resolve_terminal_overrides(ledger, ["transient"])
    with pytest.raises(RetryTerminalError):
        _resolve_terminal_overrides(ledger, ["nope"])

    ov = _resolve_terminal_overrides(ledger, ["terminal"])
    # the terminal cant_grade is the last event ⇒ its line hash is the head hash
    assert ov == {"terminal": ledger_head_hash(ledger)}


def test_retry_terminal_stamps_override_of_on_unknown_task_reattempt(tmp_path):
    """7B-2 fix: a --retry-terminal re-attempt that lands on the CLI's
    unknown_task pre-check still records override_of, so the override stays
    linked to the terminal cant_grade it overrode (the pre-check paths were
    dropping it)."""
    import yaml
    from typer.testing import CliRunner

    from harness.adapters.base import Outcome, Provenance, Telemetry, TrialRecord
    from harness.cli import app
    from harness.ledger import events
    from harness.ledger.events import record_trial
    from tests.fixtures.builders import fixed_ctx, write_experiment_yaml

    expdir = tmp_path / "exp"
    expdir.mkdir()
    write_experiment_yaml(expdir / "experiment.yaml")
    (expdir / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": [{"id": "t1", "prompt": "p", "task_class": "refactor"}]}),
        encoding="utf-8",
    )
    ledger = expdir / "ledger.ndjson"
    runner = CliRunner()
    assert runner.invoke(
        app, ["plan", str(expdir / "experiment.yaml"), "--ledger", str(ledger)]
    ).exit_code == 0

    ctx = fixed_ctx(experiment_id="exp")
    # a trial whose task id is NOT in tasks.yaml → its grade is a terminal
    # cant_grade(unknown_task); seed both the trial and that terminal event.
    rec = TrialRecord.assemble(
        trial_id="tr-ghost", task_id="ghost", arm="control", repetition=0,
        outcome=Outcome.completed, telemetry=Telemetry(), provenance=Provenance(),
        artifacts_path="/tmp/tr-ghost/artifacts",
    )
    record_trial(ledger, ctx, trial_record=rec.model_dump(mode="json"))
    events.record_cant_grade(ledger, ctx, trial_id="tr-ghost", reason="unknown_task")

    r = runner.invoke(app, ["grade", str(expdir), "--runner", "local",
                            "--retry-terminal", "tr-ghost"])
    assert r.exit_code == 0, r.output
    cants = [c for c in find_events(ledger, "cant_grade") if c["trial_id"] == "tr-ghost"]
    assert len(cants) == 2  # the original terminal + the re-attempt
    assert "override_of" not in cants[0]  # original had none
    assert len(cants[1]["override_of"]) == 64  # the re-attempt is linked


class _FreshCopyRunner:
    """A runner that (like DockerGradeRunner) grades a fresh workspace copy and
    writes its *own* holdout output — records what it was handed. It does not set
    ``grades_in_place``, so it gets the safe copy path by default."""

    def __init__(self, output):
        self.output = output
        self.saw_stale = None
        self.copy_path = None

    def run_holdouts(self, cmd, workspace, holdouts_dir, nonce=None):
        from pathlib import Path

        from harness.grade.container import HoldoutRun

        self.copy_path = Path(workspace)
        self.saw_stale = (Path(workspace) / "holdout_results.json").exists()
        return HoldoutRun(self.output)


def test_grade_ignores_forged_results_and_protects_evidence(tmp_path):
    """GR-1/GR-3: a runner that grades a fresh copy must not see an
    agent-written holdout_results.json (deleted in the copy), must grade a copy
    (not the original), and must not mutate the ledgered trial evidence.
    """
    import json

    ws = write_workspace(tmp_path)
    # the subject agent forges an all-pass results file in its own workspace
    forged = write_holdout_results(ws, True)

    # the real grader output disagrees (a failure)
    runner = _FreshCopyRunner({"assertions": [{"id": "h1", "result": "fail"}]})
    container = GradingContainer(runner=runner)
    ledger = tmp_path / "l.ndjson"
    grade_trial("trial-1", _task(), ws, ledger, fixed_ctx(), container=container)

    # graded a *copy*, not the original workspace
    assert runner.copy_path != ws
    # the forged file was deleted before grading — the grader saw a clean copy
    assert runner.saw_stale is False
    # the recorded grade reflects the grader's output, not the forged all-pass
    assert find_events(ledger, "grade")[0]["binary_score"] is False
    # the original workspace (ledgered evidence) is untouched
    assert json.loads((ws / "holdout_results.json").read_text()) == forged


def test_m_i3_unknown_runner_refused_not_silently_docker(tmp_path):
    """F-M-I3: a typo'd --runner (e.g. 'dcoker') must exit 2 naming the valid
    set — previously anything but exactly 'local' silently selected docker."""
    from typer.testing import CliRunner

    from harness.cli import app

    expdir = tmp_path / "exp"
    expdir.mkdir()
    r = CliRunner().invoke(app, ["grade", str(expdir), "--runner", "dcoker"])
    assert r.exit_code != 0
    assert "docker or local" in (r.output + (r.stderr or ""))
