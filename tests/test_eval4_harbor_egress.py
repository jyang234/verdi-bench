"""EVAL-4 AC-3 — real metering-proxy attribution (JSONL, per-trial) and
kill-on-timeout [RN-11, RN-10, D-10].

This file is unit-level only: JSONL parsing, per-trial proxy-auth injection, the
named container, the kill-on-timeout command, and network creation are all driven
with a fake runner / monkeypatched subprocess (no daemon). The real, daemon-backed
proxy and kill paths are exercised by the docker-marked tests in other files
(``test_e2e_harbor.py``), not here."""

from __future__ import annotations

import json
import subprocess as sp
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.adapters.base import Quotas
from harness.run.engines.harbor import DockerCliRunner, HarborEngine
from harness.run.seam import run_trial
from harness.run.types import OtlpConfig, ProxyConfig, RunConfig, Task, TrialRequest
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
    attempts, violation, _cost = HarborEngine._scan_proxy_log(req)
    assert set(attempts) == {"api.anthropic.com", "evil.example.com"}  # only this trial
    assert violation is True


def test_h4_missing_proxy_log_fails_loud(tmp_path):
    """PRA-H4: a configured-but-absent proxy log is NOT silently treated as zero
    egress/cost — it raises so the trial can fail infra_failed(proxy_log_missing)."""
    from harness.run.engines.harbor import ProxyLogMissingError

    req = SimpleNamespace(
        trial_id="trial-x",
        proxy=ProxyConfig(proxy_url="http://p:3128", log_path=str(tmp_path / "absent.jsonl")),
    )
    with pytest.raises(ProxyLogMissingError):
        HarborEngine._scan_proxy_log(req)


def test_m7_unconfirmed_kill_sets_kill_failed(monkeypatch):
    """PRA-M7: if after the kill the container is STILL running (the kill did not
    take), run_container reports kill_failed so the caller fails the trial closed
    instead of trusting a possibly-still-live container's (unredacted) workspace.
    Confirmation is via `docker inspect .State.Running`, not the kill/wait exit
    codes (unreliable under --rm auto-removal)."""
    def fake_run(cmd, **kw):
        if cmd[:2] == ["docker", "run"]:
            raise sp.TimeoutExpired(cmd, kw.get("timeout"))
        class _R:
            returncode = 0
            stderr = ""
            # inspect reports the container is STILL Running ⇒ kill did not take
            stdout = "true" if "inspect" in cmd else ""
        return _R()

    monkeypatch.setattr(sp, "run", fake_run)
    out = DockerCliRunner().run_container(
        ["docker", "run", "--name", "verdi-trial-x", "img@sha256:a"], timeout_s=1
    )
    assert out.timed_out is True and out.kill_failed is True


def test_m7_reaped_container_is_not_kill_failed(monkeypatch):
    """PRA-M7: a --rm container that was killed and auto-removed makes `docker
    inspect` exit nonzero (gone) — that is a CONFIRMED kill, not a failure."""
    def fake_run(cmd, **kw):
        if cmd[:2] == ["docker", "run"]:
            raise sp.TimeoutExpired(cmd, kw.get("timeout"))
        class _R:
            # inspect on a removed container exits nonzero ("no such object")
            returncode = 1 if "inspect" in cmd else 0
            stdout = stderr = ""
        return _R()

    monkeypatch.setattr(sp, "run", fake_run)
    out = DockerCliRunner().run_container(
        ["docker", "run", "--rm", "--name", "verdi-trial-y", "img@sha256:a"], timeout_s=1
    )
    assert out.timed_out is True and out.kill_failed is False


def test_scan_proxy_log_sums_metered_cost(tmp_path):
    """Review #2: harbor sources proxy_metered_cost from per-line `cost` in the
    JSONL (summed for this trial), so a null-telemetry-cost arm is enforceable on
    the real path — not only the fake engine."""
    log = tmp_path / "proxy.jsonl"
    log.write_text("\n".join([
        json.dumps({"trial": "trial-x", "host": "api.anthropic.com", "decision": "allow", "cost": 0.03}),
        json.dumps({"trial": "trial-x", "host": "api.anthropic.com", "decision": "allow", "cost": 0.05}),
        json.dumps({"trial": "other", "host": "h", "decision": "allow", "cost": 9.0}),
    ]) + "\n", encoding="utf-8")
    req = SimpleNamespace(trial_id="trial-x",
                          proxy=ProxyConfig(proxy_url="http://p", log_path=str(log)))
    _attempts, _violation, cost = HarborEngine._scan_proxy_log(req)
    assert cost == 0.08  # only this trial's lines summed


def test_scan_proxy_log_tolerates_non_object_lines(tmp_path):
    """Review #4: a valid-JSON-but-non-object line (42, null, [1]) is skipped, not
    an AttributeError crash that aborts the whole run."""
    log = tmp_path / "proxy.jsonl"
    log.write_text("\n".join([
        "42", "null", '"x"', "[1, 2]", "not json at all",
        json.dumps({"trial": "trial-x", "host": "evil", "decision": "deny"}),
    ]) + "\n", encoding="utf-8")
    req = SimpleNamespace(trial_id="trial-x",
                          proxy=ProxyConfig(proxy_url="http://p", log_path=str(log)))
    attempts, violation, _cost = HarborEngine._scan_proxy_log(req)  # must not raise
    assert attempts == ["evil"] and violation is True


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


