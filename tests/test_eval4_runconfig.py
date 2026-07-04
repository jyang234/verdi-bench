"""EVAL-4 AC-3/AC-6/AC-8 — operational RunConfig from run.config.yaml + env; image
pinning enforced [RN-13, RN-12, D-9]."""

from __future__ import annotations

import pytest

from harness.run.engines.harbor import HarborEngine
from harness.run.seam import run_trial
from harness.run.types import RunConfig, Task
from harness.schema.experiment import Arm
from tests.fixtures.run_fakes import FakeDockerRunner


def _arm():
    return Arm(name="a", platform="claude_code", model="anthropic/claude-3-5-sonnet-20241022")


# --- RN-12: image pinning enforced ----------------------------------------
def test_ac3_harbor_refuses_unpinned_image(tmp_path):
    """RN-12/D005: a tag-only image whose digest can't be resolved is refused
    (infra_failed, reason 'unpinned_image'), never run unpinned."""
    task = Task(id="t", prompt="p", image="verdi-bench/agent:latest")  # a tag, not a digest
    runner = FakeDockerRunner(native_log={}, digest=None)  # cannot resolve a digest
    rec = run_trial(task, _arm(), tmp_path / "ws", RunConfig(engine=HarborEngine(runner=runner)))
    assert rec.outcome.value == "infra_failed"
    assert getattr(rec.flags, "failure_reason", None) == "unpinned_image"
    assert runner.last_cmd is None  # the container never started


def test_pull_never_in_harbor_command(tmp_path):
    """RN-12: the run command forbids an implicit pull of an unpinned tag."""
    runner = FakeDockerRunner(native_log={})
    run_trial(Task(id="t", prompt="p"), _arm(), tmp_path / "ws",
              RunConfig(engine=HarborEngine(runner=runner)))
    assert "--pull=never" in runner.last_cmd


# --- RN-13/D-9: operational RunConfig from run.config.yaml + env -----------
def _write_config(tmp_path, body: str):
    (tmp_path / "run.config.yaml").write_text(body, encoding="utf-8")


def test_ac3_run_settings_from_config_file(tmp_path):
    """RN-13/D-9: proxy + quotas resolve from run.config.yaml, and the metering
    egress.proxy_config seam (zero callers before) is actually driven."""
    from harness.run.settings import load_run_settings

    _write_config(tmp_path, (
        "proxy:\n"
        "  url: http://proxy:3128\n"
        "  allowlist: [api.anthropic.com]\n"
        "  log_path: /var/log/verdi/proxy.jsonl\n"
        "quotas:\n"
        "  cpus: 3.0\n"
        "  mem: 8g\n"
        "provider_key_names: [ANTHROPIC_API_KEY]\n"
    ))
    s = load_run_settings(tmp_path, env={"ANTHROPIC_API_KEY": "sk-secret"})
    assert s.proxy is not None
    assert s.proxy.allowlist == ["api.anthropic.com"]
    assert s.proxy.proxy_url == "http://proxy:3128"
    assert s.quotas.cpus == 3.0 and s.quotas.mem == "8g"
    assert s.provider_keys == {"ANTHROPIC_API_KEY": "sk-secret"}


def test_null_quota_falls_back_to_default(tmp_path):
    """Review #7: an explicit `cpus: null` falls back to the pinned default, not an
    un-pinned None that would silently break cross-arm comparability [D003]."""
    from harness.run.settings import load_run_settings

    _write_config(tmp_path, "quotas:\n  cpus: null\n  mem: 8g\n")
    s = load_run_settings(tmp_path, env={})
    assert s.quotas.cpus == 2.0 and s.quotas.mem == "8g"


def test_non_dict_quotas_is_a_clear_error(tmp_path):
    """Review #7: a scalar `quotas:` raises a clear ValueError, not an opaque
    AttributeError from `.get` on a str."""
    import pytest

    from harness.run.settings import load_run_settings

    _write_config(tmp_path, "quotas: 4g\n")
    with pytest.raises(ValueError):
        load_run_settings(tmp_path, env={})


def test_absent_config_yields_conservative_defaults(tmp_path):
    """No run.config.yaml ⇒ no proxy (→ --network none), default quotas, no keys —
    the fake path and un-configured runs behave exactly as before."""
    from harness.run.settings import load_run_settings

    s = load_run_settings(tmp_path, env={"ANTHROPIC_API_KEY": "sk-secret"})
    assert s.proxy is None
    assert s.provider_keys == {}
    assert s.quotas.cpus == 2.0 and s.quotas.mem == "4g"


