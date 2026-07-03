"""EVAL-4 AC-8 — secret redaction at capture; no keys baked in images."""

from __future__ import annotations

from pathlib import Path

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


def test_ac8_denylist_scans_unlisted_suffixes(tmp_path):
    """RN-6: files outside the old allowlist (.bak, .env.local) must still scrub —
    scan everything except known binaries, not an allowlist that fails open."""
    d = tmp_path / "a"
    d.mkdir()
    (d / "backup.bak").write_text("token: ghp_" + "a" * 36 + "\n")
    (d / ".env.local").write_text("KEY=sk-" + "b" * 32 + "\n")
    # a known-binary suffix is still skipped (over-scanning binaries is pointless)
    (d / "logo.png").write_text("sk-" + "c" * 32 + "\n")
    n = redact_artifacts(d)
    assert n == 2
    assert "ghp_" not in (d / "backup.bak").read_text()
    assert "sk-" not in (d / ".env.local").read_text()
    assert "sk-" in (d / "logo.png").read_text()  # binary untouched


def test_ac8_full_pem_body_redacted():
    """RN-8: the whole PEM block scrubs, not just the BEGIN header — the key body
    (the part a downstream scanner without the marker would miss) must be gone."""
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEAsecretbodyline1\n"
        "abcDEF123+/keymaterialxyz==\n"
        "-----END RSA PRIVATE KEY-----"
    )
    scrubbed, n = redact_text(f"leaked:\n{pem}\ntail")
    assert n >= 1
    assert "MIIEowIBAAK" not in scrubbed  # body gone
    assert "keymaterialxyz" not in scrubbed
    assert "PRIVATE KEY" not in scrubbed  # markers gone too
    assert "leaked:" in scrubbed and "tail" in scrubbed


def test_ac8_truncated_pem_header_still_scrubbed():
    """Review #8: a TRUNCATED private key (BEGIN with no matching END) still has
    its marker scrubbed — the full-block pattern alone would leave it verbatim."""
    text = "-----BEGIN OPENSSH PRIVATE KEY-----\nMIIEowIBAAKtruncated-no-end-marker"
    scrubbed, n = redact_text(text)
    assert n >= 1
    assert "PRIVATE KEY-----" not in scrubbed  # header marker gone (fallback)


def test_ac8_redacts_whole_workspace_not_just_artifacts(tmp_path):
    """RN-7: the agent writes a secret into a workspace file OUTSIDE artifacts/;
    redaction must cover the whole workspace (harbor mounts it rw, grade reads it)."""
    from harness.run.engines.fake import FakeEngine

    secret = "sk-" + "w" * 32
    task = Task(id="t", prompt="p", fake_behavior={
        "native_log": {},
        "workspace_files": {"solution.py": f"API_KEY = '{secret}'\n"},
    })
    run_trial(task, _arm(), tmp_path / "ws", RunConfig(engine=FakeEngine()))
    sol = (tmp_path / "ws" / "solution.py").read_text()
    assert secret not in sol
    assert "[REDACTED]" in sol


def test_ac8_injected_key_value_redacted_as_literal(tmp_path):
    """RN-9: an injected provider key whose SHAPE isn't a known pattern still
    scrubs, because its literal value is added from config.provider_keys."""
    from harness.run.engines.fake import FakeEngine

    odd = "internal-corp-token-2f9c"  # matched by no _SECRET_PATTERNS shape
    task = Task(id="t", prompt="p", fake_behavior={
        "native_log": {},
        "transcript_extra": [f"export CORP_TOKEN={odd}"],
    })
    cfg = RunConfig(engine=FakeEngine(), provider_keys={"CORP_TOKEN": odd})
    run_trial(task, _arm(), tmp_path / "ws", cfg)
    transcript = (tmp_path / "ws" / "artifacts" / "transcript.txt").read_text()
    assert odd not in transcript


def test_ac8_unreadable_file_fails_loud(tmp_path, monkeypatch):
    """RN-16: an unreadable file at the sole write barrier is a loud failure, not
    a silent skip that would let an un-scanned artifact through."""
    import pytest

    from harness.run.redact import RedactionError

    d = tmp_path / "a"
    d.mkdir()
    (d / "secrets.log").write_text("data", encoding="utf-8")
    orig = Path.read_bytes

    def boom(self):
        if self.name == "secrets.log":
            raise OSError("simulated unreadable")
        return orig(self)

    monkeypatch.setattr(Path, "read_bytes", boom)
    with pytest.raises(RedactionError):
        redact_artifacts(d)
