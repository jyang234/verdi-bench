"""``bench grade`` [EVAL-5 §M5].

Asserts the experiment lock and the task-content commitment first, then grades
every ungraded trial in the ledger, appending exactly one grade/cant_grade event
each. ``--runner docker`` (default) runs the real network-less grading container;
``--runner local`` is the no-daemon fake/test path that reads a pre-placed
``holdout_results.json`` from the workspace.

Fractional scoring is taken from the **lock** (pre-registration), not runtime
config [AC-3].
"""

from __future__ import annotations

import getpass
from pathlib import Path

import typer

# import so the groundwork plugin self-registers
from .plugins import groundwork  # noqa: F401


def _grade_tasks_from_dicts(task_dicts: list) -> dict:
    """Map the committed task dicts to grader tasks.

    The task sha is recomputed from content (not self-attested) and matches the
    lock commitment; the fake scripting fields are **not** read from the task
    source — they exist only for fixtures/the fake engine [GR-5].
    """
    from ..corpus.commit import task_content_sha
    from .types import GradeTask

    tasks = {}
    for t in task_dicts:
        tasks[t["id"]] = GradeTask(
            id=t["id"],
            task_sha=task_content_sha(t),
            holdouts_dir=t.get("holdouts_dir", ""),
            plugin_ids=t.get("plugin_ids", []),
        )
    return tasks


class RetryTerminalError(RuntimeError):
    """A ``--retry-terminal`` target is not an overridable terminal cant_grade."""


def _resolve_terminal_overrides(ledger_path, trial_ids: list) -> dict:
    """Validate each ``--retry-terminal`` target and map it to the line hash of
    the terminal ``cant_grade`` it overrides [D-P7-2].

    Each named trial must have a **terminal** ``cant_grade`` and no ``grade``;
    otherwise refuse, naming what was found. The returned hash is the
    ledger-native reference stamped as ``override_of`` on the re-attempt's
    event."""
    from ..ledger import events
    from ..ledger.query import event_line_hash, find_events
    from .deterministic import TRANSIENT_CANT_GRADE

    graded = {e["trial_id"] for e in find_events(ledger_path, events.GRADE)}
    cant_by_trial: dict = {}
    for e in find_events(ledger_path, events.CANT_GRADE):
        cant_by_trial.setdefault(e["trial_id"], []).append(e)

    overrides: dict = {}
    for tid in trial_ids:
        if tid in graded:
            raise RetryTerminalError(
                f"--retry-terminal {tid!r}: trial already has a grade — override refused"
            )
        cants = cant_by_trial.get(tid, [])
        terminal = [e for e in cants if e["reason"] not in TRANSIENT_CANT_GRADE]
        if not terminal:
            found = (
                f"only transient cant_grade {[e['reason'] for e in cants]}"
                if cants
                else "no cant_grade at all"
            )
            raise RetryTerminalError(
                f"--retry-terminal {tid!r}: expected a terminal cant_grade to "
                f"override but found {found}"
            )
        overrides[tid] = event_line_hash(terminal[-1])
    return overrides


def _completed_trials(ledger_path) -> set:
    """Trials that must not be (re)graded: any with a grade, or a cant_grade
    whose reason is terminal. A transient cant_grade (e.g. a docker outage) is
    left regradeable [GR-11]. One ledger pass rather than two scans."""
    from ..ledger.query import iter_events
    from .deterministic import TRANSIENT_CANT_GRADE

    done: set = set()
    for e in iter_events(ledger_path):
        kind = e.get("event")
        if kind == "grade":
            done.add(e["trial_id"])
        elif kind == "cant_grade" and e["reason"] not in TRANSIENT_CANT_GRADE:
            done.add(e["trial_id"])
    return done


