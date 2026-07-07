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


def test_anchor_cli_refuses_tampered_ledger(tmp_path):
    """`bench anchor` must chain-verify before anchoring (7A-2).

    Anchoring a tampered ledger would checkpoint rewritten history as
    authentic. The verb must exit 1 naming the broken line and append
    nothing — neither the external anchor line nor the chain_anchor event.
    """
    from harness.ledger import events
    from harness.ledger.query import find_events

    spec = write_experiment_yaml(tmp_path / "experiment.yaml")
    ledger = tmp_path / "ledger.ndjson"
    anchors = tmp_path / "anchors.ndjson"
    runner.invoke(app, ["plan", str(spec), "--ledger", str(ledger)])
    from harness.ledger.events import EventContext, record_chain_anchor

    record_chain_anchor(
        ledger, EventContext(experiment_id="e", clock=lambda: "t"), head_hash="0" * 64, height=0
    )
    events_before = len(find_events(ledger, events.CHAIN_ANCHOR))
    ledger_before = ledger.read_bytes()
    # byte-flip a payload on the genesis line: its successor's back-pointer breaks
    lines = ledger.read_text().splitlines()
    lines[0] = lines[0].replace("experiment_locked", "experiment_HACKED")
    ledger.write_text("\n".join(lines) + "\n")
    ledger_after_tamper = ledger.read_bytes()

    r = runner.invoke(app, ["anchor", str(ledger), "--out", str(anchors)])
    assert r.exit_code == 1, r.output
    assert "CHAIN BROKEN" in r.output
    assert not anchors.exists()  # no external anchor line written
    # no new chain_anchor event appended (ledger unchanged since the tamper)
    assert ledger.read_bytes() == ledger_after_tamper
    assert ledger_before != ledger_after_tamper  # sanity: the tamper landed
    assert len(find_events(ledger, events.CHAIN_ANCHOR)) == events_before


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


def test_plan_verb_safety_net_unmapped_refusal_is_clean_exit_2(tmp_path, monkeypatch):
    """OI-B: a refusal the plan verb never enumerated still surfaces as a clean
    exit 2 with its message (not a traceback), because `bench plan` maps
    refusal_exit() uniformly over the VerdiRefusal base [refactor 13 OI-B]."""
    import harness.plan.api as plan_api
    from harness.errors import VerdiRefusal

    class _NovelPlanRefusal(VerdiRefusal):
        pass

    def _refuse(*args, **kwargs):
        raise _NovelPlanRefusal("a plan refusal no verb enumerated")

    monkeypatch.setattr(plan_api, "plan_experiment", _refuse)
    spec = write_experiment_yaml(tmp_path / "experiment.yaml")
    r = runner.invoke(app, ["plan", str(spec), "--ledger", str(tmp_path / "ledger.ndjson")])
    assert r.exit_code == 2, r.output
    assert "a plan refusal no verb enumerated" in r.output
    # a clean typer.Exit, never a raw traceback of the novel refusal
    assert not isinstance(r.exception, _NovelPlanRefusal)
