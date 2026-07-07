"""Hermetic tests for the real groundwork grader plugin [integration plan §3 A1].

No flowmap/groundwork binaries are needed here: the verdict→assertion MAPPING is
exercised against the committed ``groundwork review --json`` fixtures, the
subprocess ERROR TAXONOMY against tiny fake binaries (the external boundary), and
the trust-boundary asset resolution against planted directory layouts. The
real-binary end-to-end proof lives in ``test_groundwork_real_binary.py`` (gated on
VERDI_FLOWMAP_BIN/VERDI_GROUNDWORK_BIN); the container path in
``test_grade_groundwork_docker.py`` (docker-marked).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.grade.plugins import groundwork_shell
from harness.grade.plugins.groundwork import (
    GroundworkGrader,
    GroundworkShellError,
    GroundworkUnavailableError,
    _VERDICT_ASSERTION_ID,
    _VERDICT_MAP,
)
from harness.grade.types import AssertionResult, GradeTask
from tests.fixtures.groundwork_fixtures import INVSVC_DIR, load_review


def _by_id(assertions):
    out: dict = {}
    for a in assertions:
        out.setdefault(a.id, []).append(a)
    return out


def _results(assertions, rule_id):
    return [a.result for a in assertions if a.id == rule_id]


# --------------------------------------------------------------------------- #
# Verdict → assertion mapping over the committed review fixtures.
# --------------------------------------------------------------------------- #

def test_map_review_block_flags_failed_rule_with_id_preserved():
    """A BLOCK verdict → the top-line verdict fails AND the violating rule id is
    preserved as a failed assertion (the planted reach-trap violation)."""
    assertions = GroundworkGrader()._map_review(load_review("block"))
    verdict = _by_id(assertions)[_VERDICT_ASSERTION_ID][0]
    assert verdict.result == AssertionResult.failed
    # rule id preserved verbatim from groundwork's `new_violations[].rule`
    assert _results(assertions, "must_not_reach") == [AssertionResult.failed]
    # the specific policy-rule name + edge ride the detail (forensic color)
    detail = next(a.detail for a in assertions if a.id == "must_not_reach")
    assert "show-item-stays-read-only" in detail
    assert "ShowItem" in detail and "boundary:db INSERT" in detail


def test_map_review_structurally_clear_is_pass():
    """STRUCTURALLY-CLEAR (structure changed, no violation) → passed, no fails."""
    assertions = GroundworkGrader()._map_review(load_review("clear"))
    verdict = _by_id(assertions)[_VERDICT_ASSERTION_ID][0]
    assert verdict.result == AssertionResult.passed
    assert all(a.result != AssertionResult.failed for a in assertions)


def test_map_review_no_structural_signal_is_abstain_never_pass():
    """NO-STRUCTURAL-SIGNAL (body-only change) → abstain, NEVER pass [tenet 4]."""
    assertions = GroundworkGrader()._map_review(load_review("nosignal"))
    verdict = _by_id(assertions)[_VERDICT_ASSERTION_ID][0]
    assert verdict.result == AssertionResult.abstain
    assert all(a.result != AssertionResult.passed for a in assertions)


def test_map_review_caution_is_abstain_never_pass():
    """A caution (the graph cannot prove a negative — reflect frontier blind) →
    abstain, preserving the rule id; never a pass. This is the blind-spot case."""
    assertions = GroundworkGrader()._map_review(load_review("caution"))
    # the standing caution's rule id is preserved and maps to abstain
    assert AssertionResult.abstain in _results(assertions, "must_not_reach")
    assert all(a.result != AssertionResult.passed for a in assertions)
    assert all(a.result != AssertionResult.failed for a in assertions)


def test_map_review_unknown_verdict_is_abstain_never_pass():
    """An unmapped/future top-line verdict → abstain (fail closed), never a silent
    pass — even though a real violation in the same artifact still maps to fail."""
    assertions = GroundworkGrader()._map_review(load_review("unknown"))
    verdict = _by_id(assertions)[_VERDICT_ASSERTION_ID][0]
    assert verdict.result == AssertionResult.abstain
    assert "unmapped groundwork verdict" in verdict.detail
    # the concrete finding is still surfaced faithfully as a failed rule
    assert _results(assertions, "must_not_reach") == [AssertionResult.failed]


def test_review_verdict_map_matches_verdi_go_vocabulary():
    """Parity pin: _VERDICT_MAP encodes verdi-go's review Verdict constants
    (review/artifact.go: Block / StructurallyClear / NoStructuralSignal) with the
    fail-closed poles, plus the retained fixture-tier aliases."""
    assert _VERDICT_MAP["BLOCK"] == AssertionResult.failed
    assert _VERDICT_MAP["STRUCTURALLY-CLEAR"] == AssertionResult.passed
    assert _VERDICT_MAP["NO-STRUCTURAL-SIGNAL"] == AssertionResult.abstain
    # fixture-tier aliases retained so fake_plugin_output keeps working unchanged
    assert _VERDICT_MAP["pass"] == AssertionResult.passed
    assert _VERDICT_MAP["fail"] == AssertionResult.failed


def test_fake_path_unchanged_still_maps_scripted_rules():
    """The FIXTURE tier is untouched: scripted per-rule verdicts map as before,
    rule ids preserved, NO-STRUCTURAL-SIGNAL abstains, unknown abstains."""
    task = GradeTask(id="go1", task_sha="s", fake_plugin_output={"rules": [
        {"id": "RULE-A", "verdict": "pass"},
        {"id": "RULE-B", "verdict": "fail"},
        {"id": "RULE-C", "verdict": "NO-STRUCTURAL-SIGNAL"},
        {"id": "RULE-D", "verdict": "who-knows"},
    ]})
    by_id = {a.id: a for a in GroundworkGrader().grade(workspace=None, task=task)}
    assert by_id["RULE-A"].result == AssertionResult.passed
    assert by_id["RULE-B"].result == AssertionResult.failed
    assert by_id["RULE-C"].result == AssertionResult.abstain
    assert by_id["RULE-D"].result == AssertionResult.abstain  # unknown → abstain


# --------------------------------------------------------------------------- #
# Trust boundary: assets resolve from the holdouts side ONLY (never /workspace).
# --------------------------------------------------------------------------- #

def _plant_holdouts(root: Path, policy: str = '{"service":"invsvc","version":1}') -> Path:
    assets = root / "groundwork"
    assets.mkdir(parents=True)
    (assets / "policy.json").write_text(policy, encoding="utf-8")
    (assets / "base.graph.json").write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
    return assets


def test_asset_resolution_local_runner_uses_task_holdouts_dir(tmp_path, monkeypatch):
    """Off-container (no /holdouts mount): assets come from task.holdouts_dir."""
    # ensure the container branch is NOT taken (point it at a nonexistent dir)
    monkeypatch.setattr(groundwork_shell, "CONTAINER_HOLDOUTS", tmp_path / "no-such-mount")
    hd = tmp_path / "hd"
    _plant_holdouts(hd)
    task = GradeTask(id="t", task_sha="s", holdouts_dir=str(hd))
    policy, base = groundwork_shell.resolve_assets(task)
    assert policy == hd / "groundwork" / "policy.json"
    assert base == hd / "groundwork" / "base.graph.json"


def test_asset_resolution_container_mount_wins(tmp_path, monkeypatch):
    """In-container: the /holdouts mount is used even if a stale holdouts_dir
    string travelled in the task.json — the trusted mount wins."""
    mount = tmp_path / "holdouts_mount"
    _plant_holdouts(mount)
    monkeypatch.setattr(groundwork_shell, "CONTAINER_HOLDOUTS", mount)
    task = GradeTask(id="t", task_sha="s", holdouts_dir=str(tmp_path / "stale_ignored"))
    policy, base = groundwork_shell.resolve_assets(task)
    assert policy == mount / "groundwork" / "policy.json"


def test_workspace_decoy_policy_is_ignored(tmp_path, monkeypatch):
    """SECURITY [plan §2]: a policy.json/base.graph.json planted in the agent
    workspace is NEVER resolved — assets come from the holdouts side only, so the
    graded party cannot pick its own grader inputs."""
    monkeypatch.setattr(groundwork_shell, "CONTAINER_HOLDOUTS", tmp_path / "no-mount")
    # the trusted holdouts (strict) and an agent workspace with a decoy (permissive)
    hd = tmp_path / "hd"
    _plant_holdouts(hd, policy='{"service":"invsvc","version":1,"strict":true}')
    workspace = tmp_path / "workspace"
    (workspace / "groundwork").mkdir(parents=True)
    (workspace / "groundwork" / "policy.json").write_text(
        '{"service":"attacker","version":1,"permissive":true}', encoding="utf-8")
    (workspace / "policy.json").write_text('{"decoy":true}', encoding="utf-8")

    task = GradeTask(id="t", task_sha="s", holdouts_dir=str(hd))
    policy, base = groundwork_shell.resolve_assets(task)
    # resolved strictly under the holdouts tree — never under the workspace
    assert str(policy).startswith(str(hd))
    assert str(workspace) not in str(policy)
    assert json.loads(policy.read_text())["strict"] is True


def test_missing_holdouts_dir_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(groundwork_shell, "CONTAINER_HOLDOUTS", tmp_path / "no-mount")
    task = GradeTask(id="t", task_sha="s", holdouts_dir="")
    with pytest.raises(GroundworkUnavailableError):
        groundwork_shell.resolve_assets(task)


def test_missing_asset_files_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(groundwork_shell, "CONTAINER_HOLDOUTS", tmp_path / "no-mount")
    hd = tmp_path / "hd"
    (hd / "groundwork").mkdir(parents=True)  # empty — no policy/base graph
    task = GradeTask(id="t", task_sha="s", holdouts_dir=str(hd))
    with pytest.raises(GroundworkUnavailableError, match="policy asset missing"):
        groundwork_shell.resolve_assets(task)


# --------------------------------------------------------------------------- #
# Error taxonomy — every failure raises (→ cant_grade(plugin_error) upstream).
# The flowmap/groundwork binaries are the external boundary; a tiny fake stands
# in so the REAL subprocess + parse path is exercised without the toolchain.
# --------------------------------------------------------------------------- #

def _fake_bin(path: Path, *, exit_code: int = 0, stdout: str = "", stderr: str = "") -> str:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.stderr.write({stderr!r})\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return str(path)


def test_binary_missing_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("VERDI_FLOWMAP_BIN", str(tmp_path / "nope"))
    with pytest.raises(GroundworkUnavailableError, match="misconfigured"):
        groundwork_shell._resolve_binary("VERDI_FLOWMAP_BIN", "flowmap")


def test_binary_not_on_path_fails_closed(monkeypatch):
    monkeypatch.delenv("VERDI_GROUNDWORK_BIN", raising=False)
    monkeypatch.setattr(groundwork_shell.shutil, "which", lambda _n: None)
    with pytest.raises(GroundworkUnavailableError, match="not found on PATH"):
        groundwork_shell._resolve_binary("VERDI_GROUNDWORK_BIN", "groundwork")


def test_flowmap_compile_failure_raises_with_stderr(tmp_path, monkeypatch):
    fake = _fake_bin(tmp_path / "flowmap", exit_code=1,
                     stderr="loader: 3 type-check/load error(s): undefined: foo")
    monkeypatch.setenv("VERDI_FLOWMAP_BIN", fake)
    with pytest.raises(GroundworkShellError, match="compile-failure") as ei:
        groundwork_shell.regenerate_branch_graph(
            tmp_path, "sha", tmp_path, groundwork_shell._subprocess_env(tmp_path))
    assert "undefined: foo" in str(ei.value)  # stderr tail preserved


def test_groundwork_operational_exit_2_raises(tmp_path, monkeypatch):
    fake = _fake_bin(tmp_path / "groundwork", exit_code=2,
                     stderr="groundwork/graph: decode: EOF")
    monkeypatch.setenv("VERDI_GROUNDWORK_BIN", fake)
    (tmp_path / "p.json").write_text("{}")
    (tmp_path / "b.json").write_text("{}")
    with pytest.raises(GroundworkShellError, match="operational failure .exit 2"):
        groundwork_shell.run_review(
            tmp_path / "p.json", tmp_path / "b.json", tmp_path / "b.json",
            groundwork_shell._subprocess_env(tmp_path))


def test_groundwork_unexpected_exit_raises(tmp_path, monkeypatch):
    fake = _fake_bin(tmp_path / "groundwork", exit_code=7, stderr="boom")
    monkeypatch.setenv("VERDI_GROUNDWORK_BIN", fake)
    (tmp_path / "p.json").write_text("{}")
    with pytest.raises(GroundworkShellError, match="unexpected exit 7"):
        groundwork_shell.run_review(
            tmp_path / "p.json", tmp_path / "p.json", tmp_path / "p.json",
            groundwork_shell._subprocess_env(tmp_path))


def test_groundwork_malformed_json_raises(tmp_path, monkeypatch):
    fake = _fake_bin(tmp_path / "groundwork", exit_code=0, stdout="not json at all")
    monkeypatch.setenv("VERDI_GROUNDWORK_BIN", fake)
    (tmp_path / "p.json").write_text("{}")
    with pytest.raises(GroundworkShellError, match="malformed JSON"):
        groundwork_shell.run_review(
            tmp_path / "p.json", tmp_path / "p.json", tmp_path / "p.json",
            groundwork_shell._subprocess_env(tmp_path))


def test_gate_fail_exit_1_is_not_an_error(tmp_path, monkeypatch):
    """Exit 1 from groundwork means the branch violated the policy — NOT an
    operational error. It parses to a BLOCK artifact the mapper turns into fails."""
    fake = _fake_bin(tmp_path / "groundwork", exit_code=1,
                     stdout=json.dumps(load_review("block")))
    monkeypatch.setenv("VERDI_GROUNDWORK_BIN", fake)
    (tmp_path / "p.json").write_text("{}")
    artifact = groundwork_shell.run_review(
        tmp_path / "p.json", tmp_path / "p.json", tmp_path / "p.json",
        groundwork_shell._subprocess_env(tmp_path))
    assert artifact["verdict"] == "BLOCK"


def test_full_pipeline_with_fakes_maps_block(tmp_path, monkeypatch):
    """review_artifact end-to-end with fake binaries: resolve assets, regenerate
    the branch graph, run review, and hand back an artifact the plugin maps to a
    failed rule — proving the wiring without the real toolchain."""
    monkeypatch.setattr(groundwork_shell, "CONTAINER_HOLDOUTS", tmp_path / "no-mount")
    hd = tmp_path / "hd"
    _plant_holdouts(hd)
    monkeypatch.setenv("VERDI_FLOWMAP_BIN",
                       _fake_bin(tmp_path / "flowmap", stdout='{"nodes":[],"edges":[]}'))
    monkeypatch.setenv("VERDI_GROUNDWORK_BIN",
                       _fake_bin(tmp_path / "groundwork", exit_code=1,
                                 stdout=json.dumps(load_review("block"))))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task = GradeTask(id="t", task_sha="s", holdouts_dir=str(hd))
    assertions = GroundworkGrader().grade(workspace, task)
    assert _results(assertions, "must_not_reach") == [AssertionResult.failed]


# --------------------------------------------------------------------------- #
# Toolchain provenance: the real path records which flowmap+groundwork build
# produced the verdict, on the verdict assertion's free-text detail (NO schema
# change) [integration plan §10 P0]. A version-probe failure degrades to
# "unknown" and must NOT fail the grade — the verdict is already computed.
# --------------------------------------------------------------------------- #

def _fake_versioned_bin(
    path: Path, *, version_line: str, version_exit: int = 0,
    exit_code: int = 0, stdout: str = "", stderr: str = "",
) -> str:
    """A fake flowmap/groundwork that answers ``version`` distinctly from its other
    subcommands (``graph`` / ``review``) — so the real capture_toolchain path is
    exercised without the toolchain: ``<bin> version`` prints ``version_line`` and
    exits ``version_exit``; anything else prints ``stdout`` and exits ``exit_code``."""
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if len(sys.argv) > 1 and sys.argv[1] == 'version':\n"
        f"    sys.stdout.write({version_line!r} + '\\n')\n"
        f"    raise SystemExit({version_exit})\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.stderr.write({stderr!r})\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return str(path)


def test_capture_toolchain_reads_both_version_lines(tmp_path, monkeypatch):
    """capture_toolchain probes ``version`` on the SAME resolved binaries and
    returns each tool's verbatim one-line identity, no error."""
    monkeypatch.setenv("VERDI_FLOWMAP_BIN", _fake_versioned_bin(
        tmp_path / "flowmap", version_line="flowmap v1.2.3"))
    monkeypatch.setenv("VERDI_GROUNDWORK_BIN", _fake_versioned_bin(
        tmp_path / "groundwork", version_line="groundwork v4.5.6"))
    tc = groundwork_shell.capture_toolchain(groundwork_shell._subprocess_env(tmp_path))
    assert tc.error is None
    assert tc.flowmap == "flowmap v1.2.3"
    assert tc.groundwork == "groundwork v4.5.6"


