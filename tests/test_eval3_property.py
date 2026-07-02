"""EVAL-3 AC-7 — one appended event per stage entrypoint invocation.

Sweeps the entrypoint registry [master plan §M7]: every registered stage
entrypoint, invoked once against a prepared fixture, appends exactly one ledger
event. Later stories' verbs join this sweep automatically by registering.
"""

from __future__ import annotations

# Import stage modules so they self-register their entrypoints.
import harness.grade.deterministic  # noqa: F401
import harness.plan.lock  # noqa: F401
import harness.run.interleave  # noqa: F401
from harness.entrypoints import all_entrypoints
from harness.ledger.query import read_events
from tests.fixtures.builders import write_experiment_yaml


def test_ac7_one_event_per_operation(tmp_path):
    entrypoints = all_entrypoints()
    assert entrypoints, "no entrypoints registered"
    for ep in entrypoints:
        d = tmp_path / ep.name
        d.mkdir()
        write_experiment_yaml(d / "experiment.yaml")
        ledger = d / "ledger.ndjson"
        before = len(read_events(ledger))
        ep.fn(str(d))
        after = len(read_events(ledger))
        assert after - before == 1, f"{ep.name} appended {after - before} events, expected 1"