def test_ac8_provider_key_value_from_env_not_file(tmp_path):
    """RN-13/AC-8: the config file names the key; the VALUE comes from the env and
    is never invented, never written to the file."""
    import pytest

    from harness.run.settings import MissingProviderKeyError, load_run_settings

    _write_config(tmp_path, "provider_key_names: [ANTHROPIC_API_KEY]\n")
    # D-P3-1: a key NAMED in run.config.yaml but absent from the env fails the run
    # loudly (an unauthenticated arm biases the A/B); a value is still never invented.
    with pytest.raises(MissingProviderKeyError):
        load_run_settings(tmp_path, env={})
    s = load_run_settings(tmp_path, env={"ANTHROPIC_API_KEY": "sk-live"})
    # the VALUE comes from the env, and the config file named only the key (never
    # the value) — assert against the file content the test itself controls.
    assert s.provider_keys == {"ANTHROPIC_API_KEY": "sk-live"}
    assert "ANTHROPIC_API_KEY" in (tmp_path / "run.config.yaml").read_text()


def test_ac3_harbor_command_carries_proxy_and_key_names(tmp_path):
    """RN-13/AC-8: with a proxy + keys the command routes egress through the
    metering network and passes key NAMES only — values reach docker via the
    child env, never the argv (visible in `ps`)."""
    from harness.run.egress import proxy_config

    runner = FakeDockerRunner(native_log={})
    cfg = RunConfig(
        engine=HarborEngine(runner=runner),
        proxy=proxy_config(["api.anthropic.com"], proxy_url="http://proxy:3128"),
        provider_keys={"ANTHROPIC_API_KEY": "sk-secret"},
    )
    run_trial(Task(id="t", prompt="p"), _arm(), tmp_path / "ws", cfg)
    cmd = runner.last_cmd
    assert "verdi-metered" in cmd  # routed through the metering network
    # HTTP_PROXY points at the metering proxy (its exact per-trial-auth form is
    # asserted in test_eval4_harbor_egress.py).
    assert any(t.startswith("HTTP_PROXY=") and "proxy:3128" in t for t in cmd)
    assert "ANTHROPIC_API_KEY" in cmd  # NAME on the argv
    assert "sk-secret" not in " ".join(cmd)  # VALUE never on the argv
    assert runner.last_env.get("ANTHROPIC_API_KEY") == "sk-secret"  # value via child env


def test_m2_per_arm_keys_isolate_provider_credentials(tmp_path):
    """PRA-M2: with a per-arm key allowlist, an arm's container receives ONLY its
    own provider key — arm A never sees arm B's key (least-privilege insulation)."""
    from harness.run.egress import proxy_config

    arm_a = Arm(name="a", platform="claude_code", model="anthropic/claude-3-5-sonnet-20241022")
    arm_b = Arm(name="b", platform="codex", model="openai/gpt-4o-2024-08-06")
    base = dict(
        engine=HarborEngine(runner=None),  # replaced per call below
        proxy=proxy_config(["api.anthropic.com", "api.openai.com"], proxy_url="http://proxy:3128"),
        provider_keys={"ANTHROPIC_API_KEY": "sk-ant", "OPENAI_API_KEY": "sk-oai"},
        provider_key_names_by_arm={"a": ["ANTHROPIC_API_KEY"], "b": ["OPENAI_API_KEY"]},
    )

    runner_a = FakeDockerRunner(native_log={})
    run_trial(Task(id="t", prompt="p"), arm_a, tmp_path / "wsa",
              RunConfig(**{**base, "engine": HarborEngine(runner=runner_a)}))
    assert "ANTHROPIC_API_KEY" in runner_a.last_cmd
    assert "OPENAI_API_KEY" not in runner_a.last_cmd  # NOT the other arm's key
    assert runner_a.last_env.get("ANTHROPIC_API_KEY") == "sk-ant"
    assert "OPENAI_API_KEY" not in runner_a.last_env

    runner_b = FakeDockerRunner(native_log={})
    run_trial(Task(id="t", prompt="p"), arm_b, tmp_path / "wsb",
              RunConfig(**{**base, "engine": HarborEngine(runner=runner_b)}))
    assert "OPENAI_API_KEY" in runner_b.last_cmd
    assert "ANTHROPIC_API_KEY" not in runner_b.last_cmd


def test_m2_arm_missing_from_allowlist_fails_loud(tmp_path):
    """PRA-M2: when the per-arm allowlist is in use, an arm not listed fails loud
    rather than silently running unauthenticated."""
    from harness.run.settings import MissingProviderKeyError

    runner = FakeDockerRunner(native_log={})
    cfg = RunConfig(
        engine=HarborEngine(runner=runner),
        provider_keys={"ANTHROPIC_API_KEY": "sk-ant"},
        provider_key_names_by_arm={"other": ["ANTHROPIC_API_KEY"]},  # not arm "a"
    )
    with pytest.raises(MissingProviderKeyError):
        run_trial(Task(id="t", prompt="p"), _arm(), tmp_path / "ws", cfg)
