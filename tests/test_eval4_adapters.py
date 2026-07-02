"""EVAL-4 AC-2 — adapter telemetry normalization; nulls flagged, never estimated."""

from __future__ import annotations

import pytest

from harness.adapters import get_adapter
from harness.adapters.base import Provenance, Telemetry, TrialRecord
from harness.adapters.claude_code import ClaudeCodeAdapter
from harness.adapters.codex import CodexAdapter


def test_ac2_claude_code_normalization():
    log = {
        "usage": {"input_tokens": 200, "output_tokens": 80, "cache_read_input_tokens": 40},
        "total_cost_usd": 0.031,
        "duration_ms": 5000,
        "tool_use_count": 7,
    }
    t = ClaudeCodeAdapter().normalize(log)
    assert (t.tokens_in, t.tokens_out, t.tokens_cache) == (200, 80, 40)
    assert t.cost == 0.031
    assert t.wall_time_s == 5.0
    assert t.tool_calls == 7
    assert t.null_fields() == []


def test_ac2_claude_code_counts_tool_use_from_transcript():
    log = {
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "messages": [
            {"content": [{"type": "tool_use"}, {"type": "text"}]},
            {"content": [{"type": "tool_use"}]},
        ],
    }
    t = ClaudeCodeAdapter().normalize(log)
    assert t.tool_calls == 2


def test_ac2_codex_normalization():
    log = {"token_usage": {"prompt_tokens": 150, "completion_tokens": 60}, "elapsed_seconds": 3.5}
    t = CodexAdapter().normalize(log)
    assert (t.tokens_in, t.tokens_out) == (150, 60)
    assert t.wall_time_s == 3.5


def test_ac2_null_not_estimated():
    """Codex reports no cost/cache ⇒ those are null and listed, never guessed."""
    log = {"token_usage": {"prompt_tokens": 10, "completion_tokens": 5}, "elapsed_seconds": 1.0}
    t = CodexAdapter().normalize(log)
    assert t.cost is None
    assert t.tokens_cache is None
    assert set(t.null_fields()) == {"cost", "tokens_cache", "tool_calls"}
    # the record's telemetry_nulls must mirror exactly — no imputation possible
    rec = TrialRecord.assemble(
        trial_id="x", task_id="t", arm="a", repetition=0,
        outcome="completed", telemetry=t, provenance=Provenance(),
    )
    assert set(rec.telemetry_nulls) == {"cost", "tokens_cache", "tool_calls"}


def test_ac2_telemetry_nulls_must_match():
    """A record cannot claim a value where telemetry is null, nor vice versa."""
    t = Telemetry(tokens_in=10)  # everything else None
    with pytest.raises(ValueError):
        TrialRecord(
            trial_id="x", task_id="t", arm="a", repetition=0, outcome="completed",
            telemetry=t, telemetry_nulls=[], provenance=Provenance(),
        )


def test_ac2_bool_not_coerced_to_number():
    # regression: a boolean where a count/cost is expected is unmeasurable (null),
    # never imputed to 1/0 [D004]
    log = {"usage": {"input_tokens": True}, "total_cost_usd": False}
    t = ClaudeCodeAdapter().normalize(log)
    assert t.tokens_in is None
    assert t.cost is None


def test_ac2_unknown_platform_rejected():
    from harness.adapters import UnknownPlatformError

    with pytest.raises(UnknownPlatformError):
        get_adapter("opencode")  # out of scope for v1
