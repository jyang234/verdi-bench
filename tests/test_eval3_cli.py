"""EVAL-3 AC-2 / AC-3 — CLI plan / verify-chain / anchor."""

from __future__ import annotations

from typer.testing import CliRunner

from harness.cli import app
from tests.fixtures.builders import write_experiment_yaml

runner = CliRunner()


def test_ac3_verify_cli_clean(tmp_path):
    spec = write_experiment_yaml(tmp_path / "experiment.yaml")
    ledger = tmp_path / "ledger.ndjson"
    r = runner.invoke(app, ["plan", str(spec), "--ledger", str(ledger)])
    assert r.exit_code == 0, r.output
    r2 = runner.invoke(app, ["verify-chain", str(ledger)])
    assert r2.exit_code == 0
    assert "chain OK" in r2.output


def test_ac3_verify_cli_detects_tamper(tmp_path):
    spec = write_experiment_yaml(tmp_path / "experiment.yaml")
    ledger = tmp_path / "ledger.ndjson"
    runner.invoke(app, ["plan", str(spec), "--ledger", str(ledger)])
    # append a second event then corrupt the genesis line
    from harness.ledger.events import EventContext, record_chain_anchor

    record_chain_anchor(ledger, EventContext(experiment_id="e", clock=lambda: "t"), head_hash="0" * 64, height=0)
    lines = ledger.read_text().splitlines()
    lines[0] = lines[0].replace("experiment_locked", "experiment_HACKED")
    ledger.write_text("\n".join(lines) + "\n")
    r = runner.invoke(app, ["verify-chain", str(ledger)])
    assert r.exit_code == 1
    assert "CHAIN BROKEN" in r.output


def test_anchor_cli_roundtrip(tmp_path):
    spec = write_experiment_yaml(tmp_path / "experiment.yaml")
    ledger = tmp_path / "ledger.ndjson"
    anchors = tmp_path / "anchors.ndjson"
    runner.invoke(app, ["plan", str(spec), "--ledger", str(ledger)])
    r = runner.invoke(app, ["anchor", str(ledger), "--out", str(anchors)])
    assert r.exit_code == 0
    r2 = runner.invoke(app, ["verify-chain", str(ledger), "--against-anchor", str(anchors)])
    assert r2.exit_code == 0
    assert "anchor OK" in r2.output


def test_anchor_cli_writes_ledger_event(tmp_path):
    """PL-4: `bench anchor` ledgers a chain_anchor event (it was test-only), so
    the act of anchoring is itself an auditable, chained record."""
    from harness.ledger import events
    from harness.ledger.query import find_events

    spec = write_experiment_yaml(tmp_path / "experiment.yaml")
    ledger = tmp_path / "ledger.ndjson"
    anchors = tmp_path / "anchors.ndjson"
    runner.invoke(app, ["plan", str(spec), "--ledger", str(ledger)])
    r = runner.invoke(app, ["anchor", str(ledger), "--out", str(anchors)])
    assert r.exit_code == 0, r.output
    recorded = find_events(ledger, events.CHAIN_ANCHOR)
    assert len(recorded) == 1
    # the ledgered anchor names the head that was externally checkpointed
    import json

    ext = json.loads(anchors.read_text().splitlines()[0])
    assert recorded[0]["head_hash"] == ext["head_hash"]
    assert recorded[0]["height"] == ext["height"]


def test_plan_experiment_id_is_dir_name(tmp_path):
    """PL-8: plan stamps experiment_id as the experiment *directory* name (as run
    and grade do), not the yaml stem "experiment"."""
    expdir = tmp_path / "my-experiment"
    expdir.mkdir()
    spec = write_experiment_yaml(expdir / "experiment.yaml")
    ledger = expdir / "ledger.ndjson"
    r = runner.invoke(app, ["plan", str(spec), "--ledger", str(ledger)])
    assert r.exit_code == 0, r.output
    from harness.ledger import events
    from harness.ledger.query import find_events

    lock = find_events(ledger, events.EXPERIMENT_LOCKED)[0]
    assert lock["provenance"]["experiment_id"] == "my-experiment"
