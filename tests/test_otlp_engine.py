"""Engine-side OTLP wiring: harbor env injection, NO_PROXY, fake parity [refactor 09 §4].

The A11 request plumbing (``TrialRequest.otlp``), harbor's OTel env injection with
the load-bearing ``NO_PROXY`` pin, the fake-engine scripted-envelope parity (A10
pattern), and the ``_managed_collector`` run wrap — all checkable without docker.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest

from harness.adapters.base import Quotas
from harness.run.api import _managed_collector
from harness.run.engines.fake import FakeEngine
from harness.run.engines.harbor import HarborEngine
from harness.run.seam import run_trial
from harness.run.settings import RunSettings
from harness.run.types import OtlpConfig, ProxyConfig, RunConfig, Task, TrialRequest
from harness.schema.experiment import Arm
from tests.fixtures.run_fakes import FakeDockerRunner

_COLLECTOR = "http://verdi-trace-collector:4318"


def _arm():
    return Arm(name="control", platform="claude_code", model="anthropic/claude-3-5-sonnet-20241022")


def _req(*, otlp=None, proxy=None, env=None, trial_id="trial-x") -> TrialRequest:
    return TrialRequest(
        trial_id=trial_id, task_id="t", prompt="p", image="img@sha256:" + "a" * 64,
        arm=_arm(), repetition=0, workspace=Path("/tmp/ws"), quotas=Quotas(), timeout_s=60,
        ts="2026-01-01T00:00:00+00:00", proxy=proxy, otlp=otlp, env=env or {},
    )


def _env_kv(argv: list[str]) -> dict[str, str]:
    """Extract every ``--env KEY=VALUE`` pair from a docker run argv."""
    kv: dict[str, str] = {}
    for i, tok in enumerate(argv):
        if tok == "--env" and "=" in argv[i + 1]:
            k, v = argv[i + 1].split("=", 1)
            kv[k] = v
    return kv


# --- harbor env injection (A11) ---------------------------------------------
def test_harbor_injects_otel_env_when_otlp_configured():
    eng = HarborEngine(runner=FakeDockerRunner())
    otlp = OtlpConfig(endpoint=_COLLECTOR, log_path="/x/otlp.jsonl")
    proxy = ProxyConfig(
        allowlist=["api.anthropic.com"], proxy_url="http://verdi-metering-proxy:3128",
        log_path="/x/p.jsonl",
    )
    argv = eng.build_run_command(_req(otlp=otlp, proxy=proxy), "img@sha256:x")
    kv = _env_kv(argv)
    assert kv["OTEL_EXPORTER_OTLP_ENDPOINT"] == _COLLECTOR
    assert kv["OTEL_EXPORTER_OTLP_HEADERS"] == "x-verdi-trial=trial-x"
    assert kv["NO_PROXY"] == "verdi-trace-collector"


def test_harbor_no_otel_env_when_otlp_absent():
    eng = HarborEngine(runner=FakeDockerRunner())
    argv = eng.build_run_command(_req(otlp=None), "img@sha256:x")
    kv = _env_kv(argv)
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in kv
    assert "OTEL_EXPORTER_OTLP_HEADERS" not in kv
    assert "NO_PROXY" not in kv


def test_no_proxy_is_appended_not_replaced():
    """An operator-supplied NO_PROXY is MERGED with the collector host, injected
    once — the collector host is appended, the operator's value is not lost."""
    eng = HarborEngine(runner=FakeDockerRunner())
    otlp = OtlpConfig(endpoint=_COLLECTOR, log_path="/x/otlp.jsonl")
    argv = eng.build_run_command(
        _req(otlp=otlp, env={"NO_PROXY": "localhost,127.0.0.1"}), "img@sha256:x"
    )
    kv = _env_kv(argv)
    assert kv["NO_PROXY"] == "localhost,127.0.0.1,verdi-trace-collector"
    # injected exactly ONCE (the task-env loop defers NO_PROXY to the OTLP block)
    injections = [
        argv[i + 1] for i, t in enumerate(argv)
        if t == "--env" and argv[i + 1].startswith("NO_PROXY=")
    ]
    assert injections == ["NO_PROXY=localhost,127.0.0.1,verdi-trace-collector"]


