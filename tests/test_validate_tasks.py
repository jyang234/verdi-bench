"""``bench corpus validate-tasks`` — the tasks.yaml lint verb [decision A9].

The run/grade reader stays lenient (it feeds the lock hash); this verb is the
pre-lock strictness that refuses unknown keys and the known drift traps. It must
ledger nothing (pure read).
"""

from __future__ import annotations

import yaml
from typer.testing import CliRunner

from harness.cli import app

runner = CliRunner()


def _write_tasks(tmp_path, tasks):
    (tmp_path / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": tasks}), encoding="utf-8"
    )


def test_clean_tasks_pass_and_ledger_nothing(tmp_path):
    _write_tasks(tmp_path, [
        {"id": "t1", "prompt": "p", "holdouts_dir": "holdouts/t1",
         "plugin_ids": ["ruff"], "task_class": "refactor"},
        {"id": "t2", "prompt": "q"},
    ])
    before = {p.name for p in tmp_path.iterdir()}

    r = runner.invoke(app, ["corpus", "validate-tasks", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "2 task(s) OK" in r.output
    # pure read: no ledger, no new files written
    assert not (tmp_path / "ledger.ndjson").exists()
    assert {p.name for p in tmp_path.iterdir()} == before


def test_unknown_key_holdout_dir_suggests_holdouts_dir(tmp_path):
    _write_tasks(tmp_path, [{"id": "t1", "prompt": "p", "holdout_dir": "holdouts/t1"}])
    r = runner.invoke(app, ["corpus", "validate-tasks", str(tmp_path)])
    assert r.exit_code == 2
    assert "unknown key 'holdout_dir'" in r.output
    assert "did you mean 'holdouts_dir'?" in r.output
    assert "t1" in r.output


def test_unknown_key_plugins_suggests_plugin_ids(tmp_path):
    _write_tasks(tmp_path, [{"id": "t1", "prompt": "p", "plugins": ["ruff"]}])
    r = runner.invoke(app, ["corpus", "validate-tasks", str(tmp_path)])
    assert r.exit_code == 2
    assert "unknown key 'plugins'" in r.output
    assert "did you mean 'plugin_ids'?" in r.output


def test_generic_typo_gets_a_close_match_suggestion(tmp_path):
    _write_tasks(tmp_path, [{"id": "t1", "promt": "typo of prompt"}])
    r = runner.invoke(app, ["corpus", "validate-tasks", str(tmp_path)])
    assert r.exit_code == 2
    assert "unknown key 'promt'" in r.output
    assert "did you mean 'prompt'?" in r.output


def test_all_problems_reported_across_tasks(tmp_path):
    _write_tasks(tmp_path, [
        {"id": "t1", "holdout_dir": "x"},
        {"id": "t2", "plugins": []},
    ])
    r = runner.invoke(app, ["corpus", "validate-tasks", str(tmp_path)])
    assert r.exit_code == 2
    assert "t1" in r.output and "t2" in r.output
    assert "2 problem(s)" in r.output


def test_missing_tasks_yaml_exits_2(tmp_path):
    r = runner.invoke(app, ["corpus", "validate-tasks", str(tmp_path)])
    assert r.exit_code == 2
    assert "no tasks.yaml" in r.output


def test_duplicate_id_surfaced_as_lint_failure(tmp_path):
    _write_tasks(tmp_path, [{"id": "dup", "prompt": "a"}, {"id": "dup", "prompt": "b"}])
    r = runner.invoke(app, ["corpus", "validate-tasks", str(tmp_path)])
    assert r.exit_code == 2
    assert "duplicate task id" in r.output
