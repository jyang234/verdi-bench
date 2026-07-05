"""Control-reuse ledger events [control-reuse plan, slice 2].

Each reuse constructor appends exactly one event of a DISTINCT kind, and those
kinds never leak into the native trial / grade / judge_verdict queries the
official analyze path uses — the structural exclusion the design rests on.
"""

from __future__ import annotations

from harness.ledger import events
from harness.ledger.events import EventContext
from harness.ledger.query import find_events, read_events, verify


def _ctx():
    return EventContext(experiment_id="exp-reuse", actor="test", clock=lambda: "2026-07-05T00:00:00+00:00")


REUSED_FROM = {"source_experiment_id": "src-exp", "bundle_sha256": "deadbeef"}


def test_each_constructor_appends_exactly_one_event(tmp_path):
    ledger = tmp_path / "ledger.ndjson"
    ctx = _ctx()
    calls = [
        lambda: events.record_control_reused(
            ledger, ctx, source_experiment_id="src-exp",
            source_ledger_head_hash="h" * 8, bundle_sha256="deadbeef",
            fingerprint={"digest": "fp"}, control_arm="control",
            cells=[{"task_id": "t1", "repetition": 0}],
        ),
        lambda: events.record_reused_trial(
            ledger, ctx,
            trial_record={"trial_id": "tr1", "task_id": "t1", "arm": "control", "repetition": 0},
            reused_from=REUSED_FROM,
        ),
        lambda: events.record_reused_grade(
            ledger, ctx,
            grade={"trial_id": "tr1", "task_sha": "s", "assertions": [], "binary_score": True},
            reused_from=REUSED_FROM,
        ),
        lambda: events.append_reused_verdict(
            ledger, ctx, verdict={"winner": "A", "task_id": "t1"}, reused_from=REUSED_FROM,
        ),
    ]
    for call in calls:
        before = len(read_events(ledger))
        call()
        assert len(read_events(ledger)) - before == 1
    assert verify(ledger).ok


def test_reused_events_do_not_leak_into_native_queries(tmp_path):
    ledger = tmp_path / "ledger.ndjson"
    ctx = _ctx()
    events.record_reused_trial(
        ledger, ctx,
        trial_record={"trial_id": "tr1", "task_id": "t1", "arm": "control", "repetition": 0},
        reused_from=REUSED_FROM,
    )
    events.record_reused_grade(
        ledger, ctx,
        grade={"trial_id": "tr1", "task_sha": "s", "assertions": [], "binary_score": True},
        reused_from=REUSED_FROM,
    )
    events.append_reused_verdict(
        ledger, ctx, verdict={"winner": "A", "task_id": "t1"}, reused_from=REUSED_FROM,
    )
    # the official path reads only the native kinds — reused_* is invisible to it
    assert find_events(ledger, events.TRIAL) == []
    assert find_events(ledger, events.GRADE) == []
    assert find_events(ledger, events.JUDGE_VERDICT) == []
    # but the reuse kinds are there, carrying provenance
    assert len(find_events(ledger, events.REUSED_TRIAL)) == 1
    assert find_events(ledger, events.REUSED_TRIAL)[0]["reused_from"] == REUSED_FROM
    assert len(find_events(ledger, events.REUSED_GRADE)) == 1
    assert len(find_events(ledger, events.REUSED_JUDGE_VERDICT)) == 1
