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
    # call order per arm: canary probe (t-canary), then TRUE + CONTROL oracle
    # probes (t-oracle) [F-M-C2: two calls per oracle task]
    provider = FakeProvider([
        f"…and the document continues: <!-- {canary_a} -->",  # regurgitation!
        _ORACLE[len(_ORACLE) // 2 :],                          # verbatim TRUE continuation
        "pass  # nothing recalled for the perturbed prefix",   # CONTROL: no lift
        "I cannot recall that document.",                      # arm 2: negative
        "def something_else(): return None  # unrelated guess",  # arm 2 TRUE
        "pass  # arm 2 control",                               # arm 2 CONTROL
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
    # F-M-C2: both conditions' scores + the margin ride the event
    sc = control["oracle_scores"]["t-oracle"]
    assert sc["true"] >= 0.5 and sc["margin"] >= 0.2
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
        overlap_flags={"control": {"t1": True}},
    )
    probe = event["probe"]
    assert probe["status"] == "cant_probe"
    assert probe["reason"] == "timeout"  # the shared closed-set reason enum
    assert "arms" not in probe  # no partial LLM outcomes, not even the probed arm
    # …but the deterministic AC-4 evidence computed from disk survives the
    # provider outage on the same event [review fix]
    assert probe["overlap_flags"] == {"control": {"t1": True}}
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


def test_probe_strips_embedded_marker_before_sending(tmp_path):
    """A materialized prompt carries the embedded marker (that is what
    admission produces); the probe strips it and probes normally — the marker
    must never reach the model, and its presence must not disable the channel
    [review fix]."""
    ledger = tmp_path / "ledger.ndjson"
    canary = derive_canary(_SHA_A)
    provider = FakeProvider(["nothing memorized"])
    event = run_memory_probe(
        ledger, fixed_ctx(), arms=[_arm()],
        tasks=[ProbeTask(task_id="t1", task_sha=_SHA_A,
                         prompt=f"Fix the parser.\n\n<!-- {canary} -->\n",
                         has_canary=True)],
        provider=provider,
    )
    assert event["probe"]["status"] == "complete"
    assert event["probe"]["arms"]["control"]["outcomes"] == {"t1": "negative"}
    sent = provider.calls[0]["messages"][0]["content"]
    assert canary not in sent
    assert "Fix the parser." in sent


def test_probe_refuses_canary_in_prompt(tmp_path):
    """A canary surviving OUTSIDE its marker form would manufacture a false
    positive — the run fails closed with its own reason, before any provider
    call."""
    ledger = tmp_path / "ledger.ndjson"
    canary = derive_canary(_SHA_A)
    event = run_memory_probe(
        ledger, fixed_ctx(), arms=[_arm()],
        tasks=[ProbeTask(task_id="t1", task_sha=_SHA_A,
                         prompt=f"the token {canary} appears verbatim",
                         has_canary=True)],
        provider=FakeProvider([]),  # must never be called
    )
    probe = event["probe"]
    assert probe["status"] == "cant_probe"
    assert probe["reason"] == "canary_in_prompt"
    assert probe["task_id"] == "t1"


def test_probe_refuses_unfingerprintable_oracle(tmp_path):
    """An oracle too short to split-and-compare fails the run closed BEFORE
    any provider call — never a mid-run crash after burning API calls
    [review fix]."""
    ledger = tmp_path / "ledger.ndjson"
    event = run_memory_probe(
        ledger, fixed_ctx(), arms=[_arm()],
        tasks=[ProbeTask(task_id="t1", task_sha=_SHA_A,
                         prompt="p", oracle="too short to compare")],
        provider=FakeProvider([]),  # must never be called
    )
    probe = event["probe"]
    assert probe["status"] == "cant_probe"
    assert probe["reason"] == "oracle_unfingerprintable"
    assert probe["task_id"] == "t1"


def test_m_c3_alarms_and_skipped_ride_the_probe_event(tmp_path):
    """F-M-C3: insulation alarms and unscanned trials were stderr-only — a
    holdout-leak breach or a wiped-workspace trial evaporated, indistinguishable
    from scanned-clean downstream. They now ride the ledgered probe event."""
    ledger = tmp_path / "l.ndjson"
    ev = run_memory_probe(
        ledger, fixed_ctx(),
        arms=[_arm("control")],
        tasks=[ProbeTask(task_id="t1", task_sha=_SHA_A, prompt="p", has_canary=True)],
        provider=FakeProvider(["nothing memorized"]),
        alarms=["trial x: holdout leak"], skipped=["trial y: UNSCANNED"],
    )
    probe = ev["probe"]
    assert probe["alarms"] == ["trial x: holdout leak"]
    assert probe["skipped"] == ["trial y: UNSCANNED"]


def test_m_c3_official_fence_refuses_on_insulation_alarm(tmp_path):
    """F-M-C3: an insulation alarm on the latest probe is a violation that must
    be resolved (quarantine + re-scan/probe) — never rendered past. Named
    cant_analyze reason; probes predating the field are skipped."""
    import pytest

    from harness.analyze.report import (
        CantAnalyzeReason,
        InsulationAlarmError,
        _assert_no_insulation_alarms,
        cant_analyze_reason,
    )

    ledger = tmp_path / "l.ndjson"
    run_memory_probe(  # legacy-shaped probe: no alarms field -> no refusal
        ledger, fixed_ctx(), arms=[_arm("control")],
        tasks=[ProbeTask(task_id="t1", task_sha=_SHA_A, prompt="p", has_canary=True)],
        provider=FakeProvider(["nothing"]),
    )
    _assert_no_insulation_alarms(ledger)
    run_memory_probe(
        ledger, fixed_ctx(), arms=[_arm("control")],
        tasks=[ProbeTask(task_id="t1", task_sha=_SHA_A, prompt="p", has_canary=True)],
        provider=FakeProvider(["nothing"]),
        alarms=["trial x: holdout leak"],
    )
    with pytest.raises(InsulationAlarmError, match="quarantine"):
        _assert_no_insulation_alarms(ledger)
    assert (
        cant_analyze_reason(InsulationAlarmError("x"))
        is CantAnalyzeReason.insulation_alarm
    )


def test_m_c2_perturb_identifiers_is_deterministic():
    from harness.contamination.probe import perturb_identifiers

    out = perturb_identifiers(_ORACLE)
    assert out == perturb_identifiers(_ORACLE)  # pure, no randomness
    assert out != _ORACLE                       # identifiers actually renamed
    assert "def " in out and "return" in out    # keywords/structure preserved


def test_m_c2_formulaic_continuation_no_longer_flags(tmp_path):
    """F-M-C2: formulaic code a clean model can legitimately continue tripped
    the >=50% reconstruction test — and one false positive is asymmetric,
    refusing the official render. A model that continues the PERTURBED control
    prefix just as well (margin ~ 0) is now negative; only a memorization LIFT
    over the control flags."""
    from harness.contamination.probe import _split_oracle, perturb_identifiers

    ledger = tmp_path / "ledger.ndjson"
    _, true_remainder = _split_oracle(_ORACLE)
    _, control_remainder = _split_oracle(perturb_identifiers(_ORACLE))
    # a clean strong continuer: reconstructs formulaic code in BOTH conditions
    provider = FakeProvider([true_remainder, control_remainder])
    event = run_memory_probe(
        ledger, fixed_ctx(), arms=[_arm("control")],
        tasks=[ProbeTask(task_id="t-oracle", task_sha=_SHA_B,
                         prompt="p", oracle=_ORACLE)],
        provider=provider,
    )
    arm = event["probe"]["arms"]["control"]
    assert arm["outcomes"] == {"t-oracle": "negative"}
    sc = arm["oracle_scores"]["t-oracle"]
    assert sc["true"] >= 0.5          # would have flagged pre-control
    assert sc["control"] >= 0.5       # the control explains it away
    assert sc["margin"] < 0.2
