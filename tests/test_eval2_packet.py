"""EVAL-2 AC-2 — packet is identity-free by construction and by canary scan."""

from __future__ import annotations

import inspect

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from harness.judge.packet import (
    IdentityLeakError,
    ResponseArtifacts,
    build_packet,
    validate_identity_free,
)
from tests.fixtures.judge_fakes import make_packet


def test_ac2_packet_allowlist_only():
    """The build_packet signature is the allowlist: only task/rubric/diff/holdout.

    Arm labels, agent/model names, transcripts, telemetry, paths are not
    parameters, so they are structurally unreachable.
    """
    params = set(inspect.signature(build_packet).parameters)
    assert params == {"response_a", "response_b", "task_prompt", "rubric"}
    fields = set(ResponseArtifacts.__dataclass_fields__)
    assert fields == {"diff", "holdout_results"}  # outcomes only, no identity


def test_ac2_identity_canary_blocks_send():
    # an arm/model id leaking into a diff trips the canary and is never sent
    pkt = make_packet(diff_a="normal diff", diff_b="leaked arm control-treatment here")
    with pytest.raises(IdentityLeakError):
        validate_identity_free(pkt, canaries=["control-treatment"])


def test_ac2_clean_packet_passes():
    validate_identity_free(make_packet(), canaries=["arm-x", "arm-y"])  # no raise


@settings(max_examples=50, deadline=None)
@given(canary=st.text(alphabet="ABCDEFGHJKLMNP", min_size=6, max_size=14))
def test_ac2_packet_identity_free(canary):
    """Seed an identity literal into an allowlisted field ⇒ canary scan catches
    it; a packet carrying arm identity is never sent."""
    marker = "ARMID_" + canary
    pkt = build_packet(
        ResponseArtifacts(diff=f"code with {marker}", holdout_results=[]),
        ResponseArtifacts(diff="clean", holdout_results=[]),
        task_prompt="task",
        rubric="rubric",
    )
    with pytest.raises(IdentityLeakError):
        validate_identity_free(pkt, canaries=[marker])


def test_ac2_agent_name_patterns_caught():
    # generic agent/model name tells are also scanned (defense in depth)
    pkt = make_packet(diff_a="uses claude-code under the hood")
    with pytest.raises(IdentityLeakError):
        validate_identity_free(pkt)