def test_no_proxy_contract_collector_bypasses_the_metering_proxy():
    """§8 NO_PROXY contract (unit): a metered trial with a collector configured
    names the collector host in NO_PROXY, so the OTel exporter bypasses HTTP(S)_PROXY
    — and the collector host is NOT on the proxy allowlist, so a post that DID route
    through the proxy would be denied. The 'zero collector-bound lines in the proxy
    log' end state is proven live in the docker e2e (test_e2e_otlp_capture.py)."""
    eng = HarborEngine(runner=FakeDockerRunner())
    otlp = OtlpConfig(endpoint=_COLLECTOR, log_path="/x/otlp.jsonl")
    proxy = ProxyConfig(
        allowlist=["api.anthropic.com"], proxy_url="http://verdi-metering-proxy:3128",
        log_path="/x/p.jsonl",
    )
    kv = _env_kv(eng.build_run_command(_req(otlp=otlp, proxy=proxy), "img@sha256:x"))
    assert "verdi-trace-collector" in kv["NO_PROXY"].split(",")
    assert kv["HTTP_PROXY"] == "http://trial-x@verdi-metering-proxy:3128"  # metered trial
    assert not proxy.is_allowed("verdi-trace-collector")  # would be denied if routed


# --- fake-engine scripted-envelope parity (A10 pattern) ----------------------
def _fake_run(tmp_path, *, otlp_key_present, spans):
    ws = tmp_path / "ws"
    log = tmp_path / "otlp.jsonl"  # outside the workspace (host-side, like the collector)
    otlp = OtlpConfig(endpoint=_COLLECTOR, log_path=str(log))
    fb = {"native_log": {}}
    if otlp_key_present:
        fb["otlp_spans"] = spans
    rec = run_trial(
        Task(id="t", prompt="p", fake_behavior=fb), _arm(), ws,
        RunConfig(engine=FakeEngine(), otlp=otlp),
    )
    return rec, log


def test_fake_writes_scripted_otlp_envelopes(tmp_path):
    rec, log = _fake_run(
        tmp_path, otlp_key_present=True,
        spans=[{"resourceSpans": [{"scopeSpans": [{"spans": [{"name": "op"}]}]}]}],
    )
    (line,) = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines()]
    assert line["trial"] == rec.trial_id
    assert line["seq"] == 0
    assert line["content_type"] == "application/json"
    assert line["body_json"] == {"resourceSpans": [{"scopeSpans": [{"spans": [{"name": "op"}]}]}]}


def test_fake_empty_otlp_spans_leaves_present_empty_log(tmp_path):
    """otlp_spans=[] simulates a LIVE collector that captured nothing — the log
    exists (present, empty) so the ladder reports honest emptiness, not absence."""
    _rec, log = _fake_run(tmp_path, otlp_key_present=True, spans=[])
    assert log.exists()
    assert log.read_text(encoding="utf-8") == ""


def test_fake_no_otlp_spans_writes_no_log(tmp_path):
    """No otlp_spans key simulates a DEAD/absent collector — no log is written, so
    a configured trial fails closed span_log_missing (the proxy_log_missing parity)."""
    _rec, log = _fake_run(tmp_path, otlp_key_present=False, spans=None)
    assert not log.exists()


# --- the run wrap (_managed_collector) --------------------------------------
def test_managed_collector_passthrough_when_not_opted_in(tmp_path):
    otlp = OtlpConfig(endpoint=_COLLECTOR, log_path="/x/otlp.jsonl")
    s = RunSettings(otlp=otlp, otlp_managed=False)
    with _managed_collector(s, "harbor", tmp_path) as o:
        assert o is s.otlp  # untouched, no stand-up


def test_managed_collector_noop_for_fake_engine(tmp_path):
    s = RunSettings(otlp=None, otlp_managed=True)
    with _managed_collector(s, "fake", tmp_path) as o:
        assert o is s.otlp  # managed is a no-op for the hermetic-by-fiat fake


def test_managed_collector_stands_up_and_yields_config(tmp_path, monkeypatch):
    from harness.hermetic import tracing
    from harness.hermetic.tracing import CollectorConfig

    stood: dict = {}

    @contextlib.contextmanager
    def fake_managed(*, log_path=None, keep_raw=False, image=None):
        stood["log_path"] = str(log_path)
        yield CollectorConfig(endpoint=_COLLECTOR, log_path=str(log_path))

    monkeypatch.setattr(tracing.TraceCollector, "managed", staticmethod(fake_managed))
    s = RunSettings(otlp_managed=True)
    with _managed_collector(s, "harbor", tmp_path) as o:
        assert o.endpoint == _COLLECTOR
        assert o.log_path.endswith("otlp/otlp.jsonl")  # defaulted under the exp dir
    assert stood["log_path"].endswith("otlp/otlp.jsonl")
