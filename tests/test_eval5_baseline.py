"""EVAL-5 AC-2 — flake baseline quarantine + ledgering; scheduler honors it.

Quarantine is keyed by the task *version* ``(task_id, task_sha)`` [D-2, GR-10]:
the EVAL-5 spec quarantines "that task version", so a clean baseline for a new
version must not launder an old flaky version's quarantine.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.grade.baseline import DEFAULT_K, flake_baseline, load_quarantine
from harness.grade.container import (
    GradeRunner,
    GraderUnavailableError,
    GradingContainer,
    HoldoutRun,
)
from harness.grade.types import GradeTask
from harness.ledger.query import find_events
from harness.plan.interleave import Trial
from harness.run.interleave import QuarantinedTaskError, schedule
from harness.run.types import RunConfig, Task
from harness.schema.experiment import Arm
from tests.fixtures.builders import fixed_ctx
from tests.fixtures.grade_fakes import SeqGradeRunner, write_workspace

PASS = {"assertions": [{"id": "h1", "result": "pass"}]}
FAIL = {"assertions": [{"id": "h1", "result": "fail"}]}


def test_ac2_baseline_clean(tmp_path):
    ws = write_workspace(tmp_path)
    ledger = tmp_path / "l.ndjson"
    container = GradingContainer(runner=SeqGradeRunner([PASS] * 5))
    out = flake_baseline(GradeTask(id="t1", task_sha="sha1"), ledger, fixed_ctx(),
                         workspace=ws, container=container)
    assert out.verdict == "clean"
    ev = find_events(ledger, "flake_baseline")[0]
    assert ev["k"] == 5 and len(ev["results"]) == 5
    assert ev["task_sha"] == "sha1"


def test_ac2_baseline_quarantine(tmp_path):
    ws = write_workspace(tmp_path)
    ledger = tmp_path / "l.ndjson"
    # one of five runs fails ⇒ quarantined (zero tolerance)
    container = GradingContainer(runner=SeqGradeRunner([PASS, PASS, FAIL, PASS, PASS]))
    out = flake_baseline(GradeTask(id="t1", task_sha="sha1"), ledger, fixed_ctx(),
                         workspace=ws, container=container)
    assert out.verdict == "quarantined"
    # keyed by the task VERSION, not the bare id [D-2]
    assert load_quarantine(ledger) == {("t1", "sha1")}


def test_gr13_baseline_runs_carry_assertion_vector(tmp_path):
    """GR-13: every *completed* baseline run records its full assertion vector,
    not just {run, passed} — a quarantine verdict must be auditable from the
    ledger alone. A revert to {run, passed} fails this."""
    ws = write_workspace(tmp_path)
    ledger = tmp_path / "l.ndjson"
    container = GradingContainer(runner=SeqGradeRunner([PASS] * 5))
    flake_baseline(GradeTask(id="t1", task_sha="sha1"), ledger, fixed_ctx(),
                   workspace=ws, container=container)
    ev = find_events(ledger, "flake_baseline")[0]
    for r in ev["results"]:
        assert "assertions" in r, "completed baseline run dropped its assertion vector"
        assert r["assertions"] == [{"id": "h1", "source": "holdout_test",
                                    "result": "pass", "detail": None}]


def test_ac2_baseline_requires_k_at_least_one(tmp_path):
    """GR-10: k=0 must not ledger a 'clean' verdict from zero evidence."""
    ledger = tmp_path / "l.ndjson"
    ws = write_workspace(tmp_path)
    with pytest.raises(ValueError):
        flake_baseline(GradeTask(id="t1", task_sha="sha1"), ledger, fixed_ctx(),
                       workspace=ws, container=GradingContainer(runner=SeqGradeRunner([PASS])), k=0)
    assert find_events(ledger, "flake_baseline") == []


def test_ac2_transient_grader_outage_is_not_flake(tmp_path):
    """GR-8: a transient grader-unavailable outage is NOT flake evidence — it
    fails loud (no verdict, no quarantine) so an infra hiccup can't quarantine a
    healthy task version."""
    class DeadRunner(GradeRunner):
        grader_name = "dead"
        runs_plugins_in_container = False
        grades_in_place = False
        def preflight(self) -> None:
            """No-op: the outage surfaces from run_holdouts, not preflight."""
        def run_holdouts(self, cmd, workspace, holdouts_dir, nonce=None):
            raise GraderUnavailableError("docker daemon down")

    ledger = tmp_path / "l.ndjson"
    ws = write_workspace(tmp_path)
    with pytest.raises(GraderUnavailableError):
        flake_baseline(GradeTask(id="t1", task_sha="sha1"), ledger, fixed_ctx(),
                       workspace=ws, container=GradingContainer(runner=DeadRunner()))
    assert find_events(ledger, "flake_baseline") == []  # no verdict ledgered
    assert load_quarantine(ledger) == set()  # NOT quarantined by the outage


def test_ac2_baseline_runs_are_independent_copies(tmp_path):
    """GR-9: each of the k runs grades a fresh copy of the unmodified workspace,
    so run i's output can't be re-scored as run i+1 (locks the Phase-1 fresh-copy
    guarantee)."""
    seen: list[Path] = []

    class RecordingRunner(GradeRunner):
        grader_name = "rec"
        runs_plugins_in_container = False
        grades_in_place = False
        def preflight(self) -> None:
            """No daemon to probe."""
        def run_holdouts(self, cmd, workspace, holdouts_dir, nonce=None):
            seen.append(Path(workspace))
            return HoldoutRun(PASS)

    ledger = tmp_path / "l.ndjson"
    ws = write_workspace(tmp_path)
    flake_baseline(GradeTask(id="t1", task_sha="s"), ledger, fixed_ctx(),
                   workspace=ws, container=GradingContainer(runner=RecordingRunner()), k=3)
    assert len(seen) == 3 and len(set(seen)) == 3  # three distinct fresh copies
    assert ws not in seen  # never the original (evidence untouched)


def test_ac2_quarantined_version_unschedulable(tmp_path):
    """The scheduler refuses a quarantined (task_id, task_sha); a different-sha
    version of the same task id is allowed [D-2]."""
    from harness.run.engines.fake import FakeEngine

    ledger = tmp_path / "l.ndjson"
    ws = write_workspace(tmp_path)
    flake_baseline(GradeTask(id="t1", task_sha="bad"), ledger, fixed_ctx(),
                   workspace=ws, container=GradingContainer(runner=SeqGradeRunner([FAIL] * 5)))
    quarantined = load_quarantine(ledger)  # {("t1", "bad")}

    arms = {"A": Arm(name="A", platform="claude_code", model="anthropic/claude-3-5-sonnet-20241022")}
    order = [Trial(task_id="t1", arm="A", repetition=0)]
    common = dict(
        arms=arms, workspace_root=tmp_path / "run", ctx=fixed_ctx(),
        config=RunConfig(engine=FakeEngine()), cost_ceiling=100.0,
        quarantined_tasks=quarantined,
    )
    # the quarantined version is refused
    with pytest.raises(QuarantinedTaskError):
        schedule(order, tasks={"t1": Task(id="t1", prompt="p", task_sha="bad")},
                 ledger_path=tmp_path / "run1.ndjson", **common)
    # a DIFFERENT version of the same task id runs fine
    res = schedule(order, tasks={"t1": Task(id="t1", prompt="p", task_sha="good")},
                   ledger_path=tmp_path / "run2.ndjson", **common)
    assert len(res.records) == 1


def test_ac2_quarantine_is_version_scoped(tmp_path):
    """D-2/GR-10: a clean baseline for a NEW task version does not clear the OLD
    flaky version's quarantine — the old version stays quarantined."""
    ledger = tmp_path / "l.ndjson"
    ws = write_workspace(tmp_path)
    flake_baseline(GradeTask(id="t1", task_sha="old"), ledger, fixed_ctx(),
                   workspace=ws, container=GradingContainer(runner=SeqGradeRunner([FAIL] * 5)))
    flake_baseline(GradeTask(id="t1", task_sha="new"), ledger, fixed_ctx(),
                   workspace=ws, container=GradingContainer(runner=SeqGradeRunner([PASS] * 5)))
    # only the old version is quarantined; the new clean version is schedulable
    assert load_quarantine(ledger) == {("t1", "old")}


