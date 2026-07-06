"""EVAL-5 AC-4 — plugin seam + groundwork rule mapping."""

from __future__ import annotations

from pathlib import Path

from harness.grade.container import GradingContainer
from harness.grade.deterministic import grade_trial
from harness.grade.plugins import GraderPlugin, get_plugin, register_plugin
from harness.grade.plugins.groundwork import GroundworkGrader
from harness.grade.types import Assertion, AssertionResult, GradeTask
from harness.ledger.query import find_events
from tests.fixtures.builders import fixed_ctx
from tests.fixtures.grade_fakes import ScriptedGradeRunner, write_workspace


def test_ac4_plugin_contract(tmp_path):
    @register_plugin
    class DummyPlugin(GraderPlugin):
        id = "dummy"

        def grade(self, workspace, task):
            return [Assertion(id="d1", source="plugin:dummy", result=AssertionResult.passed)]

    ws = write_workspace(tmp_path)
    ledger = tmp_path / "l.ndjson"
    task = GradeTask(id="t1", task_sha="s", plugin_ids=["dummy"])
    grade_trial(
        "trial-1", task, ws, ledger, fixed_ctx(),
        container=GradingContainer(runner=ScriptedGradeRunner(
            {"assertions": [{"id": "h1", "result": "pass"}]})),
    )
    grade = find_events(ledger, "grade")[0]
    sources = {a["source"] for a in grade["assertions"]}
    assert "plugin:dummy" in sources and "holdout_test" in sources


def test_ac4_groundwork_plugin_preserves_rule_ids():
    task = GradeTask(id="go1", task_sha="s", fake_plugin_output={"rules": [
        {"id": "RULE-A", "verdict": "pass"},
        {"id": "RULE-B", "verdict": "fail"},
        {"id": "RULE-C", "verdict": "NO-STRUCTURAL-SIGNAL"},
    ]})
    assertions = GroundworkGrader().grade(workspace=None, task=task)
    by_id = {a.id: a for a in assertions}
    assert set(by_id) == {"RULE-A", "RULE-B", "RULE-C"}  # rule ids preserved
    assert by_id["RULE-A"].result == AssertionResult.passed
    assert by_id["RULE-B"].result == AssertionResult.failed
    # NO-STRUCTURAL-SIGNAL ⇒ abstain, NEVER pass
    assert by_id["RULE-C"].result == AssertionResult.abstain


def test_ac4_plugin_abstain_does_not_fail_binary(tmp_path):
    """A plugin abstain must not flip the binary score (holdouts decide it)."""
    ws = write_workspace(tmp_path)
    ledger = tmp_path / "l.ndjson"
    task = GradeTask(id="go1", task_sha="s", plugin_ids=["groundwork"],
                     fake_plugin_output={"rules": [{"id": "R", "verdict": "NO-STRUCTURAL-SIGNAL"}]})
    grade_trial(
        "trial-1", task, ws, ledger, fixed_ctx(),
        container=GradingContainer(runner=ScriptedGradeRunner(
            {"assertions": [{"id": "h1", "result": "pass"}]})),
    )
    grade = find_events(ledger, "grade")[0]
    assert grade["binary_score"] is True  # holdout passed; plugin abstain irrelevant


def test_ac4_groundwork_registered():
    assert isinstance(get_plugin("groundwork"), GroundworkGrader)


def test_builtin_plugins_resolve_on_the_run_plugin_path_without_cli():
    """[refactor 01 §4 D3] The in-container entrypoint imports only
    ``harness.grade.run_plugin`` / ``harness.grade.plugins`` — never
    ``harness.grade.cli`` — so registration must ride the plugins package
    itself. Before the fix the only registration transport was a side-effect
    import in grade/cli.py, leaving every real containerized plugin run an
    ``UnknownPluginError``. Fresh interpreter: this suite imports the CLI
    in-process, which would mask the defect."""
    import subprocess
    import sys

    code = (
        "import sys\n"
        "import harness.grade.run_plugin  # the in-container entrypoint module\n"
        "from harness.grade.plugins import BUILTIN_PLUGINS, get_plugin\n"
        "assert 'harness.grade.cli' not in sys.modules, 'cli leaked into the entrypoint path'\n"
        "assert 'groundwork' in BUILTIN_PLUGINS\n"
        "print(type(get_plugin('groundwork')).__name__)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, (
        "a registered built-in plugin must resolve on the run_plugin path "
        f"without grade/cli having been imported;\nstderr:\n{result.stderr}"
    )
    assert result.stdout.strip() == "GroundworkGrader"


