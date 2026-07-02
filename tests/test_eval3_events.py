"""EVAL-3 AC-6 — event provenance stamping and the constructor write path."""

from __future__ import annotations

import pytest

from harness.ledger import events
from harness.ledger.events import EventContext, UnregisteredEventError
from tests.fixtures.builders import fixed_ctx


def test_ac6_event_provenance_stamped(tmp_path):
    ledger = tmp_path / "l.ndjson"
    ev = events.record_chain_anchor(ledger, fixed_ctx(), head_hash="a" * 64, height=0)
    prov = ev["provenance"]
    assert set(prov) == {"ts", "actor", "experiment_id", "instrument"}
    assert set(prov["instrument"]) == {"version", "git_sha"}
    assert prov["actor"] == "tester"
    assert prov["experiment_id"] == "exp-fixture"


def test_ac6_unregistered_event_rejected(tmp_path):
    ledger = tmp_path / "l.ndjson"
    with pytest.raises(UnregisteredEventError):
        events.emit(ledger, fixed_ctx(), "totally_made_up", {"x": 1})


def test_ac6_reserved_keys_rejected(tmp_path):
    ledger = tmp_path / "l.ndjson"
    with pytest.raises(ValueError):
        events.emit(ledger, fixed_ctx(), events.CHAIN_ANCHOR, {"provenance": {}})


def test_ac6_all_shipped_events_registered():
    # every constructor's type must be registered
    for name in [
        "experiment_locked",
        "acknowledged_underpowered",
        "chain_anchor",
        "trial",
        "trial_infra_failed",
        "run_stopped_cost_ceiling",
        "executed_order",
        "grade",
        "cant_grade",
        "flake_baseline",
        "judge_verdict",
    ]:
        assert name in events.REGISTERED_EVENTS
