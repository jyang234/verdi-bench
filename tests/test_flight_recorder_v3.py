"""Flight recorder v3 — thought↔action linkage [flight-recorder charter].

The user-approved additive schema bump: ``ReasoningEntry`` gains ``relative_ts``
and ``turn`` (the 0-based trajectory-step index a reasoning span belongs to),
so operator-tier views can interleave reasoning with the trajectory into one
process timeline. Additive-null-defaulted like v1→v2: older records read back
with both null forever; no reader may require them. ``trial_detail`` exposes
the sha-verified recorder so the per-trial process view has both halves.
"""

from __future__ import annotations

import json

import pytest
import yaml

from harness.adapters.generic import GenericAdapter, GenericLogError
from harness.run.flight_recorder import (
    FLIGHT_RECORDER_SCHEMA_VERSION,
    FlightRecorder,
    ReasoningEntry,
    persist_flight_recorder,
    resolve_flight_recorder,
)
from harness.status.trial import trial_detail
from tests.fixtures.builders import fixed_ctx, locked_experiment
from tests.test_eval14_observability_ui import rich_experiment


def test_v3_linkage_roundtrips_through_persist_and_resolve(tmp_path):
    rec = FlightRecorder(trial_id="t", platform="generic", entries=[
        ReasoningEntry(content="plan", agent="planner", relative_ts=1.5, turn=0),
        ReasoningEntry(content="draft", agent="worker-1", relative_ts=9.0, turn=1, tokens=40),
        ReasoningEntry(content="ambient note"),  # unlinked — legitimate
    ])
    assert rec.schema_version == FLIGHT_RECORDER_SCHEMA_VERSION == 3
    sha = persist_flight_recorder(rec, tmp_path)
    status, back = resolve_flight_recorder(str(tmp_path), sha)
    assert status == "verified"
    assert [(e.turn, e.relative_ts) for e in back.entries] == [(0, 1.5), (1, 9.0), (None, None)]


def test_v2_record_bytes_read_back_with_null_linkage(tmp_path):
    """Compatibility pin: a pre-v3 artifact (schema_version 2, no linkage keys)
    parses unchanged — both fields null, never an error, never imputed."""
    old = {
        "schema_version": 2,
        "trial_id": "t-old",
        "platform": "generic",
        "entries": [{"content": "legacy reasoning", "tokens": 7, "cost": None, "agent": "planner"}],
    }
    path = tmp_path / "flight_recorder.json"
    path.write_text(json.dumps(old, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    import hashlib

    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    status, back = resolve_flight_recorder(str(tmp_path), sha)
    assert status == "verified"
    (entry,) = back.entries
    assert entry.relative_ts is None and entry.turn is None
    assert entry.content == "legacy reasoning" and entry.tokens == 7


def test_turn_is_a_declaration_not_a_guess():
    # junk (a boolean where a number belongs) is unmeasurable ⇒ null [D004]
    assert ReasoningEntry(content="x", turn=True).turn is None
    assert ReasoningEntry(content="x", relative_ts="soon").relative_ts is None
    # a NEGATIVE index is a malformed declaration and is refused loudly —
    # never laundered into "unlinked" (the closed-vocabulary precedent)
    with pytest.raises(ValueError):
        ReasoningEntry(content="x", turn=-1)


def test_generic_adapter_parses_linkage_at_any_declared_version():
    for version in (1, 2):
        entries = GenericAdapter().normalize_reasoning({
            "verdi_log_version": version,
            "reasoning": [
                {"content": "plan", "agent": "planner", "relative_ts": 2.0, "turn": 0},
                {"content": "unlinked ambient note"},
            ],
        })
        assert (entries[0].turn, entries[0].relative_ts) == (0, 2.0)
        assert (entries[1].turn, entries[1].relative_ts) == (None, None)
    # a malformed declared link fails the parse loudly (fails the trial closed
    # at the scheduler door), never a silently unlinked entry
    with pytest.raises(GenericLogError):
        GenericAdapter().normalize_reasoning(
            {"verdi_log_version": 1, "reasoning": [{"content": "x", "turn": -2}]})


# --- trial_detail exposes the recorder for the per-trial process view --------------
def _linked_experiment(dirpath):
    """One generic-platform trial whose native log carries a 2-step trajectory
    and linkage-bearing reasoning (two linked turns + one unlinked note)."""
    from harness.plan.interleave import derive_schedule, enumerate_trials
    from harness.run.engines.fake import FakeEngine
    from harness.run.interleave import schedule
    from harness.run.types import RunConfig, Task

    arms = [{"name": "control", "platform": "generic",
             "model": "anthropic/claude-haiku-4-5-20251001", "payload": {}},
            {"name": "treatment", "platform": "generic",
             "model": "openai/gpt-4.1-mini-2025-04-14", "payload": {}}]
    spec, _sp, ledger = locked_experiment(dirpath, arms=arms, repetitions=1)
    (dirpath / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": [{"id": "t1", "prompt": "p"}]}), encoding="utf-8")
    native = {
        "verdi_log_version": 2,
        "telemetry": {"tokens_out": 90},
        "trajectory": [
            {"kind": "message", "agent": "planner", "relative_ts": 2.0, "detail": "the plan"},
            {"kind": "file_edit", "agent": "worker-1", "relative_ts": 9.0,
             "files_touched": ["solution.py"], "detail": "the code"},
        ],
        "reasoning": [
            {"content": "thought before planning", "agent": "planner", "relative_ts": 1.5, "turn": 0, "tokens": 30},
            {"content": "thought before editing", "agent": "worker-1", "relative_ts": 8.0, "turn": 1, "tokens": 60},
            {"content": "clock-only note", "relative_ts": 8.5},  # ts merge, no turn
            {"content": "ambient unlinked note"},
        ],
    }
    tasks = {"t1": Task(id="t1", prompt="p", fake_behavior={"native_log": native})}
    arms_by_name = {a.name: a for a in spec.arms}
    order = derive_schedule(spec.seed, enumerate_trials(["t1"], list(arms_by_name), 1))
    schedule(order, tasks=tasks, arms=arms_by_name, workspace_root=dirpath / "workspaces",
             ledger_path=ledger, ctx=fixed_ctx(experiment_id=dirpath.name),
             config=RunConfig(engine=FakeEngine()), cost_ceiling=spec.cost_ceiling.amount)
    from harness.ledger.query import find_events

    return [ev["trial_record"]["trial_id"] for ev in find_events(ledger, "trial")]


def test_trial_detail_carries_the_verified_recorder(tmp_path):
    trial_ids = _linked_experiment(tmp_path)
    d = trial_detail(tmp_path, trial_ids[0])
    fr = d["flight_recorder"]
    assert fr["status"] == "verified"
    assert [e["turn"] for e in fr["entries"]] == [0, 1, None, None]
    assert [e["relative_ts"] for e in fr["entries"]] == [1.5, 8.0, 8.5, None]
    assert fr["entries"][0]["content"] == "thought before planning"


def test_trial_detail_recorder_absence_is_honest(tmp_path):
    """A platform that captured no reasoning reads back status 'absent' with no
    entries — a state, never an error, and never an empty-list impersonation."""
    fx = rich_experiment(tmp_path)  # claude_code-platform log: no reasoning
    d = trial_detail(tmp_path, fx["flagged"])
    assert d["flight_recorder"] == {"status": "absent", "entries": None}
