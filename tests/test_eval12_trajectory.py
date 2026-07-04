"""EVAL-12 slice A — trajectory capture: versioned record, sha binding, honesty.

AC-1: one shared step schema across adapters, unmeasured fields null, version
stamped, sha ledgered as an additive trial-event field. AC-2: capture is
post-redaction and fail-loud; absent is distinguishable from empty.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from harness.adapters.claude_code import ClaudeCodeAdapter
from harness.adapters.codex import CodexAdapter
from harness.ledger.events import record_trial
from harness.ledger.query import find_events
from harness.plan.interleave import Trial
from harness.run.engines.fake import FakeEngine
from harness.run.interleave import schedule
from harness.run.seam import run_trial
from harness.run.trajectory import (
    TRAJECTORY_FILENAME,
    TRAJECTORY_SCHEMA_VERSION,
    TrajectoryCorruptError,
    TrajectoryRecord,
    load_trajectory,
)
from harness.run.types import RunConfig, Task
from harness.schema.experiment import Arm
from tests.fixtures.builders import fixed_ctx

CLAUDE_ARM = Arm(name="A", platform="claude_code", model="anthropic/claude-3-5-sonnet-20241022")
CODEX_ARM = Arm(name="B", platform="codex", model="openai/gpt-4o-2024-08-06")

# Platform-native trajectory fixtures [AC-1]: the same three semantic steps —
# a message, a file edit, a command — in each platform's own log dialect.
CLAUDE_NATIVE = {
    "messages": [
        {"content": [{"type": "text", "text": "plan"}]},
        {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "src/app.py"}}]},
        {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}}]},
    ]
}
CODEX_NATIVE = {
    "events": [
        {"type": "message", "elapsed_s": 0.5},
        {"type": "patch", "elapsed_s": 2.0, "files": ["src/app.py"]},
        {"type": "exec", "elapsed_s": 3.5, "parsed_cmd": "test", "exit_code": 0},
    ]
}


def _run(tmp_path, arm, native_log, **behavior):
    task = Task(id="t", prompt="p", fake_behavior={"native_log": native_log, **behavior})
    return run_trial(task, arm, tmp_path / "ws", RunConfig(engine=FakeEngine()))


def _trajectory_path(record) -> Path:
    return Path(record.artifacts_path) / TRAJECTORY_FILENAME


# --- AC-1: normalized, versioned record --------------------------------------
def test_ac1_normalized_versioned_record(tmp_path):
    """Both platforms normalize to the same step schema; what a platform cannot
    measure is null, never estimated; the persisted record stamps its version."""
    claude_steps = ClaudeCodeAdapter().normalize_trajectory(CLAUDE_NATIVE)
    codex_steps = CodexAdapter().normalize_trajectory(CODEX_NATIVE)

    assert [s.kind for s in claude_steps] == ["message", "file_edit", "tool_call"]
    assert [s.kind for s in codex_steps] == ["message", "file_edit", "test_run"]
    assert claude_steps[1].files_touched == ["src/app.py"]
    assert codex_steps[1].files_touched == ["src/app.py"]

    # Null asymmetry is honest data, per field: claude-code cannot measure
    # per-step timings or exit codes; codex cannot measure per-step tokens/cost.
    assert all(s.relative_ts is None and s.exit_code is None for s in claude_steps)
    assert [s.relative_ts for s in codex_steps] == [0.5, 2.0, 3.5]
    assert codex_steps[2].exit_code == 0
    assert all(s.tokens is None and s.cost is None for s in claude_steps + codex_steps)

    # The persisted artifact stamps the schema version.
    rec = _run(tmp_path, CLAUDE_ARM, CLAUDE_NATIVE)
    stored = load_trajectory(_trajectory_path(rec))
    assert stored.schema_version == TRAJECTORY_SCHEMA_VERSION
    assert stored.trial_id == rec.trial_id
    assert stored.platform == "claude_code"
    assert [s.kind for s in stored.steps] == ["message", "file_edit", "tool_call"]


def test_ac1_sha_ledgered_additive(tmp_path):
    """The trial event carries a top-level ``trajectory_sha`` matching the
    artifact bytes; the embedded trial_record keeps its pre-EVAL-12 shape; a
    trajectory-less trial's event has no such field at all [D001]."""
    arms = {"A": CLAUDE_ARM}
    tasks = {
        "with": Task(id="with", prompt="p", fake_behavior={"native_log": CLAUDE_NATIVE}),
        "without": Task(id="without", prompt="p", fake_behavior={"native_log": {}}),
    }
    order = [Trial(task_id="with", arm="A", repetition=0),
             Trial(task_id="without", arm="A", repetition=0)]
    ledger = tmp_path / "ledger.ndjson"
    schedule(
        order, tasks=tasks, arms=arms, workspace_root=tmp_path / "ws",
        ledger_path=ledger, ctx=fixed_ctx(), config=RunConfig(engine=FakeEngine()),
        cost_ceiling=100.0,
    )
    by_task = {ev["trial_record"]["task_id"]: ev for ev in find_events(ledger, "trial")}

    ev = by_task["with"]
    artifact = Path(ev["trial_record"]["artifacts_path"]) / TRAJECTORY_FILENAME
    assert ev["trajectory_sha"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    # the sha lives in exactly one place: the top-level additive event field
    assert "trajectory_sha" not in ev["trial_record"]

    # absent trajectory ⇒ absent field (= the pre-EVAL-12 posture; no reader
    # may require it, so its absence is representable, not an error)
    assert "trajectory_sha" not in by_task["without"]
    assert "trajectory_sha" not in by_task["without"]["trial_record"]


def test_record_trial_hoists_embedded_sha(tmp_path):
    """A full TrialRecord dump (the scheduler's write path) lands the sha as
    the top-level field, never duplicated inside trial_record."""
    rec = _run(tmp_path, CODEX_ARM, CODEX_NATIVE)
    assert rec.trajectory_sha is not None
    ledger = tmp_path / "ledger.ndjson"
    ev = record_trial(ledger, fixed_ctx(), trial_record=rec.model_dump(mode="json"))
    assert ev["trajectory_sha"] == rec.trajectory_sha
    assert "trajectory_sha" not in ev["trial_record"]


# --- AC-2: post-redaction capture, fail-loud, honest absence ------------------
@settings(max_examples=25, deadline=None)
@given(suffix=st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=32, max_size=32))
def test_ac2_capture_post_redaction(tmp_path_factory, suffix):
    """Property: a secret canary planted in step content never reaches the
    persisted record — the trajectory passes the EVAL-4 scrub before persist."""
    secret = "sk-" + suffix
    ws = tmp_path_factory.mktemp("ws")
    native = {
        "messages": [
            {"content": [{"type": "tool_use", "name": "Edit",
                          "input": {"file_path": f"/w/{secret}/x.py"}}]},
        ]
    }
    task = Task(id="t", prompt="p", fake_behavior={"native_log": native})
    rec = run_trial(task, CLAUDE_ARM, ws, RunConfig(engine=FakeEngine()))
    raw = _trajectory_path(rec).read_bytes().decode("utf-8")
    assert secret not in raw
    stored = load_trajectory(_trajectory_path(rec))
    assert stored.steps[0].files_touched == ["/w/[REDACTED]/x.py"]


