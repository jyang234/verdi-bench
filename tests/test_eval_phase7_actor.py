"""7C-2 / GR-12 — shared resolve_actor: fail-loud provenance, never 'unknown'."""

from __future__ import annotations

import pytest
import yaml
from typer.testing import CliRunner

from harness.cli import app
from harness.ledger.actor import ActorResolutionError, resolve_actor
from harness.ledger.query import find_events
from tests.fixtures.builders import write_experiment_yaml

runner = CliRunner()


def test_resolve_actor_flag_wins():
    assert resolve_actor("alice") == "alice"


def test_resolve_actor_falls_back_to_os_user(monkeypatch):
    import harness.ledger.actor as actor_mod

    monkeypatch.setattr(actor_mod.getpass, "getuser", lambda: "os-user")
    assert resolve_actor(None) == "os-user"


def test_resolve_actor_refuses_never_unknown(monkeypatch):
    import harness.ledger.actor as actor_mod

    def boom():
        raise OSError("no name in the environment")

    monkeypatch.setattr(actor_mod.getpass, "getuser", boom)
    with pytest.raises(ActorResolutionError) as exc:
        resolve_actor(None)
    assert "--actor" in str(exc.value)
    assert "unknown" in str(exc.value)  # names the policy: never records 'unknown'


def test_bench_plan_actor_flag_ledgers_named_actor(tmp_path):
    spec = write_experiment_yaml(tmp_path / "experiment.yaml")
    ledger = tmp_path / "ledger.ndjson"
    r = runner.invoke(app, ["plan", str(spec), "--ledger", str(ledger), "--actor", "alice"])
    assert r.exit_code == 0, r.output
    lock = find_events(ledger, "experiment_locked")[0]
    assert lock["provenance"]["actor"] == "alice"


def test_bench_plan_refuses_when_actor_unresolvable(tmp_path, monkeypatch):
    """With no --actor and the OS user unresolvable, the verb refuses (exit 2)
    naming --actor — it never ledgers 'unknown'."""
    import harness.ledger.actor as actor_mod

    monkeypatch.setattr(actor_mod.getpass, "getuser", lambda: (_ for _ in ()).throw(OSError()))
    spec = write_experiment_yaml(tmp_path / "experiment.yaml")
    ledger = tmp_path / "ledger.ndjson"
    r = runner.invoke(app, ["plan", str(spec), "--ledger", str(ledger)])
    assert r.exit_code == 2
    assert find_events(ledger, "experiment_locked") == []  # nothing ledgered


def test_corpus_approve_requires_explicit_approver(tmp_path):
    """D-P7-7: corpus approve drops the environment fallback — --approver is
    required (approver identity is security-relevant)."""
    expdir = tmp_path / "exp"
    expdir.mkdir()
    (expdir / "ledger.ndjson").write_text("", encoding="utf-8")
    key = tmp_path / "key.hex"
    key.write_text("00" * 32, encoding="utf-8")
    r = runner.invoke(app, [
        "corpus", "approve", str(expdir), "--candidate-id", "c", "--task-sha", "s",
        "--signing-key", str(key),
    ])
    assert r.exit_code == 2  # missing required --approver
    assert "approver" in (r.output + (r.stderr or "")).lower()
