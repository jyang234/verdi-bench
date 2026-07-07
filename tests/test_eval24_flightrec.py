"""EVAL-24 — flight recorder: per-trial reasoning capture [spec: docs/design/specs/eval24.spec.md].

AC-1 additive-sha capture through the redaction door; AC-2 judge/grade isolation
by construction; AC-3 the advisory review over reasoning (blinded, byte-bounded,
config-resolved model); AC-4 null-honest capture + disclosed cross-arm asymmetry
+ primary-metric ineligibility; AC-5 the unblinded operator compare render.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import fields
from pathlib import Path

import yaml

from harness.adapters.base import Outcome, Provenance, Telemetry, TrialRecord
from harness.adapters.generic import GenericAdapter
from harness.ledger import events as E
from harness.ledger.events import record_trial
from harness.ledger.query import find_events, read_events
from harness.run.flight_recorder import (
    DEFAULT_REASONING_BUDGET_BYTES,
    FLIGHT_RECORDER_FILENAME,
    FlightRecorder,
    persist_flight_recorder,
    resolve_flight_recorder,
)
from tests.fixtures.builders import ctx_for, locked_experiment


# --- AC-1: additive-sha capture, redaction door, honest absence ----------------
def test_ac1_recorder_additive_sha_redacted(tmp_path):
    """Reasoning parses through the generic seam, persists through the EVAL-4
    secret door, and resolves sha-verified; a native/non-verdi log captures none."""
    log = {"verdi_log_version": 1,
           "reasoning": [{"content": "auth would reuse sk-ant-api03-" + "A" * 90}]}
    entries = GenericAdapter().normalize_reasoning(log)
    rec = FlightRecorder(trial_id="t1", platform="generic", entries=entries)
    sha = persist_flight_recorder(rec, tmp_path)
    on_disk = (tmp_path / FLIGHT_RECORDER_FILENAME).read_text(encoding="utf-8")
    assert "sk-ant-api03-AAAA" not in on_disk and "REDACTED" in on_disk  # redaction door
    assert resolve_flight_recorder(str(tmp_path), sha)[0] == "verified"  # sha round-trip
    assert resolve_flight_recorder(str(tmp_path), None) == ("absent", None)  # honest absence
    assert resolve_flight_recorder(str(tmp_path), "deadbeef")[0] == "sha_mismatch"
    # a native-format / non-verdi log never gets verdi semantics — no reasoning
    assert GenericAdapter().normalize_reasoning({"foo": 1}) is None
    assert GenericAdapter().normalize_reasoning({"verdi_log_version": 1}) is None


def test_ac1_sha_hoisted_additive_absent_when_none(tmp_path):
    """flight_recorder_sha rides the top-level event (transport-only in the
    record), and a reasoning-less trial simply lacks the field — no reader may
    require it, the trajectory_sha precedent."""
    ledger = tmp_path / "ledger.ndjson"
    ctx = ctx_for(tmp_path)
    with_fr = TrialRecord.assemble(
        trial_id="t1", task_id="a", arm="control", repetition=0, outcome=Outcome.completed,
        telemetry=Telemetry(), provenance=Provenance(), flight_recorder_sha="abc123",
    )
    record_trial(ledger, ctx, trial_record=with_fr.model_dump(mode="json"))
    without = TrialRecord.assemble(
        trial_id="t2", task_id="a", arm="control", repetition=0, outcome=Outcome.completed,
        telemetry=Telemetry(), provenance=Provenance(),
    )
    record_trial(ledger, ctx, trial_record=without.model_dump(mode="json"))
    evs = {e["trial_record"]["trial_id"]: e for e in find_events(ledger, E.TRIAL)}
    assert evs["t1"]["flight_recorder_sha"] == "abc123"          # hoisted to the event
    assert "flight_recorder_sha" not in evs["t1"]["trial_record"]  # payload shape unchanged
    assert "flight_recorder_sha" not in evs["t2"]                 # absent = captured none


def test_ac1_corrupt_recorder_reason_is_named():
    """A capture-time FlightRecorderCorruptError fails the trial closed with a
    clean machine-readable reason (the trajectory_corrupt precedent), not the
    generic trial_error fallback."""
    from harness.run.flight_recorder import FlightRecorderCorruptError
    from harness.run.interleave import _reason_for

    assert _reason_for(FlightRecorderCorruptError("boom")) == "flight_recorder_corrupt"


# --- AC-2: judge/grade/fence isolation BY CONSTRUCTION -------------------------
def test_ac2_recorder_judge_grade_isolated():
    """The recorder is not a parameter of the judge packet, the per-arm response
    view, or the deterministic grade — so reasoning cannot reach a verdict, a
    score, or an official decision."""
    from harness.grade.deterministic import grade_trial
    from harness.judge.packet import ResponseArtifacts, build_packet

    forbidden = {"reasoning", "flight_recorder", "flight_recorder_sha", "recorder"}
    packet_params = set(inspect.signature(build_packet).parameters)
    assert packet_params == {"response_a", "response_b", "rubric", "task_prompt"}
    assert not (packet_params & forbidden)
    assert not ({f.name for f in fields(ResponseArtifacts)} & forbidden)
    assert not (set(inspect.signature(grade_trial).parameters) & forbidden)


# --- AC-3: advisory review over reasoning — blinded, bounded, config-resolved --
def _flagging_response(flagged_detector: str) -> str:
    from harness.forensics.detectors import DETECTOR_IDS

    return json.dumps({
        "suspicions": {d: (d == flagged_detector) for d in DETECTOR_IDS},
        "narrative": "the reasoning admits hardcoding the expected answer",
    })


def test_ac3_recorder_review_blinded_bounded():
    """Reasoning is scrubbed before the provider (blinded — mangled, not blocked),
    its pathology signal reaches the reviewer, and the byte budget / unconfigured
    model fail closed to named reasons."""
    from harness.forensics.review import JUDGMENT_TAG, forensic_review
    from harness.judge.providers.fake import FakeProvider

    canaries = ["control", "treatment", "claude-haiku-4-5", "openai/gpt-4o-2024-08-06"]
    reasoning = ("I am Claude, made by Anthropic; the treatment arm's gpt-4o would "
                 "struggle. To finish fast I'll hardcode the expected answer and skip "
                 "running the real holdout test.")
    provider = FakeProvider([_flagging_response("hardcoded_expected_output")])
    review = forensic_review(
        "t1", reasoning, canaries=canaries, provider=provider,
        provider_model="anthropic/claude-haiku-4-5-20251001",
        max_reasoning_bytes=DEFAULT_REASONING_BUDGET_BYTES,
    )
    # blinded: no identity reaches the provider payload (scrubbed, not refused)
    assert review.cant_review_reason is None
    payload = "".join(m["content"] for m in provider.calls[0]["messages"]).lower()
    for canary in ("claude", "anthropic", "control", "treatment", "gpt-4o"):
        assert canary not in payload, canary
    # the reasoning's pathology signal survives the scrub and reaches the reviewer
    assert review.suspicions["hardcoded_expected_output"] is True
    assert review.narrative.startswith(JUDGMENT_TAG)
    # D003: an over-budget reasoning transcript degrades to a named coverage gap
    over_budget = "reasoning about the plan " * 12000  # > 262144 bytes
    assert forensic_review(
        "t1", over_budget, provider=provider,
        max_reasoning_bytes=DEFAULT_REASONING_BUDGET_BYTES,
    ).cant_review_reason == "context_overflow"
    # D002: no hardcoded model default — an unconfigured model fails closed
    assert forensic_review("t1", reasoning).cant_review_reason == "provider_error"


# --- AC-4: null-honest capture, disclosed asymmetry, primary-metric ineligible -
def test_ac4_reasoning_null_honest_asymmetry_disclosed(tmp_path):
    """Reasoning is never a primary metric; a mixed experiment (one arm captures
    reasoning, one does not) surfaces a disclosed confound, and symmetric capture
    raises none (null-honest, no false positive)."""
    from harness.analyze.confounds import _flag_reasoning_capture_asymmetry, flag_confounds
    from harness.schema.metrics import PrimaryMetric

    assert "reasoning" not in PrimaryMetric.values()
    assert "flight_recorder" not in PrimaryMetric.values()

    spec, _spec_path, ledger = locked_experiment(tmp_path)  # arms: control, treatment

    def trial_line(arm: str, sha: str | None) -> str:
        ev = {"event": "trial", "trial_id": f"{arm}-1",
              "trial_record": {"trial_id": f"{arm}-1", "arm": arm, "task_id": "t"}}
        if sha is not None:
            ev["flight_recorder_sha"] = sha
        return json.dumps(ev)

    with ledger.open("a", encoding="utf-8") as f:  # find_events reads by type, not chain
        f.write(trial_line("control", "abc") + "\n")
        f.write(trial_line("treatment", None) + "\n")
    names = [fl["flag"] for fl in flag_confounds(ledger, spec)]
    assert "reasoning_capture_asymmetry" in names
    assert _flag_reasoning_capture_asymmetry(ledger)["reasoning_trials_by_arm"] == {
        "control": 1, "treatment": 0
    }
    # symmetric capture: no confound
    with ledger.open("a", encoding="utf-8") as f:
        f.write(trial_line("treatment", "def").replace("treatment-1", "treatment-2") + "\n")
    # now both arms have >=1 captured trial → asymmetry clears
    assert _flag_reasoning_capture_asymmetry(ledger) is None


# --- AC-5: unblinded operator compare render ----------------------------------
def test_ac5_recorder_operator_tier_exploratory(tmp_path):
    """A real generic-arm run captures reasoning; the paired compare view surfaces
    it per arm, unblinded (operator tier). The observability LLM-free contract is
    enforced by lint-imports (make verify)."""
    from harness.judge.assemble import comparison_id_for
    from harness.plan.interleave import derive_schedule, enumerate_trials
    from harness.run.engines.fake import FakeEngine
    from harness.run.interleave import schedule
    from harness.run.types import RunConfig, Task
    from harness.serve.compare import paired_comparisons

    generic_arms = [
        {"name": "control", "platform": "generic",
         "model": "anthropic/claude-haiku-4-5-20251001", "payload": {}},
        {"name": "treatment", "platform": "generic",
         "model": "openai/gpt-4.1-mini-2025-04-14", "payload": {}},
    ]
    spec, _spec_path, ledger = locked_experiment(tmp_path, arms=generic_arms, repetitions=1)
    (tmp_path / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": [{"id": "t1", "prompt": "p"}]}), encoding="utf-8"
    )
    ctx = ctx_for(tmp_path)
    arms = {a.name: a for a in spec.arms}
    native = {"verdi_log_version": 1, "telemetry": {"tokens_out": 40},
              "trajectory": [{"kind": "file_edit", "files_touched": ["solution.py"], "agent": "worker-1"}],
              "reasoning": [
                  {"content": "weighed the edge cases first", "agent": "planner"},
                  {"content": "then wrote add(a, b) returning a + b", "agent": "worker-1"}]}
    tasks = {"t1": Task(id="t1", prompt="p", fake_behavior={"native_log": native})}
    order = derive_schedule(spec.seed, enumerate_trials(["t1"], list(arms), 1))
    schedule(order, tasks=tasks, arms=arms, workspace_root=tmp_path / "workspaces",
             ledger_path=ledger, ctx=ctx, config=RunConfig(engine=FakeEngine()),
             cost_ceiling=spec.cost_ceiling.amount)

    trial_ids: dict[str, str] = {}
    for ev in read_events(ledger):
        if ev.get("event") == "trial":
            rec = ev["trial_record"]
            trial_ids[rec["arm"]] = rec["trial_id"]
            assert ev.get("flight_recorder_sha") is not None  # captured + chain-bound
            ws = Path(rec["artifacts_path"]).parent
            ws.mkdir(parents=True, exist_ok=True)
            (ws / "solution.py").write_text(f"# {rec['arm']}\n", encoding="utf-8")
    for arm, passed in (("control", False), ("treatment", True)):
        E.record_grade(ledger, ctx, trial_id=trial_ids[arm], task_sha="sha-x",
                       assertions=[{"id": "h1", "source": "holdout_test",
                                    "result": "pass" if passed else "fail"}],
                       binary_score=passed)
    E.append_verdict(ledger, ctx, verdict={
        "comparison_id": comparison_id_for("t1", 0), "winner": "B", "reason": "x",
        "provenance": {"judge_model": "google/gemini-1.5-pro-002", "rubric_sha256": "s"}})

    c = paired_comparisons(tmp_path)
    # the compare payload names the models each arm actually ran (UI clarity)
    assert c["arm_a_model"] == "anthropic/claude-haiku-4-5-20251001"
    assert c["arm_b_model"] == "openai/gpt-4.1-mini-2025-04-14"
    pair = c["pairs"][0]
    # the compare view surfaces per-arm reasoning, unblinded (operator tier),
    # carrying the per-entry sub-agent role the UI groups by [AC-6]
    assert pair["a"]["reasoning"][0]["content"].startswith("weighed")
    assert pair["a"]["reasoning"][0]["agent"] == "planner"
    assert "add(a, b)" in pair["b"]["reasoning"][1]["content"]
    assert pair["b"]["reasoning"][1]["agent"] == "worker-1"


def test_ac6_reasoning_attributed_to_subagent():
    """Reasoning attributes to a sub-agent over the closed EVAL-21 vocabulary;
    out-of-vocabulary labels are refused; slice_reasoning_by_agent groups a
    workflow's reasoning by role with the unattributed bucket explicit [AC-6]."""
    import pytest

    from harness.adapters.generic import GenericAdapter, GenericLogError
    from harness.run.flight_recorder import (
        UNATTRIBUTED,
        FlightRecorder,
        ReasoningEntry,
        slice_reasoning_by_agent,
    )

    # valid roles (with ordinal) accepted; null = unattributed (v1 backward compat)
    assert ReasoningEntry(content="plan", agent="planner").agent == "planner"
    assert ReasoningEntry(content="w", agent="worker-2").agent == "worker-2"
    assert ReasoningEntry(content="x").agent is None
    # out-of-vocabulary label refused at the schema (identity unrepresentable)
    with pytest.raises(ValueError):
        ReasoningEntry(content="x", agent="llama-planner")
    # a declared generic log with a bad reasoning agent fails loud
    with pytest.raises(GenericLogError):
        GenericAdapter().normalize_reasoning(
            {"verdi_log_version": 1, "reasoning": [{"content": "x", "agent": "wizard"}]})
    # a real workflow log parses agent-attributed reasoning; slice groups by role
    entries = GenericAdapter().normalize_reasoning({"verdi_log_version": 1, "reasoning": [
        {"content": "decompose the task", "agent": "planner"},
        {"content": "solve add(a, b)", "agent": "worker-1"},
        {"content": "solve palindrome", "agent": "worker-2"},
        {"content": "ambient note"}]})            # unattributed
    rec = FlightRecorder(trial_id="t", platform="generic", entries=entries)
    assert rec.schema_version == 3                # additive v3 bump (charter linkage)
    groups = slice_reasoning_by_agent(rec)
    assert set(groups) == {"planner", "worker-1", "worker-2", UNATTRIBUTED}
    assert [e.content for e in groups["planner"]] == ["decompose the task"]
    assert [e.content for e in groups[UNATTRIBUTED]] == ["ambient note"]
