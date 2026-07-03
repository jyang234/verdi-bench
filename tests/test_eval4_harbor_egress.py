"""EVAL-4 AC-3 — real metering-proxy attribution (JSONL, per-trial) and
kill-on-timeout [RN-11, RN-10, D-10].

Unit level: JSONL parsing, per-trial proxy-auth injection, the named container,
the kill-on-timeout command, and network creation are all driven with a fake
runner / monkeypatched subprocess (no daemon). The real proxy + real kill are
docker-marked (2H, CI)."""

from __future__ import annotations

import json
import subprocess as sp
from types import SimpleNamespace

from harness.run.engines.harbor import DockerCliRunner, HarborEngine
from harness.run.seam import run_trial
from harness.run.types import ProxyConfig, RunConfig, Task
from harness.schema.experiment import Arm
from tests.fixtures.run_fakes import FakeDockerRunner


def _arm():
    return Arm(name="control", platform="claude_code", model="anthropic/claude-3-5-sonnet-20241022")


def _pinned_task():
    return Task(id="t", prompt="p", image="verdi-bench/agent@sha256:" + "a" * 64)


# --- RN-11: real JSONL proxy log, keyed per trial --------------------------
def test_scan_proxy_log_parses_jsonl_by_trial(tmp_path):
    """RN-11: the proxy log is structured JSONL keyed on trial; a deny for THIS
    trial flags a violation, other trials' lines are ignored."""
    log = tmp_path / "proxy.jsonl"
    log.write_text("\n".join([
        json.dumps({"trial": "trial-x", "host": "api.anthropic.com", "decision": "allow"}),
        json.dumps({"trial": "trial-x", "host": "evil.example.com", "decision": "deny"}),
        json.dumps({"trial": "other", "host": "evil.example.com", "decision": "deny"}),
    ]) + "\n", encoding="utf-8")
    req = SimpleNamespace(
        trial_id="trial-x",
        proxy=ProxyConfig(proxy_url="http://p:3128", log_path=str(log)),
    )
    attempts, violation = HarborEngine._scan_proxy_log(req)
    assert set(attempts) == {"api.anthropic.com", "evil.example.com"}  # only this trial
    assert violation is True


def test_proxy_url_carries_per_trial_auth(tmp_path):
    """RN-11/D-10: the injected proxy URL attributes egress to the trial (the
    metering proxy sees the trial id as the CONNECT credential)."""
    from harness.run.egress import proxy_config

    runner = FakeDockerRunner(native_log={})
    cfg = RunConfig(engine=HarborEngine(runner=runner),
                    proxy=proxy_config(["api.anthropic.com"], proxy_url="http://proxy:3128"))
    run_trial(_pinned_task(), _arm(), tmp_path / "ws", cfg, trial_id="trial-fixed")
    cmd = runner.last_cmd
    assert any(t == f"HTTP_PROXY=http://trial-fixed@proxy:3128" for t in cmd)
    assert any(t == f"HTTPS_PROXY=http://trial-fixed@proxy:3128" for t in cmd)


def test_harbor_ensures_metered_network_when_proxied(tmp_path):
    """RN-11: the verdi-metered network is created/verified before a proxied
    trial runs (it was referenced by --network but never created)."""
    from harness.run.egress import proxy_config

    runner = FakeDockerRunner(native_log={})
    cfg = RunConfig(engine=HarborEngine(runner=runner),
                    proxy=proxy_config(["api.anthropic.com"], proxy_url="http://proxy:3128"))
    run_trial(_pinned_task(), _arm(), tmp_path / "ws", cfg)
    assert runner.metered_network_ensured is True


# --- RN-10: named container, killed on timeout -----------------------------
def test_container_is_named_for_kill(tmp_path):
    """RN-10: the container is named after the trial so it is killable by name."""
    runner = FakeDockerRunner(native_log={})
    run_trial(_pinned_task(), _arm(), tmp_path / "ws",
              RunConfig(engine=HarborEngine(runner=runner)), trial_id="trial-fixed")
    cmd = runner.last_cmd
    assert "--name" in cmd and cmd[cmd.index("--name") + 1] == "verdi-trial-fixed"


def test_container_runs_as_invoking_user(tmp_path):
    """RN-7: the trial runs as the invoking user, so files it writes into the
    bind-mounted workspace are harness-owned and can be redacted at capture (a
    root container would leave root-owned files the harness can't scrub)."""
    import os

    runner = FakeDockerRunner(native_log={})
    run_trial(_pinned_task(), _arm(), tmp_path / "ws",
              RunConfig(engine=HarborEngine(runner=runner)))
    cmd = runner.last_cmd
    assert "--user" in cmd and cmd[cmd.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"


def test_docker_runner_kills_named_container_on_timeout(monkeypatch):
    """RN-10: on timeout the named container is killed (not just the CLI), so it
    stops writing into the mounted workspace before redaction runs."""
    calls: list[list[str]] = []

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        if cmd[:2] == ["docker", "run"]:
            raise sp.TimeoutExpired(cmd, kw.get("timeout"))
        return _R()

    monkeypatch.setattr(sp, "run", fake_run)
    out = DockerCliRunner().run_container(
        ["docker", "run", "--name", "verdi-trial-x", "img@sha256:a"], timeout_s=1
    )
    assert out.timed_out is True
    kills = [c for c in calls if c[:2] == ["docker", "kill"]]
    assert kills and "verdi-trial-x" in kills[0]


def test_docker_runner_creates_metered_network_if_absent(monkeypatch):
    """RN-11: ensure_metered_network creates the network only when it's absent."""
    calls: list[list[str]] = []

    class _R:
        def __init__(self, rc=0):
            self.returncode = rc
            self.stdout = ""
            self.stderr = ""

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        if cmd[:3] == ["docker", "network", "inspect"]:
            return _R(rc=1)  # network absent
        return _R(rc=0)

    monkeypatch.setattr(sp, "run", fake_run)
    DockerCliRunner().ensure_metered_network()
    assert any(c[:3] == ["docker", "network", "create"] for c in calls)
