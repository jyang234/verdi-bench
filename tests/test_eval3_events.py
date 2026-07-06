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
    """Sweep the LIVE registry against the events module's declarative table
    [refactor 01 §4 D9; refactor 06 §2].

    Registration is now table-driven: every event kind is one ``EventSpec`` row
    in ``_EVENT_SPECS`` and ``REGISTERED_EVENTS`` derives from it. The sweep is
    re-pointed from the retired ``register_event("…")`` literals to the table —
    each row is ``EventSpec(<CONST>, …)``, its name the event-name CONSTANT — so
    the same invariants still hold against source + live attributes and a future
    event type joins the sweep automatically:

    1. the ``EventSpec`` rows, with no duplicates, name exactly
       ``REGISTERED_EVENTS`` — nothing registers a kind outside the table and no
       row is dead;
    2. every registered kind is bound to an UPPERCASE module constant;
    3. every registered kind is written by a constructor through the generic
       builder — its constant is ``build_event``'s first positional argument, so
       no kind is registered without a typed write path.
    """
    tree = ast.parse(inspect.getsource(events))

    # Uppercase string module constants — the event-name constants.
    const_by_name = {k: v for k, v in vars(events).items() if k.isupper() and isinstance(v, str)}

    def _const_at(call, idx):
        """The event-name string of ``call`` whose positional arg ``idx`` is an
        event-name CONSTANT, else ``None``."""
        args = call.args
        if len(args) > idx and isinstance(args[idx], ast.Name) and args[idx].id in const_by_name:
            return const_by_name[args[idx].id]
        return None

    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]

    # 1. table rows == live registry, duplicate-free.
    spec_names = [n for c in calls if c.func.id == "EventSpec" and (n := _const_at(c, 0))]
    assert len(spec_names) == len(set(spec_names)), (
        f"duplicate EventSpec rows: {sorted(n for n in set(spec_names) if spec_names.count(n) > 1)}"
    )
    assert set(spec_names) == events.REGISTERED_EVENTS, (
        "live registry and the EventSpec table disagree — a kind was registered "
        "outside the table (or a row went dead): "
        f"{sorted(set(spec_names) ^ events.REGISTERED_EVENTS)}"
    )

    # 2. every kind is a named UPPERCASE module constant.
    assert set(const_by_name.values()) == events.REGISTERED_EVENTS, (
        f"registered kinds without a module constant (or stale constants): "
        f"{sorted(set(const_by_name.values()) ^ events.REGISTERED_EVENTS)}"
    )

    # 3. every kind is written through the generic builder — its constant is
    #    build_event's first positional argument.
    built = {n for c in calls if c.func.id == "build_event" and (n := _const_at(c, 0))}
    assert built == events.REGISTERED_EVENTS, (
        f"registered kinds no constructor builds: {sorted(events.REGISTERED_EVENTS - built)}"
    )

    # PL-14: the acknowledgment folded into experiment_locked; the separate event
    # type is retired.
    assert "acknowledged_underpowered" not in events.REGISTERED_EVENTS
