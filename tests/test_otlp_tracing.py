"""``TraceCollector`` lifecycle — stand-up, metered-only, D-09-1, CLI [refactor 09 §3].

Unit-level (no live daemon): a recording DockerClient captures argv, so the
metered-network-only stand-up, the readiness probe, the teardown, the D-09-1 raw-log
deletion, and the operator verbs are all checkable without docker. The live
end-to-end collector is proven in ``test_e2e_otlp_capture.py``.
"""

from __future__ import annotations

import subprocess

import pytest
from typer.testing import CliRunner

from harness.hermetic.tracing import (
    COLLECTOR_PORT,
    MANAGED_COLLECTOR_NAME,
    CollectorConfig,
    TraceCollector,
    TraceCollectorError,
    teardown_managed,
)


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


def test_collector_refuses_without_daemon(tmp_path):
    d = _RecordingDocker()
    d.available = False
    with pytest.raises(TraceCollectorError, match="docker daemon is unavailable"):
        TraceCollector(log_path=tmp_path / "otlp.jsonl", docker=d).start()


def test_collector_stands_up_metered_only_and_yields_config(tmp_path):
    """start() creates ONLY the metered (internal) network — never egress — runs the
    collector with COLLECTOR_LOG injected, probes readiness, and yields the endpoint."""
    d = _RecordingDocker(script={("docker", "network", "inspect"): 1})  # network absent
    log = tmp_path / "otlp" / "otlp.jsonl"
    cfg = TraceCollector(log_path=log, docker=d).start()
    assert isinstance(cfg, CollectorConfig)
    flat = [" ".join(c) for c in d.calls]
    # the metered network is created --internal; the egress network is NEVER touched
    assert any("network create --internal verdi-metered" in f for f in flat)
    assert not any("verdi-egress" in f for f in flat), "collector must never touch egress"
    assert not any(c[:3] == ["docker", "network", "connect"] for c in d.calls)
    # the collector runs the mounted stdlib script with the log basename injected
    run_cmd = next(c for c in d.calls if c[:2] == ["docker", "run"])
    assert "/verdi/collector.py" in run_cmd
    assert any(t.startswith("COLLECTOR_LOG=") for t in run_cmd)
    # readiness is a probe on the collector, not a fixed wait
    assert any(c[:3] == ["docker", "exec", MANAGED_COLLECTOR_NAME] for c in d.calls)
    assert cfg.endpoint == f"http://{MANAGED_COLLECTOR_NAME}:{COLLECTOR_PORT}"
    assert cfg.log_path == str(log)


def test_collector_teardown_removes_container_and_metered_network(tmp_path):
    d = _RecordingDocker()
    TraceCollector(log_path=tmp_path / "otlp.jsonl", docker=d).stop()
    flat = [" ".join(c) for c in d.calls]
    assert any(f"rm -f {MANAGED_COLLECTOR_NAME}" in f for f in flat)
    assert any("network rm verdi-metered" in f for f in flat)
    assert not any("verdi-egress" in f for f in flat)  # it never owned egress


def test_collector_context_manager_tears_down_on_error(tmp_path, monkeypatch):
    """A stand-up that fails after making the network still tears everything down."""
    d = _RecordingDocker()
    monkeypatch.setattr(
        TraceCollector,
        "_await_ready",
        lambda self: (_ for _ in ()).throw(TraceCollectorError("never ready")),
    )
    with pytest.raises(TraceCollectorError):
        with TraceCollector(log_path=tmp_path / "otlp.jsonl", docker=d):
            pass
    flat = [" ".join(c) for c in d.calls]
    assert any("network rm verdi-metered" in f for f in flat)  # torn down despite error


def test_collector_honors_custom_log_basename(tmp_path):
    """The 988af58 lesson at the host tier: the COLLECTOR_LOG env carries the
    operator's basename under the mounted dir, so a custom log path is honored."""
    d = _RecordingDocker(script={("docker", "network", "inspect"): 0})
    log = tmp_path / "custom-otlp.jsonl"
    cfg = TraceCollector(log_path=log, docker=d).start()
    assert cfg.log_path == str(log)
    run_call = next(c for c in d.calls if c[:2] == ["docker", "run"] and "-d" in c)
    env_tokens = [run_call[i + 1] for i, t in enumerate(run_call) if t in ("--env", "-e")]
    assert "COLLECTOR_LOG=/var/log/verdi/custom-otlp.jsonl" in env_tokens, env_tokens


