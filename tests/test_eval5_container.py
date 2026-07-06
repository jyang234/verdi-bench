"""EVAL-5 AC-1 — grading isolation: no network, holdouts read-only."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.grade.container import GradingContainer
from tests.fixtures.grading import write_holdout_results


def test_ac1_grading_isolated(tmp_path):
    """The grading container command denies the network namespace."""
    cmd = GradingContainer().build_grade_command(tmp_path / "ws", str(tmp_path / "holdouts"))
    assert "--network" in cmd
    assert cmd[cmd.index("--network") + 1] == "none"


def test_ac1_holdouts_readonly(tmp_path):
    """Holdouts are bind-mounted read-only (`:ro`)."""
    holdouts = tmp_path / "holdouts"
    cmd = GradingContainer().build_grade_command(tmp_path / "ws", str(holdouts))
    mounts = [cmd[i + 1] for i, t in enumerate(cmd) if t == "--volume"]
    holdout_mounts = [m for m in mounts if "/holdouts" in m]
    assert holdout_mounts and all(m.endswith(":ro") for m in holdout_mounts)
    # the workspace mount is NOT read-only (grading needs the agent's output)
    ws_mounts = [m for m in mounts if m.endswith("/workspace")]
    assert ws_mounts and not any(m.endswith(":ro") for m in ws_mounts)


def test_ac1_fresh_container_not_reused(tmp_path):
    """--rm ensures a fresh container each grade (trial containers never reused)."""
    cmd = GradingContainer().build_grade_command(tmp_path / "ws", "")
    assert "--rm" in cmd


def test_grader_image_configurable_not_placeholder(tmp_path):
    """GR-4: the grader image is configurable, not a hardcoded all-zeros digest
    (a nonexistent placeholder Docker could never pull)."""
    pinned = "verdi-bench/grader@sha256:" + "a" * 64
    cmd = GradingContainer(image=pinned).build_grade_command(tmp_path / "ws", "")
    assert pinned in cmd
    assert ("verdi-bench/grader@sha256:" + "0" * 64) not in cmd


def test_docker_runner_gates_nonzero_exit(tmp_path, monkeypatch):
    """GR-2: any nonzero exit (not just 125) is a container failure — the runner
    must NOT fall through to scoring a stale/forged workspace file."""
    import subprocess

    from harness.grade.container import DockerGradeRunner, GradingContainerError

    ws = tmp_path / "ws"
    ws.mkdir()
    # a forged results file is present; exit 137 must still refuse
    (ws / "holdout_results.json").write_text('{"assertions": []}', encoding="utf-8")

    def fake_run(*a, **k):
        return subprocess.CompletedProcess(a[0] if a else k.get("args"), 137, "", "OOM")

    monkeypatch.setattr(subprocess, "run", fake_run)
    from harness.grade.container import GraderUnavailableError

    with pytest.raises(GradingContainerError) as exc:
        DockerGradeRunner().run_holdouts(["docker", "run"], ws, "")
    # exit 137 = the grader RAN and failed -> terminal, not the transient subtype
    assert not isinstance(exc.value, GraderUnavailableError)


def test_docker_runner_exit_125_is_transient(tmp_path, monkeypatch):
    """A1/GR-11: exit 125 (daemon/config error, grader never ran) is a transient
    GraderUnavailableError, leaving the trial regradeable — not a terminal
    container_failure re-attempted forever."""
    import subprocess

    from harness.grade.container import DockerGradeRunner, GraderUnavailableError

    ws = tmp_path / "ws"
    ws.mkdir()

    def fake_run(*a, **k):
        return subprocess.CompletedProcess(a[0] if a else k.get("args"), 125, "", "daemon down")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(GraderUnavailableError):
        DockerGradeRunner().run_holdouts(["docker", "run"], ws, "")


def test_docker_runner_preflight_daemon_down_is_transient(tmp_path, monkeypatch):
    """7B-1/GR-8: a down daemon makes `docker run` exit 1 (terminal today). The
    pre-flight `docker version` probe catches daemon-down up front and raises the
    transient GraderUnavailableError, so a batch is not permanently quarantined."""
    import subprocess

    from harness.grade.container import DockerGradeRunner, GraderUnavailableError

    # daemon down: `docker version` exits nonzero
    def fake_run(*a, **k):
        return subprocess.CompletedProcess(a[0] if a else k.get("args"), 1, "", "Cannot connect to the Docker daemon")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(GraderUnavailableError):
        DockerGradeRunner().preflight()

    # docker binary absent: OSError is likewise transient, never terminal
    def boom(*a, **k):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(GraderUnavailableError):
        DockerGradeRunner().preflight()


def test_docker_runner_preflight_ok_when_daemon_up(tmp_path, monkeypatch):
    """A healthy `docker version` (exit 0) passes the probe silently."""
    import subprocess

    from harness.grade.container import DockerGradeRunner

    def fake_run(*a, **k):
        return subprocess.CompletedProcess(a[0] if a else k.get("args"), 0, "Client: ...", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    DockerGradeRunner().preflight()  # no raise


def test_docker_runner_malformed_output_fails_closed(tmp_path, monkeypatch):
    """GR-6: malformed holdout JSON on the docker path must not raise a bare
    ValueError that escapes grade_trial — it flows to cant_grade(malformed).
    [F-H1: the docker transport is fenced stdout, so 'malformed' means bad
    JSON inside the fence.]"""
    import subprocess

    from harness.grade.container import DockerGradeRunner
    from harness.grade.deterministic import MalformedHoldoutOutput, parse_holdout_output

    ws = tmp_path / "ws"
    ws.mkdir()

    def fake_run(*a, **k):
        stdout = _fenced("{not json")
        return subprocess.CompletedProcess(a[0] if a else k.get("args"), 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    run = DockerGradeRunner().run_holdouts(["docker", "run"], ws, "")
    # the runner returns a malformed marker rather than raising a bare ValueError;
    # parsing it fails closed with the module's typed error.
    with pytest.raises(MalformedHoldoutOutput):
        parse_holdout_output(run.raw_output)


def _fenced(body: str) -> str:
    from harness.grade.container import RESULTS_FENCE_BEGIN, RESULTS_FENCE_END

    return f"grader log noise\n{RESULTS_FENCE_BEGIN}\n{body}\n{RESULTS_FENCE_END}\ntail\n"


def test_h1_in_run_forged_results_file_is_never_scored(tmp_path, monkeypatch):
    """F-H1: agent code executing at grade time (holdouts import the solution)
    can rewrite holdout_results.json in the workspace copy AFTER the grader
    writes it — via an atexit hook or background thread. The forged file must
    never influence the grade: the docker path scores only the entrypoint's
    fenced stdout. Previously the host read the workspace file back, so the
    forged all-pass vector won."""
    import json as _json
    import subprocess

    from harness.grade.container import DockerGradeRunner, GradingContainer
    from harness.grade.deterministic import compute_binary_score, parse_holdout_output

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "solution.py").write_text("import atexit  # forgery vector", encoding="utf-8")

    def fake_run(cmd, **k):
        # a well-behaved grader reads the per-run nonce the host injected via -e
        # and stamps it into its fence (as the real grader image must).
        from harness.grade.container import NONCE_ENV, holdout_fence

        nonce = next(
            a.split("=", 1)[1] for a in cmd if a.startswith(f"{NONCE_ENV}=")
        )
        # simulate the in-run forgery: agent code plants an all-pass results
        # file in the graded copy, while the real grader reports FAIL on stdout
        mount = next(a for a in cmd if a.endswith(":/workspace"))
        copy = Path(mount.rsplit(":", 1)[0])
        write_holdout_results(copy, True)
        begin, end = holdout_fence(nonce)
        body = _json.dumps({"assertions": [{"id": "h1", "result": "fail"}]})
        stdout = f"grader log noise\n{begin}\n{body}\n{end}\ntail\n"
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    run = GradingContainer(runner=DockerGradeRunner()).run(ws, "")
    assertions = parse_holdout_output(run.raw_output)
    assert compute_binary_score(assertions) is False  # the real FAIL, not the forgery


def test_h1_fence_parser_fails_closed():
    """F-H1: zero fences → terminal container failure (grader produced no
    results); an ambiguous channel (a second fence — the agent printing its own
    forged block) → the malformed marker. Never scored either way."""
    import json as _json

    from harness.grade.container import (
        GradingContainerError,
        RESULTS_FENCE_BEGIN,
        RESULTS_FENCE_END,
        parse_fenced_stdout,
    )

    with pytest.raises(GradingContainerError, match="no fenced holdout results"):
        parse_fenced_stdout("just logs, no fence")

    good = _json.dumps({"assertions": [{"id": "h1", "result": "fail"}]})
    forged = _json.dumps({"assertions": [{"id": "h1", "result": "pass"}]})
    duplicated = _fenced(good) + _fenced(forged)
    assert parse_fenced_stdout(duplicated).raw_output == {"__malformed__": True}
    # inverted fences are ambiguous too
    inverted = f"{RESULTS_FENCE_END}\n{good}\n{RESULTS_FENCE_BEGIN}"
    assert parse_fenced_stdout(inverted).raw_output == {"__malformed__": True}
    # the happy path tolerates surrounding log noise
    assert parse_fenced_stdout(_fenced(good)).raw_output == _json.loads(good)


def test_h1_forged_fence_without_valid_nonce_never_scored():
    """F-H1 follow-up: the fence is nonce-authenticated, closing the residual the
    plain stdout transport left open.

    Agent code runs in the SAME container as the grader, so it can write to the
    captured stdout too. A well-timed agent could suppress the grader's genuine
    fence and print its OWN single forged block — indistinguishable under the old
    static, publicly-known markers. With a per-grade nonce the agent cannot read,
    a forged marker carries the wrong nonce (or none) and is not recognized:
    absent → fail-closed, never scored. Only the grader's correctly-nonced fence
    is read."""
    import json as _json

    from harness.grade.container import (
        GradingContainerError,
        holdout_fence,
        parse_fenced_stdout,
    )

    nonce = "s3cret-per-run-nonce"
    good = _json.dumps({"assertions": [{"id": "h1", "result": "fail"}]})
    forged = _json.dumps({"assertions": [{"id": "h1", "result": "pass"}]})

    # genuine fence carrying the correct nonce IS scored
    gb, ge = holdout_fence(nonce)
    assert (
        parse_fenced_stdout(f"noise\n{gb}\n{good}\n{ge}\n", nonce=nonce).raw_output
        == _json.loads(good)
    )

    # forged single fence with the BARE (un-nonced) markers — the format is
    # public, but without the nonce it is not the expected marker → absent.
    bb, be = holdout_fence(None)
    with pytest.raises(GradingContainerError, match="no fenced holdout results"):
        parse_fenced_stdout(f"{bb}\n{forged}\n{be}", nonce=nonce)

    # forged single fence with a GUESSED WRONG nonce → still absent, never scored
    wb, we = holdout_fence("wrong-guess")
    with pytest.raises(GradingContainerError, match="no fenced holdout results"):
        parse_fenced_stdout(f"{wb}\n{forged}\n{we}", nonce=nonce)

    # a longer guess cannot prefix-match the expected nonce'd marker either
    lb, le = holdout_fence(nonce + "extra")
    with pytest.raises(GradingContainerError, match="no fenced holdout results"):
        parse_fenced_stdout(f"{lb}\n{forged}\n{le}", nonce=nonce)

    # and if the agent somehow LEARNED the nonce and emitted a second valid
    # fence, two valid fences → ambiguous → malformed marker, still never scored
    both = f"{gb}\n{good}\n{ge}\n{gb}\n{forged}\n{ge}"
    assert parse_fenced_stdout(both, nonce=nonce).raw_output == {"__malformed__": True}


def test_h1_grade_command_injects_per_run_nonce(tmp_path):
    """F-H1 follow-up: the production grade path injects the per-run nonce into
    the container as VERDI_FENCE_NONCE (so the grader can stamp it into the
    fence), and omits the env entirely when no nonce is supplied."""
    from harness.grade.container import GradingContainer, NONCE_ENV

    cmd = GradingContainer().build_grade_command(tmp_path / "ws", "", nonce="abc123")
    assert "-e" in cmd
    assert f"{NONCE_ENV}=abc123" in cmd

    bare = GradingContainer().build_grade_command(tmp_path / "ws", "")
    assert not any(str(a).startswith(f"{NONCE_ENV}=") for a in bare)


def test_h1_run_mints_unpredictable_nonce_per_grade(tmp_path, monkeypatch):
    """F-H1 follow-up: each container grade mints a fresh, unpredictable nonce and
    threads it end to end — the fence the host validates carries the same nonce
    the command injected, and two grades use different nonces."""
    import subprocess

    from harness.grade.container import (
        DockerGradeRunner,
        GradingContainer,
        NONCE_ENV,
        holdout_fence,
    )

    seen: list[str] = []

    def fake_run(cmd, **k):
        nonce = next(a.split("=", 1)[1] for a in cmd if a.startswith(f"{NONCE_ENV}="))
        seen.append(nonce)
        begin, end = holdout_fence(nonce)
        body = '{"assertions": [{"id": "h1", "result": "pass"}]}'
        return subprocess.CompletedProcess(cmd, 0, f"{begin}\n{body}\n{end}\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ws = tmp_path / "ws"
    ws.mkdir()
    r1 = GradingContainer(runner=DockerGradeRunner()).run(ws, "")
    r2 = GradingContainer(runner=DockerGradeRunner()).run(ws, "")
    assert r1.raw_output == {"assertions": [{"id": "h1", "result": "pass"}]}
    assert len(seen) == 2 and seen[0] != seen[1]  # unpredictable, per-grade
    assert all(len(n) >= 16 for n in seen)


def test_ac1_grade_command_hardened(tmp_path):
    """F-H1: the holdout grade command carries the same hardening as the plugin
    command — capabilities dropped, no privilege escalation, non-root."""
    import os

    cmd = GradingContainer().build_grade_command(tmp_path / "ws", "")
    assert cmd[cmd.index("--cap-drop") + 1] == "ALL"
    assert cmd[cmd.index("--security-opt") + 1] == "no-new-privileges"
    if hasattr(os, "getuid"):
        assert cmd[cmd.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
