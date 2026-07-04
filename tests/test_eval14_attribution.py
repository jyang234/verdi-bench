"""EVAL-14 — per-agent trajectory attribution + per-model telemetry.

Attribution is the arm's self-reported testimony: it rides flags and
exploratory analysis, never the authoritative telemetry stream or an official
gate [AC-4]. Identity leakage via agent labels is unrepresentable by
construction — a closed role vocabulary validated at parse, the blind
subsystem untouched [AC-3, D003].
"""

from __future__ import annotations

import pytest

from harness.adapters.generic import (
    GenericLogError,
    normalize_generic_by_model,
    normalize_generic_trajectory,
)
from harness.run.trajectory import (
    AGENT_ROLES,
    TRAJECTORY_SCHEMA_VERSION,
    UNATTRIBUTED,
    TrajectoryRecord,
    TrajectoryStep,
    slice_by_agent,
)
from harness.schema.experiment import Arm

DECLARED = ["meta/llama-3-70b-instruct-20240620", "qwen/qwen2-coder-32b-20240901"]

V2_LOG = {
    "verdi_log_version": 2,
    "telemetry": {"cost": 0.42, "tokens_out": 340},
    "telemetry_by_model": {
        DECLARED[0]: {"cost": 0.30, "tokens_out": 300},
        DECLARED[1]: {"cost": 0.12, "tokens_out": 40},
    },
    "trajectory": [
        {"kind": "message", "command": "", "agent": "planner"},
        {"kind": "tool_call", "command": "ls", "agent": "worker-1"},
        {"kind": "test_run", "exit_code": 0, "command": "pytest -q", "agent": "worker-1"},
        {"kind": "message", "command": ""},  # unattributed
    ],
}

WORKFLOW_ARM = Arm.model_validate(
    {
        "name": "treatment",
        "platform": "generic",
        "model": DECLARED[0],
        "aux_models": [{"model": DECLARED[1]}],
        "payload": {},
    }
)


# --- AC-1: TrajectoryStep v3 --------------------------------------------------
def test_ac1_agent_field_additive_v3():
    assert TRAJECTORY_SCHEMA_VERSION == 3
    rec = TrajectoryRecord(
        trial_id="t", platform="generic",
        steps=[TrajectoryStep(kind="message", agent="planner")],
    )
    assert rec.steps[0].agent == "planner"
    # single-agent adapters never set it: honest null, no reader requires it
    assert TrajectoryStep(kind="message").agent is None


def test_ac1_v2_reads_back_null_agent():
    v2 = {
        "schema_version": 2,
        "trial_id": "old",
        "platform": "codex",
        "steps": [{"kind": "tool_call", "command": "ls"}, {"kind": "message", "command": ""}],
    }
    rec = TrajectoryRecord.model_validate(v2)
    assert rec.schema_version == 2
    assert all(s.agent is None for s in rec.steps)


# --- AC-2: generic log v2 -----------------------------------------------------
def test_ac2_log_v2_by_model_declared_keys():
    by_model = normalize_generic_by_model(V2_LOG, DECLARED)
    assert by_model[DECLARED[0]].cost == 0.30
    assert by_model[DECLARED[1]].tokens_out == 40
    # per-model nulls are honest nulls
    assert by_model[DECLARED[0]].tokens_in is None
    # v2 trajectory carries agent labels through the shared schema
    steps = normalize_generic_trajectory(V2_LOG)
    assert [s.agent for s in steps] == ["planner", "worker-1", "worker-1", None]


def test_ac2_undeclared_model_key_fails_loud():
    log = {
        "verdi_log_version": 2,
        "telemetry_by_model": {"openai/gpt-4o-2024-08-06": {"cost": 0.1}},
    }
    with pytest.raises(GenericLogError) as exc:
        normalize_generic_by_model(log, DECLARED)
    assert "openai/gpt-4o-2024-08-06" in str(exc.value)


def test_ac2_v1_logs_parse_unchanged():
    from harness.adapters import get_adapter

    v1 = {"verdi_log_version": 1, "telemetry": {"cost": 0.5}}
    t = get_adapter("generic").normalize(v1)
    assert t.cost == 0.5
    # a v1 (or non-verdi) log yields NO by-model attribution — honest absence
    assert normalize_generic_by_model(v1, DECLARED) is None
    assert normalize_generic_by_model({"usage": {}}, DECLARED) is None


# --- AC-3: closed role vocabulary ----------------------------------------------
def test_ac3_closed_role_vocabulary():
    # literal pin, deliberately not the constant: extending the vocabulary must
    # fail this test until a human approves a schema-version bump [D003]
    assert AGENT_ROLES == {
        "planner", "executor", "orchestrator", "router", "critic",
        "reviewer", "tester", "researcher", "worker",
    }
    assert TRAJECTORY_SCHEMA_VERSION == 3
    for label in sorted(AGENT_ROLES) + ["worker-1", "worker-42", "critic-2"]:
        assert TrajectoryStep(kind="message", agent=label).agent == label


