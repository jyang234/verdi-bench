"""Memory probe: fail-closed membership probes per arm model [EVAL-10 AC-3, D002]."""

from __future__ import annotations

import pytest

from harness.contamination.canary import derive_canary
from harness.contamination.probe import ProbeError, ProbeTask, run_memory_probe
from harness.judge.providers.base import ProviderTimeout
from harness.judge.providers.fake import FakeProvider
from harness.ledger.query import read_events
from harness.schema.experiment import Arm
from tests.fixtures.builders import fixed_ctx

_SHA_A = "a1" * 32
_SHA_B = "b2" * 32

_ORACLE = """
def normalize_scores(scores):
    total = sum(scores)
    if total == 0:
        raise ValueError("cannot normalize an all-zero score vector")
    return [s / total for s in scores]
"""


def _arm(name="control", model="anthropic/claude-3-5-sonnet-20241022"):
    return Arm(name=name, platform="claude_code", model=model)


def test_ac3_regurgitation_flags(tmp_path):
    """A fake model that regurgitates the canary flags the task; a model that
    completes the oracle verbatim flags via the oracle-prefix channel; a bland
    completion stays negative [AC-3]."""
    ledger = tmp_path / "ledger.ndjson"
    canary_a = derive_canary(_SHA_A)
    tasks = [
        ProbeTask(task_id="t-canary", task_sha=_SHA_A,
                  prompt="Fix the parser so trailing commas survive.", has_canary=True),
        ProbeTask(task_id="t-oracle", task_sha=_SHA_B,
                  prompt="Normalize the score vector.", oracle=_ORACLE),
    ]
    # call order per arm: canary probe for t-canary, oracle probe for t-oracle
    provider = FakeProvider([
        f"…and the document continues: <!-- {canary_a} -->",  # regurgitation!
        _ORACLE[len(_ORACLE) // 2 :],                          # verbatim continuation
        "I cannot recall that document.",                      # arm 2: negative
        "def something_else(): return None  # unrelated guess in reply",
    ])
    event = run_memory_probe(
        ledger, fixed_ctx(),
        arms=[_arm("control"), _arm("treatment", model="openai/gpt-4o-2024-08-06")],
        tasks=tasks, provider=provider,
    )
    probe = event["probe"]
    assert probe["status"] == "complete"
    control = probe["arms"]["control"]
    assert control["outcomes"] == {"t-canary": "flagged", "t-oracle": "flagged"}
    assert control["evidence"]["t-canary"] == ["canary_regurgitation"]
    assert control["evidence"]["t-oracle"] == ["oracle_prefix"]
    treatment = probe["arms"]["treatment"]
    assert treatment["outcomes"] == {"t-canary": "negative", "t-oracle": "negative"}
    # the probe prompt never contained the canary — the model produced it
    assert all(canary_a not in c["messages"][0]["content"] for c in provider.calls)
    # exactly one contamination_probe event for the whole run
    evs = [e for e in read_events(ledger) if e["event"] == "contamination_probe"]
    assert len(evs) == 1


def test_ac3_cant_probe_fail_closed(tmp_path):
    """A provider error fails the whole run closed: one CANT_PROBE event with
    the closed-set reason and NO outcomes — never a silent partial probe [AC-3]."""
    ledger = tmp_path / "ledger.ndjson"
    tasks = [
        ProbeTask(task_id="t1", task_sha=_SHA_A,
                  prompt="Fix the parser.", has_canary=True),
        ProbeTask(task_id="t2", task_sha=_SHA_B,
                  prompt="Normalize scores.", has_canary=True),
    ]
    # first arm answers, second arm times out — the whole run must fail closed,
    # discarding the first arm's outcomes rather than partially reporting them
    provider = FakeProvider([
        "nothing memorized",
        "nothing memorized",
        ProviderTimeout("upstream deadline exceeded"),
    ])
    event = run_memory_probe(
        ledger, fixed_ctx(),
        arms=[_arm("control"), _arm("treatment", model="openai/gpt-4o-2024-08-06")],
        tasks=tasks, provider=provider,
    )
    probe = event["probe"]
    assert probe["status"] == "cant_probe"
    assert probe["reason"] == "timeout"  # the shared closed-set reason enum
    assert "arms" not in probe  # no partial outcomes, not even the probed arm
    evs = [e for e in read_events(ledger) if e["event"] == "contamination_probe"]
    assert len(evs) == 1


def test_probe_unprobed_is_honest(tmp_path):
    """A task with no canary, no oracle, and no overlap scan is ``unprobed`` —
    absence of measurement is never reported as negative."""
    ledger = tmp_path / "ledger.ndjson"
    event = run_memory_probe(
        ledger, fixed_ctx(),
        arms=[_arm()],
        tasks=[ProbeTask(task_id="t-bare", task_sha=_SHA_A, prompt="do a thing")],
        provider=FakeProvider([]),  # no call must happen
    )
    outcomes = event["probe"]["arms"]["control"]["outcomes"]
    assert outcomes == {"t-bare": "unprobed"}


def test_probe_merges_overlap_channel(tmp_path):
    """The deterministic AC-4 scan merges into the same event: a scanned-clean
    task is negative, a scanned-flagged task is flagged with the channel named,
    and unknown keys are refused loudly."""
    ledger = tmp_path / "ledger.ndjson"
    tasks = [
        ProbeTask(task_id="t1", task_sha=_SHA_A, prompt="p1"),
        ProbeTask(task_id="t2", task_sha=_SHA_B, prompt="p2"),
    ]
    event = run_memory_probe(
        ledger, fixed_ctx(), arms=[_arm()], tasks=tasks,
        provider=FakeProvider([]),
        overlap_flags={"control": {"t1": True, "t2": False}},
    )
    arm_out = event["probe"]["arms"]["control"]
    assert arm_out["outcomes"] == {"t1": "flagged", "t2": "negative"}
    assert arm_out["evidence"]["t1"] == ["solution_overlap"]

    with pytest.raises(ProbeError, match="unknown arm"):
        run_memory_probe(
            ledger, fixed_ctx(), arms=[_arm()], tasks=tasks,
            provider=FakeProvider([]), overlap_flags={"ghost": {}},
        )
    with pytest.raises(ProbeError, match="unknown task"):
        run_memory_probe(
            ledger, fixed_ctx(), arms=[_arm()], tasks=tasks,
            provider=FakeProvider([]), overlap_flags={"control": {"ghost": True}},
        )


def test_probe_refuses_canary_in_prompt(tmp_path):
    """A probe prompt already containing the canary would manufacture a false
    positive — the run fails closed with its own reason, sending nothing."""
    ledger = tmp_path / "ledger.ndjson"
    canary = derive_canary(_SHA_A)
    event = run_memory_probe(
        ledger, fixed_ctx(), arms=[_arm()],
        tasks=[ProbeTask(task_id="t1", task_sha=_SHA_A,
                         prompt=f"embedded <!-- {canary} --> already",
                         has_canary=True)],
        provider=FakeProvider([]),  # must never be called
    )
    probe = event["probe"]
    assert probe["status"] == "cant_probe"
    assert probe["reason"] == "canary_in_prompt"
    assert probe["task_id"] == "t1"