def test_full_pipeline_records_toolchain_versions_in_verdict_detail(tmp_path, monkeypatch):
    """End-to-end real path: the ``groundwork:verdict`` assertion detail carries
    ``; toolchain: flowmap <v>, groundwork <v>`` — provenance is additive; the
    BLOCK → failed verdict is unchanged."""
    monkeypatch.setattr(groundwork_shell, "CONTAINER_HOLDOUTS", tmp_path / "no-mount")
    hd = tmp_path / "hd"
    _plant_holdouts(hd)
    monkeypatch.setenv("VERDI_FLOWMAP_BIN", _fake_versioned_bin(
        tmp_path / "flowmap", version_line="flowmap v9.9.9-test",
        stdout='{"nodes":[],"edges":[]}'))
    monkeypatch.setenv("VERDI_GROUNDWORK_BIN", _fake_versioned_bin(
        tmp_path / "groundwork", version_line="groundwork v8.8.8-test",
        exit_code=1, stdout=json.dumps(load_review("block"))))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task = GradeTask(id="t", task_sha="s", holdouts_dir=str(hd))
    assertions = GroundworkGrader().grade(workspace, task)

    verdict = next(a for a in assertions if a.id == _VERDICT_ASSERTION_ID)
    assert "; toolchain: flowmap v9.9.9-test, groundwork v8.8.8-test" in verdict.detail
    assert verdict.detail.startswith("groundwork review verdict: BLOCK")
    # the verdict itself is unchanged — provenance never alters the pole
    assert verdict.result == AssertionResult.failed
    assert _results(assertions, "must_not_reach") == [AssertionResult.failed]


