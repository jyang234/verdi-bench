"""EVAL-15 — trajectory v3 per-step detail (the EVAL-14-D004 capture slice).

AC map: v3 additive contract + v2 reads (AC-1), adapter asymmetry (AC-2),
persist-time redaction over detail (AC-3), blinded-surface exclusion (AC-4),
renderer exclusion vs operator drill-down (AC-5).
Spec: docs/design/specs/eval15.spec.md.
"""

from __future__ import annotations

import json
import re
import string
import threading
import urllib.request
from pathlib import Path

import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

from harness.adapters.claude_code import ClaudeCodeAdapter
from harness.adapters.codex import CodexAdapter
from harness.forensics.detectors import DETECTOR_IDS
from harness.forensics.scan import run_forensics
from harness.ledger.query import find_events, read_events
from harness.plan.interleave import derive_schedule, enumerate_trials
from harness.run.engines.fake import FakeEngine
from harness.run.interleave import schedule
from harness.run.trajectory import (
    TRAJECTORY_FILENAME,
    TrajectoryRecord,
    TrajectoryStep,
    parse_trajectory,
    persist_trajectory,
    resolve_trajectory,
    trajectory_sha256,
)
from harness.run.types import RunConfig, Task
from tests.fixtures.builders import fixed_ctx, locked_experiment


# --- AC-1: additive v3, v2 reads back null --------------------------------------
def test_ac1_v3_additive_and_v2_reads_null(tmp_path):
    # a v2 artifact, byte-built exactly as the v2 writer would have
    v2 = {
        "schema_version": 2,
        "trial_id": "t-old",
        "platform": "claude_code",
        "steps": [
            {"kind": "message", "relative_ts": None, "tokens": None, "cost": None,
             "files_touched": None, "exit_code": None, "command": ""},
            {"kind": "tool_call", "relative_ts": 1.0, "tokens": None, "cost": None,
             "files_touched": None, "exit_code": 0, "command": "pytest -q"},
        ],
    }
    raw = json.dumps(v2, sort_keys=True, separators=(",", ":")).encode("utf-8")
    rec = parse_trajectory(raw)
    assert rec.schema_version == 2  # the record keeps its own version
    assert all(s.detail is None for s in rec.steps)  # reads back null throughout

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / TRAJECTORY_FILENAME).write_bytes(raw)
    status, resolved = resolve_trajectory(str(artifacts), trajectory_sha256(raw))
    assert status == "verified" and resolved is not None  # no reader requires detail

    # v3 round-trip: "" (measured empty) is distinct from null (unmeasured)
    v3 = TrajectoryRecord(
        trial_id="t-new", platform="codex",
        steps=[TrajectoryStep(kind="message", detail=""),
               TrajectoryStep(kind="message", detail=None),
               TrajectoryStep(kind="tool_call", detail="3 passed")],
    )
    assert v3.schema_version == 3
    sha = persist_trajectory(v3, artifacts)
    status, back = resolve_trajectory(str(artifacts), sha)
    assert status == "verified"
    assert [s.detail for s in back.steps] == ["", None, "3 passed"]


