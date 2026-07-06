"""The multi-turn reference image emits a verdi-compliant, agent-attributed log.

Validates ``images/reference/multi-agent/agent.py``'s PURE ``build_agent_log``
against the real verdi parsers — proving the reference is harbor/EVAL-21/EVAL-24
compliant without docker or real keys. [images/reference/multi-agent/README.md]
"""

from __future__ import annotations

import importlib.util
from collections import Counter
from pathlib import Path

from harness.adapters.generic import GenericAdapter, normalize_generic_by_model
from harness.run.flight_recorder import FlightRecorder, slice_reasoning_by_agent

_AGENT = Path(__file__).resolve().parents[1] / "images" / "reference" / "multi-agent" / "agent.py"
_MODEL = "anthropic/claude-haiku-4-5-20251001"


def _load_agent():
    spec = importlib.util.spec_from_file_location("_ma_ref_agent", _AGENT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # import-safe: main() is guarded, build_agent_log is pure
    return mod


def _sample_log():
    agent = _load_agent()
    # an ordered multi-turn run: planner → worker drafts+revises → critic → orchestrator
    turns = [
        {"agent": "planner", "reasoning": "decompose: add, then palindrome", "kind": "message",
         "detail": "plan: add | is_palindrome", "tokens": 20, "model": _MODEL, "ts": 1.5},
        {"agent": "worker-1", "reasoning": "draft: add returns a + b", "kind": "file_edit",
         "detail": "def add(a,b): return a+b", "files": ["solution.py"], "tokens": 15, "model": _MODEL, "ts": 3.2},
        {"agent": "worker-1", "reasoning": "revise: no edge cases needed", "kind": "file_edit",
         "detail": "def add(a, b):\n    return a + b", "files": ["solution.py"], "tokens": 12, "model": _MODEL, "ts": 5.0},
        {"agent": "worker-2", "reasoning": "draft: palindrome via slicing", "kind": "file_edit",
         "detail": "def is_palindrome(s): return s==s[::-1]", "files": ["solution.py"], "tokens": 18, "model": _MODEL, "ts": 7.1},
        {"agent": "worker-2", "reasoning": "revise: normalize case", "kind": "file_edit",
         "detail": "def is_palindrome(s):\n    return s.lower()==s.lower()[::-1]",
         "files": ["solution.py"], "tokens": 14, "model": _MODEL, "ts": 9.4},
        {"agent": "critic", "reasoning": "checks out; no bugs", "kind": "message",
         "detail": "looks correct", "tokens": 10, "model": _MODEL, "ts": 11.0},
        # the closing report: deterministic (no tokens — honest null), stating the
        # deliverable, its provenance, the critique's disposition, and a REAL
        # import-check exit code [the aggregation must not end the record mutely]
        {"agent": "orchestrator", "reasoning":
            "final deliverable: solution.py (4 lines, defining add, is_palindrome) "
            "assembled from 2 workers' revised outputs (6 model turns total); critic "
            "note recorded above, not auto-applied; import smoke check exit 0",
         "kind": "test_run", "command": "python3 -c 'import solution'",
         "detail": "import ok", "exit_code": 0, "ts": 12.5},
    ]
    return agent.build_agent_log(model=_MODEL, turns=turns)


def test_reference_log_is_multi_turn_agent_attributed_and_compliant():
    log = _sample_log()
    adapter = GenericAdapter()

    # reasoning is agent-attributed AND multi-turn: each worker appears TWICE
    # (draft + revise), so the iteration is visible [EVAL-24 AC-6].
    entries = adapter.normalize_reasoning(log)
    by_role = Counter(e.agent for e in entries)
    assert by_role["worker-1"] == 2 and by_role["worker-2"] == 2
    assert set(by_role) == {"planner", "worker-1", "worker-2", "critic", "orchestrator"}

    # trajectory: agent-attributed steps that carry the turn's RESPONSE in detail
    # (the code a worker wrote), not just "a file was edited" [EVAL-21].
    steps = adapter.normalize_trajectory(log)
    file_edits = [s for s in steps if s.kind == "file_edit"]
    assert len(file_edits) == 4 and all(s.detail for s in file_edits)
    assert {s.agent for s in steps} == {"planner", "worker-1", "worker-2", "critic", "orchestrator"}

    # telemetry_by_model summed across all the turns
    by_model = normalize_generic_by_model(log, [_MODEL])
    assert by_model is not None and by_model[_MODEL].tokens_out == 20 + 15 + 12 + 18 + 14 + 10

    # the flight recorder slices reasoning by sub-agent, preserving per-turn order
    rec = FlightRecorder(trial_id="t", platform="generic", entries=entries)
    groups = slice_reasoning_by_agent(rec)
    assert len(groups["worker-1"]) == 2 and groups["worker-1"][0].content.startswith("draft")

    # per-turn usage rides the reasoning entry when measured; the deterministic
    # orchestrator turn reports none and reads back null — never zero [EVAL-4-D004]
    assert [e.tokens for e in groups["worker-1"]] == [15, 12]
    assert groups["orchestrator"][0].tokens is None
    # ... and its closing report is a complete final statement with the REAL
    # smoke-check exit code on the step, not a mute "aggregated N outputs"
    assert groups["orchestrator"][0].content.startswith("final deliverable:")
    assert "not auto-applied" in groups["orchestrator"][0].content
    (orch_step,) = [s for s in steps if s.agent == "orchestrator"]
    assert orch_step.kind == "test_run" and orch_step.exit_code == 0

    # v3 linkage [flight-recorder charter]: every reasoning entry declares its
    # own step's index and the turn's measured clock, and the steps carry the
    # same clock — thought and action interleave into ONE process timeline
    assert [e.turn for e in entries] == list(range(7))
    assert [e.relative_ts for e in entries] == [1.5, 3.2, 5.0, 7.1, 9.4, 11.0, 12.5]
    assert [s.relative_ts for s in steps] == [1.5, 3.2, 5.0, 7.1, 9.4, 11.0, 12.5]


def test_reference_log_survives_the_persist_redaction_door(tmp_path):
    """A leaked key in a worker's reasoning is scrubbed by the real recorder door."""
    from harness.run.flight_recorder import persist_flight_recorder, resolve_flight_recorder

    log = _sample_log()
    log["reasoning"][1]["content"] += " (used sk-ant-api03-" + "A" * 90 + ")"
    entries = GenericAdapter().normalize_reasoning(log)
    rec = FlightRecorder(trial_id="t", platform="generic", entries=entries)
    sha = persist_flight_recorder(rec, tmp_path)
    text = (tmp_path / "flight_recorder.json").read_text(encoding="utf-8")
    assert "sk-ant-api03-AAAA" not in text and "REDACTED" in text
    assert resolve_flight_recorder(str(tmp_path), sha)[0] == "verified"
