"""OTLP normalization through the run seam [refactor 10 §1, §3, §6.4].

Proves the span→trajectory projection wired through the ACTUAL run pipeline (the
minimal seam accommodation): the otlp platform's capture reads the redacted
``otlp_spans.json``, a mapping violation fails the trial closed as ``spans_corrupt``
(A12) carrying the incurred spend, zero selected spans is honest absence, and the
D-10-1 coherence check refuses an otlp arm with no collector BEFORE any trial. The
fake engine's scripted spans reach a ``verified`` trajectory through the identical
path the Harbor engine uses — the cross-engine contract-suite row (§7).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from harness.adapters.base import Outcome
from harness.adapters.otlp import OtlpAdapter, SpanMappingError
from harness.ledger import events
from harness.ledger.query import find_events
from harness.plan.interleave import enumerate_trials
from harness.run.api import OtlpCoherenceError, _assert_otlp_coherence, run_experiment
from harness.run.engines.fake import FakeEngine
from harness.run.flight_recorder import resolve_flight_recorder
from harness.run.interleave import schedule
from harness.run.seam import (
    CapturePipeline,
    PostEngineFailure,
    SpendTracker,
    _CaptureContext,
    run_trial,
)
from harness.run.trajectory import resolve_trajectory
from harness.cli import app
from harness.run.types import OtlpConfig, RunConfig, Task
from harness.schema.experiment import Arm
from tests.fixtures.builders import ctx_for, write_experiment_yaml

_OTLP_ARM = Arm(name="A", platform="otlp", model="anthropic/claude-haiku-4-5-20251001")


def _sv(s):
    return {"stringValue": s}


def _iv(n):
    return {"intValue": str(n)}


def _span(sid, attrs, *, start="1000000000000000000", parent=None, events=None):
    s = {"spanId": sid, "startTimeUnixNano": start, "attributes": attrs}
    if parent:
        s["parentSpanId"] = parent
    if events:
        s["events"] = events
    return s


def _body(spans):
    """A LIVE-collector body_json the fake engine writes as one envelope line."""
    return {"resourceSpans": [{"scopeSpans": [{"spans": spans}]}]}


def _config(tmp_path) -> RunConfig:
    return RunConfig(
        engine=FakeEngine(),
        otlp=OtlpConfig(endpoint="http://verdi-trace-collector:4318", log_path=str(tmp_path / "otlp.jsonl")),
    )


def _run(tmp_path, spans, *, outcome="completed") -> "tuple":
    task = Task(
        id="t", prompt="p",
        fake_behavior={"native_log": {}, "outcome": outcome, "otlp_spans": [_body(spans)]},
    )
    rec = run_trial(task, _OTLP_ARM, tmp_path / "ws", _config(tmp_path))
    return rec


# --- cross-engine: scripted spans → verified trajectory (contract-suite row) --
def test_scripted_spans_reach_a_verified_trajectory(tmp_path):
    """§7 cross-engine row: the fake engine's scripted spans flow through the SAME
    redact → normalize → persist path Harbor uses, to a ``verified`` trajectory."""
    msg = _span(
        "s1",
        [
            {"key": "gen_ai.operation.name", "value": _sv("chat")},
            {"key": "gen_ai.usage.input_tokens", "value": _iv(10)},
            {"key": "gen_ai.usage.output_tokens", "value": _iv(4)},
            {"key": "gen_ai.content.completion", "value": _sv("done")},
        ],
    )
    tool = _span(
        "s2",
        [{"key": "gen_ai.tool.name", "value": _sv("Read")}],
        start="1000000000500000000", parent="s1",
    )
    rec = _run(tmp_path, [msg, tool])
    assert rec.outcome == Outcome.completed
    assert rec.spans_sha is not None  # the engine captured the spans (spec 09)
    assert rec.trajectory_sha is not None  # the adapter normalized them (spec 10)

    status, traj = resolve_trajectory(rec.artifacts_path, rec.trajectory_sha)
    assert status == "verified"
    assert [s.kind for s in traj.steps] == ["message", "tool_call"]
    assert traj.steps[0].tokens == 14 and traj.steps[0].relative_ts == 0.0
    assert traj.steps[1].relative_ts == 0.5
    assert traj.platform == "otlp"


def test_scripted_reasoning_spans_reach_a_verified_flight_recorder(tmp_path):
    msg = _span("s1", [{"key": "gen_ai.operation.name", "value": _sv("chat")}])
    reason = _span(
        "s2",
        [{"key": "gen_ai.content.reasoning", "value": _sv("first decompose the task")}],
        start="1000000000100000000", parent="s1",
    )
    rec = _run(tmp_path, [msg, reason])
    assert rec.flight_recorder_sha is not None
    status, fr = resolve_flight_recorder(rec.artifacts_path, rec.flight_recorder_sha)
    assert status == "verified"
    assert [e.content for e in fr.entries] == ["first decompose the task"]
    assert fr.entries[0].turn == 0  # linked to the message step's turn


# --- spans_corrupt: a mapping violation fails the trial closed (A12) ----------
_BAD_AGENT_SPAN = [
    _span(
        "s1",
        [
            {"key": "gen_ai.operation.name", "value": _sv("chat")},
            {"key": "verdi.agent", "value": _sv("llama-planner")},  # outside vocabulary
        ],
    )
]


def test_bad_agent_raises_post_engine_failure_from_span_mapping(tmp_path):
    """run_trial surfaces the span mapping violation as a PostEngineFailure carrying
    the incurred spend — the persist-failure discipline, so the scheduler ledgers
    it instead of losing the datapoint (and its cost)."""
    with pytest.raises(PostEngineFailure) as exc:
        _run(tmp_path, _BAD_AGENT_SPAN)
    assert isinstance(exc.value.cause, SpanMappingError)


def test_schedule_ledgers_spans_corrupt(tmp_path):
    """Through the scheduler the same violation becomes a ledgered
    ``trial_infra_failed(spans_corrupt)`` — the A12 vocabulary value, wired."""
    exp = tmp_path / "exp"
    exp.mkdir()
    ctx = ctx_for(exp)
    ledger = exp / "ledger.ndjson"
    task = Task(
        id="t", prompt="p",
        fake_behavior={"native_log": {}, "otlp_spans": [_body(_BAD_AGENT_SPAN)]},
    )
    order = enumerate_trials(["t"], ["A"], 1)
    schedule(
        order, tasks={"t": task}, arms={"A": _OTLP_ARM},
        workspace_root=exp / "ws", ledger_path=ledger, ctx=ctx,
        config=_config(tmp_path), cost_ceiling=100.0,
    )
    reasons = [ev.get("reason") for ev in find_events(ledger, events.TRIAL_INFRA_FAILED)]
    assert reasons == ["spans_corrupt"], reasons


# --- honest absence -----------------------------------------------------------
def test_zero_selected_spans_is_honest_absence(tmp_path):
    """A LIVE collector with only an infra span → spans captured (spec 09) but no
    trajectory artifact (spec 10 honest absence); the trial completes."""
    infra = _span("s1", [{"key": "http.method", "value": _sv("GET")}])
    rec = _run(tmp_path, [infra])
    assert rec.outcome == Outcome.completed
    assert rec.spans_sha is not None  # the (infra-only) capture is preserved
    assert rec.trajectory_sha is None  # no action steps → no trajectory
    assert not (Path(rec.artifacts_path) / "trajectory.json").exists()


# --- timeout carve-out: a corrupt artifact on timeout is absence, not corrupt --
def _capture_with_corrupt_spans(tmp_path, outcome: Outcome):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "otlp_spans.json").write_text("{ this is not json", encoding="utf-8")
    pipeline = CapturePipeline(SpendTracker(spend=0.5))
    pipeline.redact(tmp_path, [])
    ctx = _CaptureContext(
        adapter=OtlpAdapter(), trial_id="t", platform="otlp",
        artifacts_dir=artifacts, extra_patterns=[],
    )
    return pipeline.capture(outcome, ctx)


def test_corrupt_spans_on_completed_fails_closed(tmp_path):
    with pytest.raises(PostEngineFailure) as exc:
        _capture_with_corrupt_spans(tmp_path, Outcome.completed)
    assert isinstance(exc.value.cause, SpanMappingError)
    assert exc.value.spend == 0.5  # PRA-M8: the incurred spend is carried


def test_corrupt_spans_on_timeout_is_honest_absence(tmp_path):
    """A timeout kill can truncate the artifact mid-write, so a corrupt
    otlp_spans.json on a TIMEOUT is honest absence — the RN-17 datapoint survives
    instead of being erased as spans_corrupt."""
    shas = _capture_with_corrupt_spans(tmp_path, Outcome.timeout)
    assert shas["trajectory_sha"] is None
    assert shas["flight_recorder_sha"] is None


# --- D-10-1 coherence: otlp arm with no collector refuses at preflight ---------
def test_assert_otlp_coherence_unit():
    otlp_cfg = OtlpConfig(endpoint="http://c:4318")
    # otlp arm + no collector → refuse
    with pytest.raises(OtlpCoherenceError, match="platform: otlp"):
        _assert_otlp_coherence([_OTLP_ARM], None)
    # otlp arm + collector → OK; non-otlp arm + no collector → OK (no-op)
    _assert_otlp_coherence([_OTLP_ARM], otlp_cfg)
    _assert_otlp_coherence([Arm(name="B", platform="generic", model="openai/gpt-4.1-mini-2025-04-14")], None)


def test_run_experiment_refuses_otlp_arm_without_collector(tmp_path):
    """End to end: a locked experiment with an otlp arm and no configured collector
    fails at run preflight — before any trial — with both settings named."""
    from typer.testing import CliRunner

    arms_cfg = [
        {"name": "control", "platform": "otlp", "model": "anthropic/claude-haiku-4-5-20251001", "payload": {}},
        {"name": "treatment", "platform": "generic", "model": "openai/gpt-4.1-mini-2025-04-14", "payload": {}},
    ]
    exp = tmp_path / "exp"
    exp.mkdir()
    write_experiment_yaml(exp / "experiment.yaml", arms=arms_cfg, repetitions=1)
    (exp / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": [{"id": "t1", "prompt": "p"}]}), encoding="utf-8"
    )
    # lock (plan) with tasks present so the otlp arm passes plan-time platform
    # capability, then reach the run-time coherence refusal
    plan = CliRunner().invoke(
        app, ["plan", str(exp / "experiment.yaml"), "--ledger", str(exp / "ledger.ndjson")]
    )
    assert plan.exit_code == 0, plan.output
    with pytest.raises(OtlpCoherenceError, match="no OTLP collector"):
        run_experiment(exp, engine="fake")
    # and NO trial event was written — the refusal preceded all spend
    assert find_events(exp / "ledger.ndjson", events.TRIAL) == []
