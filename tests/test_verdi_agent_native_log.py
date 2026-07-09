"""``verdi_agent.AgentLog.finish_native`` — the sanctioned native-log terminal [refactor 03 §2].

The in-image SDK normally WRITES the verdi generic log. ``finish_native`` is the
one exception: a ``platform: claude_code``-style arm whose adapter parses the
underlying stack's OWN result JSON persists that JSON VERBATIM as
``artifacts/agent_log.json`` (evidence is read, never reconstructed). These pin:

* the write is byte-verbatim and the parsed dict is returned;
* a native log that is not a JSON object is refused loudly (a non-object would
  corrupt the harness read as ``telemetry_corrupt`` [RN-17]);
* ``finish()`` after ``finish_native()`` is a programming error (one file, one
  format);
* ``run_visible``'s error path leaves the native file UNTOUCHED when the terminal
  was native (the file is the evidence), and is otherwise unchanged.

The SDK is loaded exactly as the container does — ``images/base`` on ``sys.path``
so ``import verdi_agent`` resolves — and its artifact paths are redirected into a
tmp workspace so no test writes to ``/workspace`` [refactor 03 §3].
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parents[1] / "images" / "base"


@pytest.fixture
def va(monkeypatch, tmp_path):
    """``verdi_agent`` with ``ARTIFACTS``/``AGENT_LOG_PATH`` redirected at a tmp dir.

    Returns ``(module, log_path)``. The methods read those module globals at call
    time, so the monkeypatch redirects every write off ``/workspace``."""
    if str(_BASE) not in sys.path:
        sys.path.insert(0, str(_BASE))
    import verdi_agent as mod

    artifacts = tmp_path / "artifacts"
    log_path = artifacts / "agent_log.json"
    monkeypatch.setattr(mod, "ARTIFACTS", artifacts)
    monkeypatch.setattr(mod, "AGENT_LOG_PATH", log_path)
    return mod, log_path


def test_finish_native_writes_verbatim_and_returns_parsed(va):
    mod, log_path = va
    # deliberately irregular whitespace so a re-serialization would NOT round-trip
    raw = '{ "type":"result",\n  "is_error":false,\n"usage":{"input_tokens":10,"output_tokens":5} }'
    parsed = mod.AgentLog().finish_native(raw)
    # byte-verbatim: the exact stdout text, not a re-serialized dict
    assert log_path.read_bytes() == raw.encode("utf-8")
    assert json.loads(log_path.read_text(encoding="utf-8")) == parsed
    assert parsed["usage"]["input_tokens"] == 10 and parsed["is_error"] is False


@pytest.mark.parametrize("bad", ['[]', '42', '"a string"', 'null', 'true', '{not json'])
def test_finish_native_refuses_non_object_loudly(va, bad):
    mod, log_path = va
    with pytest.raises(ValueError):
        mod.AgentLog().finish_native(bad)
    # refusal happens before any write — nothing is persisted
    assert not log_path.exists()


def test_finish_after_finish_native_is_a_programming_error(va):
    mod, log_path = va
    log = mod.AgentLog()
    log.finish_native('{"type":"result"}')
    with pytest.raises(RuntimeError):
        log.finish()
    # the refused finish() did not clobber the native evidence
    assert log_path.read_text(encoding="utf-8") == '{"type":"result"}'


def test_run_visible_native_terminal_keeps_verbatim_on_error(va):
    mod, log_path = va
    raw = '{"type":"result","is_error":true,"subtype":"error_during_execution"}'

    def main(log):
        log.finish_native(raw)
        raise RuntimeError("claude-code CLI exited 3")

    with pytest.raises(SystemExit) as ei:
        mod.run_visible(main)
    assert ei.value.code == 1
    # the native file IS the evidence — no generic rewrite clobbered it; the
    # failure stays visible via exit 1 + the native log's own is_error
    assert log_path.read_text(encoding="utf-8") == raw
    assert json.loads(log_path.read_text(encoding="utf-8"))["is_error"] is True


def test_run_visible_error_without_native_writes_generic_log(va):
    """The non-native error path is unchanged: a scorable generic log + exit 1."""
    mod, log_path = va

    def main(log):
        raise RuntimeError("boom")

    with pytest.raises(SystemExit) as ei:
        mod.run_visible(main)
    assert ei.value.code == 1
    written = json.loads(log_path.read_text(encoding="utf-8"))
    assert written["verdi_log_version"] == 1
    assert any(
        "agent error: RuntimeError: boom" in s.get("detail", "")
        for s in written.get("trajectory", [])
    )