def test_injected_provider_key_scrubbed_from_record(tmp_path):
    """The injected provider-key VALUE scrubs as a literal even though its shape
    matches no known key pattern — the same RN-9 door the workspace gets."""
    key_value = "weird-shape-key-123456"
    native = {
        "messages": [
            {"content": [{"type": "tool_use", "name": "Edit",
                          "input": {"file_path": f"/creds/{key_value}.pem"}}]},
        ]
    }
    task = Task(id="t", prompt="p", fake_behavior={"native_log": native})
    config = RunConfig(engine=FakeEngine(), provider_keys={"OPENAI_API_KEY": key_value})
    rec = run_trial(task, CLAUDE_ARM, tmp_path / "ws", config)
    raw = _trajectory_path(rec).read_bytes().decode("utf-8")
    assert key_value not in raw
    assert "[REDACTED]" in raw


def test_ac2_corrupt_fails_closed(tmp_path):
    """An unwritable trajectory fails the trial closed:
    ``trial_infra_failed(trajectory_corrupt)``, no trial event — the
    telemetry_corrupt precedent."""
    arms = {"A": CLAUDE_ARM}
    tasks = {
        "t": Task(
            id="t", prompt="p",
            fake_behavior={
                "native_log": CLAUDE_NATIVE,
                # a directory squatting on the artifact path makes the
                # trajectory unwritable — no mocks, a real filesystem fault
                "workspace_files": {f"artifacts/{TRAJECTORY_FILENAME}/blocker.txt": "x"},
            },
        )
    }
    ledger = tmp_path / "ledger.ndjson"
    res = schedule(
        [Trial(task_id="t", arm="A", repetition=0)],
        tasks=tasks, arms=arms, workspace_root=tmp_path / "ws",
        ledger_path=ledger, ctx=fixed_ctx(), config=RunConfig(engine=FakeEngine()),
        cost_ceiling=100.0,
    )
    assert res.records == []
    assert find_events(ledger, "trial") == []
    failures = find_events(ledger, "trial_infra_failed")
    assert [ev["reason"] for ev in failures] == ["trajectory_corrupt"]


def test_timeout_with_corrupt_native_log_keeps_datapoint(tmp_path):
    """A timeout kill can truncate agent_log.json mid-write. The timeout outcome
    is data (RN-17 keeps it at the engine seam); capture must record an honest
    absent trajectory, not destroy the datapoint as trajectory_corrupt."""
    rec = _run(
        tmp_path, CLAUDE_ARM, CLAUDE_NATIVE,
        outcome="timeout",
        # overwrite the engine-written log with truncated bytes, as a kill
        # mid-write would leave it
        workspace_files={"artifacts/agent_log.json": "{truncated"},
    )
    from harness.adapters.base import Outcome

    assert rec.outcome == Outcome.timeout
    assert rec.trajectory_sha is None
    assert not _trajectory_path(rec).exists()

    # the same corrupt log on a COMPLETED trial still fails closed
    with pytest.raises(TrajectoryCorruptError):
        _run(
            tmp_path / "completed", CLAUDE_ARM, CLAUDE_NATIVE,
            workspace_files={"artifacts/agent_log.json": "{truncated"},
        )


