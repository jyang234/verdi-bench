"""EVAL-4 AC-9 — holdout canaries never reach the trial; ADVISORY stamp."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from harness.adapters.base import ADVISORY
from harness.run.engines.fake import FakeEngine
from harness.run.seam import HoldoutLeakError, run_trial
from harness.run.types import RunConfig, Task
from harness.schema.experiment import Arm


def _arm():
    return Arm(name="A", platform="claude_code", model="anthropic/claude-3-5-sonnet-20241022")


def _artifact_blob(ws) -> str:
    blob = []
    for p in (ws / "artifacts").rglob("*"):
        if p.is_file():
            blob.append(p.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(blob)


@settings(max_examples=40, deadline=None)
@given(
    canary=st.text(alphabet="ABCDEFGHIJKLMNOP", min_size=8, max_size=16),
    prompt=st.text(alphabet="abcdef ghij", min_size=1, max_size=40),
)
def test_ac9_holdout_canaries_absent(tmp_path_factory, canary, prompt):
    # canary seeded into holdouts must never surface in the trial fs or prompt
    ws = tmp_path_factory.mktemp("ws")
    canary_token = "CANARY_" + canary
    task = Task(id="t", prompt=prompt, holdout_canaries=[canary_token],
                fake_behavior={"native_log": {}})
    rec = run_trial(task, _arm(), ws, RunConfig(engine=FakeEngine()))
    blob = _artifact_blob(ws)
    assert canary_token not in blob
    assert canary_token not in task.prompt  # prompt payload is canary-free


def test_ac9_leak_into_prompt_refused(tmp_path):
    canary = "CANARY_LEAK_1"
    task = Task(id="t", prompt=f"here is a holdout {canary}", holdout_canaries=[canary])
    with pytest.raises(HoldoutLeakError):
        run_trial(task, _arm(), tmp_path / "ws", RunConfig(engine=FakeEngine()))


def test_ac9_advisory_stamp(tmp_path):
    rec = run_trial(Task(id="t", prompt="p"), _arm(), tmp_path / "ws",
                    RunConfig(engine=FakeEngine()))
    assert rec.provenance.tier == ADVISORY