def test_ac2_same_version_clean_rebaseline_clears(tmp_path):
    """A genuinely fixed flake — the SAME version re-baselined clean — clears that
    version's quarantine (latest-event-wins within a version)."""
    ledger = tmp_path / "l.ndjson"
    ws = write_workspace(tmp_path)
    flake_baseline(GradeTask(id="t1", task_sha="v1"), ledger, fixed_ctx(),
                   workspace=ws, container=GradingContainer(runner=SeqGradeRunner([FAIL] * 5)))
    assert load_quarantine(ledger) == {("t1", "v1")}
    flake_baseline(GradeTask(id="t1", task_sha="v1"), ledger, fixed_ctx(),
                   workspace=ws, container=GradingContainer(runner=SeqGradeRunner([PASS] * 5)))
    assert load_quarantine(ledger) == set()  # same version, now clean


def test_ac2_quarantine_refused_preflight_no_partial_run(tmp_path):
    """Review #6: a quarantined version is refused PRE-FLIGHT, before any trial
    runs — even when a clean task is scheduled ahead of it (no partial execution)."""
    from harness.run.engines.fake import FakeEngine

    arms = {"A": Arm(name="A", platform="claude_code", model="anthropic/claude-3-5-sonnet-20241022")}
    tasks = {
        "clean": Task(id="clean", prompt="p", task_sha="ok", fake_behavior={"native_log": {}}),
        "bad": Task(id="bad", prompt="p", task_sha="flaky"),
    }
    order = [Trial(task_id="clean", arm="A", repetition=0), Trial(task_id="bad", arm="A", repetition=0)]
    ledger = tmp_path / "run.ndjson"
    with pytest.raises(QuarantinedTaskError):
        schedule(order, tasks=tasks, arms=arms, workspace_root=tmp_path / "ws", ledger_path=ledger,
                 ctx=fixed_ctx(), config=RunConfig(engine=FakeEngine()), cost_ceiling=100.0,
                 quarantined_tasks={("bad", "flaky")})
    assert find_events(ledger, "trial") == []  # nothing ran — pre-flight halt


