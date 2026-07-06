"""``harness.hermetic`` — DockerClient, HardenedCommand, networks, MeteringProxy [refactor 04 §1].

Unit-level (no live daemon): the docker subprocess call is monkeypatched or a fake
DockerClient records argv, so the shared recipe, the daemon probe, the network
lifecycle, and the managed-proxy stand-up/teardown are checkable without docker.
The live end-to-end managed proxy is proven in ``test_e2e_managed_proxy.py``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from harness.hermetic import network as net
from harness.hermetic.docker import (
    DAEMON_ERROR_EXIT,
    TIMEOUT_EXIT,
    DockerClient,
    HardenedCommand,
)
from harness.hermetic.metering import MeteringProxy, MeteringProxyError


# --- DockerClient ----------------------------------------------------------
def test_docker_client_run_passes_argv_and_merges_env(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        seen["kw"] = kw
        return subprocess.CompletedProcess(argv, 0, "out", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    proc = DockerClient().run(["docker", "ps"], timeout_s=7, env={"K": "V"})
    assert proc.returncode == 0 and proc.stdout == "out"
    assert seen["argv"] == ["docker", "ps"]
    assert seen["kw"]["timeout"] == 7 and seen["kw"]["check"] is False
    # env is layered over the process environment (only the delta is passed in)
    assert seen["kw"]["env"]["K"] == "V" and "PATH" in seen["kw"]["env"]


def test_docker_client_run_empty_env_inherits(monkeypatch):
    seen = {}
    monkeypatch.setattr(subprocess, "run",
                        lambda argv, **kw: seen.update(kw) or subprocess.CompletedProcess(argv, 0, "", ""))
    DockerClient().run(["docker", "ps"])
    assert seen["env"] is None  # inherit the parent environment unchanged


def test_docker_client_run_propagates_timeout_and_oserror(monkeypatch):
    def boom_timeout(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw.get("timeout"))

    monkeypatch.setattr(subprocess, "run", boom_timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        DockerClient().run(["docker", "run"], timeout_s=1)

    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("docker")))
    with pytest.raises(OSError):
        DockerClient().run(["docker", "run"])


def test_docker_client_run_does_not_raise_on_nonzero(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, DAEMON_ERROR_EXIT, "", "boom"))
    proc = DockerClient().run(["docker", "run"])  # check=False: nonzero is data, not an error
    assert proc.returncode == DAEMON_ERROR_EXIT


def test_daemon_available_true_false(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "Server:", ""))
    assert DockerClient().daemon_available() is True
    monkeypatch.setattr(subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "cannot connect"))
    assert DockerClient().daemon_available() is False
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("docker")))
    assert DockerClient().daemon_available() is False


def test_exit_code_constants():
    assert (DAEMON_ERROR_EXIT, TIMEOUT_EXIT) == (125, 124)


# --- HardenedCommand: the shared recipe ------------------------------------
def test_harden_shared_security_recipe():
    assert HardenedCommand().harden().build() == [
        "docker", "run", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
    ]
    # harbor's trial shape pins a pids cap; grade's shapes do not
    assert HardenedCommand().harden(pids_limit=512).build()[-2:] == ["--pids-limit", "512"]


def test_memory_pins_swap_to_the_limit():
    assert HardenedCommand().memory("4g").build()[2:] == [
        "--memory", "4g", "--memory-swap", "4g",
    ]


def test_user_matches_invoking_user_or_noop():
    argv = HardenedCommand().user().build()
    if hasattr(os, "getuid"):
        assert argv[2:] == ["--user", f"{os.getuid()}:{os.getgid()}"]
    else:  # pragma: no cover - POSIX in CI
        assert argv == ["docker", "run"]


def test_volume_resolves_host_and_marks_ro(tmp_path):
    ws = tmp_path / "ws"
    argv = HardenedCommand().volume(ws, "/workspace").volume(ws, "/holdouts", ro=True).build()
    assert argv[2:4] == ["--volume", f"{ws.resolve()}:/workspace"]
    assert argv[4:6] == ["--volume", f"{ws.resolve()}:/holdouts:ro"]


def test_env_spellings_distinct():
    assert HardenedCommand().env("KEY").build()[2:] == ["--env", "KEY"]
    assert HardenedCommand().env_kv("HTTP_PROXY", "http://p").build()[2:] == [
        "--env", "HTTP_PROXY=http://p",
    ]
    assert HardenedCommand().e_env("VERDI_FENCE_NONCE", "abc").build()[2:] == [
        "-e", "VERDI_FENCE_NONCE=abc",
    ]


def test_builder_reproduces_harbor_trial_shape(tmp_path):
    """The full harbor trial argv (proxy + quotas + key + request mount) built
    through the shared recipe matches the hand-pinned byte sequence."""
    ws, rf = tmp_path / "ws", tmp_path / "req.json"
    user = ["--user", f"{os.getuid()}:{os.getgid()}"] if hasattr(os, "getuid") else []
    argv = (
        HardenedCommand().rm().pull_never().name("verdi-t1").user()
        .harden(pids_limit=512).cpus(2.0).memory("4g")
        .env_kv("HTTP_PROXY", "http://t1@p:3128").env_kv("HTTPS_PROXY", "http://t1@p:3128")
        .network("verdi-metered").env("ANTHROPIC_API_KEY")
        .volume(ws, "/workspace").volume(rf, "/verdi/request.json", ro=True)
        .workdir("/workspace").image("img@sha256:x").build()
    )
    assert argv == (
        ["docker", "run", "--rm", "--pull=never", "--name", "verdi-t1"] + user
        + ["--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "512",
           "--cpus", "2.0", "--memory", "4g", "--memory-swap", "4g",
           "--env", "HTTP_PROXY=http://t1@p:3128", "--env", "HTTPS_PROXY=http://t1@p:3128",
           "--network", "verdi-metered", "--env", "ANTHROPIC_API_KEY",
           "--volume", f"{ws.resolve()}:/workspace",
           "--volume", f"{rf.resolve()}:/verdi/request.json:ro",
           "--workdir", "/workspace", "img@sha256:x"]
    )


# --- network helpers -------------------------------------------------------
class _RecordingDocker:
    """A DockerClient stand-in that records argv and returns scripted results."""

    def __init__(self, script=None):
        self.calls: list[list[str]] = []
        self._script = script or {}
        self.available = True

    def run(self, argv, **kw):
        self.calls.append(list(argv))
        rc = self._script.get(tuple(argv[:3]), 0)
        return subprocess.CompletedProcess(argv, rc, "", "")

    def daemon_available(self):
        return self.available


def test_ensure_metered_network_creates_when_absent():
    d = _RecordingDocker(script={("docker", "network", "inspect"): 1})  # absent
    net.ensure_metered_network(d)
    assert any(c[:3] == ["docker", "network", "create"] for c in d.calls)
    assert net.METERED_NETWORK == "verdi-metered"  # the frozen constant


def test_ensure_metered_network_noop_when_present():
    d = _RecordingDocker(script={("docker", "network", "inspect"): 0})  # present
    net.ensure_metered_network(d)
    assert not any(c[:3] == ["docker", "network", "create"] for c in d.calls)


def test_create_network_raises_on_failure():
    d = _RecordingDocker(script={("docker", "network", "inspect"): 1,
                                 ("docker", "network", "create"): 1})
    with pytest.raises(net.NetworkError):
        net.create_network(d, "verdi-egress")


# --- MeteringProxy ---------------------------------------------------------
def test_metering_proxy_refuses_without_daemon(tmp_path):
    d = _RecordingDocker()
    d.available = False
    with pytest.raises(MeteringProxyError, match="docker daemon is unavailable"):
        MeteringProxy(["api.anthropic.com"], log_path=tmp_path / "p.jsonl", docker=d).start()


def test_metering_proxy_injects_allowlist_and_yields_config(tmp_path):
    """start() creates both networks, runs the proxy with the allowlist INJECTED
    (never a hardcoded set), connects egress, and yields a wired ProxyConfig."""
    d = _RecordingDocker(script={("docker", "network", "inspect"): 1})  # networks absent
    log = tmp_path / "metering" / "verdi.jsonl"
    cfg = MeteringProxy(["api.anthropic.com", "api.openai.com"],
                        log_path=log, docker=d).start()
    flat = [" ".join(c) for c in d.calls]
    # both networks created, internal on the metered side
    assert any("network create --internal verdi-metered" in f for f in flat)
    assert any("network create verdi-egress" in f for f in flat)
    # the allowlist is injected as env, not hardcoded
    run_cmd = next(c for c in d.calls if c[:2] == ["docker", "run"])
    assert "VERDI_PROXY_ALLOW=api.anthropic.com,api.openai.com" in run_cmd
    assert any(t.startswith("PROXY_LOG=") for t in run_cmd)
    # the proxy is attached to egress and the config points trials at it
    assert any(c[:3] == ["docker", "network", "connect"] and "verdi-egress" in c for c in d.calls)
    assert cfg.proxy_url == "http://verdi-metering-proxy:3128"
    assert cfg.log_path == str(log)
    assert cfg.allowlist == ["api.anthropic.com", "api.openai.com"]
    # readiness is a probe, not a fixed wait
    assert any(c[:3] == ["docker", "exec", "verdi-metering-proxy"] for c in d.calls)


def test_metering_proxy_teardown_removes_container_and_networks(tmp_path):
    d = _RecordingDocker()
    mp = MeteringProxy(["h"], log_path=tmp_path / "p.jsonl", docker=d)
    mp.stop()
    flat = [" ".join(c) for c in d.calls]
    assert any("rm -f verdi-metering-proxy" in f for f in flat)
    assert any("network rm verdi-egress" in f for f in flat)
    assert any("network rm verdi-metered" in f for f in flat)


def test_metering_proxy_context_manager_tears_down_on_error(tmp_path, monkeypatch):
    """A stand-up that fails after making networks still tears everything down."""
    d = _RecordingDocker()
    monkeypatch.setattr(MeteringProxy, "_await_ready",
                        lambda self: (_ for _ in ()).throw(MeteringProxyError("never ready")))
    with pytest.raises(MeteringProxyError):
        with MeteringProxy(["h"], log_path=tmp_path / "p.jsonl", docker=d):
            pass
    flat = [" ".join(c) for c in d.calls]
    assert any("network rm verdi-metered" in f for f in flat)  # torn down despite the error


def test_metering_proxy_honors_custom_log_basename(tmp_path):
    """[P3 interim review F1] The container must write the OPERATOR's filename:
    a custom basename previously fell open — the proxy wrote verdi.jsonl while
    ProxyConfig.log_path pointed at the touched-but-empty custom file, so the
    PRA-H4 scanner read zero egress. The PROXY_LOG env token must carry the
    operator's basename under the mounted log dir."""
    d = _RecordingDocker(script={("docker", "network", "inspect"): 0})
    log = tmp_path / "custom-egress.jsonl"
    cfg = MeteringProxy(["api.anthropic.com"], log_path=log, docker=d).start()
    assert cfg.log_path == str(log)
    run_call = next(c for c in d.calls if c[:2] == ["docker", "run"] and "-d" in c)
    env_tokens = [run_call[i + 1] for i, t in enumerate(run_call) if t in ("--env", "-e")]
    assert "PROXY_LOG=/var/log/verdi/custom-egress.jsonl" in env_tokens, env_tokens
