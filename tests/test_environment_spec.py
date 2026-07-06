"""EnvironmentSpec (A3) — declared per-task files / env / extra_hosts [refactor 03 §5].

Additive task fields that reach the engine (files staged into /workspace, env
injected non-secret) and the derived proxy allowlist (extra_hosts). The write-side
carries them flat; a parity test keeps the set identical to the canonical
:class:`harness.images.spec.EnvironmentSpec`.
"""

from __future__ import annotations

import pytest

from harness.images.spec import ENVIRONMENT_FIELDS, EnvironmentSpec
from harness.run.egress import spec_allowlist, task_extra_hosts
from harness.run.engines.fake import FakeEngine
from harness.run.engines.harbor import HarborEngine
from harness.run.environment import EnvironmentStagingError, stage_files
from harness.run.seam import HoldoutLeakError, run_trial
from harness.run.types import RunConfig, Task, TrialRequest
from harness.schema.experiment import Arm
from harness.schema.tasks import TaskSpec, tasks_to_yaml
from tests.fixtures.run_fakes import FakeDockerRunner


def _arm(**kw):
    base = dict(name="control", platform="generic", model="anthropic/claude-sonnet-4-5-20250929")
    base.update(kw)
    return Arm(**base)


# --- field parity + write side ---------------------------------------------
def test_environment_fields_parity_with_taskspec_and_sdk_task():
    from dataclasses import fields as dc_fields

    from harness.sdk.experiment import Task as SdkTask

    assert set(EnvironmentSpec.model_fields) == set(ENVIRONMENT_FIELDS)
    task_fields = set(TaskSpec.model_fields)
    sdk_fields = {f.name for f in dc_fields(SdkTask)}
    for name in ENVIRONMENT_FIELDS:
        assert name in task_fields, f"TaskSpec is missing environment field {name!r}"
        assert name in sdk_fields, f"SDK Task is missing environment field {name!r}"


def test_taskspec_accepts_and_roundtrips_environment(tmp_path):
    spec = TaskSpec(
        id="t1",
        prompt="p",
        files={"fixtures/data.txt": "hello"},
        env={"MODE": "fast"},
        extra_hosts=["pypi.org"],
    )
    text = tasks_to_yaml([spec])
    assert "fixtures/data.txt" in text and "MODE" in text and "pypi.org" in text


def test_task_content_sha_covers_environment_fields():
    from harness.corpus.commit import task_content_sha

    base = {"id": "t1", "prompt": "p"}
    with_files = {**base, "files": {"a.txt": "x"}}
    assert task_content_sha(base) != task_content_sha(with_files)


def test_sdk_task_emits_environment_fields():
    from harness.sdk.experiment import Task as SdkTask

    d = SdkTask(id="t1", files={"a.txt": "x"}, env={"K": "v"}, extra_hosts=("h.example",)).to_spec_dict()
    assert d["files"] == {"a.txt": "x"}
    assert d["env"] == {"K": "v"}
    assert d["extra_hosts"] == ["h.example"]
    # unset environment stays absent (lean file)
    assert "files" not in SdkTask(id="t2").to_spec_dict()


# --- staging (both engines honor files) ------------------------------------
@pytest.mark.parametrize("engine_name", ["fake", "harbor"])
def test_both_engines_stage_declared_files(engine_name, tmp_path):
    config = (
        RunConfig(engine=FakeEngine())
        if engine_name == "fake"
        else RunConfig(engine=HarborEngine(runner=FakeDockerRunner(native_log={})))
    )
    task = Task(
        id="t1",
        prompt="do it",
        image="verdi/x@sha256:" + "a" * 64,
        files={"scaffold/start.py": "print('seed')\n"},
        fake_behavior={"native_log": {}},
    )
    ws = tmp_path / "ws"
    run_trial(task, _arm(), ws, config)
    staged = ws / "scaffold" / "start.py"
    assert staged.exists() and staged.read_text() == "print('seed')\n"


