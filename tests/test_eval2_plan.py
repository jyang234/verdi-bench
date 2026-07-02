"""EVAL-2 AC-5 (plan-time alias rejection) + M7 (multi-judge is possible)."""

from __future__ import annotations

import pytest

from harness.judge.client import judge_pair
from harness.judge.providers.fake import FakeProvider
from harness.schema.errors import AliasJudgeIdError
from harness.schema.experiment import ExperimentSpec
from tests.fixtures.builders import fixed_ctx, valid_experiment_dict
from tests.fixtures.judge_fakes import make_config, make_packet, verdict_json


def test_ac5_alias_model_id_rejected():
    data = valid_experiment_dict()
    data["judge"]["model"] = "google/gemini-pro"  # unversioned alias
    with pytest.raises(AliasJudgeIdError):
        ExperimentSpec.from_dict(data)


def test_ac5_versioned_judge_accepted():
    spec = ExperimentSpec.from_dict(valid_experiment_dict())
    assert spec.judge.model == "google/gemini-1.5-pro-002"


def test_ac1_multiple_judges_same_packet(tmp_path):
    """M7: because the judge is pure config, multiple judges can grade identical
    packets — verdict deltas between them measure judge bias directly. Nothing
    prevents it."""
    packet = make_packet()
    ledger = tmp_path / "l.ndjson"
    # judge 1 prefers content A; judge 2 is position-biased (always Response 1)
    v1 = judge_pair(packet, make_config(model="google/gemini-1.5-pro-002"),
                    ledger, fixed_ctx(), ts="t0",
                    provider=FakeProvider([verdict_json("1"), verdict_json("2")]))
    v2 = judge_pair(packet, make_config(model="anthropic/claude-3-5-sonnet-20241022"),
                    ledger, fixed_ctx(), ts="t0",
                    provider=FakeProvider([verdict_json("1"), verdict_json("1")]))
    assert v1.winner.value == "A"
    assert v2.winner.value == "TIE"  # the delta is the measurable bias signal
    assert v1.provenance.packet_sha256 == v2.provenance.packet_sha256  # identical packet
