"""EVAL-4 AC-3 — egress flagged; image digest in provenance."""

from __future__ import annotations

from harness.run.egress import proxy_config
from harness.run.seam import run_trial
from harness.run.types import RunConfig, Task
from harness.schema.experiment import Arm


def _arm():
    return Arm(name="a", platform="claude_code", model="anthropic/claude-3-5-sonnet-20241022")


def test_ac3_egress_flagged(tmp_path):
    proxy = proxy_config(
        ["api.anthropic.com"], proxy_url="http://proxy:3128", log_path=str(tmp_path / "proxy.log")
    )
    # task tries to reach an allowed host and a forbidden one
    task = Task(
        id="t1",
        prompt="p",
        fake_behavior={
            "native_log": {"usage": {"input_tokens": 1, "output_tokens": 1}},
            "egress_attempts": ["api.anthropic.com", "evil.example.com"],
        },
    )
    from harness.run.engines.fake import FakeEngine

    config = RunConfig(engine=FakeEngine(), proxy=proxy)
    rec = run_trial(task, _arm(), tmp_path / "ws", config)
    assert rec.flags.egress_violation is True
    # proxy log recorded the denial (the cross-check signal), keyed to trial
    log = (tmp_path / "proxy.log").read_text()
    assert "DENY evil.example.com" in log
    assert rec.trial_id in log


def test_ac3_no_violation_when_allowlisted(tmp_path):
    proxy = proxy_config(["api.anthropic.com"], proxy_url="http://proxy:3128",
                         log_path=str(tmp_path / "proxy.log"))
    task = Task(id="t1", prompt="p", fake_behavior={"egress_attempts": ["api.anthropic.com"]})
    from harness.run.engines.fake import FakeEngine

    rec = run_trial(task, _arm(), tmp_path / "ws", RunConfig(engine=FakeEngine(), proxy=proxy))
    assert rec.flags.egress_violation is False


def test_ac3_image_digest_provenance(tmp_path):
    task = Task(
        id="t1",
        prompt="p",
        image="verdi-bench/agent@sha256:" + "b" * 64,
        fake_behavior={"native_log": {}},
    )
    from harness.run.engines.fake import FakeEngine

    rec = run_trial(task, _arm(), tmp_path / "ws", RunConfig(engine=FakeEngine()))
    assert rec.provenance.image_digest == "sha256:" + "b" * 64
    assert rec.provenance.harbor_version is not None
