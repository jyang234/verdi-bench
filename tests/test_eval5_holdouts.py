"""First-class polymorphic holdouts [refactor 05 §1].

Exercises the declared-holdout hierarchy (materialize/execute/load), the
host-side ``LocalExecutingGradeRunner`` (ADVISORY), the ``run_holdouts``
in-image entrypoint's fenced/nonce discipline, the SDK inline-holdout sugar
(compiled out to ``holdouts_dir``, never serialized), and the single-sourced
``holdout_results.json`` constant. All non-docker; the real container path is
proved in ``tests/test_e2e_run_holdouts.py``.
"""

from __future__ import annotations

import json
import sys

import pytest
import yaml
from typer.testing import CliRunner

from harness.cli import app
from harness.grade.container import (
    GradingContainer,
    LocalExecutingGradeRunner,
)
from harness.grade.deterministic import grade_trial, parse_holdout_output
from harness.grade.holdouts import (
    AssertionHoldout,
    CommandHoldout,
    PytestFileHoldout,
    as_holdout,
    assertions_to_raw,
    load_declared_holdout,
)
from harness.grade.types import GradeTask
from harness.ledger.query import find_events
from tests.fixtures.builders import fixed_ctx, write_experiment_yaml

runner = CliRunner()


def _solution(ws, body="def add(a, b):\n    return a + b\n"):
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "solution.py").write_text(body, encoding="utf-8")
    return ws


_ADD5 = "from solution import add; assert add(2, 3) == 5"


# --- holdout.json v1 contract (A2) -----------------------------------------
def test_assertion_holdout_json_is_v1_shape(tmp_path):
    hd = tmp_path / "hd"
    AssertionHoldout(expression=_ADD5, id="h1").materialize(hd)
    spec = json.loads((hd / "holdout.json").read_text())
    assert spec == {
        "schema_version": 1, "kind": "assertion", "id": "h1", "expression": _ADD5,
    }


def test_pytest_holdout_writes_side_file_and_excludes_body(tmp_path):
    hd = tmp_path / "hd"
    body = "def test_add():\n    from solution import add\n    assert add(2, 3) == 5\n"
    PytestFileHoldout(path="test_holdout.py", body=body, id="hp").materialize(hd)
    spec = json.loads((hd / "holdout.json").read_text())
    # the body is a materialize-time input, NOT part of the contract file
    assert spec == {"schema_version": 1, "kind": "pytest", "id": "hp",
                    "path": "test_holdout.py"}
    assert (hd / "test_holdout.py").read_text() == body  # the side file IS the content


def test_command_holdout_json_is_v1_shape(tmp_path):
    hd = tmp_path / "hd"
    CommandHoldout(argv=["true"], id="hc").materialize(hd)
    spec = json.loads((hd / "holdout.json").read_text())
    assert spec == {"schema_version": 1, "kind": "command", "id": "hc", "argv": ["true"]}


# --- execution semantics (subprocess, exit 0 = pass) -----------------------
def test_assertion_holdout_pass_and_fail(tmp_path):
    h = AssertionHoldout(expression=_ADD5)
    passed = h.execute(_solution(tmp_path / "ok"))
    assert [a.result.value for a in passed] == ["pass"]
    assert passed[0].source == "holdout_test" and passed[0].detail is None

    failed = h.execute(_solution(tmp_path / "bad", "def add(a, b):\n    return a + b + 1\n"))
    assert [a.result.value for a in failed] == ["fail"]
    assert failed[0].detail  # a diagnostic is attached on failure


def test_pytest_holdout_executes_after_reload(tmp_path):
    hd = tmp_path / "hd"
    PytestFileHoldout(
        path="test_holdout.py",
        body="def test_add():\n    from solution import add\n    assert add(2, 3) == 5\n",
    ).materialize(hd)
    # reload from disk (no body) — execute must still find the materialized file
    loaded = load_declared_holdout(hd)
    assert loaded.execute(_solution(tmp_path / "ws"))[0].result.value == "pass"


