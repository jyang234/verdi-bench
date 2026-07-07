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
from tests.fixtures.builders import fixed_ctx, locked_experiment, write_experiment_yaml

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
    from harness.plan.api import ExperimentIdResolutionError

    ledger = tmp_path / "ledger.ndjson"
    root_spec = Path("/experiment.yaml")  # resolves to root; parent has no name
    with pytest.raises(ExperimentIdResolutionError) as exc:
        plan_experiment(root_spec, ledger, actor="tester")
    assert str(root_spec) in str(exc.value)  # names the offending path
    assert not ledger.exists()  # refused before genesis: zero events appended


# --- AC-6: a successful lock removes its <ledger>.planlock flock file ----------
def test_successful_lock_removes_planlock_file(tmp_path):
    """[ux-friction AC-6] After a green plan the experiment dir carries only the
    user files and the ledger — the stray <ledger>.planlock flock file a
    successful lock leaves today (F5) is removed on success."""
    spec = write_experiment_yaml(tmp_path / "experiment.yaml")
    ledger = tmp_path / "ledger.ndjson"
    lock_experiment(spec, ledger, ctx=fixed_ctx(), **_FAST_LOCK)

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
    lock_experiment(spec, ledger, ctx=fixed_ctx(), **_FAST_LOCK)
    planlock = Path(str(ledger) + ".planlock")
    assert not planlock.exists()

    with pytest.raises(AlreadyLockedError):
        lock_experiment(spec, ledger, ctx=fixed_ctx(), **_FAST_LOCK)

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
