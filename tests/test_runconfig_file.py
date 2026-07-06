"""``RunConfigFile`` — one typed parse of run.config.yaml [refactor 04 §4].

Replaces the isinstance ladder and the CLI's second raw read. These tests pin the
block mapping checks (exact legacy messages), the null-quota / null-names
leniency, and the reuse_control bundle surfacing the CLI now consumes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.run.settings import (
    RunConfigFile,
    RunSettings,
    load_run_settings,
)
from harness.run.types import DEFAULT_QUOTAS, RunConfig


def test_parse_full_config_types_every_block():
    cfg = RunConfigFile.parse({
        "proxy": {"url": "http://proxy:3128", "allowlist": ["api.anthropic.com"],
                  "log_path": "/var/log/verdi/proxy.jsonl"},
        "quotas": {"cpus": 3.0, "mem": "8g"},
        "provider_key_names": ["ANTHROPIC_API_KEY"],
        "provider_key_names_by_arm": {"control": ["ANTHROPIC_API_KEY"]},
        "reuse_control": {"bundle": "control.bundle"},
    })
    assert cfg.proxy.url == "http://proxy:3128"
    assert cfg.proxy.allowlist == ["api.anthropic.com"]
    assert cfg.quotas.cpus == 3.0 and cfg.quotas.mem == "8g"
    assert cfg.provider_key_names == ["ANTHROPIC_API_KEY"]
    assert cfg.provider_key_names_by_arm == {"control": ["ANTHROPIC_API_KEY"]}
    assert cfg.reuse_control.bundle == "control.bundle"


def test_empty_and_none_parse_to_defaults():
    for data in (None, {}):
        cfg = RunConfigFile.parse(data)
        assert cfg.proxy is None and cfg.quotas is None
        assert cfg.provider_key_names == []
        assert cfg.provider_key_names_by_arm is None
        assert cfg.reuse_control is None


def test_proxy_non_mapping_keeps_exact_message():
    with pytest.raises(ValueError) as exc:
        RunConfigFile.parse({"proxy": "notamap"})
    assert str(exc.value) == "run.config.yaml 'proxy' must be a mapping, got str"


def test_quotas_non_mapping_keeps_exact_message():
    with pytest.raises(ValueError) as exc:
        RunConfigFile.parse({"quotas": "4g"})
    assert str(exc.value) == "run.config.yaml 'quotas' must be a mapping, got str"


def test_by_arm_non_mapping_keeps_exact_message():
    with pytest.raises(ValueError) as exc:
        RunConfigFile.parse({"provider_key_names_by_arm": ["oops"]})
    assert str(exc.value) == (
        "run.config.yaml 'provider_key_names_by_arm' must be a mapping "
        "{arm_name: [key_names]}"
    )


def test_null_leniency_matches_legacy():
    cfg = RunConfigFile.parse({
        "provider_key_names": None,
        "provider_key_names_by_arm": {"a": None},
        "reuse_control": "not-a-mapping",  # silently ignored, as the CLI did
    })
    assert cfg.provider_key_names == []
    assert cfg.provider_key_names_by_arm == {"a": []}
    assert cfg.reuse_control is None


def test_unknown_top_level_key_is_ignored():
    # operational file leniency preserved (no extra='forbid' on the run config)
    cfg = RunConfigFile.parse({"quotas": {"cpus": 1}, "future_knob": True})
    assert cfg.quotas.cpus == 1.0


# --- reuse_control surfacing (CLI's second raw read is gone) ----------------
def _write(tmp_path, body):
    (tmp_path / "run.config.yaml").write_text(body, encoding="utf-8")


def test_reuse_control_relative_bundle_resolved_against_experiment_dir(tmp_path):
    _write(tmp_path, "reuse_control:\n  bundle: ctl/control.bundle\n")
    s = load_run_settings(tmp_path, env={})
    assert s.reuse_control_bundle == tmp_path / "ctl" / "control.bundle"


def test_reuse_control_absolute_bundle_kept(tmp_path):
    abs_bundle = (tmp_path / "elsewhere.bundle").resolve()
    _write(tmp_path, f"reuse_control:\n  bundle: {abs_bundle}\n")
    s = load_run_settings(tmp_path, env={})
    assert s.reuse_control_bundle == abs_bundle


def test_no_reuse_control_is_none(tmp_path):
    _write(tmp_path, "quotas:\n  cpus: 2.0\n")
    assert load_run_settings(tmp_path, env={}).reuse_control_bundle is None
    # absent file → default RunSettings, still None
    assert load_run_settings(tmp_path / "empty", env={}).reuse_control_bundle is None


# --- DEFAULT_QUOTAS single source -------------------------------------------
def test_default_quotas_is_the_single_source():
    assert (DEFAULT_QUOTAS.cpus, DEFAULT_QUOTAS.mem) == (2.0, "4g")
    # both dataclasses default to it, as fresh (independent) instances
    assert RunConfig(engine=None).quotas.cpus == 2.0
    assert RunSettings().quotas.mem == "4g"
    a, b = RunConfig(engine=None).quotas, RunSettings().quotas
    assert a == DEFAULT_QUOTAS and b == DEFAULT_QUOTAS
    assert a is not DEFAULT_QUOTAS and b is not DEFAULT_QUOTAS  # copies, not shared


# --- otlp block (in-trial OTLP trace capture) [refactor 09 §4, A11] ----------
def test_otlp_managed_flag_parses():
    cfg = RunConfigFile.parse({"otlp": {"managed": True}})
    assert cfg.otlp.managed is True
    # absent ⇒ the un-opted-in default
    assert RunConfigFile.parse({}).otlp is None
    assert RunConfigFile.parse({"otlp": {"endpoint": "http://c:4318"}}).otlp.managed is False


def test_otlp_explicit_endpoint_form_parses():
    cfg = RunConfigFile.parse(
        {"otlp": {"endpoint": "http://c:4318", "log_path": "/var/log/verdi/otlp.jsonl"}}
    )
    assert cfg.otlp.endpoint == "http://c:4318"
    assert cfg.otlp.log_path == "/var/log/verdi/otlp.jsonl"


def test_otlp_non_mapping_keeps_exact_message():
    with pytest.raises(ValueError) as exc:
        RunConfigFile.parse({"otlp": "nope"})
    assert str(exc.value) == "run.config.yaml 'otlp' must be a mapping, got str"


def test_load_run_settings_surfaces_managed_otlp(tmp_path):
    (tmp_path / "run.config.yaml").write_text("otlp:\n  managed: true\n", encoding="utf-8")
    s = load_run_settings(tmp_path, env={})
    assert s.otlp_managed is True
    assert s.otlp is None  # the managed lifecycle supplies endpoint + log_path


def test_load_run_settings_surfaces_explicit_otlp(tmp_path):
    (tmp_path / "run.config.yaml").write_text(
        "otlp:\n  endpoint: http://c:4318\n  log_path: /var/log/verdi/otlp.jsonl\n",
        encoding="utf-8",
    )
    s = load_run_settings(tmp_path, env={})
    assert s.otlp_managed is False
    assert s.otlp.endpoint == "http://c:4318"
    assert s.otlp.log_path == "/var/log/verdi/otlp.jsonl"


def test_otlp_managed_plus_endpoint_is_refused(tmp_path):
    """otlp.managed provides its own endpoint — an operator-supplied one is
    contradictory and refused loudly (the proxy.managed + proxy.url precedent)."""
    (tmp_path / "run.config.yaml").write_text(
        "otlp:\n  managed: true\n  endpoint: http://c:4318\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="managed collector provides its own endpoint"):
        load_run_settings(tmp_path, env={})


def test_otlp_explicit_without_endpoint_is_refused(tmp_path):
    (tmp_path / "run.config.yaml").write_text(
        "otlp:\n  log_path: /var/log/verdi/otlp.jsonl\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="sets no endpoint and is not managed"):
        load_run_settings(tmp_path, env={})


def test_non_list_provider_key_names_fail_loudly():
    """A bare string must refuse, never char-split into ['F','O','O'] the way the
    old ``list(x or [])`` ladder silently did [P1 review F3]."""
    with pytest.raises(Exception) as exc:
        RunConfigFile.parse({"provider_key_names": "OPENAI_API_KEY"})
    assert "provider_key_names" in str(exc.value)

    with pytest.raises(Exception) as exc:
        RunConfigFile.parse({"provider_key_names_by_arm": {"control": "OPENAI_API_KEY"}})
    assert "provider_key_names_by_arm" in str(exc.value) or "control" in str(exc.value)