# --- D-09-1 raw-log retention ------------------------------------------------
def test_d091_default_deletes_envelope_log_on_teardown(tmp_path):
    d = _RecordingDocker()
    log = tmp_path / "otlp.jsonl"
    log.write_text('{"trial":"t","seq":0}\n', encoding="utf-8")
    TraceCollector(log_path=log, docker=d).stop()
    assert not log.exists(), "D-09-1 default: the raw envelope log is deleted on teardown"


def test_d091_keep_raw_retains_envelope_log(tmp_path):
    d = _RecordingDocker()
    log = tmp_path / "otlp.jsonl"
    log.write_text('{"trial":"t","seq":0}\n', encoding="utf-8")
    TraceCollector(log_path=log, keep_raw=True, docker=d).stop()
    assert log.exists(), "keep_raw retains the raw envelope log (operator-tier)"


def test_d091_teardown_managed_deletes_unless_keep_raw(tmp_path):
    d = _RecordingDocker()
    log = tmp_path / "otlp.jsonl"
    log.write_text("x\n", encoding="utf-8")
    teardown_managed(docker=d, log_path=log)
    assert not log.exists()
    log.write_text("x\n", encoding="utf-8")
    teardown_managed(docker=d, log_path=log, keep_raw=True)
    assert log.exists()


def test_managed_owns_temp_logdir_removed_on_teardown():
    """An absent log_path gets a managed temp dir removed on teardown (default)."""
    d = _RecordingDocker()
    tc = TraceCollector(docker=d)
    tc._logfile.parent.mkdir(parents=True, exist_ok=True)
    tc._logfile.write_text("x\n", encoding="utf-8")
    logdir = tc._logfile.parent
    tc.stop()
    assert not logdir.exists()


# --- operator verbs (bench otlp up/down) -------------------------------------
def test_bench_otlp_up_and_down(monkeypatch, tmp_path):
    from harness.cli import app
    from harness.hermetic import cli as hcli
    from harness.hermetic import tracing

    monkeypatch.setattr(
        tracing.TraceCollector,
        "start",
        lambda self: CollectorConfig(
            endpoint=f"http://{MANAGED_COLLECTOR_NAME}:{COLLECTOR_PORT}",
            log_path=str(self._logfile),
        ),
    )
    torn: dict = {}
    monkeypatch.setattr(hcli, "teardown_collector", lambda **kw: torn.update(kw))

    r = CliRunner().invoke(
        app, ["otlp", "up", "--log-path", str(tmp_path / "otlp.jsonl")]
    )
    assert r.exit_code == 0, r.output
    assert f"trace collector up: http://{MANAGED_COLLECTOR_NAME}:{COLLECTOR_PORT}" in r.output
    assert "D-09-1" in r.output  # the raw-log deletion default is surfaced

    r2 = CliRunner().invoke(app, ["otlp", "down"])
    assert r2.exit_code == 0, r2.output
    assert torn.get("name") == MANAGED_COLLECTOR_NAME
    assert torn.get("keep_raw") is False


def test_bench_otlp_up_partial_failure_tears_down(monkeypatch):
    """[M4] A crash mid-stand-up on `bench otlp up` must not leak the container/network."""
    from harness.cli import app
    from harness.hermetic import cli as hcli

    calls = {"start": 0, "stop": 0}

    class _Boom:
        def __init__(self, *a, **kw):
            pass

        def start(self):
            calls["start"] += 1
            raise TraceCollectorError("scripted mid-stand-up crash")

        def stop(self):
            calls["stop"] += 1

    monkeypatch.setattr(hcli, "TraceCollector", _Boom)
    result = CliRunner().invoke(app, ["otlp", "up"])
    assert result.exit_code == 1
    assert "did not come up" in (result.output or "") + str(result.exception or "")
    assert calls == {"start": 1, "stop": 1}


# --- SDK facade re-export ----------------------------------------------------
def test_trace_collector_re_exported_by_sdk():
    """The surfacing triad [refactor 09 §3]: the SDK re-exports TraceCollector,
    owner staying hermetic (sdk imports hermetic, never the reverse)."""
    import harness.sdk as sdk
    from harness.hermetic.tracing import TraceCollector as Owner

    assert sdk.TraceCollector is Owner
    assert "TraceCollector" in sdk.__all__
    assert sdk.CollectorConfig is CollectorConfig
