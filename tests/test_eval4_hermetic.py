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


def test_label_builder_emits_key_value():
    """``--label KEY=VALUE`` — the ownership tag a label-scoped teardown filters on,
    so a lifecycle op removes only containers it owns (incident 2026-07-10)."""
    assert HardenedCommand().label("verdi.managed-sidecar", "metering-proxy").build()[2:] == [
        "--label", "verdi.managed-sidecar=metering-proxy",
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
    """A DockerClient stand-in that records argv and returns scripted results.

    A ``docker inspect`` call (the reverse-endpoint IP resolution [RN-11]) returns
    ``inspect_ip`` as stdout so the managed proxy's ``_config`` can build its
    reverse_endpoints; pass ``inspect_ip=""`` to script the empty-inspect failure."""

    def __init__(self, script=None, inspect_ip="10.88.0.7"):
        self.calls: list[list[str]] = []
        self._script = script or {}
        self._inspect_ip = inspect_ip
        self.available = True

    def run(self, argv, **kw):
        self.calls.append(list(argv))
        rc = self._script.get(tuple(argv[:3]), 0)
        stdout = self._inspect_ip if argv[:2] == ["docker", "inspect"] else ""
        return subprocess.CompletedProcess(argv, rc, stdout, "")

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
    mp = MeteringProxy(["api.anthropic.com", "api.openai.com"], log_path=log, docker=d)
    cfg = mp.start()
    flat = [" ".join(c) for c in d.calls]
    # both networks created, internal on the metered side
    assert any("network create --internal verdi-metered" in f for f in flat)
    assert any("network create verdi-egress" in f for f in flat)
    # the allowlist is injected as env, not hardcoded
    run_cmd = next(c for c in d.calls if c[:2] == ["docker", "run"])
    assert "VERDI_PROXY_ALLOW=api.anthropic.com,api.openai.com" in run_cmd
    assert any(t.startswith("PROXY_LOG=") for t in run_cmd)
    # the proxy is attached to egress and the config points trials at THIS instance's
    # unique name (never a shared bare name — incident 2026-07-10)
    assert any(c[:3] == ["docker", "network", "connect"] and "verdi-egress" in c for c in d.calls)
    assert cfg.proxy_url == f"http://{mp.name}:3128"
    assert mp.name.startswith("verdi-metering-proxy-")
    assert cfg.log_path == str(log)
    assert cfg.allowlist == ["api.anthropic.com", "api.openai.com"]
    # readiness is a probe, not a fixed wait
    assert any(c[:3] == ["docker", "exec", mp.name] for c in d.calls)


# --- MeteringProxy reverse listeners [RN-11] -------------------------------
def test_metering_proxy_injects_reverse_ports(tmp_path):
    """_stand_up injects VERDI_REVERSE_PORTS mapping port 3129+i to allowlist host i
    (bare hosts, no :port), so the proxy binds a reverse listener per allowed host."""
    d = _RecordingDocker(script={("docker", "network", "inspect"): 1})
    MeteringProxy(["api.anthropic.com", "api.openai.com"],
                  log_path=tmp_path / "p.jsonl", docker=d).start()
    run_cmd = next(c for c in d.calls if c[:2] == ["docker", "run"])
    assert "VERDI_REVERSE_PORTS=3129=api.anthropic.com,3130=api.openai.com" in run_cmd


def test_metering_proxy_config_yields_reverse_endpoints(tmp_path):
    """_config resolves the proxy's metered-network IP (docker inspect) and yields a
    reverse_endpoints map {host: http://<ip>:<3129+i>} (no /t suffix — harbor adds it)."""
    d = _RecordingDocker(script={("docker", "network", "inspect"): 0}, inspect_ip="10.88.0.7")
    cfg = MeteringProxy(["api.anthropic.com", "api.openai.com"],
                        log_path=tmp_path / "p.jsonl", docker=d).start()
    assert cfg.reverse_endpoints == {
        "api.anthropic.com": "http://10.88.0.7:3129",
        "api.openai.com": "http://10.88.0.7:3130",
    }
    # the IP came from an inspect over the METERED_NETWORK, not the container name
    assert any(c[:2] == ["docker", "inspect"] and "verdi-metered" in " ".join(c) for c in d.calls)


def test_metering_proxy_config_raises_on_empty_inspect(tmp_path):
    """An empty/failed inspect must fail loudly — a ProxyConfig with unusable reverse
    endpoints would strand every claude trial with no signal [RN-11]."""
    d = _RecordingDocker(script={("docker", "network", "inspect"): 0}, inspect_ip="")
    with pytest.raises(MeteringProxyError):
        MeteringProxy(["api.anthropic.com"], log_path=tmp_path / "p.jsonl", docker=d).start()


def test_metering_proxy_no_reverse_ports_when_allowlist_empty(tmp_path):
    """An empty allowlist injects no VERDI_REVERSE_PORTS and yields no reverse
    endpoints (nothing to front) — no inspect needed."""
    d = _RecordingDocker(script={("docker", "network", "inspect"): 1})
    cfg = MeteringProxy([], log_path=tmp_path / "p.jsonl", docker=d).start()
    run_cmd = next(c for c in d.calls if c[:2] == ["docker", "run"])
    assert not any(t.startswith("VERDI_REVERSE_PORTS=") for t in run_cmd)
    assert cfg.reverse_endpoints == {}


def test_metering_proxy_teardown_removes_container_and_networks(tmp_path):
    d = _RecordingDocker()
    mp = MeteringProxy(["h"], log_path=tmp_path / "p.jsonl", docker=d)
    mp.stop()
    flat = [" ".join(c) for c in d.calls]
    assert any(f"rm -f {mp.name}" in f for f in flat)  # this instance's own name
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


# --- instance-unique names + label-scoped teardown (incident 2026-07-10) ----
# A fixed global container name let any lifecycle actor operate on a name it did
# not own: a concurrent e2e cleanup removed a LIVE harbor run's proxy and
# invalidated 21/24 trials, and two concurrent ``bench run``s would kill each
# other's proxy via ``start()``'s stale-sweep. The fix: instance-unique default
# names + an ownership label the teardown sweeps on.
class _SweepDocker:
    """A DockerClient stand-in whose label-filtered ``docker ps`` returns scripted
    container ids, so the label-scoped teardown's discover-then-remove is checkable."""

    def __init__(self, ids):
        self.calls: list[list[str]] = []
        self._ids = list(ids)

    def run(self, argv, **kw):
        self.calls.append(list(argv))
        stdout = "\n".join(self._ids) + "\n" if argv[:2] == ["docker", "ps"] else ""
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    def daemon_available(self):
        return True


def test_two_default_proxies_get_distinct_names(tmp_path):
    """Two default proxies must NOT share a container name — a shared name lets one
    lifecycle op remove the other's live container. Both carry the constant as a
    prefix; a deterministic per-instance suffix (pid+counter, no randomness) diverges
    them. Pure constructor — no docker."""
    a = MeteringProxy([], log_path=tmp_path / "a.jsonl")
    b = MeteringProxy([], log_path=tmp_path / "b.jsonl")
    assert a.name != b.name, "default proxies collided on a global name"
    assert a.name.startswith("verdi-metering-proxy-")
    assert b.name.startswith("verdi-metering-proxy-")


def test_metering_proxy_stand_up_carries_ownership_label(tmp_path):
    """Every managed proxy container is tagged ``verdi.managed-sidecar=metering-proxy``
    so teardown can sweep by ownership rather than by a shared name."""
    d = _RecordingDocker(script={("docker", "network", "inspect"): 1})
    MeteringProxy([], log_path=tmp_path / "p.jsonl", docker=d).start()
    run_cmd = next(c for c in d.calls if c[:2] == ["docker", "run"])
    assert "--label" in run_cmd, run_cmd
    assert run_cmd[run_cmd.index("--label") + 1] == "verdi.managed-sidecar=metering-proxy"


def test_teardown_managed_sweeps_by_label_when_no_name():
    """Default ``teardown_managed`` removes EVERY container this kind owns (label
    filter), never a bare shared name — so it cannot miss a suffixed live proxy nor
    remove an unrelated bare-name container."""
    from harness.hermetic.metering import teardown_managed

    d = _SweepDocker(["cid1", "cid2"])
    teardown_managed(docker=d)
    flat = [" ".join(c) for c in d.calls]
    assert any(
        "ps -aq --filter label=verdi.managed-sidecar=metering-proxy" in f for f in flat
    ), flat
    assert any("rm -f cid1" in f for f in flat)
    assert any("rm -f cid2" in f for f in flat)


def test_teardown_managed_removes_exact_name_when_given():
    """An explicit ``name=`` removes exactly that container (operator escape hatch),
    with no label sweep."""
    from harness.hermetic.metering import teardown_managed

    d = _SweepDocker([])
    teardown_managed(docker=d, name="verdi-metering-proxy-42-1")
    flat = [" ".join(c) for c in d.calls]
    assert any("rm -f verdi-metering-proxy-42-1" in f for f in flat)
    assert not any("--filter" in c for c in d.calls), "exact name must not sweep by label"