@pytest.mark.parametrize(
    "bad", ["llama-planner", "Planner", "the good arm", "worker-", "worker-1234", ""]
)
def test_ac3_nonconforming_label_refused(bad):
    with pytest.raises(Exception) as exc:
        TrajectoryStep(kind="message", agent=bad)
    assert "vocabulary" in str(exc.value)
    # via the generic parse it is the format's named error, with the step index
    log = {"verdi_log_version": 2, "trajectory": [{"kind": "message", "agent": bad}]}
    with pytest.raises(GenericLogError) as exc2:
        normalize_generic_trajectory(log)
    assert "trajectory[0]" in str(exc2.value)


# --- AC-4: aggregation honesty --------------------------------------------------
def _run_workflow_trial(tmp_path, native_log):
    from harness.run.engines.fake import FakeEngine
    from harness.run.seam import run_trial
    from harness.run.types import RunConfig, Task

    task = Task(id="t1", prompt="p", fake_behavior={"native_log": native_log})
    return run_trial(task, WORKFLOW_ARM, tmp_path / "ws", RunConfig(engine=FakeEngine()))


def test_ac4_by_model_delta_surfaced_never_reconciled(tmp_path):
    log = dict(V2_LOG, telemetry={"cost": 0.50, "tokens_out": 340})
    rec = _run_workflow_trial(tmp_path, log)
    # totals untouched: authoritative stream is the whole-trial block
    assert rec.telemetry.cost == 0.50
    # by-model sums (0.42) differ from the total (0.50): surfaced, not fixed
    assert rec.flags.by_model_delta == {"cost": -0.08}
    assert rec.flags.telemetry_by_model[DECLARED[0]]["cost"] == 0.30


def test_ac4_authoritative_stream_unchanged(tmp_path):
    with_block = _run_workflow_trial(tmp_path / "a", V2_LOG)
    without = _run_workflow_trial(
        tmp_path / "b",
        {k: v for k, v in V2_LOG.items() if k != "telemetry_by_model"},
    )
    # the cost guard and every comparison read telemetry — identical either way
    assert with_block.telemetry == without.telemetry
    assert getattr(without.flags, "telemetry_by_model", None) is None
    # matching sums: attribution present, no delta flag fabricated
    assert getattr(with_block.flags, "by_model_delta", None) is None


def test_native_platform_log_with_verdi_key_never_fails_the_trial(tmp_path):
    # agent-controlled content: a claude_code/codex agent_log.json that happens
    # to carry verdi_log_version must NOT get verdi-format semantics — a
    # colliding key must not be able to fail a native arm's trial.
    from harness.run.engines.fake import FakeEngine
    from harness.run.seam import run_trial
    from harness.run.types import RunConfig, Task

    native_arm = Arm.model_validate(
        {"name": "control", "platform": "claude_code",
         "model": "anthropic/claude-3-5-sonnet-20241022", "payload": {}}
    )
    for bad_log in (
        {"verdi_log_version": 99},
        {"verdi_log_version": 2,
         "telemetry_by_model": {"undeclared/model-123": {"cost": 0.1}}},
    ):
        task = Task(id="t1", prompt="p", fake_behavior={"native_log": bad_log})
        rec = run_trial(
            task, native_arm, tmp_path / f"ws-{len(bad_log)}",
            RunConfig(engine=FakeEngine()),
        )
        assert rec.outcome.value == "completed"
        assert getattr(rec.flags, "telemetry_by_model", None) is None


def test_generic_log_error_has_machine_readable_reason():
    from harness.run.interleave import _reason_for

    assert _reason_for(GenericLogError("x")) == "generic_log_error"


def test_infra_failed_trial_keeps_engine_reason(tmp_path):
    # the by-model parse must not run on an infra-failed trial: a corrupt v2
    # block would mask the engine's more specific failure reason.
    from harness.run.engines.fake import FakeEngine
    from harness.run.seam import run_trial
    from harness.run.types import RunConfig, Task

    bad_log = {
        "verdi_log_version": 2,
        "telemetry_by_model": {"undeclared/model-123": {"cost": 0.1}},
    }
    task = Task(
        id="t1", prompt="p",
        fake_behavior={"outcome": "infra_failed", "infra_reason": "daemon_error",
                       "native_log": bad_log},
    )
    rec = run_trial(task, WORKFLOW_ARM, tmp_path / "ws", RunConfig(engine=FakeEngine()))
    assert rec.outcome.value == "infra_failed"
    assert rec.flags.failure_reason == "daemon_error"


