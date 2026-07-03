"""EVAL-4 CO-2 / D-P4-2 — bench run consults is_schedulable.

Before Phase 4 ``bench run`` read tasks.yaml and never consulted a manifest, so a
pending/quarantined task ran, graded, and fed findings. With ``--corpus-manifest``
the scheduler gates each task on ``is_schedulable``; a non-admitted task fails its
cells closed (``trial_infra_failed(not_schedulable)``) and the executed order
still lands. A tasks.yaml/manifest drift is refused loudly.
"""

from __future__ import annotations

import yaml
from typer.testing import CliRunner

from harness.cli import app
from harness.corpus.registry import CorpusManifest, TaskEntry
from harness.ledger.query import find_events
from tests.fixtures.builders import write_experiment_yaml

runner = CliRunner()


def _plan(expdir, tasks):
    expdir.mkdir(parents=True, exist_ok=True)
    write_experiment_yaml(expdir / "experiment.yaml")
    (expdir / "tasks.yaml").write_text(yaml.safe_dump({"tasks": tasks}), encoding="utf-8")
    ledger = expdir / "ledger.ndjson"
    assert runner.invoke(
        app, ["plan", str(expdir / "experiment.yaml"), "--ledger", str(ledger)]
    ).exit_code == 0
    return ledger


def _manifest(expdir, status):
    m = CorpusManifest(
        corpus_id="public-mini", semver="1.0.0", kind="public",
        tasks=[TaskEntry(task_id="t1", sha="x" * 64, status=status)],
    )
    p = expdir / "manifest.json"
    m.save(p)
    return p


def test_co2_run_refuses_non_admitted_task(tmp_path):
    expdir = tmp_path / "exp"
    ledger = _plan(expdir, [{"id": "t1", "prompt": "solve"}])
    mpath = _manifest(expdir, status="pending-curation")

    r = runner.invoke(app, ["run", str(expdir), "--corpus-manifest", str(mpath)])
    assert r.exit_code == 0, r.output
    assert find_events(ledger, "trial") == []  # nothing ran
    infra = find_events(ledger, "trial_infra_failed")
    assert infra and all(e["reason"] == "not_schedulable" for e in infra)
    assert find_events(ledger, "executed_order")  # AC-4: order still landed


def test_co2_admitted_task_runs(tmp_path):
    expdir = tmp_path / "exp"
    ledger = _plan(expdir, [{"id": "t1", "prompt": "solve"}])
    mpath = _manifest(expdir, status="admitted")

    r = runner.invoke(app, ["run", str(expdir), "--corpus-manifest", str(mpath)])
    assert r.exit_code == 0, r.output
    assert find_events(ledger, "trial")  # admitted -> it runs


def test_co2_manifest_drift_refused(tmp_path):
    """A tasks.yaml task absent from the manifest is a fail-closed drift refusal."""
    expdir = tmp_path / "exp"
    _plan(expdir, [{"id": "t1", "prompt": "solve"}])
    m = CorpusManifest(corpus_id="public-mini", semver="1.0.0", kind="public",
                       tasks=[TaskEntry(task_id="t2", sha="x" * 64, status="admitted")])
    mpath = expdir / "manifest.json"
    m.save(mpath)

    r = runner.invoke(app, ["run", str(expdir), "--corpus-manifest", str(mpath)])
    assert r.exit_code == 2
    assert "disagree" in r.output


def test_co2_no_manifest_is_backward_compatible(tmp_path):
    """Without --corpus-manifest, scheduling is ungated (existing behavior)."""
    expdir = tmp_path / "exp"
    ledger = _plan(expdir, [{"id": "t1", "prompt": "solve"}])
    r = runner.invoke(app, ["run", str(expdir)])
    assert r.exit_code == 0, r.output
    assert find_events(ledger, "trial")  # runs, no gate