# --- AC-2: adapter asymmetry, read-never-reconstructed ---------------------------
def test_ac2_adapter_detail_asymmetry_null_honest():
    cc = ClaudeCodeAdapter().normalize_trajectory(
        {
            "messages": [
                {"content": [{"type": "text", "text": "planning the fix"}]},
                {"content": [{"type": "text", "text": 42}]},  # malformed: null
                {"content": [{"type": "tool_use", "id": "tu1", "name": "Bash",
                              "input": {"command": "pytest -q"}}]},
                {"content": [{"type": "tool_result", "tool_use_id": "tu1",
                              "content": [{"type": "text", "text": "2 failed"}]}]},
                {"content": [{"type": "tool_use", "id": "tu2", "name": "Edit",
                              "input": {"file_path": "a.py", "old_string": "x = 1",
                                        "new_string": "x = 2"}}]},
                {"content": [{"type": "tool_use", "name": "Write",
                              "input": {"file_path": "b.py", "content": "print()"}}]},
                {"content": [{"type": "tool_use", "name": "MultiEdit",
                              "input": {"file_path": "c.py", "edits": [
                                  {"old_string": "a", "new_string": "b"},
                                  {"old_string": "c", "new_string": "d"}]}}]},
                {"content": [{"type": "tool_use", "id": "tu3", "name": "Edit",
                              "input": {"file_path": "d.py", "old_string": 7}}]},
                {"content": [{"type": "tool_result", "tool_use_id": "unknown",
                              "content": "orphan result"}]},  # no such id: ignored
            ]
        }
    )
    details = [s.detail for s in cc]
    assert details[0] == "planning the fix"          # message text, verbatim
    assert details[1] is None                         # non-string text: null
    assert details[2] == "2 failed"                   # paired by the log's own id
    assert details[3] == "--- old_string\nx = 1\n+++ new_string\nx = 2"
    assert details[4] == "print()"                    # Write: the content written
    assert details[5] == (
        "--- old_string\na\n+++ new_string\nb\n--- old_string\nc\n+++ new_string\nd"
    )
    assert details[6] is None                         # malformed edit input: null

    cx = CodexAdapter().normalize_trajectory(
        {
            "events": [
                {"type": "message", "elapsed_s": 1, "text": "starting"},
                {"type": "message", "elapsed_s": 2},                 # no text: null
                {"type": "patch", "elapsed_s": 3, "files": ["a.py"],
                 "diff": "-x\n+y"},
                {"type": "patch", "elapsed_s": 4, "files": ["b.py"]},  # no diff: null
                {"type": "exec", "elapsed_s": 5, "cmd": "pytest", "exit_code": 0,
                 "parsed_cmd": "test", "output": "3 passed"},
                {"type": "exec", "elapsed_s": 6, "cmd": "ls", "exit_code": 0},
            ]
        }
    )
    assert [s.detail for s in cx] == ["starting", None, "-x\n+y", None, "3 passed", None]


# --- AC-3: detail rides the persist-time scrub -----------------------------------
@settings(max_examples=20, deadline=None)
@given(st.text(alphabet=string.ascii_letters + string.digits, min_size=6, max_size=20))
def test_ac3_detail_redaction_property(tmp_path_factory, suffix):
    canary = f"EVAL15SECRET{suffix}"
    artifacts = tmp_path_factory.mktemp("traj")
    record = TrajectoryRecord(
        trial_id="t-scrub", platform="claude_code",
        steps=[
            TrajectoryStep(kind="message", detail=f"the key is {canary} apparently"),
            TrajectoryStep(kind="file_edit", detail=f"+password = {canary!r}"),
            TrajectoryStep(kind="tool_call", command="env", detail=canary),
        ],
    )
    persist_trajectory(record, artifacts, extra_patterns=[re.escape(canary)])
    data = (Path(artifacts) / TRAJECTORY_FILENAME).read_bytes()
    assert canary.encode("utf-8") not in data  # never persists, in any step kind
    back = parse_trajectory(data)  # the scrub masked, it did not break structure
    assert all(s.detail is not None for s in back.steps)  # masked, not erased


# --- AC-4 fixture: an experiment whose trajectory detail carries arm identity ------
_ARM_MODEL = "anthropic/claude-3-5-sonnet-20241022"  # the fixture spec's arm model


class _RecordingProvider:
    """Provider double that records every message list it is asked to complete."""

    def __init__(self) -> None:
        self.seen: list[list[dict]] = []
        self._response = json.dumps(
            {"suspicions": {d: False for d in DETECTOR_IDS},
             "narrative": "steady work, nothing suspicious"}
        )

    def complete(self, model_id: str, messages: list[dict], temperature: float) -> str:
        self.seen.append(messages)
        return self._response