def test_ac2_absent_distinguishable_from_empty(tmp_path):
    """An engine that cannot produce a trajectory records honest absence — no
    artifact, no sha — never a fabricated empty record; an explicitly empty
    trajectory persists as a real (empty-steps) record with a sha."""
    absent = _run(tmp_path / "a", CLAUDE_ARM, {})
    assert absent.trajectory_sha is None
    assert not _trajectory_path(absent).exists()

    empty = _run(tmp_path / "b", CLAUDE_ARM, {"messages": []})
    assert empty.trajectory_sha is not None
    stored = load_trajectory(_trajectory_path(empty))
    assert stored.steps == []


def test_malformed_native_shapes_never_crash():
    """A garbled native log field must degrade to an honest null, never crash
    normalize_trajectory (a crash misfiles a completed trial as a generic
    trial_error) and never shred a scalar into characters."""
    steps = ClaudeCodeAdapter().normalize_trajectory(
        {"messages": [{"content": [{"type": "tool_use", "name": "Edit", "input": "raw"}]}]}
    )
    assert steps[0].kind == "file_edit" and steps[0].files_touched is None

    codex = CodexAdapter()
    # a bare-string files field is unmeasurable, not eight one-char paths
    steps = codex.normalize_trajectory({"events": [{"type": "patch", "files": "src/a.py"}]})
    assert steps[0].files_touched is None
    # a measured-empty patch is [], distinguishable from unmeasurable [D004]
    steps = codex.normalize_trajectory({"events": [{"type": "patch", "files": []}]})
    assert steps[0].files_touched == []


def test_load_trajectory_corrupt_raises(tmp_path):
    """Present-but-invalid content is corrupt, loudly — distinct from absent."""
    p = tmp_path / TRAJECTORY_FILENAME
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(TrajectoryCorruptError):
        load_trajectory(p)
    p.write_text(json.dumps({"schema_version": 1, "steps": []}), encoding="utf-8")
    with pytest.raises(TrajectoryCorruptError):  # missing required fields
        load_trajectory(p)
    with pytest.raises(TrajectoryCorruptError):  # absent file is unreadable here
        load_trajectory(tmp_path / "missing.json")


def test_canonical_bytes_deterministic(tmp_path):
    """Same trial content ⇒ byte-identical artifact ⇒ stable sha [AC-1]."""
    a = _run(tmp_path / "a", CODEX_ARM, CODEX_NATIVE)
    b = _run(tmp_path / "b", CODEX_ARM, CODEX_NATIVE)
    bytes_a = _trajectory_path(a).read_bytes()
    bytes_b = _trajectory_path(b).read_bytes()
    # trial ids differ; everything else about the serialization is canonical
    assert bytes_a.replace(a.trial_id.encode(), b"TID") == bytes_b.replace(
        b.trial_id.encode(), b"TID"
    )
    # literal pin, deliberately not the constant: bumping the versioned
    # contract must fail a test until a human approves it [EVAL-12-D001]
    assert TrajectoryRecord.model_validate(json.loads(bytes_a)).schema_version == 2


def test_v1_record_loads_under_v2_model(tmp_path):
    """Migration [EVAL-11-D005]: a pre-change v1 record (no ``command`` field)
    validates under the v2 model and reads back with ``command`` null on every
    step — unmeasurable, never backfilled."""
    v1 = {
        "schema_version": 1,
        "trial_id": "old",
        "platform": "claude_code",
        "steps": [{"kind": "tool_call"}, {"kind": "message", "relative_ts": 1.0}],
    }
    p = tmp_path / TRAJECTORY_FILENAME
    p.write_text(json.dumps(v1), encoding="utf-8")
    rec = load_trajectory(p)
    assert rec.schema_version == 1
    assert all(s.command is None for s in rec.steps)


def test_command_measured_vs_unmeasurable():
    """[EVAL-11-D005] Both adapters distinguish a measured command string, a
    measured not-a-shell-command (\"\"), and an unmeasurable null."""
    claude = ClaudeCodeAdapter().normalize_trajectory(
        {
            "messages": [
                {"content": [{"type": "tool_use", "name": "Bash",
                              "input": {"command": "rm -rf build"}}]},
                {"content": [{"type": "tool_use", "name": "Edit",
                              "input": {"file_path": "a.py"}}]},
                {"content": [{"type": "tool_use", "name": "Bash", "input": "garbled"}]},
            ]
        }
    )
    assert [s.command for s in claude] == ["rm -rf build", "", None]

    codex = CodexAdapter().normalize_trajectory(
        {
            "events": [
                {"type": "exec", "cmd": "pytest -q", "parsed_cmd": "test"},
                {"type": "patch", "files": []},
                {"type": "exec"},
            ]
        }
    )
    assert [s.command for s in codex] == ["pytest -q", "", None]