def register(app: typer.Typer) -> None:
    @app.command()
    def grade(
        experiment_dir: Path = typer.Argument(..., help="Directory with experiment.yaml"),
        runner: str = typer.Option(
            "docker", "--runner", help="docker (real container) | local (no-daemon fake/test)"
        ),
        retry_terminal: list[str] = typer.Option(
            [], "--retry-terminal",
            help="Trial id with a terminal cant_grade to re-attempt (repeatable); "
                 "the resulting event records override_of [D-P7-2]",
        ),
    ) -> None:
        """Grade every ungraded trial deterministically."""
        from ..corpus.commit import (
            TaskCommitmentError,
            assert_task_commitment,
            load_task_dicts,
        )
        from ..ledger import events
        from ..ledger.events import EventContext
        from ..ledger.query import find_events
        from ..plan.lock import assert_lock
        from ..schema.experiment import ExperimentSpec
        from .container import (
            DockerGradeRunner,
            GraderUnavailableError,
            GradingContainer,
            LocalGradeRunner,
        )
        from .deterministic import (
            REASON_ARTIFACTS_MISSING,
            REASON_DAEMON,
            REASON_UNKNOWN_TASK,
            grade_trial,
        )

        experiment_dir = Path(experiment_dir)
        spec_path = experiment_dir / "experiment.yaml"
        ledger_path = experiment_dir / "ledger.ndjson"
        lock_event = assert_lock(spec_path, ledger_path)
        spec = ExperimentSpec.from_yaml(spec_path)

        task_dicts = load_task_dicts(experiment_dir)
        # PL-7/D-6: refuse tasks swapped after the lock before grading anything.
        try:
            assert_task_commitment(
                lock_event, task_dicts,
                corpus_id=spec.corpus.id, semver=spec.corpus.version,
            )
        except TaskCommitmentError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)
        grade_tasks = _grade_tasks_from_dicts(task_dicts)
        already = _completed_trials(ledger_path)

        # D-P7-2: --retry-terminal re-attempts named trials whose grade was a
        # terminal cant_grade. Validate each (must be terminal, not already
        # graded), drop it from the skip set, and stamp the resulting event with
        # override_of = the overridden cant_grade's line hash.
        try:
            overrides = _resolve_terminal_overrides(ledger_path, retry_terminal)
        except RetryTerminalError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)
        already = already - set(overrides)

        try:
            actor = getpass.getuser()
        except Exception:
            actor = "unknown"
        ctx = EventContext(experiment_id=experiment_dir.name, actor=actor)
        runner_impl = LocalGradeRunner() if runner == "local" else DockerGradeRunner()
        container = GradingContainer(runner=runner_impl)

        # 7B-1/GR-8: probe the grader once before the batch. A down docker daemon
        # makes `docker run` exit 1, which the per-trial path would misclassify as
        # terminal container_failure — permanently quarantining healthy trials. On
        # probe failure, mark every pending trial cant_grade(grader_unavailable) —
        # transient/regradeable — and exit nonzero naming the daemon.
        try:
            container.preflight()
        except GraderUnavailableError as e:
            pending = [
                ev["trial_record"]["trial_id"]
                for ev in find_events(ledger_path, "trial")
                if ev["trial_record"]["trial_id"] not in already
            ]
            for tid in pending:
                events.record_cant_grade(ledger_path, ctx, trial_id=tid, reason=REASON_DAEMON)
            typer.echo(
                f"grader unavailable: {e}; marked {len(pending)} trial(s) "
                "grader_unavailable (transient, regradeable)",
                err=True,
            )
            raise typer.Exit(code=1)

        graded = 0
        for ev in find_events(ledger_path, "trial"):
            rec = ev["trial_record"]
            tid = rec["trial_id"]
            if tid in already:
                continue
            task = grade_tasks.get(rec["task_id"])
            if task is None:
                # GR-7: an unknown task is a fail-closed cant_grade, not a silent
                # skip that leaves the trial ungraded and unrecorded forever.
                events.record_cant_grade(ledger_path, ctx, trial_id=tid, reason=REASON_UNKNOWN_TASK)
                continue
            if not rec.get("artifacts_path"):
                events.record_cant_grade(
                    ledger_path, ctx, trial_id=tid, reason=REASON_ARTIFACTS_MISSING
                )
                continue
            workspace = Path(rec["artifacts_path"]).parent
            grade_trial(
                tid, task, workspace, ledger_path, ctx,
                container=container, fractional=spec.fractional_scoring,
                override_of=overrides.get(tid),
            )
            graded += 1
        typer.echo(f"graded {graded} trial(s)")