def test_ac4_unknown_plugin_fails_closed(tmp_path):
    ws = write_workspace(tmp_path)
    ledger = tmp_path / "l.ndjson"
    task = GradeTask(id="t1", task_sha="s", plugin_ids=["does-not-exist"])
    out = grade_trial(
        "trial-1", task, ws, ledger, fixed_ctx(),
        container=GradingContainer(runner=ScriptedGradeRunner(
            {"assertions": [{"id": "h1", "result": "pass"}]})),
    )
    assert out.graded is False
    assert find_events(ledger, "cant_grade")[0]["reason"] == "plugin_error"


def test_m6_plugin_isolation_documented():
    """PRA-M6: the plugin seam and the deep dive must state that plugins run
    network-less in the grade container on the real path (with the no-daemon
    in-process path an explicit ADVISORY exception)."""
    import harness.grade.plugins as plugins_mod

    doc = plugins_mod.__doc__.lower()
    assert "network none" in doc.replace("-", " ") or "--network none" in plugins_mod.__doc__
    assert "advisory" in doc  # the local in-process fallback is disclosed
    deep = (Path(__file__).resolve().parents[1] / "docs" / "deep-dive.md").read_text()
    assert "network-less" in deep.lower()


def test_m6_plugin_command_is_network_less_and_hardened():
    """PRA-M6: the containerized plugin command drops all network and
    capabilities and runs the plugin entrypoint — plugins are no longer executed
    unsandboxed in the host process."""
    from harness.grade.container import GradingContainer

    gc = GradingContainer(image="verdi-bench/grader@sha256:" + "a" * 64)
    cmd = gc.build_plugin_command(Path("/ws"), ["groundwork"])
    assert cmd[:5] == ["docker", "run", "--rm", "--network", "none"]
    assert "--cap-drop" in cmd and cmd[cmd.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges" in cmd
    assert cmd[-4:] == ["python", "-m", "harness.grade.run_plugin", "groundwork"]


def test_m6_docker_runner_routes_plugins_to_container():
    """PRA-M6: the docker runner declares it runs plugins in a container, so
    GradingContainer.run_plugins takes the isolated path (not in-process)."""
    from harness.grade.container import DockerGradeRunner

    assert DockerGradeRunner.runs_plugins_in_container is True


def test_h1_plugin_results_ride_fenced_stdout_never_workspace(tmp_path, monkeypatch):
    """F-H1 A.4: plugin verdicts are scored from the entrypoint's fenced stdout;
    a plugin_results.json planted in the workspace copy during the run (the same
    in-run forgery vector as holdouts) never influences the assertions."""
    import json
    import subprocess

    from harness.grade.container import (
        DockerGradeRunner,
        GradingContainer,
        NONCE_ENV,
        plugin_fence,
    )
    from harness.grade.types import GradeTask

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "config.yaml").write_text("x: 1", encoding="utf-8")
    real = [{"id": "rule-1", "source": "plugin:groundwork", "result": "fail"}]
    forged = [{"id": "rule-1", "source": "plugin:groundwork", "result": "pass"}]

    def fake_run(cmd, **k):
        # the entrypoint reads the injected per-run nonce and stamps it into the
        # fence (as run_plugin.py does); a forged file in the copy is never read.
        nonce = next(a.split("=", 1)[1] for a in cmd if a.startswith(f"{NONCE_ENV}="))
        mount = next(a for a in cmd if a.endswith(":/workspace"))
        copy = Path(mount.rsplit(":", 1)[0])
        (copy / "plugin_results.json").write_text(json.dumps(forged), encoding="utf-8")
        begin, end = plugin_fence(nonce)
        stdout = f"{begin}\n{json.dumps(real)}\n{end}\n"
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    container = GradingContainer(runner=DockerGradeRunner())
    task = GradeTask(id="t", task_sha="s", plugin_ids=["groundwork"])
    out = container.run_plugins(ws, ["groundwork"], task)
    assert [a.model_dump(mode="json")["result"] for a in out] == ["fail"]


def test_m_o1_groundwork_without_fixture_output_fails_the_grade_closed(tmp_path):
    """F-M-O1: a production GradeTask (no fake_plugin_output) declaring the
    groundwork plugin previously graded with the plugin contributing zero
    assertions — a silent no-op. It now fails the grade closed as
    cant_grade(plugin_error)."""
    from harness.grade.container import GradingContainer, LocalGradeRunner
    from harness.grade.deterministic import grade_trial
    from harness.ledger.query import find_events
    from tests.fixtures.builders import fixed_ctx

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "holdout_results.json").write_text(
        '{"assertions": [{"id": "h1", "result": "pass"}]}', encoding="utf-8"
    )
    ledger = tmp_path / "l.ndjson"
    task = GradeTask(id="t", task_sha="s", plugin_ids=["groundwork"])
    out = grade_trial("t1", task, ws, ledger, fixed_ctx(),
                      container=GradingContainer(runner=LocalGradeRunner()))
    assert out.graded is False
    (ev,) = find_events(ledger, "cant_grade")
    assert ev["reason"] == "plugin_error"