# --- AC-5: the exploratory consumer ----------------------------------------------
def _seed_workflow_experiment(tmp_path):
    from tests.fixtures.builders import fixed_ctx, locked_experiment, seed_trial_and_grade

    arms = [
        {"name": "control", "platform": "claude_code",
         "model": "anthropic/claude-3-5-sonnet-20241022", "payload": {}},
        {"name": "treatment", "platform": "generic", "model": DECLARED[0],
         "aux_models": [{"model": DECLARED[1]}], "payload": {}},
    ]
    spec, _, ledger = locked_experiment(tmp_path, arms=arms)
    ctx = fixed_ctx()
    seed_trial_and_grade(
        ledger, ctx, trial_id="t-1", task_id="task-1", arm="control",
        telemetry={"cost": 0.2},
    )
    seed_trial_and_grade(
        ledger, ctx, trial_id="t-2", task_id="task-1", arm="treatment",
        telemetry={"cost": 0.42},
        flags={"telemetry_by_model": {
            DECLARED[0]: {"cost": 0.30}, DECLARED[1]: {"cost": 0.12},
        }},
    )
    return spec, ledger


def test_ac5_per_model_section_exploratory(tmp_path):
    from harness.analyze.report import _secondary_metrics, _secondary_lines
    from types import SimpleNamespace

    spec, ledger = _seed_workflow_experiment(tmp_path)
    sm = _secondary_metrics(ledger, spec)
    assert sm["exploratory"] is True
    assert sm["per_model_means"]["treatment"][DECLARED[0]]["cost"] == 0.30
    lines = "\n".join(_secondary_lines(SimpleNamespace(secondary_metrics=sm)))
    assert "self-reported" in lines and "exploratory" in lines


def test_ac5_attributing_arm_with_null_telemetry_still_renders(tmp_path):
    # an arm with all-null whole-trial telemetry but real by-model attribution
    # must appear in the render — it was silently dropped when the arm listing
    # iterated per_arm_means only.
    from types import SimpleNamespace

    from harness.analyze.report import _secondary_lines, _secondary_metrics
    from tests.fixtures.builders import fixed_ctx, locked_experiment, seed_trial_and_grade

    arms = [
        {"name": "control", "platform": "claude_code",
         "model": "anthropic/claude-3-5-sonnet-20241022", "payload": {}},
        {"name": "treatment", "platform": "generic", "model": DECLARED[0],
         "aux_models": [{"model": DECLARED[1]}], "payload": {}},
    ]
    spec, _, ledger = locked_experiment(tmp_path, arms=arms)
    ctx = fixed_ctx()
    seed_trial_and_grade(
        ledger, ctx, trial_id="t-1", task_id="task-1", arm="control",
        telemetry={"cost": 0.2},
    )
    seed_trial_and_grade(
        ledger, ctx, trial_id="t-2", task_id="task-1", arm="treatment",
        telemetry={},  # all-null whole-trial telemetry: absent from per_arm_means
        flags={"telemetry_by_model": {DECLARED[0]: {"cost": 0.30}}},
    )
    sm = _secondary_metrics(ledger, spec)
    assert "treatment" not in sm["per_arm_means"]  # the precondition of the bug
    lines = "\n".join(_secondary_lines(SimpleNamespace(secondary_metrics=sm)))
    assert "treatment" in lines and "0.3" in lines


def test_ac5_unattributed_never_zero(tmp_path):
    from harness.analyze.report import _secondary_metrics, _secondary_lines
    from types import SimpleNamespace

    spec, ledger = _seed_workflow_experiment(tmp_path)
    sm = _secondary_metrics(ledger, spec)
    # control reported no attribution: absent from the map, rendered honestly
    assert "control" not in sm["per_model_means"]
    lines = "\n".join(_secondary_lines(SimpleNamespace(secondary_metrics=sm)))
    assert "control: models=not attributed" in lines
    # and a ledger with NO attribution at all renders no attribution lines
    from tests.fixtures.builders import fixed_ctx, locked_experiment, seed_trial_and_grade

    spec2, _, ledger2 = locked_experiment(tmp_path / "plain")
    seed_trial_and_grade(
        ledger2, fixed_ctx(), trial_id="t-9", task_id="task-1", arm="control"
    )
    sm2 = _secondary_metrics(ledger2, spec2)
    lines2 = "\n".join(_secondary_lines(SimpleNamespace(secondary_metrics=sm2)))
    assert "attribution" not in lines2


# --- AC-6: forensics substrate ----------------------------------------------------
def test_ac6_per_agent_slicing_helper():
    rec = TrajectoryRecord(
        trial_id="t", platform="generic",
        steps=[
            TrajectoryStep(kind="message", agent="planner"),
            TrajectoryStep(kind="tool_call", agent="worker-1"),
            TrajectoryStep(kind="message"),
            TrajectoryStep(kind="test_run", agent="worker-1"),
        ],
    )
    groups = slice_by_agent(rec)
    assert sorted(groups) == ["planner", UNATTRIBUTED, "worker-1"]
    assert [s.kind for s in groups["worker-1"]] == ["tool_call", "test_run"]
    assert len(groups[UNATTRIBUTED]) == 1
    # the bucket name cannot collide with a declared label
    with pytest.raises(Exception):
        TrajectoryStep(kind="message", agent=UNATTRIBUTED)