def test_version_probe_failure_degrades_to_unknown_without_failing_grade(tmp_path, monkeypatch):
    """A failing ``flowmap version`` (its ``graph`` still works, so the verdict
    computes) degrades the toolchain line to ``unknown (<reason>)`` — the grade is
    NOT failed: provenance is best-effort, the verdict is load-bearing."""
    monkeypatch.setattr(groundwork_shell, "CONTAINER_HOLDOUTS", tmp_path / "no-mount")
    hd = tmp_path / "hd"
    _plant_holdouts(hd)
    # `flowmap graph` succeeds (review runs); `flowmap version` exits non-zero.
    monkeypatch.setenv("VERDI_FLOWMAP_BIN", _fake_versioned_bin(
        tmp_path / "flowmap", version_line="", version_exit=3,
        stdout='{"nodes":[],"edges":[]}'))
    monkeypatch.setenv("VERDI_GROUNDWORK_BIN", _fake_versioned_bin(
        tmp_path / "groundwork", version_line="groundwork v8.8.8-test",
        exit_code=1, stdout=json.dumps(load_review("block"))))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task = GradeTask(id="t", task_sha="s", holdouts_dir=str(hd))
    assertions = GroundworkGrader().grade(workspace, task)  # must NOT raise

    verdict = next(a for a in assertions if a.id == _VERDICT_ASSERTION_ID)
    assert "; toolchain: unknown (" in verdict.detail
    # the real verdict still computed and rode through untouched
    assert verdict.result == AssertionResult.failed
    assert _results(assertions, "must_not_reach") == [AssertionResult.failed]


