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
from tests.fixtures.builders import write_experiment_yaml

# small sim params keep the plan power check fast in tests
_TWO_TASKS = {"tasks": [{"id": "t1", "prompt": "p"}, {"id": "t2", "prompt": "p"}]}


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
