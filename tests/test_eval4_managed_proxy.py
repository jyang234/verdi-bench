"""``proxy.managed`` opt-in — run.config wiring, run wrap, SDK + CLI [refactor 04 §1].

Unit-level (no live daemon): the managed metering proxy is monkeypatched, so the
opt-in's resolution, the run_experiment wrap, the fake-engine no-op, the loud
refusal, and the operator verbs are all checkable without docker. The live
stand-up is proven in ``test_e2e_managed_proxy.py``.
"""

from __future__ import annotations

import contextlib

import pytest
from typer.testing import CliRunner

from harness.run.api import _managed_proxy
from harness.run.settings import RunConfigFile, RunSettings, load_run_settings
from harness.run.types import ProxyConfig


# --- run.config.yaml resolution --------------------------------------------
def test_runconfigfile_parses_managed_flag():
    cfg = RunConfigFile.parse({"proxy": {"managed": True, "allowlist": ["api.anthropic.com"]}})
    assert cfg.proxy.managed is True
    # absent ⇒ False (the un-opted-in default)
    assert RunConfigFile.parse({"proxy": {"allowlist": ["h"]}}).proxy.managed is False


def test_load_run_settings_surfaces_managed(tmp_path):
    (tmp_path / "run.config.yaml").write_text(
        "proxy:\n  managed: true\n  allowlist: [api.anthropic.com]\n", encoding="utf-8"
    )
    settings = load_run_settings(tmp_path)
    assert settings.proxy_managed is True
    assert settings.proxy.allowlist == ["api.anthropic.com"]