def test_command_holdout_exit_code_is_the_verdict(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    assert CommandHoldout(argv=[sys.executable, "-c", "exit(0)"]).execute(ws)[0].result.value == "pass"
    assert CommandHoldout(argv=[sys.executable, "-c", "exit(1)"]).execute(ws)[0].result.value == "fail"


def test_execute_scrubs_fence_nonce_and_sets_no_bytecode(tmp_path, monkeypatch):
    """The child that runs agent code must NOT see the fence nonce (deep-dive
    §2.4) and must run with PYTHONDONTWRITEBYTECODE=1 (no __pycache__ in the
    graded diff). Assert both from inside the executed subprocess."""
    monkeypatch.setenv("VERDI_FENCE_NONCE", "super-secret-nonce")
    ws = tmp_path / "ws"
    ws.mkdir()
    expr = (
        "import os; "
        "assert 'VERDI_FENCE_NONCE' not in os.environ, 'nonce leaked to child'; "
        "assert os.environ.get('PYTHONDONTWRITEBYTECODE') == '1'"
    )
    assert AssertionHoldout(expression=expr).execute(ws)[0].result.value == "pass"


def test_executed_assertions_flow_through_frozen_parser(tmp_path):
    """assertions_to_raw + parse_holdout_output round-trips to holdout_test
    assertions — the frozen deterministic parser is unchanged."""
    assertions = AssertionHoldout(expression=_ADD5, id="hx").execute(_solution(tmp_path / "ws"))
    reparsed = parse_holdout_output(assertions_to_raw(assertions))
    assert [(a.id, a.source, a.result.value) for a in reparsed] == [("hx", "holdout_test", "pass")]


# --- loader: opaque/bespoke stays unchanged (A2) ---------------------------
def test_loader_none_for_missing_file(tmp_path):
    assert load_declared_holdout(tmp_path / "nope") is None


def test_loader_none_for_opaque_holdout_without_kind(tmp_path):
    """A holdout.json without a ``kind`` is opaque input for a bespoke grader
    image — nothing existing breaks; it must NOT be library-executed."""
    hd = tmp_path / "hd"
    hd.mkdir()
    (hd / "holdout.json").write_text(
        json.dumps({"fail_to_pass": ["t::x"], "test_patch": "diff ..."}), encoding="utf-8"
    )
    assert load_declared_holdout(hd) is None


def test_loader_roundtrips_each_kind(tmp_path):
    for h in (
        AssertionHoldout(expression=_ADD5, id="a"),
        PytestFileHoldout(path="t.py", body="def test_x():\n    assert True\n", id="p"),
        CommandHoldout(argv=["true"], id="c"),
    ):
        hd = tmp_path / h.id
        h.materialize(hd)
        loaded = load_declared_holdout(hd)
        assert type(loaded) is type(h) and loaded.id == h.id


def test_loader_rejects_unknown_kind_loudly(tmp_path):
    hd = tmp_path / "hd"
    hd.mkdir()
    (hd / "holdout.json").write_text(
        json.dumps({"schema_version": 1, "kind": "sorcery"}), encoding="utf-8"
    )
    with pytest.raises(Exception):  # pydantic discriminator ValidationError
        load_declared_holdout(hd)


def test_loader_rejects_future_schema_version_loudly(tmp_path):
    hd = tmp_path / "hd"
    hd.mkdir()
    (hd / "holdout.json").write_text(
        json.dumps({"schema_version": 2, "kind": "assertion", "expression": "assert True"}),
        encoding="utf-8",
    )
    with pytest.raises(Exception):
        load_declared_holdout(hd)


def test_as_holdout_accepts_instance_and_dict():
    inst = AssertionHoldout(expression="assert True")
    assert as_holdout(inst) is inst
    coerced = as_holdout({"kind": "assertion", "expression": "assert True"})
    assert isinstance(coerced, AssertionHoldout)


# --- LocalExecutingGradeRunner (ADVISORY host execution) -------------------
def test_local_executing_runner_grades_declared_holdout(tmp_path):
    hd = tmp_path / "hd"
    AssertionHoldout(expression=_ADD5).materialize(hd)
    ws = _solution(tmp_path / "ws")
    ledger = tmp_path / "l.ndjson"
    grade_trial(
        "trial-le", GradeTask(id="t", task_sha="s", holdouts_dir=str(hd)),
        ws, ledger, fixed_ctx(),
        container=GradingContainer(runner=LocalExecutingGradeRunner()),
    )
    g = find_events(ledger, "grade")[0]
    # executed, scored, and stamped non-"docker" (so analyze banners ADVISORY)
    assert g["binary_score"] is True
    assert g["grader"] == "local-exec"
    assert g["assertions"][0]["source"] == "holdout_test"


def test_local_executing_grade_is_advisory_tier(tmp_path):
    from harness.analyze.report import _tier_summary

    hd = tmp_path / "hd"
    AssertionHoldout(expression=_ADD5).materialize(hd)
    ledger = tmp_path / "l.ndjson"
    grade_trial(
        "trial-le", GradeTask(id="t", task_sha="s", holdouts_dir=str(hd)),
        _solution(tmp_path / "ws"), ledger, fixed_ctx(),
        container=GradingContainer(runner=LocalExecutingGradeRunner()),
    )
    assert _tier_summary(ledger)["advisory"] is True


def test_local_executing_runner_fails_closed_without_declared_holdout(tmp_path):
    """An opaque (no-kind) holdout is not library-executable — the local-exec
    runner must fail the grade closed, never silently score nothing."""
    hd = tmp_path / "hd"
    hd.mkdir()
    (hd / "holdout.json").write_text(json.dumps({"fail_to_pass": ["x"]}), encoding="utf-8")
    ledger = tmp_path / "l.ndjson"
    out = grade_trial(
        "trial-le", GradeTask(id="t", task_sha="s", holdouts_dir=str(hd)),
        _solution(tmp_path / "ws"), ledger, fixed_ctx(),
        container=GradingContainer(runner=LocalExecutingGradeRunner()),
    )
    assert out.graded is False
    assert find_events(ledger, "cant_grade")[0]["reason"] == "container_failure"


def test_local_executing_runner_declares_advisory_seam():
    """The runner opts out of the fresh-copy discipline (read-only host exec) and
    is non-"docker" so analyze's tier logic bands it ADVISORY unchanged."""
    r = LocalExecutingGradeRunner()
    assert r.grades_in_place is True
    assert r.grader_name == "local-exec" != "docker"


# --- CLI --runner local-exec wiring ----------------------------------------
def test_cli_runner_rejects_unknown_choice(tmp_path):
    r = runner.invoke(app, ["grade", str(tmp_path), "--runner", "bogus"])
    assert r.exit_code != 0
    assert "local-exec" in (r.output + (r.stderr or ""))  # the valid set names it


def test_cli_grade_runner_local_exec_end_to_end(tmp_path):
    """The full CLI wiring: plan -> run (fake) -> grade --runner local-exec
    executes the declared holdout against each fake trial workspace and records a
    scored, ADVISORY (grader="local-exec") grade; the chain still verifies."""
    expdir = tmp_path / "exp"
    expdir.mkdir(parents=True)
    # absolute holdouts_dir so grade resolves it independent of CWD (the docker
    # path resolves the mount relative to CWD identically — see the report).
    hd = expdir / "holdouts" / "t1"
    AssertionHoldout(expression=_ADD5).materialize(hd)
    write_experiment_yaml(expdir / "experiment.yaml", repetitions=1)
    tasks = [{
        "id": "t1", "prompt": "add", "holdouts_dir": str(hd),
        "fake_behavior": {
            "native_log": {"total_cost_usd": 0.01},
            "workspace_files": {"solution.py": "def add(a, b):\n    return a + b\n"},
        },
    }]
    (expdir / "tasks.yaml").write_text(yaml.safe_dump({"tasks": tasks}), encoding="utf-8")
    ledger = expdir / "ledger.ndjson"

    assert runner.invoke(
        app, ["plan", str(expdir / "experiment.yaml"), "--ledger", str(ledger)]
    ).exit_code == 0
    assert runner.invoke(app, ["run", str(expdir)]).exit_code == 0
    r = runner.invoke(app, ["grade", str(expdir), "--runner", "local-exec"])
    assert r.exit_code == 0, r.output
    grades = find_events(ledger, "grade")
    assert grades and all(g["grader"] == "local-exec" for g in grades)
    assert all(g["binary_score"] is True for g in grades)
    assert runner.invoke(app, ["verify-chain", str(ledger)]).exit_code == 0


# --- run_holdouts in-image entrypoint (fence + nonce) ----------------------
def test_run_holdouts_entrypoint_emits_nonce_authenticated_fence(tmp_path, monkeypatch):
    import io
    from contextlib import redirect_stdout

    import harness.grade.run_holdouts as rh
    from harness.grade.container import parse_fenced_stdout

    hd = tmp_path / "holdouts"
    AssertionHoldout(expression=_ADD5).materialize(hd)
    ws = _solution(tmp_path / "workspace")
    monkeypatch.setattr(rh, "_HOLDOUTS_MOUNT", hd)
    monkeypatch.setattr(rh, "_WORKSPACE", ws)
    monkeypatch.setenv("VERDI_FENCE_NONCE", "nonce-xyz")

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = rh.main(["run_holdouts"])
    assert rc == 0
    # only the marker carrying the injected nonce authenticates the channel
    parsed = parse_fenced_stdout(buf.getvalue(), 0, nonce="nonce-xyz")
    assert parsed.raw_output == {"assertions": [{"id": "h1", "result": "pass"}]}
    # main dropped the nonce from os.environ (shallow defense-in-depth)
    import os

    assert "VERDI_FENCE_NONCE" not in os.environ


def test_run_holdouts_entrypoint_fails_closed_without_declared_kind(tmp_path, monkeypatch):
    import harness.grade.run_holdouts as rh

    hd = tmp_path / "holdouts"
    hd.mkdir()
    (hd / "holdout.json").write_text(json.dumps({"fail_to_pass": ["x"]}), encoding="utf-8")
    monkeypatch.setattr(rh, "_HOLDOUTS_MOUNT", hd)
    monkeypatch.setattr(rh, "_WORKSPACE", tmp_path / "ws")
    # no fence emitted, nonzero exit → host reads the channel absent (terminal)
    assert rh.main(["run_holdouts"]) == 1


# --- SDK inline holdout sugar (A3) -----------------------------------------
def _experiment(**task_kwargs):
    from harness.sdk.experiment import Experiment, Task

    M = "fake/deterministic-2026-01-01"
    return (
        Experiment("demo", seed=1, cost_ceiling_usd=1.0)
        .arm("control", model=M).arm("treatment", model=M).judge(M)
        .task(Task(**task_kwargs))
    )


def test_sdk_inline_holdout_compiles_to_holdouts_dir(tmp_path):
    exp = _experiment(id="t1", prompt="add", holdout=AssertionHoldout(expression=_ADD5))
    exp.write(tmp_path / "exp")
    tasks_yaml = (tmp_path / "exp" / "tasks.yaml").read_text()
    # the inline holdout is compiled OUT — only holdouts_dir on disk
    assert "holdout:" not in tasks_yaml
    assert "holdouts_dir: holdouts/t1" in tasks_yaml
    spec = json.loads((tmp_path / "exp" / "holdouts" / "t1" / "holdout.json").read_text())
    assert spec["kind"] == "assertion" and spec["schema_version"] == 1


def test_sdk_inline_holdout_grades_via_local_exec(tmp_path):
    """End-to-end: SDK-compiled inline holdout is executed by the local-exec
    runner against the trial workspace and scored."""
    exp = _experiment(id="t1", prompt="add", holdout=AssertionHoldout(expression=_ADD5))
    exp.write(tmp_path / "exp")
    hd = tmp_path / "exp" / "holdouts" / "t1"
    ledger = tmp_path / "l.ndjson"
    grade_trial(
        "trial-1", GradeTask(id="t1", task_sha="s", holdouts_dir=str(hd)),
        _solution(tmp_path / "ws"), ledger, fixed_ctx(),
        container=GradingContainer(runner=LocalExecutingGradeRunner()),
    )
    g = find_events(ledger, "grade")[0]
    assert g["binary_score"] is True and g["grader"] == "local-exec"


def test_taskspec_holdout_is_never_serialized(tmp_path):
    from harness.schema.tasks import TaskSpec, tasks_to_yaml

    ts = TaskSpec(id="t1", holdout=AssertionHoldout(expression=_ADD5))
    assert "holdout" not in tasks_to_yaml([ts])
    assert "holdout" not in ts.model_dump(mode="json")


def test_sdk_refuses_both_inline_holdout_and_holdouts_dir(tmp_path):
    exp = _experiment(id="t1", holdouts_dir="holdouts/t1",
                      holdout=AssertionHoldout(expression="assert True"))
    with pytest.raises(ValueError, match="both"):
        exp.write(tmp_path / "exp")


# --- the single-sourced holdout_results.json constant ----------------------
def test_holdout_results_filename_is_single_sourced():
    from harness.grade.container import HOLDOUT_RESULTS
    from harness.judge.assemble import HOLDOUT_RESULTS as JUDGE_HOLDOUT_RESULTS

    assert JUDGE_HOLDOUT_RESULTS is HOLDOUT_RESULTS == "holdout_results.json"