def test_stage_files_refuses_escape(tmp_path):
    with pytest.raises(EnvironmentStagingError):
        stage_files(tmp_path, {"../escape.txt": "nope"})
    with pytest.raises(EnvironmentStagingError):
        stage_files(tmp_path, {"/abs.txt": "nope"})


def test_canary_in_declared_files_is_a_leak(tmp_path):
    task = Task(
        id="t1",
        prompt="clean prompt",
        image="verdi/x@sha256:" + "a" * 64,
        holdout_canaries=["CANARY-XYZ"],
        files={"seed.txt": "contains CANARY-XYZ oops"},
        fake_behavior={"native_log": {}},
    )
    with pytest.raises(HoldoutLeakError):
        run_trial(task, _arm(), tmp_path / "ws", RunConfig(engine=FakeEngine()))


# --- env injection (Harbor, after provider keys, non-overriding) -----------
def _request(**kw):
    from harness.adapters.base import Quotas

    base = dict(
        trial_id="tr-1", task_id="t1", prompt="p", image="verdi/x@sha256:" + "a" * 64,
        arm=_arm(), repetition=0, workspace="/tmp/ws", quotas=Quotas(), timeout_s=60, ts="2026-01-01T00:00:00Z",
    )
    base.update(kw)
    return TrialRequest(**base)


def test_harbor_injects_task_env_after_provider_keys():
    req = _request(env={"MODE": "fast"}, provider_keys={"ANTHROPIC_API_KEY": "sk"})
    argv = HarborEngine().build_run_command(req, "verdi/x@sha256:" + "a" * 64)
    # the provider KEY is injected by name only (value off the argv); the task env
    # is a KEY=VALUE (non-secret).
    assert "MODE=fast" in argv
    assert "ANTHROPIC_API_KEY" in argv and "ANTHROPIC_API_KEY=sk" not in argv


def test_harbor_task_env_never_overrides_provider_key():
    # a task env that collides with a provider-key name must NOT be injected as
    # KEY=VALUE (it would shadow the secret) — the provider key wins.
    req = _request(env={"ANTHROPIC_API_KEY": "PLAINTEXT"}, provider_keys={"ANTHROPIC_API_KEY": "sk"})
    argv = HarborEngine().build_run_command(req, "verdi/x@sha256:" + "a" * 64)
    assert "ANTHROPIC_API_KEY=PLAINTEXT" not in argv


# --- extra_hosts feed the derived allowlist (all arms, symmetry intact) ----
class _FakeArm:
    def __init__(self, model_hosts):
        self.model_hosts = model_hosts


class _FakeSpec:
    def __init__(self, arms, infra_hosts):
        self.arms = arms
        self.infra_hosts = infra_hosts


def test_task_extra_hosts_union_dedupes():
    dicts = [{"extra_hosts": ["a.example", " "]}, {"extra_hosts": ["b.example", "a.example"]}, {}]
    assert task_extra_hosts(dicts) == ["a.example", "b.example"]


def test_extra_hosts_extend_derived_allowlist_for_all_arms():
    spec = _FakeSpec(
        arms=[_FakeArm({"primary": ["api.anthropic.com"]}), _FakeArm({"primary": ["api.openai.com"]})],
        infra_hosts=["pypi.org"],
    )
    allow = spec_allowlist(spec, ["extra.example"])
    assert "extra.example" in allow
    # present for the whole run (both arms share one derived allowlist)
    assert {"api.anthropic.com", "api.openai.com", "pypi.org"} <= set(allow)


def test_extra_hosts_inert_when_spec_declares_no_hosts():
    spec = _FakeSpec(arms=[_FakeArm({}), _FakeArm({})], infra_hosts=[])
    # no spec hosts → runtime-allowlist mode; task hosts must not flip it on
    assert spec_allowlist(spec, ["extra.example"]) == []
