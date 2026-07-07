"""EVAL-2 §M4 — the shared JudgingSession, blinding by construction [refactor 05 §5].

The native and reused-control judging paths share one JudgingSession; its
loop behavior (idempotency, token ceiling, one-verdict-per-comparison) is pinned
end-to-end by the eval2 CLI and control-reuse-judge suites. This file pins the
one property those paths cannot show directly: the session *requires* the
spec-derived canaries, so a call site cannot silently judge against the generic
corpus alone the way the low-level ``judge_pair(canaries=None)`` default allows.
"""

from __future__ import annotations

import pytest

from harness.judge.session import JudgingSession
from tests.fixtures.builders import ctx_for
from tests.fixtures.judge_fakes import make_config


def test_judging_session_requires_canaries(tmp_path):
    """Blinding by construction [refactor 05 §5]: a session built with a None
    canary set would judge against the generic identity corpus alone — missing
    the contestants' declared identities (arm names, model ids) that only the
    spec-derived canaries scrub. It refuses that loudly rather than degrading
    silently the way ``judge_pair(canaries=None)`` does at the low level."""
    with pytest.raises(ValueError, match="canaries"):
        JudgingSession(
            tmp_path / "l.ndjson", ctx_for(tmp_path),
            config=make_config(), rubric="r", prompts={}, canaries=None,
        )


def test_judging_session_accepts_a_spec_derived_canary_set(tmp_path):
    """The complement: a provided list (the arm_canaries output) is accepted and
    stored for every judge_pair call the session drives. An empty list is a valid
    (spec-derived) set — distinct from a *forgotten* None argument."""
    session = JudgingSession(
        tmp_path / "l.ndjson", ctx_for(tmp_path),
        config=make_config(), rubric="r", prompts={},
        canaries=["arm-control", "anthropic/claude-haiku-4-5-20251001"],
    )
    assert session.canaries == ["arm-control", "anthropic/claude-haiku-4-5-20251001"]