def test_ac2_quarantine_without_task_sha_fails_loud(tmp_path):
    """Review #6/fail-loudly: quarantine cannot be enforced on a Task with no
    task_sha (version id), so it raises rather than silently matching open."""
    from harness.run.engines.fake import FakeEngine

    arms = {"A": Arm(name="A", platform="claude_code", model="anthropic/claude-3-5-sonnet-20241022")}
    tasks = {"t1": Task(id="t1", prompt="p")}  # no task_sha
    order = [Trial(task_id="t1", arm="A", repetition=0)]
    with pytest.raises(QuarantinedTaskError):
        schedule(order, tasks=tasks, arms=arms, workspace_root=tmp_path / "ws",
                 ledger_path=tmp_path / "run.ndjson", ctx=fixed_ctx(),
                 config=RunConfig(engine=FakeEngine()), cost_ceiling=100.0,
                 quarantined_tasks={("t1", "some-sha")})


def test_h2_operating_characteristic_stays_documented():
    """F-H2: the disclosed miss probability of zero-tolerance k-run baselining
    ((1-p)^k, ≈90% at p=2% with the default k=5) must stay true of DEFAULT_K —
    if the default changes, the docs in baseline.py / deep-dive.md change with it."""
    assert DEFAULT_K == 5
    assert abs((1 - 0.02) ** DEFAULT_K - 0.9039) < 5e-4


# --- grader tier on the ledgered event [human-approved 2026-07-07] -----------
def test_baseline_event_records_docker_grader_tier(tmp_path):
    """The TRUSTED docker tier stamps ``grader="docker"`` on the flake_baseline
    event, taken from the runner actually used. Only the docker boundary (the
    ``DockerClient``) is faked — the real ``DockerGradeRunner`` code path runs and
    scores its own fenced stdout, so the recorded tier is not caller-supplied."""
    from harness.grade.container import DockerGradeRunner
    from harness.grade.fence import NONCE_ENV, holdout_fence

    class _FakeDocker:
        """Fakes ONLY the docker boundary: ``docker version`` answers, and a grade
        run echoes a correctly-nonced PASSING holdout fence (via the real fence
        helper) so the real runner parses + scores it as a genuine clean run."""

        def run(self, argv, *, timeout_s=None, env=None, text=True):
            import subprocess

            if argv[:2] == ["docker", "version"]:
                return subprocess.CompletedProcess(argv, 0, "ok", "")
            nonce = next(
                (t.split("=", 1)[1] for t in argv if t.startswith(f"{NONCE_ENV}=")),
                None,
            )
            begin, end = holdout_fence(nonce)
            body = json.dumps({"assertions": [{"id": "h1", "result": "pass"}]})
            return subprocess.CompletedProcess(argv, 0, f"{begin}{body}{end}", "")

    ws = write_workspace(tmp_path)
    ledger = tmp_path / "l.ndjson"
    container = GradingContainer(runner=DockerGradeRunner(docker=_FakeDocker()))
    out = flake_baseline(GradeTask(id="t1", task_sha="sha1"), ledger, fixed_ctx(),
                         workspace=ws, container=container)
    assert out.verdict == "clean"
    (ev,) = find_events(ledger, "flake_baseline")
    assert ev["grader"] == "docker"  # the trusted tier, from the runner used


def test_baseline_event_records_local_exec_grader_tier(tmp_path):
    """The no-daemon local-exec tier stamps its ADVISORY grader name on the event,
    so an ADVISORY baseline is self-recorded — never laundered as docker. No
    docker boundary is involved; the runner executes the declared holdout."""
    from harness.grade.container import GradingContainer as GC
    from harness.grade.holdouts import CommandHoldout
    from harness.grade.runners import LocalExecutingGradeRunner

    ws = write_workspace(tmp_path)
    holdouts = tmp_path / "holdouts"
    holdouts.mkdir()
    CommandHoldout(argv=["true"], id="gate").materialize(holdouts)  # exit 0 ⇒ pass
    ledger = tmp_path / "l.ndjson"
    container = GC(runner=LocalExecutingGradeRunner())
    out = flake_baseline(GradeTask(id="t1", task_sha="sha1", holdouts_dir=str(holdouts)),
                         ledger, fixed_ctx(), workspace=ws, container=container, k=1)
    assert out.verdict == "clean"
    (ev,) = find_events(ledger, "flake_baseline")
    assert ev["grader"] == "local-exec" != "docker"  # ADVISORY, self-recorded
