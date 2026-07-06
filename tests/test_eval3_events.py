"""EVAL-3 AC-6 — event provenance stamping and the constructor write path."""

from __future__ import annotations

import ast
import inspect

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
    """Sweep the LIVE registry against the events module's own registrations
    [refactor 01 §4 D9].

    The previous version pinned a hand-written list of 14 of the (then) 31
    event types, so a shipped-but-unlisted type sat outside the guard forever
    — the exact drift a registry meta-test exists to prevent. Everything below
    is derived from the module source + live attributes, so a future event
    type joins the sweep automatically:

    1. the ``register_event("…")`` literals in events.py, with no duplicates,
       are exactly ``REGISTERED_EVENTS`` — nothing registers an event kind
       anywhere else, and no registration is dead;
    2. every registered kind is bound to an UPPERCASE module constant;
    3. every registered kind is emitted by at least one constructor in the
       module (its constant appears as ``emit``'s event-type argument), so no
       kind is registered without a typed write path.
    """
    tree = ast.parse(inspect.getsource(events))

    # 1. source registrations == live registry, duplicate-free.
    source_names = [
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "register_event"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    ]
    assert len(source_names) == len(set(source_names)), (
        f"duplicate register_event() calls: {sorted(set(n for n in source_names if source_names.count(n) > 1))}"
    )
    assert set(source_names) == events.REGISTERED_EVENTS, (
        "live registry and events.py registrations disagree — an event kind was "
        "registered outside the events module (or a registration went dead): "
        f"{sorted(set(source_names) ^ events.REGISTERED_EVENTS)}"
    )

    # 2. every kind is a named UPPERCASE module constant.
    constants = {v for k, v in vars(events).items() if k.isupper() and isinstance(v, str)}
    assert constants == events.REGISTERED_EVENTS, (
        f"registered kinds without a module constant (or stale constants): "
        f"{sorted(constants ^ events.REGISTERED_EVENTS)}"
    )

    # 3. every kind has a constructor that emits it.
    const_by_name = {k: v for k, v in vars(events).items() if k.isupper() and isinstance(v, str)}
    emitted = {
        const_by_name[node.args[2].id]
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "emit"
        and len(node.args) >= 3
        and isinstance(node.args[2], ast.Name)
        and node.args[2].id in const_by_name
    }
    assert emitted == events.REGISTERED_EVENTS, (
        f"registered kinds no constructor emits: {sorted(events.REGISTERED_EVENTS - emitted)}"
    )

    # PL-14: the acknowledgment folded into experiment_locked; the separate event
    # type is retired.
    assert "acknowledged_underpowered" not in events.REGISTERED_EVENTS
