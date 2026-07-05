"""EVAL-3 AC-7 / XC-3 — one appended event per stage entrypoint invocation.

Sweeps the entrypoint registry [master plan §M7]: every registered stage
entrypoint, invoked once against a prepared fixture, appends exactly one ledger
event (success or fail-closed refusal). This is the §7.2 fail-closed guarantee
made mechanical across every ledgered operation.

The sweep is *discovering*, not hardwired: it imports every stage module so each
self-registers, then asserts the registry covers an explicit **expected set**.
A stage that forgets to register fails the test closed, rather than silently
sitting outside the property (XC-3). Preconditioned operations (a reveal needs a
prior verdict, an admission needs approval + baseline) declare a ``prepare``
hook that seeds those events *before* the count is snapshotted, so only the
operation's single event is measured.
"""

from __future__ import annotations

# Import every stage module so it self-registers its entrypoint(s).
import harness.analyze.cli  # noqa: F401
import harness.contamination.probe  # noqa: F401
import harness.corpus.admit  # noqa: F401
import harness.forensics.cli  # noqa: F401
import harness.corpus.ledger_ops  # noqa: F401
import harness.grade.baseline  # noqa: F401  (registers `corpus-baseline` [F-H2])
import harness.grade.deterministic  # noqa: F401
import harness.judge.client  # noqa: F401
import harness.ledger.anchors  # noqa: F401  (registers the `anchor` entrypoint)
import harness.plan.lock  # noqa: F401
import harness.process.score  # noqa: F401
import harness.review.build  # noqa: F401
import harness.review.record  # noqa: F401
import harness.run.interleave  # noqa: F401
from harness.entrypoints import all_entrypoints
from harness.ledger.query import read_events
from tests.fixtures.builders import write_experiment_yaml

# Every ledgered stage operation must be registered — a stage that forgets to
# register fails this set assertion (fails closed), closing the XC-3 gap where
# "later stories join automatically" failed open.
EXPECTED_ENTRYPOINTS = {
    "plan-lock",
    "plan-lock-underpowered",  # PL-14: the acknowledged-underpowered path
    "run-trial",
    "grade-trial",
    "judge",
    "process",
    "forensics",
    "review-batch",  # F-M-O2: the reviewed queue ledgered as a unit
    "review-build",
    "review-record",
    "review-reveal",
    "analyze",
    "selfcheck",  # EVAL-1-D008: the coverage selfcheck ledgers one event
    "corpus-admit",
    "corpus-baseline",  # F-H2: the admission prerequisite's production producer
    "corpus-calibration-run",
    "corpus-subset-draw",
    "contamination-probe",
    "anchor",  # PRA-L5: bench anchor appends exactly one chain_anchor event
}


def test_xc3_registry_covers_every_stage_operation():
    registered = {ep.name for ep in all_entrypoints()}
    missing = EXPECTED_ENTRYPOINTS - registered
    assert not missing, f"stage operations not registered in the one-event property: {missing}"


def test_ac7_one_event_per_operation(tmp_path):
    entrypoints = all_entrypoints()
    assert entrypoints, "no entrypoints registered"
    for ep in entrypoints:
        d = tmp_path / ep.name
        d.mkdir()
        write_experiment_yaml(d / "experiment.yaml")
        ledger = d / "ledger.ndjson"
        if ep.prepare is not None:
            ep.prepare(str(d))  # seed preconditions — not part of the measured op
        before = len(read_events(ledger))
        ep.fn(str(d))
        after = len(read_events(ledger))
        assert after - before == 1, f"{ep.name} appended {after - before} events, expected 1"