def _experiment_with_identity_detail(tmp_path):
    spec, _, ledger = locked_experiment(tmp_path, repetitions=1)
    (tmp_path / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": [{"id": "t1", "prompt": "p"}]}), encoding="utf-8"
    )
    ctx = fixed_ctx(experiment_id=tmp_path.name)
    arms = {a.name: a for a in spec.arms}
    native_log = {
        "messages": [
            {"content": [{"type": "text",
                          "text": f"as {_ARM_MODEL} I will fix the parser"}]},
        ]
    }
    tasks = {
        "t1": Task(id="t1", prompt="p", fake_behavior={
            "native_log": native_log,
            "transcript_extra": ["worked through the task, ran tests, done"],
        })
    }
    order = derive_schedule(spec.seed, enumerate_trials(["t1"], list(arms), 1))
    schedule(
        order, tasks=tasks, arms=arms, workspace_root=tmp_path / "workspaces",
        ledger_path=ledger, ctx=ctx, config=RunConfig(engine=FakeEngine()),
        cost_ceiling=spec.cost_ceiling.amount,
    )
    return spec, ledger, ctx


def test_ac4_blinded_review_never_sees_detail(tmp_path):
    spec, ledger, ctx = _experiment_with_identity_detail(tmp_path)
    provider = _RecordingProvider()

    run_forensics(tmp_path, ctx=ctx, review=True, provider=provider,
                  provider_model="fake/forensic")

    # the canary lives on the operator tier: the persisted trajectory carries it
    trial_ev = find_events(ledger, "trial")[0]
    status, record = resolve_trajectory(
        trial_ev["trial_record"]["artifacts_path"], trial_ev.get("trajectory_sha")
    )
    assert status == "verified"
    # claude_code arm's trajectory carries the identity string in detail
    # (the codex arm honestly has none — its log had no events key)
    if trial_ev["trial_record"]["arm"] == spec.arms[0].name:
        assert any(s.detail and _ARM_MODEL in s.detail for s in record.steps)

    # the review RAN (not a vacuous pass) and no provider message carries it
    assert provider.seen, "the advisory review never reached the provider"
    for messages in provider.seen:
        for m in messages:
            assert _ARM_MODEL not in str(m.get("content", ""))
    assert len(find_events(ledger, "forensics_report")) == 1


# --- AC-5: renderers exclude, the drill-down serves ---------------------------------
def test_ac5_renderers_exclude_detail_drilldown_serves_it(tmp_path):
    from harness.analyze.dossier import render_dossier
    from harness.analyze.report import compute_findings
    from harness.analyze.timeline import trial_timeline
    from harness.schema.experiment import ExperimentSpec
    from harness.serve.server import make_server
    from harness.status.trial import trial_detail
    from tests.fixtures.scenarios import rich_experiment

    fx = rich_experiment(tmp_path)
    planted = "reading the task"  # the fixture native log's message text

    # timeline rows: the detail KEY is absent, not merely null
    rows = trial_timeline(fx["ledger"])
    steps = [s for arms in rows.values() for rws in arms.values()
             for r in rws if r["steps"] for s in r["steps"]]
    assert steps, "fixture must yield verified steps"
    assert all("detail" not in s for s in steps)

    # the dossier (whose only step source is the timeline) embeds no detail
    spec = ExperimentSpec.from_yaml(tmp_path / "experiment.yaml")
    findings = compute_findings(fx["ledger"], spec, seed=spec.seed)
    html = render_dossier(findings, fx["ledger"], "exploratory")
    assert planted not in html
    assert "tool_call" in html  # steps themselves still render

    # the operator drill-down is the one surface that serves it
    detail = trial_detail(tmp_path, fx["flagged"])
    assert any(s.get("detail") == planted for s in detail["trajectory"]["steps"])

    srv = make_server(tmp_path, port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        url = (f"http://127.0.0.1:{srv.server_address[1]}"
               f"/api/trial?id={fx['flagged']}")
        with urllib.request.urlopen(url) as resp:
            served = json.loads(resp.read())
        assert any(s.get("detail") == planted for s in served["trajectory"]["steps"])
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)