# --------------------------------------------------------------------------- #
# Container wiring: the plugin container mounts the trusted holdouts read-only at
# /holdouts, and the in-container task names that mount (never the host path).
# --------------------------------------------------------------------------- #

def test_plugin_command_mounts_holdouts_readonly_when_set():
    from harness.grade.plugins.launch import build_plugin_command

    cmd = build_plugin_command("img", Path("/ws"), ["groundwork"], holdouts_dir="/host/hd")
    assert "/host/hd:/holdouts:ro" in " ".join(cmd)
    # the argv-identity tail is preserved (the mount rides the flags, not the cmd)
    assert cmd[-4:] == ["python", "-m", "harness.grade.run_plugin", "groundwork"]


def test_plugin_command_has_no_holdouts_mount_when_unset():
    from harness.grade.plugins.launch import build_plugin_command

    cmd = build_plugin_command("img", Path("/ws"), ["groundwork"])
    assert "/holdouts" not in " ".join(cmd)


def test_in_container_task_json_names_the_holdouts_mount_not_host_path(tmp_path, monkeypatch):
    """The task the plugin reads in-container must name ``/holdouts`` (the trusted
    read-only mount), NOT the host holdouts_dir — so asset resolution hits the
    mount [integration plan §2]. Capture the task.json the launcher writes."""
    import subprocess

    from harness.grade.plugins import launch

    workspace = tmp_path / "ws"
    workspace.mkdir()
    captured: dict = {}

    def fake_run_grading_container(docker, cmd, *, noun):
        # find the mounted task.json (host side) and read what the container sees
        task_mount = next(a for a in cmd if a.endswith(":/verdi/task.json:ro"))
        host_task = Path(task_mount.split(":")[0])
        captured["task"] = json.loads(host_task.read_text())
        captured["cmd"] = cmd
        begin, end = launch.plugin_fence(
            next(a.split("=", 1)[1] for a in cmd if a.startswith("VERDI_FENCE_NONCE=")))
        return subprocess.CompletedProcess(cmd, 0, f"{begin}\n[]\n{end}\n", "")

    monkeypatch.setattr(launch, "run_grading_container", fake_run_grading_container)
    task = GradeTask(id="t", task_sha="s", holdouts_dir=str(tmp_path / "host_holdouts"),
                     plugin_ids=["groundwork"])
    launch.run_plugins_in_container(object(), "img", workspace, ["groundwork"], task)
    assert captured["task"]["holdouts_dir"] == "/holdouts"  # mount, never the host path
    assert str(tmp_path / "host_holdouts") + ":/holdouts:ro" in " ".join(captured["cmd"])