def test_managed_plus_url_is_refused(tmp_path):
    """proxy.managed provides its own url — an operator-supplied one is
    contradictory and refused loudly, never silently overridden."""
    (tmp_path / "run.config.yaml").write_text(
        "proxy:\n  managed: true\n  url: http://p:3128\n  allowlist: [h]\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="managed"):
        load_run_settings(tmp_path)


# --- the run_experiment wrap (_managed_proxy) ------------------------------
def test_managed_proxy_passthrough_when_not_opted_in(tmp_path):
    s = RunSettings(proxy=ProxyConfig(allowlist=["h"], proxy_url="http://p"), proxy_managed=False)
    with _managed_proxy(s, "harbor", tmp_path) as p:
        assert p is s.proxy  # untouched, no stand-up


def test_managed_proxy_noop_for_fake_engine(tmp_path):
    """The fake engine is hermetic-by-fiat (no docker) — a managed proxy would
    break that, so managed is a no-op for it even when set."""
    s = RunSettings(proxy=ProxyConfig(allowlist=["h"]), proxy_managed=True)
    with _managed_proxy(s, "fake", tmp_path) as p:
        assert p is s.proxy


def test_managed_proxy_stands_up_and_merges(tmp_path, monkeypatch):
    """For a containerizing engine, the managed proxy stands up with the allowlist
    injected, and its url + log_path are merged onto the spec-derived ProxyConfig
    (keeping allowlist + infra_hosts); log_path defaults under the experiment dir."""
    from harness.hermetic import metering

    stood: dict = {}

    @contextlib.contextmanager
    def fake_managed(allow, *, log_path=None, image=metering.PROXY_BASE_IMAGE):
        stood["allow"] = list(allow)
        stood["log_path"] = str(log_path)
        yield ProxyConfig(
            allowlist=list(allow),
            proxy_url="http://verdi-metering-proxy:3128",
            log_path=str(log_path),
        )

    monkeypatch.setattr(metering.MeteringProxy, "managed", staticmethod(fake_managed))
    s = RunSettings(
        proxy=ProxyConfig(allowlist=["api.anthropic.com"], infra_hosts=["pypi.org"]),
        proxy_managed=True,
    )
    with _managed_proxy(s, "harbor", tmp_path) as p:
        assert p.proxy_url == "http://verdi-metering-proxy:3128"
        assert p.allowlist == ["api.anthropic.com"]  # spec-derived allowlist kept
        assert p.infra_hosts == ["pypi.org"]  # infra kept
        assert p.log_path.endswith("metering/verdi.jsonl")  # defaulted under exp dir
    assert stood["allow"] == ["api.anthropic.com"]  # injected into the proxy


def test_managed_proxy_carries_reverse_endpoints(tmp_path, monkeypatch):
    """THE wiring bug: the managed proxy's reverse_endpoints must ride onto the
    spec-derived ProxyConfig through replace() — otherwise a claude trial gets no
    ANTHROPIC_BASE_URL and strands on the metered network with no egress."""
    from harness.hermetic import metering

    @contextlib.contextmanager
    def fake_managed(allow, *, log_path=None, image=metering.PROXY_BASE_IMAGE):
        yield ProxyConfig(
            allowlist=list(allow),
            proxy_url="http://verdi-metering-proxy:3128",
            log_path=str(log_path),
            reverse_endpoints={"api.anthropic.com": "http://10.0.0.9:3129"},
        )

    monkeypatch.setattr(metering.MeteringProxy, "managed", staticmethod(fake_managed))
    s = RunSettings(proxy=ProxyConfig(allowlist=["api.anthropic.com"]), proxy_managed=True)
    with _managed_proxy(s, "harbor", tmp_path) as p:
        assert p.reverse_endpoints == {"api.anthropic.com": "http://10.0.0.9:3129"}
        assert p.allowlist == ["api.anthropic.com"]  # base fields still kept


# --- SDK passthrough --------------------------------------------------------
def test_sdk_run_config_writes_managed(tmp_path):
    """Experiment.run_config(...) writes proxy.managed into run.config.yaml, so the
    north-star harbor variant is authorable; it round-trips through RunConfigFile."""
    from harness.sdk import Experiment, Task

    exp = (
        Experiment("ab", seed=1, cost_ceiling_usd=1.0)
        .arm("control", model="anthropic/claude-haiku-4-5-20251001", platform="claude_code")
        .arm("treatment", model="openai/gpt-4o-2024-08-06", platform="codex")
        .judge("fake/deterministic-2026-01-01")
        .task(Task("t", prompt="do it", fake_behavior={"native_log": {}}))
        .run_config({"proxy": {"managed": True, "allowlist": ["api.anthropic.com"]}})
    )
    ws = exp.write(tmp_path / "ab")
    import yaml

    written = yaml.safe_load((ws.dir / "run.config.yaml").read_text(encoding="utf-8"))
    assert RunConfigFile.parse(written).proxy.managed is True


# --- operator verbs (bench proxy up/down) ----------------------------------
def test_bench_proxy_up_and_down(monkeypatch, tmp_path):
    from harness.cli import app
    from harness.hermetic import cli as hcli
    from harness.hermetic import metering

    monkeypatch.setattr(
        metering.MeteringProxy,
        "start",
        lambda self: ProxyConfig(
            allowlist=self._allow,
            proxy_url="http://verdi-metering-proxy:3128",
            log_path=str(self._logfile),
        ),
    )
    torn: dict = {}
    monkeypatch.setattr(hcli, "teardown_managed", lambda **kw: torn.update(kw))

    r = CliRunner().invoke(
        app, ["proxy", "up", "--allow", "api.anthropic.com", "--log-path", str(tmp_path / "p.jsonl")]
    )
    assert r.exit_code == 0, r.output
    assert "metering proxy up: http://verdi-metering-proxy:3128" in r.output
    assert "api.anthropic.com" in r.output

    r2 = CliRunner().invoke(app, ["proxy", "down"])
    assert r2.exit_code == 0, r2.output
    assert torn.get("name") == "verdi-metering-proxy"


def test_bench_run_aborts_on_managed_proxy_failure(monkeypatch, tmp_path):
    """bench run maps a managed-proxy stand-up failure to a loud exit-2 refusal
    (never a silently-unmetered run)."""
    from harness.hermetic.metering import MeteringProxyError
    from harness.run import cli as run_cli

    def boom(*a, **k):
        raise MeteringProxyError("docker daemon is unavailable")

    monkeypatch.setattr(run_cli, "run_experiment", boom)
    from harness.cli import app

    r = CliRunner().invoke(app, ["run", str(tmp_path), "--engine", "harbor"])
    assert r.exit_code == 2
    assert "RUN ABORTED" in r.output and "managed metering proxy" in r.output


def test_proxy_up_partial_failure_tears_down(monkeypatch):
    """[P3 interim review M4] A crash mid-stand-up on `bench proxy up` must not
    leak networks/containers until a manual `bench proxy down`."""
    from typer.testing import CliRunner

    from harness.cli import app
    from harness.hermetic import cli as hermetic_cli
    from harness.hermetic.metering import MeteringProxyError

    calls = {"start": 0, "stop": 0}

    class _Boom:
        def __init__(self, *a, **kw):
            pass

        def start(self):
            calls["start"] += 1
            raise MeteringProxyError("scripted mid-stand-up crash")

        def stop(self):
            calls["stop"] += 1

    monkeypatch.setattr(hermetic_cli, "MeteringProxy", _Boom)
    result = CliRunner().invoke(app, ["proxy", "up", "--allow", "api.anthropic.com"])
    assert result.exit_code == 1
    assert "did not come up" in (result.output or "") + str(result.exception or "")
    assert calls == {"start": 1, "stop": 1}
