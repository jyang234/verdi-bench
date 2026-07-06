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

from harness.adapters.generic import GenericAdapter, GenericLogError
from harness.run.flight_recorder import (
    FLIGHT_RECORDER_SCHEMA_VERSION,
    FlightRecorder,
    ReasoningEntry,
    persist_flight_recorder,
    resolve_flight_recorder,
)
from harness.status.trial import trial_detail
from tests.fixtures.scenarios import linked_experiment, rich_experiment


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
def test_trial_detail_carries_the_verified_recorder(tmp_path):
    trial_ids = linked_experiment(tmp_path)
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