def test_l9_container_hardening_flags_present(tmp_path):
    """PRA-L9: every trial container drops capabilities, forbids privilege
    escalation, caps pids, and pins swap to the memory limit."""
    runner = FakeDockerRunner(native_log={})
    run_trial(_pinned_task(), _arm(), tmp_path / "ws",
              RunConfig(engine=HarborEngine(runner=runner)))
    cmd = runner.last_cmd
    assert cmd[cmd.index("--cap-drop") + 1] == "ALL"
    assert cmd[cmd.index("--security-opt") + 1] == "no-new-privileges"
    assert "--pids-limit" in cmd


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


# --- RN-11 reverse listeners: ANTHROPIC_BASE_URL steering + NO_PROXY ----------
def _env_kv(argv: list[str]) -> dict[str, str]:
    """Extract every ``--env KEY=VALUE`` pair from a docker run argv."""
    kv: dict[str, str] = {}
    for i, tok in enumerate(argv):
        if tok == "--env" and "=" in argv[i + 1]:
            k, v = argv[i + 1].split("=", 1)
            kv[k] = v
    return kv


def _reverse_request(reverse_endpoints, *, otlp=None, env=None, trial_id="trial-x") -> TrialRequest:
    return TrialRequest(
        trial_id=trial_id, task_id="t", prompt="p", image="img@sha256:" + "a" * 64,
        arm=_arm(), repetition=0, workspace=Path("/tmp/ws"), quotas=Quotas(), timeout_s=60,
        ts="2026-01-01T00:00:00+00:00",
        proxy=ProxyConfig(
            allowlist=list(reverse_endpoints), proxy_url="http://verdi-metering-proxy:3128",
            log_path="/x/p.jsonl", reverse_endpoints=dict(reverse_endpoints),
        ),
        otlp=otlp, env=env or {},
    )


def test_harbor_injects_reverse_base_url_and_keeps_proxy():
    """A reverse endpoint for api.anthropic.com steers the pinned claude CLI via
    ANTHROPIC_BASE_URL (base + /t/<trial>), while HTTP(S)_PROXY stay injected so a
    well-behaved client keeps using the CONNECT tunnel [RN-11]."""
    eng = HarborEngine(runner=FakeDockerRunner())
    req = _reverse_request({"api.anthropic.com": "http://10.0.0.5:3129"}, trial_id="trial-rev")
    kv = _env_kv(eng.build_run_command(req, "img@sha256:x"))
    assert kv["ANTHROPIC_BASE_URL"] == "http://10.0.0.5:3129/t/trial-rev"
    assert kv["HTTP_PROXY"] == "http://trial-rev@verdi-metering-proxy:3128"
    assert kv["HTTPS_PROXY"] == "http://trial-rev@verdi-metering-proxy:3128"


def test_harbor_skips_reverse_host_with_no_base_url_env():
    """A reverse endpoint whose host is not in the engine's base-URL map (e.g.
    api.openai.com) injects no env var and no NO_PROXY entry — the map has no
    consumer for it [RN-11]."""
    eng = HarborEngine(runner=FakeDockerRunner())
    kv = _env_kv(eng.build_run_command(
        _reverse_request({"api.openai.com": "http://10.0.0.6:3129"}), "img@sha256:x"
    ))
    assert not any(k.endswith("_BASE_URL") for k in kv)
    assert "NO_PROXY" not in kv


def test_no_proxy_reverse_only_names_the_reverse_ip():
    """A reverse-steered trial names the reverse listener's IP in NO_PROXY so a
    proxy-honoring client does not CONNECT-tunnel to the raw IP (the allowlist would
    deny it and poison the trial) [RN-11]."""
    eng = HarborEngine(runner=FakeDockerRunner())
    kv = _env_kv(eng.build_run_command(
        _reverse_request({"api.anthropic.com": "http://10.0.0.5:3129"}), "img@sha256:x"
    ))
    assert kv["NO_PROXY"] == "10.0.0.5"


def test_no_proxy_otlp_plus_reverse_orders_collector_then_reverse():
    """otlp + reverse: the merged NO_PROXY keeps the operator value in front, then
    the collector host, then each injected reverse IP — one injection [RN-11]."""
    eng = HarborEngine(runner=FakeDockerRunner())
    otlp = OtlpConfig(endpoint="http://verdi-trace-collector:4318", log_path="/x/o.jsonl")
    argv = eng.build_run_command(
        _reverse_request(
            {"api.anthropic.com": "http://10.0.0.5:3129"}, otlp=otlp, env={"NO_PROXY": "localhost"}
        ),
        "img@sha256:x",
    )
    kv = _env_kv(argv)
    assert kv["NO_PROXY"] == "localhost,verdi-trace-collector,10.0.0.5"
    injections = [
        argv[i + 1] for i, t in enumerate(argv)
        if t == "--env" and argv[i + 1].startswith("NO_PROXY=")
    ]
    assert injections == ["NO_PROXY=localhost,verdi-trace-collector,10.0.0.5"]  # exactly once


def test_no_shim_flags_in_metered_argv():
    """Regression pin for the egress-shim removal: a metered trial's argv carries
    no --add-host / --sysctl / VERDI_EGRESS_SHIM (superseded by the reverse path)."""
    eng = HarborEngine(runner=FakeDockerRunner())
    joined = " ".join(eng.build_run_command(
        _reverse_request({"api.anthropic.com": "http://10.0.0.5:3129"}), "img@sha256:x"
    ))
    assert "--add-host" not in joined
    assert "--sysctl" not in joined
    assert "VERDI_EGRESS_SHIM" not in joined
