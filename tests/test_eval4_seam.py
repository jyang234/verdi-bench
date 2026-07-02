"""EVAL-4 AC-1 — the seam contract suite, run against BOTH engines.

The fake and Harbor engines must produce equivalent, well-formed records from
equivalent inputs. This parametrized suite is the contract; the fake is also the
fixture backbone for downstream stories.
"""

from __future__ import annotations

import pytest

from harness.adapters.base import ADVISORY, Outcome, TrialRecord
from harness.run.engines.fake import FakeEngine
from harness.run.engines.harbor import HarborEngine
from harness.run.seam import run_trial
from harness.run.types import RunConfig, Task
from harness.schema.experiment import Arm
from tests.fixtures.run_fakes import FakeDockerRunner

NATIVE_LOG = {
    "usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 10},
    "total_cost_usd": 0.02,
    "duration_ms": 4200,
    "tool_use_count": 3,
}


def _arm():
    return Arm(name="control", platform="claude_code", model="anthropic/claude-3-5-sonnet-20241022")


def _configs():
    fake = RunConfig(engine=FakeEngine())
    harbor = RunConfig(engine=HarborEngine(runner=FakeDockerRunner(native_log=NATIVE_LOG)))
    return {"fake": fake, "harbor": harbor}


def _task_for(engine_name: str) -> Task:
    if engine_name == "fake":
        return Task(id="t1", prompt="do the thing", fake_behavior={"native_log": NATIVE_LOG})
    return Task(id="t1", prompt="do the thing")


@pytest.mark.parametrize("engine_name", ["fake", "harbor"])
def test_ac1_seam_contract(engine_name, tmp_path):
    config = _configs()[engine_name]
    rec = run_trial(_task_for(engine_name), _arm(), tmp_path / "ws", config)
    assert isinstance(rec, TrialRecord)
    assert rec.task_id == "t1"
    assert rec.arm == "control"
    assert rec.outcome == Outcome.completed
    # telemetry normalized identically from the same native log
    assert rec.telemetry.tokens_in == 100
    assert rec.telemetry.tokens_out == 50
    assert rec.telemetry.cost == 0.02
    assert rec.telemetry.tool_calls == 3
    assert rec.telemetry_nulls == []  # all fields measured
    assert rec.provenance.tier == ADVISORY
    assert rec.provenance.image_digest is not None
    assert rec.artifacts_path is not None


@pytest.mark.parametrize("engine_name", ["fake", "harbor"])
def test_ac1_record_shape_stable(engine_name, tmp_path):
    config = _configs()[engine_name]
    rec = run_trial(_task_for(engine_name), _arm(), tmp_path / "ws", config)
    # round-trips through the pydantic contract
    dumped = rec.model_dump(mode="json")
    assert TrialRecord.model_validate(dumped) == rec


def test_ac1_engine_isolated():
    """No module outside the run-engine seam imports Harbor [import-linter].

    Assert here too: only harness.run.engines.harbor references docker/Harbor.
    """
    import ast
    import pathlib

    # Only the engine module and the engine factory may name Harbor.
    allowed = {"harness/run/engines/harbor.py", "harness/run/engines/__init__.py"}
    root = pathlib.Path("harness")
    offenders = []
    for py in root.rglob("*.py"):
        if py.as_posix() in allowed:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [n.name for n in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                last = name.rsplit(".", 1)[-1]
                if last == "harbor" or name == "docker":
                    offenders.append((py.as_posix(), name))
    assert not offenders, f"Harbor/docker imported outside the seam: {offenders}"
