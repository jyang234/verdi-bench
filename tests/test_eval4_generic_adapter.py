"""Generic adapter — the zero-code path onto the adapter seam [EVAL-4 AC-2,
EVAL-12 AC-1].

A test subject that emits the verdi normalized log format runs under
``platform: generic`` with no harness-side code, and the ``Adapter`` base
speaks the format by default, so a bare subclass is a working adapter out of
the box. Honesty rules split by declaration: an *undeclared* (non-verdi) log
is honest nulls throughout, while structural violations inside a *declared*
log fail loudly — a typo'd field must never launder into "unmeasured".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.adapters import get_adapter, known_platforms
from harness.adapters.base import TELEMETRY_FIELDS, Adapter, Telemetry
from harness.adapters.generic import GenericAdapter, GenericLogError

FULL_LOG = {
    "verdi_log_version": 1,
    "telemetry": {
        "tokens_in": 1200,
        "tokens_out": 340,
        "tokens_cache": 800,
        "cost": 0.42,
        "wall_time_s": 61.5,
        "tool_calls": 7,
    },
    "trajectory": [
        {"kind": "message", "command": ""},
        {"kind": "tool_call", "relative_ts": 1.5, "tokens": 100, "cost": 0.01, "command": "ls"},
        {"kind": "file_edit", "files_touched": ["a.py"], "command": ""},
        {"kind": "test_run", "exit_code": 0, "command": "pytest -q"},
    ],
}


def test_generic_platform_registered():
    assert "generic" in known_platforms()
    assert isinstance(get_adapter("generic"), GenericAdapter)


def test_full_log_normalizes_field_for_field():
    t = get_adapter("generic").normalize(FULL_LOG)
    assert t == Telemetry(
        tokens_in=1200, tokens_out=340, tokens_cache=800,
        cost=0.42, wall_time_s=61.5, tool_calls=7,
    )
    assert t.null_fields() == []


def test_omitted_fields_are_honest_nulls():
    log = {"verdi_log_version": 1, "telemetry": {"tokens_in": 10}}
    t = get_adapter("generic").normalize(log)
    assert t.tokens_in == 10
    assert set(t.null_fields()) == {
        "tokens_out", "tokens_cache", "cost", "wall_time_s", "tool_calls",
    }


def test_omitted_telemetry_block_is_honest_nulls():
    t = get_adapter("generic").normalize({"verdi_log_version": 1})
    assert t.null_fields() == list(TELEMETRY_FIELDS)


def test_non_verdi_log_is_all_null_not_an_error():
    # No version stamp ⇒ the log never claimed the format: everything is
    # unmeasurable and the trajectory honestly absent — not a failure [D004].
    adapter = get_adapter("generic")
    t = adapter.normalize({"usage": {"input_tokens": 5}})
    assert t.null_fields() == list(TELEMETRY_FIELDS)
    assert adapter.normalize_trajectory({"events": []}) is None


@pytest.mark.parametrize(
    "bad", [{"verdi_log_version": 3}, {"verdi_log_version": "1"}, {"verdi_log_version": True}]
)
def test_unsupported_version_fails_loud(bad):
    with pytest.raises(GenericLogError):
        get_adapter("generic").normalize(bad)
    with pytest.raises(GenericLogError):
        get_adapter("generic").normalize_trajectory(bad)


def test_unknown_telemetry_key_fails_loud_not_null():
    # a typo'd field in a self-declared log must not launder into "unmeasured"
    log = {"verdi_log_version": 1, "telemetry": {"token_in": 10}}
    with pytest.raises(GenericLogError) as exc:
        get_adapter("generic").normalize(log)
    assert "token_in" in str(exc.value)


def test_non_object_telemetry_block_fails_loud():
    with pytest.raises(GenericLogError):
        get_adapter("generic").normalize({"verdi_log_version": 1, "telemetry": [1, 2]})


def test_trajectory_roundtrips_into_shared_schema():
    steps = get_adapter("generic").normalize_trajectory(FULL_LOG)
    assert [s.kind for s in steps] == ["message", "tool_call", "file_edit", "test_run"]
    assert steps[1].relative_ts == 1.5 and steps[1].tokens == 100
    assert steps[2].files_touched == ["a.py"]
    assert steps[3].exit_code == 0 and steps[3].command == "pytest -q"


def test_absent_trajectory_key_is_honest_absence():
    assert get_adapter("generic").normalize_trajectory({"verdi_log_version": 1}) is None


def test_malformed_step_fails_loud_with_index():
    log = {
        "verdi_log_version": 1,
        "trajectory": [{"kind": "message", "command": ""}, {"kind": "nonesuch"}],
    }
    with pytest.raises(GenericLogError) as exc:
        get_adapter("generic").normalize_trajectory(log)
    assert "trajectory[1]" in str(exc.value)


def test_non_list_trajectory_fails_loud():
    with pytest.raises(GenericLogError):
        get_adapter("generic").normalize_trajectory(
            {"verdi_log_version": 1, "trajectory": {}}
        )


def test_bare_adapter_subclass_speaks_generic_out_of_the_box():
    # The extensibility contract: a subclass that overrides nothing but the
    # platform name is a complete, working adapter for the normalized format.
    class MyStackAdapter(Adapter):
        platform = "my_stack"

    t = MyStackAdapter().normalize(FULL_LOG)
    assert t.cost == 0.42
    steps = MyStackAdapter().normalize_trajectory(FULL_LOG)
    assert steps is not None and len(steps) == 4


def test_generic_arm_runs_through_the_seam(tmp_path):
    # End to end on the fake engine: a `platform: generic` arm whose "agent"
    # emits the normalized log yields a full TrialRecord — telemetry populated,
    # trajectory persisted and sha-stamped — with zero platform-specific code.
    from harness.run.engines.fake import FakeEngine
    from harness.run.seam import run_trial
    from harness.run.trajectory import TRAJECTORY_FILENAME, load_trajectory
    from harness.run.types import RunConfig, Task
    from harness.schema.experiment import Arm

    arm = Arm(
        name="treatment", platform="generic",
        model="meta/llama-3-70b-instruct-20240620",
    )
    task = Task(id="t1", prompt="do the thing", fake_behavior={"native_log": FULL_LOG})
    rec = run_trial(task, arm, tmp_path / "ws", RunConfig(engine=FakeEngine()))
    assert rec.outcome.value == "completed"
    assert rec.telemetry.tokens_in == 1200 and rec.telemetry_nulls == []
    assert rec.trajectory_sha is not None
    traj = load_trajectory(Path(rec.artifacts_path) / TRAJECTORY_FILENAME)
    assert traj.platform == "generic" and len(traj.steps) == 4
