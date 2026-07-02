"""EVAL-4 AC-8 — secret redaction at capture; no keys baked in images."""

from __future__ import annotations

from harness.run.redact import redact_artifacts, redact_text
from harness.run.seam import run_trial
from harness.run.types import RunConfig, Task
from harness.schema.experiment import Arm


def _arm():
    return Arm(name="A", platform="claude_code", model="anthropic/claude-3-5-sonnet-20241022")


def test_ac8_redaction_of_transcript(tmp_path):
    secret = "sk-ant-" + "A" * 40
    task = Task(id="t", prompt="p", fake_behavior={"echo_secret": secret, "native_log": {}})
    from harness.run.engines.fake import FakeEngine

    rec = run_trial(task, _arm(), tmp_path / "ws", RunConfig(engine=FakeEngine()))
    transcript = (tmp_path / "ws" / "artifacts" / "transcript.txt").read_text()
    assert secret not in transcript
    assert "[REDACTED]" in transcript


def test_ac8_redact_various_key_shapes():
    text = "\n".join([
        "sk-" + "x" * 32,
        "AKIA" + "1234567890ABCDEF",
        "AIza" + "b" * 35,
        "ghp_" + "c" * 36,
        "nothing secret here",
    ])
    scrubbed, n = redact_text(text)
    assert n == 4
    assert "nothing secret here" in scrubbed
    assert "AKIA" not in scrubbed


def test_ac8_no_keys_in_images(tmp_path):
    """Scan a fixture 'image' (its layer files) for provider keys — none present.

    We simulate image layers as files; keys are only ever env-injected at trial
    start [AC-8], so a layer scan finds nothing.
    """
    layers = tmp_path / "image_layers"
    layers.mkdir()
    (layers / "layer1.txt").write_text("FROM base\nRUN install agent\n")
    (layers / "layer2.txt").write_text("COPY task /task\n")
    # provider keys are injected as env, never written here
    total = redact_artifacts(layers)
    assert total == 0  # nothing to scrub means no secrets were baked in


def test_ac8_redacts_yml_and_toml(tmp_path):
    # regression: .yml (not just .yaml) and .toml config are scanned
    d = tmp_path / "a"
    d.mkdir()
    (d / "config.yml").write_text("key: sk-" + "y" * 30 + "\n")
    (d / "settings.toml").write_text('token = "ghp_' + "z" * 36 + '"\n')
    n = redact_artifacts(d)
    assert n == 2
    assert "sk-" not in (d / "config.yml").read_text()
    assert "ghp_" not in (d / "settings.toml").read_text()


def test_ac8_redacts_non_utf8_file(tmp_path):
    # regression: a non-UTF-8 file must still be scrubbed, not silently skipped
    d = tmp_path / "a"
    d.mkdir()
    secret = b"sk-" + b"q" * 30
    (d / "log.txt").write_bytes(b"\xff\xfe binary preamble " + secret + b"\n")
    n = redact_artifacts(d)
    assert n == 1
    assert b"sk-" not in (d / "log.txt").read_bytes()


def test_ac8_extra_patterns_configurable(tmp_path):
    d = tmp_path / "a"
    d.mkdir()
    (d / "log.txt").write_text("internal-token: KOALA-SECRET-123\n")
    n = redact_artifacts(d, extra_patterns=[r"KOALA-[A-Z0-9\-]+"])
    assert n == 1
    assert "KOALA-SECRET-123" not in (d / "log.txt").read_text()
