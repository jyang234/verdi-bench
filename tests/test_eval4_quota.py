"""EVAL-4 AC-6 — quotas applied; contention caveat under concurrency."""

from __future__ import annotations

import pytest

from harness.adapters.base import Quotas
from harness.run.engines.harbor import HarborEngine
from harness.run.seam import run_trial
from harness.run.types import RunConfig, Task, TrialRequest
from harness.schema.experiment import Arm
from tests.fixtures.run_fakes import FakeDockerRunner


def _arm():
    return Arm(name="A", platform="claude_code", model="anthropic/claude-3-5-sonnet-20241022")


def _request(tmp_path, quotas):
    return TrialRequest(
        trial_id="t1", task_id="task", prompt="p", image="img@sha256:" + "a" * 64,
        arm=_arm(), repetition=0, workspace=tmp_path, quotas=quotas, timeout_s=60, ts="t0",
    )


def test_ac6_quota_applied_in_command(tmp_path):
    """The docker run command carries the pinned CPU/mem quotas [D003]."""
    eng = HarborEngine(runner=FakeDockerRunner())
    cmd = eng.build_run_command(_request(tmp_path, Quotas(cpus=2.0, mem="4g")), "img@sha256:x")
    assert "--cpus" in cmd and cmd[cmd.index("--cpus") + 1] == "2.0"
    assert "--memory" in cmd and cmd[cmd.index("--memory") + 1] == "4g"


def test_ac6_quota_recorded_in_provenance(tmp_path):
    config = RunConfig(
        engine=HarborEngine(runner=FakeDockerRunner()), quotas=Quotas(cpus=1.5, mem="2g")
    )
    rec = run_trial(Task(id="task", prompt="p"), _arm(), tmp_path / "ws", config)
    assert rec.provenance.quotas.cpus == 1.5
    assert rec.provenance.quotas.mem == "2g"


def test_ac6_contention_flag_under_concurrency(tmp_path):
    from harness.run.engines.fake import FakeEngine

    config = RunConfig(engine=FakeEngine(), concurrency=4)
    rec = run_trial(Task(id="task", prompt="p"), _arm(), tmp_path / "ws", config)
    assert rec.flags.contention_caveat is True


def test_ac6_no_contention_flag_serial(tmp_path):
    from harness.run.engines.fake import FakeEngine

    rec = run_trial(Task(id="task", prompt="p"), _arm(), tmp_path / "ws",
                    RunConfig(engine=FakeEngine(), concurrency=1))
    assert rec.flags.contention_caveat is False


def test_ac6_no_ambient_network_without_proxy(tmp_path):
    eng = HarborEngine(runner=FakeDockerRunner())
    cmd = eng.build_run_command(_request(tmp_path, Quotas()), "img@sha256:x")
    # no proxy ⇒ network is fully denied
    assert "--network" in cmd and cmd[cmd.index("--network") + 1] == "none"
