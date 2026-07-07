"""First-run UX friction fixes [docs/design/ux-friction.spec.md].

Test functions are named descriptively here and are consolidated/renamed to
test_ac<N>_* in tests/test_eval25_*.py at spec promotion.
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
from tests.fixtures.builders import ctx_for, fixed_ctx, locked_experiment, write_experiment_yaml

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


def test_plan_experiment_id_is_path_independent(tmp_path, monkeypatch):
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


def test_plan_refuses_empty_resolved_experiment_name(tmp_path):
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
def test_successful_lock_removes_planlock_file(tmp_path):
    """[ux-friction AC-6] After a green plan the experiment dir carries only the
    user files and the ledger — the stray <ledger>.planlock flock file a
    successful lock leaves today (F5) is removed on success."""
    spec = write_experiment_yaml(tmp_path / "experiment.yaml")
    ledger = tmp_path / "ledger.ndjson"
    lock_experiment(spec, ledger, ctx=ctx_for(tmp_path), **_FAST_LOCK)

    planlock = Path(str(ledger) + ".planlock")
    assert not planlock.exists()  # cleaned up on success
    assert len(find_events(ledger, events.EXPERIMENT_LOCKED)) == 1  # lock still happened


def test_refused_second_lock_after_cleanup_does_not_resurrect_planlock(tmp_path):
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
def test_status_header_prefers_ledger_experiment_id(tmp_path, monkeypatch):
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
def test_derive_experiment_id_resolves_relative_paths_to_directory_name(
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


def test_derive_experiment_id_refuses_empty_resolved_name():
    """[ux-friction AC-1] A path that resolves to a nameless directory (the
    filesystem root) refuses with a typed error naming the offending path, rather
    than ever returning '' for a ledger to stamp."""
    from harness.ledger.identity import ExperimentIdResolutionError, derive_experiment_id

    root = Path("/")
    with pytest.raises(ExperimentIdResolutionError) as exc:
        derive_experiment_id(root)
    assert str(root.resolve()) in str(exc.value)  # names the offending path


def test_event_context_experiment_id_is_resolved(tmp_path, monkeypatch):
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


def test_run_trial_events_carry_resolved_experiment_id(tmp_path, monkeypatch):
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


def test_grade_events_carry_resolved_experiment_id(tmp_path, monkeypatch):
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
