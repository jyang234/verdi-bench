"""``grade`` stage API [refactor 02 §3].

The importable library entry point behind ``bench grade`` [EVAL-5 §M5]: assert
the experiment lock and the task-content commitment, then grade every ungraded
trial deterministically, appending exactly one grade/cant_grade event each. The
typer verb (``harness/grade/cli.py``) is a thin shell that maps the enumerated
refusals below to exit codes and echoes the graded count.

Fractional scoring is taken from the **lock** (pre-registration), not runtime
config [AC-3].
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..errors import VerdiRefusal


def _grade_tasks_from_dicts(task_dicts: list, exp_dir: Path) -> dict:
    """Map the committed task dicts to grader tasks.

    The task sha is recomputed from content (not self-attested) and matches the
    lock commitment; the fake scripting fields are **not** read from the task
    source — they exist only for fixtures/the fake engine [GR-5].

    ``holdouts_dir`` is committed as a path RELATIVE TO THE EXPERIMENT DIR (the
    :class:`~harness.schema.tasks.TaskSpec` contract, and what the SDK inline
    holdout sugar / ``corpus.materialize`` emit). It is resolved here against
    ``exp_dir`` — mirroring ``run.control_reuse``'s ``experiment_dir /
    holdouts_dir`` — so the declared holdout is found regardless of the process
    CWD (the docker mount and the local-exec loader both otherwise resolved it
    against CWD). Resolution is a no-op for an absolute holdouts_dir (pathlib
    join semantics) and NEVER changes the committed bytes: ``task_content_sha``
    hashes the untouched ``t`` [refactor 03D seam fix].
    """
    from ..corpus.commit import task_content_sha
    from .types import GradeTask

    tasks = {}
    for t in task_dicts:
        holdouts_dir = t.get("holdouts_dir") or ""
        tasks[t["id"]] = GradeTask(
            id=t["id"],
            task_sha=task_content_sha(t),
            holdouts_dir=str(exp_dir / holdouts_dir) if holdouts_dir else "",
            plugin_ids=t.get("plugin_ids", []),
        )
    return tasks


class RetryTerminalError(VerdiRefusal, RuntimeError):
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

    # The common `bench grade` invocation passes no --retry-terminal; skip the two
    # full-ledger scans below when there is nothing to resolve.
    if not trial_ids:
        return {}

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


class GraderUnavailableRefusal(VerdiRefusal, RuntimeError):
    """The grader preflight failed: every pending trial was marked a *transient*
    ``cant_grade(grader_unavailable)`` and the batch refuses (exit 1) so a down
    daemon is not misclassified as terminal container_failure [7B-1/GR-8]."""


@dataclass(frozen=True)
class GradeOutcome:
    """What ``bench grade`` computed this pass, as an honest split [ux-friction AC-2].

    ``graded`` is the total number of trials this pass produced a grade-family
    event for, and equals ``scored`` + ``cant_grade`` — kept as the summary
    header (``graded N trial(s)``) so the terse all-scored line is unchanged. The
    split fields make a fail-closed pass legible instead of success-shaped (F6:
    ``graded 6 trial(s)`` when 0 were scored and all 6 were cant_grade):

    - ``scored`` — trials that got a real deterministic ``grade`` event.
    - ``cant_grade`` — trials that got a fail-closed ``cant_grade`` event, of any
      reason (the grade_trial reasons AND the pre-check unknown_task /
      artifacts_missing refusals).
    - ``cant_grade_reasons`` — ``{reason: count}`` for this pass, so the summary
      names WHY (e.g. ``holdout_results_missing ×6``); additive with a default so
      the vocabulary extends without touching this contract [AC-4].
    """

    graded: int
    scored: int = 0
    cant_grade: int = 0
    cant_grade_reasons: dict = field(default_factory=dict)


def grade_experiment(
    exp_dir: Path,
    *,
    runner: str = "docker",
    retry_terminal: list[str] | None = None,
    actor: str | None = None,
) -> GradeOutcome:
    """Grade every ungraded trial deterministically [EVAL-5 §M5].

    ``runner`` is ``docker`` (real network-less grading container), ``local``
    (the no-daemon fake/test path that reads a pre-placed ``holdout_results.json``),
    or ``local-exec`` (the no-daemon path that EXECUTES a declared holdout on the
    host — ADVISORY, refactor 05 §1). Raises the enumerated refusals the CLI maps
    to exit codes — ``TaskCommitmentError``/``RetryTerminalError``/
    ``ActorResolutionError`` (exit 2) and ``GraderUnavailableRefusal`` (exit 1,
    after marking pending trials transient) — and returns the graded count.
    """
    from ..corpus.commit import (
        assert_task_commitment,
        load_task_dicts,
    )
    from ..ledger import events
    from ..ledger.actor import resolve_actor
    from ..ledger.events import EventContext
    from ..ledger.identity import derive_experiment_id
    from ..ledger.query import find_events
    from ..plan.lock import assert_lock
    from .fence import GraderUnavailableError
    from .runners import (
        DockerGradeRunner,
        GradingContainer,
        LocalExecutingGradeRunner,
        LocalGradeRunner,
    )
    from .deterministic import (
        REASON_ARTIFACTS_MISSING,
        REASON_DAEMON,
        REASON_UNKNOWN_TASK,
        grade_trial,
    )

    exp_dir = Path(exp_dir)
    spec_path = exp_dir / "experiment.yaml"
    ledger_path = exp_dir / "ledger.ndjson"
    _lock = assert_lock(spec_path, ledger_path)
    lock_event, spec = _lock.event, _lock.spec  # PRA-M1: no second spec read

    task_dicts = load_task_dicts(exp_dir)
    # PL-7/D-6: refuse tasks swapped after the lock before grading anything.
    assert_task_commitment(
        lock_event, task_dicts,
        corpus_id=spec.corpus.id, semver=spec.corpus.version,
    )
    grade_tasks = _grade_tasks_from_dicts(task_dicts, exp_dir)
    already = _completed_trials(ledger_path)

    # D-P7-2: --retry-terminal re-attempts named trials whose grade was a
    # terminal cant_grade. Validate each (must be terminal, not already graded),
    # drop it from the skip set, and stamp the resulting event with override_of =
    # the overridden cant_grade's line hash.
    overrides = _resolve_terminal_overrides(ledger_path, list(retry_terminal or []))
    already = already - set(overrides)

    resolved_actor = resolve_actor(actor)
    # [ux-friction AC-1] one shared seam: resolve exp_dir before naming, so
    # `bench grade .` stamps the directory's real name, not '' (F1 on grade events).
    ctx = EventContext(experiment_id=derive_experiment_id(exp_dir), actor=resolved_actor)
    # The CLI validates ``runner`` against this exact set before any I/O.
    runner_impl = {
        "local": LocalGradeRunner,
        "local-exec": LocalExecutingGradeRunner,
    }.get(runner, DockerGradeRunner)()
    container = GradingContainer(runner=runner_impl)

    # 7B-1/GR-8: probe the grader once before the batch. A down docker daemon
    # makes `docker run` exit 1, which the per-trial path would misclassify as
    # terminal container_failure — permanently quarantining healthy trials. On
    # probe failure, mark every pending trial cant_grade(grader_unavailable) —
    # transient/regradeable — and refuse (exit 1) naming the daemon.
    try:
        container.preflight()
    except GraderUnavailableError as e:
        pending = [
            ev["trial_record"]["trial_id"]
            for ev in find_events(ledger_path, "trial")
            if ev["trial_record"]["trial_id"] not in already
        ]
        for tid in pending:
            events.record_cant_grade(
                ledger_path, ctx, trial_id=tid, reason=REASON_DAEMON,
                override_of=overrides.get(tid),
            )
        raise GraderUnavailableRefusal(
            f"grader unavailable: {e}; marked {len(pending)} trial(s) "
            "grader_unavailable (transient, regradeable)"
        ) from e

    # AC-2: tally the honest split as we go — every processed trial produces
    # exactly one grade-family event, either a real grade (scored) or a
    # fail-closed cant_grade (by reason). The counts drive a summary that
    # discloses a 0-scored pass instead of reading as N successes (F6).
    scored = 0
    cant_reasons: dict[str, int] = {}

    def _count_cant(reason: str) -> None:
        cant_reasons[reason] = cant_reasons.get(reason, 0) + 1

    for ev in find_events(ledger_path, "trial"):
        rec = ev["trial_record"]
        tid = rec["trial_id"]
        if tid in already:
            continue
        task = grade_tasks.get(rec["task_id"])
        if task is None:
            # GR-7: an unknown task is a fail-closed cant_grade, not a silent
            # skip that leaves the trial ungraded and unrecorded forever. Thread
            # override_of so a --retry-terminal re-attempt that lands here is
            # still linked to the terminal event it overrode [D-P7-2].
            events.record_cant_grade(ledger_path, ctx, trial_id=tid,
                                     reason=REASON_UNKNOWN_TASK,
                                     override_of=overrides.get(tid))
            _count_cant(REASON_UNKNOWN_TASK)
            continue
        if not rec.get("artifacts_path"):
            events.record_cant_grade(
                ledger_path, ctx, trial_id=tid, reason=REASON_ARTIFACTS_MISSING,
                override_of=overrides.get(tid),
            )
            _count_cant(REASON_ARTIFACTS_MISSING)
            continue
        workspace = Path(rec["artifacts_path"]).parent
        result = grade_trial(
            tid, task, workspace, ledger_path, ctx,
            container=container, fractional=spec.fractional_scoring,
            override_of=overrides.get(tid),
        )
        # read the split from the event grade_trial actually appended, so the
        # counts never drift from the ledger.
        if result.graded:
            scored += 1
        else:
            _count_cant(result.event["reason"])
    cant_grade = sum(cant_reasons.values())
    return GradeOutcome(
        graded=scored + cant_grade, scored=scored,
        cant_grade=cant_grade, cant_grade_reasons=cant_reasons,
    )
