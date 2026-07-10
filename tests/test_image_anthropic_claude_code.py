"""The official ``anthropic-claude-code`` trial agent drives the CLI in JSON mode [refactor 03 §3].

The agent runs the pinned ``claude`` CLI with ``--output-format json`` and persists
its stdout result object VERBATIM as the native ``artifacts/agent_log.json`` so the
``platform: claude_code`` adapter measures tokens/cost/wall-time from the CLI's OWN
report. These tests import the agent the way the container does (``images/base`` on
``sys.path`` so ``import verdi_agent`` resolves), mock ``subprocess.run`` — the
external boundary — and redirect the SDK's artifact paths into a tmp workspace.

Four boundary cases mirror the SDK terminal's contract:

* exit 0 + valid JSON  → native log persisted byte-exact; the real
  ``ClaudeCodeAdapter`` reads cost/tokens/wall-time back out of it;
* exit != 0 + valid JSON → native log persisted intact AND the failure raised;
* exit 0 + non-JSON    → refuse to fabricate a log (raise), write nothing;
* exit != 0 + non-JSON → the keyless ``bench images verify`` plumbing path keeps a
  scorable GENERIC log with the ``test_run`` step (today's behavior).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_BASE = _ROOT / "images" / "base"
_AGENT = _ROOT / "images" / "official" / "anthropic-claude-code" / "agent.py"

# The shipped invocation. --permission-mode bypassPermissions: print mode can
# answer no permission prompt, and acceptEdits auto-accepted EDITS only — every
# non-edit tool was silently denied (bug #5, 2026-07-08 recon: 234 denials across
# 160 trials). The hermetic trial container is the sandbox that makes bypass safe.
_SHIPPED_CLI = ["claude", "--print", "--permission-mode", "bypassPermissions", "--output-format", "json"]

# A realistic pinned-CLI result object (PROBE-VERIFIED shape): type/subtype/
# is_error/result/total_cost_usd/usage/num_turns/duration_ms/session_id.
_RESULT_JSON = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "Implemented add() and is_palindrome().",
    "total_cost_usd": 0.0423,
    "usage": {
        "input_tokens": 1200,
        "output_tokens": 340,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 512,
    },
    "num_turns": 6,
    "duration_ms": 8300,
    "session_id": "sess-abc123",
}


def _load(module_name: str, path: Path):
    if str(_BASE) not in sys.path:
        sys.path.insert(0, str(_BASE))
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)  # import-safe: main() is guarded
    return mod


agent = _load("_official_cc_agent_main", _AGENT)


class _FakeReq:
    prompt = "solve it"
    arm = "armA"
    model_id = "claude-x"
    payload: dict = {}


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Redirect the SDK's artifact paths at a tmp dir and stub the request read.

    Returns ``(verdi_agent, log_path)``; ``subprocess.run`` is mocked per-test."""
    import verdi_agent as va

    artifacts = tmp_path / "artifacts"
    log_path = artifacts / "agent_log.json"
    monkeypatch.setattr(va, "ARTIFACTS", artifacts)
    monkeypatch.setattr(va, "AGENT_LOG_PATH", log_path)
    monkeypatch.setattr(agent, "read_request", lambda: _FakeReq())
    # Hermetic HOME: session-transcript capture reads $HOME/.claude/projects, and
    # these tests must never see (or copy) the developer's real ~/.claude.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return va, log_path


def _mock_run(monkeypatch, *, stdout: str, returncode: int, stderr: str = ""):
    captured: dict = {}

    def run(argv, **kw):
        captured["argv"] = argv
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(agent.subprocess, "run", run)
    return captured


# --- argv pin ---------------------------------------------------------------
def test_cli_argv_includes_output_format_json():
    assert agent.CLI == _SHIPPED_CLI
    assert agent.CLI[:4] == ["claude", "--print", "--permission-mode", "bypassPermissions"]
    assert agent.CLI[-2:] == ["--output-format", "json"]


# --- model delivery: the arm's declared model reaches the CLI ---------------
def _request(model: str, *, prompt: str = "solve it"):
    """A real :class:`verdi_agent.Request` so ``model`` → ``model_id`` (provider-
    prefix stripping) is exercised end to end, not stubbed."""
    import verdi_agent as va

    return va.Request({"prompt": prompt, "arm": "armA", "model": model, "payload": {}})


def test_argv_carries_arm_model_after_cli_before_prompt(env, monkeypatch):
    """A non-empty model becomes ONE equals-form ``--model=<id>`` token, positioned
    right after the shared CLI tokens and before the trailing prompt (the provider
    prefix stripped by ``req.model_id``)."""
    va, _ = env
    monkeypatch.setattr(agent, "read_request",
                        lambda: _request("anthropic/claude-haiku-4-5-20251001"))
    captured = _mock_run(monkeypatch, stdout=json.dumps(_RESULT_JSON), returncode=0)

    agent.main(va.AgentLog())

    assert captured["argv"] == [*_SHIPPED_CLI, "--model=claude-haiku-4-5-20251001", "solve it"]
    assert captured["argv"].count("--model=claude-haiku-4-5-20251001") == 1


def test_argv_omits_model_flag_when_model_absent(env, monkeypatch):
    """An empty model (the keyless ``bench images verify`` request) omits the flag
    entirely — today's argv is preserved byte-for-byte."""
    va, _ = env
    monkeypatch.setattr(agent, "read_request", lambda: _request(""))
    captured = _mock_run(monkeypatch, stdout=json.dumps(_RESULT_JSON), returncode=0)

    agent.main(va.AgentLog())

    assert captured["argv"] == [*_SHIPPED_CLI, "solve it"]
    assert not any(a.startswith("--model") for a in captured["argv"])


# --- exit 0 + valid JSON → native log, adapter reads it ---------------------
def test_exit0_valid_json_persists_native_and_adapter_reads_it(env, monkeypatch):
    from harness.adapters.claude_code import ClaudeCodeAdapter

    va, log_path = env
    raw = json.dumps(_RESULT_JSON)
    captured = _mock_run(monkeypatch, stdout=raw + "\n", returncode=0)

    agent.main(va.AgentLog())

    content = log_path.read_text(encoding="utf-8")
    assert content == raw  # exactly the (stripped) stdout text, verbatim
    t = ClaudeCodeAdapter().normalize(json.loads(content))
    assert t.cost == 0.0423
    assert (t.tokens_in, t.tokens_out, t.tokens_cache) == (1200, 340, 512)
    assert t.wall_time_s == 8.3
    # the invocation carried the native flag, the arm model (equals form), + the
    # prompt (_FakeReq.model_id == "claude-x").
    assert captured["argv"] == [*_SHIPPED_CLI, "--model=claude-x", "solve it"]


# --- exit != 0 + valid JSON → native log intact AND raise -------------------
def test_exit3_valid_json_persists_native_and_raises(env, monkeypatch):
    va, log_path = env
    err = {**_RESULT_JSON, "is_error": True, "subtype": "error_during_execution",
           "result": "fatal: broke near the end"}
    raw = json.dumps(err)
    _mock_run(monkeypatch, stdout=raw, returncode=3, stderr="stderr tail")

    with pytest.raises(RuntimeError) as ei:
        agent.main(va.AgentLog())
    assert "3" in str(ei.value)
    # the native evidence is persisted intact despite the nonzero exit
    assert log_path.read_text(encoding="utf-8") == raw
    assert json.loads(log_path.read_text(encoding="utf-8"))["is_error"] is True


# --- exit 0 + valid JSON but is_error: true → native log intact AND raise ----
def test_exit0_is_error_true_persists_native_and_raises(env, monkeypatch):
    """The pinned CLI can exit 0 while reporting ``is_error: true`` — an API error
    surfaced IN-BAND (observed live: ConnectionRefused, empty modelUsage, cost 0).
    Such a session ENDED IN ERROR and must not flow to grading as a success: main
    persists the native log verbatim (evidence first) THEN raises, so the engine
    fails the cell closed (trial_infra_failed, RN-15)."""
    va, log_path = env
    err = {**_RESULT_JSON, "is_error": True,
           "result": "API Error: Unable to connect to API (ConnectionRefused)"}
    raw = json.dumps(err)
    _mock_run(monkeypatch, stdout=raw, returncode=0)

    with pytest.raises(RuntimeError, match="is_error"):
        agent.main(va.AgentLog())
    # the native evidence is persisted intact despite the refusal
    assert log_path.read_text(encoding="utf-8") == raw
    assert json.loads(log_path.read_text(encoding="utf-8"))["is_error"] is True

    # guard the happy path: is_error absent (or falsey) must NOT raise. _RESULT_JSON
    # ships is_error: False; a result dict OMITTING the key must also pass cleanly.
    ok = {k: v for k, v in _RESULT_JSON.items() if k != "is_error"}
    _mock_run(monkeypatch, stdout=json.dumps(ok), returncode=0)
    agent.main(va.AgentLog())  # absent is_error → clean success, no raise


# --- exit 0 + non-JSON → refuse to fabricate --------------------------------
def test_exit0_non_json_refuses_to_fabricate(env, monkeypatch):
    va, log_path = env
    _mock_run(monkeypatch, stdout="hello, not json at all", returncode=0)

    with pytest.raises(RuntimeError) as ei:
        agent.main(va.AgentLog())
    assert "json result" in str(ei.value).lower()
    # nothing was written — no fabricated native log
    assert not log_path.exists()


# --- exit != 0 + non-JSON → scorable generic log (today's plumbing path) -----
def test_exit3_non_json_writes_generic_log_with_test_run(env, monkeypatch):
    va, log_path = env
    _mock_run(monkeypatch, stdout="CLI crashed: no auth", returncode=3)

    with pytest.raises(RuntimeError):
        agent.main(va.AgentLog())
    written = json.loads(log_path.read_text(encoding="utf-8"))
    assert written["verdi_log_version"] == 1
    kinds = [s["kind"] for s in written["trajectory"]]
    assert "test_run" in kinds and "message" in kinds
    (test_step,) = [s for s in written["trajectory"] if s["kind"] == "test_run"]
    assert test_step["exit_code"] == 3


# --- session-transcript capture (flight-recorder evidence) -------------------
# The pinned CLI writes its full session transcript as JSONL under
# $HOME/.claude/projects/<slug>/<session>.jsonl; the agent copies every one into
# artifacts/claude-session/ (path preserved relative to projects/) BEFORE any
# parse/raise branch, so every exit path carries the evidence.
_TRANSCRIPTS = {"a/s1.jsonl": '{"type":"user"}\n', "b/s2.jsonl": '{"type":"assistant"}\n'}


def _plant_transcripts(tmp_path: Path) -> None:
    projects = tmp_path / "home" / ".claude" / "projects"
    for rel, text in _TRANSCRIPTS.items():
        p = projects / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def test_session_transcripts_captured_on_success(env, monkeypatch, tmp_path):
    va, _ = env
    _plant_transcripts(tmp_path)
    _mock_run(monkeypatch, stdout=json.dumps(_RESULT_JSON), returncode=0)

    agent.main(va.AgentLog())

    dest = tmp_path / "artifacts" / "claude-session"
    for rel, text in _TRANSCRIPTS.items():
        assert (dest / rel).read_text(encoding="utf-8") == text  # slug dirs preserved


def test_session_transcripts_captured_on_nonjson_failure_path(env, monkeypatch, tmp_path):
    """Capture happens BEFORE the parse/raise branching: the keyless nonzero-exit
    non-JSON fallback path still carries the transcripts."""
    va, _ = env
    _plant_transcripts(tmp_path)
    _mock_run(monkeypatch, stdout="CLI crashed: no auth", returncode=3)

    with pytest.raises(RuntimeError):
        agent.main(va.AgentLog())
    dest = tmp_path / "artifacts" / "claude-session"
    for rel in _TRANSCRIPTS:
        assert (dest / rel).is_file()


def test_session_transcripts_captured_on_inner_timeout(env, monkeypatch, tmp_path):
    """The inner cli_timeout_s raise (subprocess.TimeoutExpired) still captures —
    a timed-out trial is the forensically richest window — and the exception
    propagates unchanged to run_visible."""
    va, _ = env
    _plant_transcripts(tmp_path)

    def run(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(agent.subprocess, "run", run)

    with pytest.raises(subprocess.TimeoutExpired):
        agent.main(va.AgentLog())
    dest = tmp_path / "artifacts" / "claude-session"
    for rel in _TRANSCRIPTS:
        assert (dest / rel).is_file()


def test_no_transcripts_no_claude_session_dir(env, monkeypatch, tmp_path):
    """Supplementary evidence: absent transcripts write nothing and create no dir
    (a visible absence downstream, never a failure)."""
    va, _ = env
    _mock_run(monkeypatch, stdout=json.dumps(_RESULT_JSON), returncode=0)

    agent.main(va.AgentLog())

    assert not (tmp_path / "artifacts" / "claude-session").exists()


def test_transcript_copy_error_warns_but_does_not_kill_trial(env, monkeypatch, tmp_path, capsys):
    """An unreadable transcript is a one-line stderr warning naming the file — the
    trial completes and the other transcripts are still captured."""
    va, log_path = env
    _plant_transcripts(tmp_path)
    real_copy2 = va.shutil.copy2

    def flaky(src, dst, *a, **kw):
        if str(src).endswith("s1.jsonl"):
            raise OSError("pretend unreadable")
        return real_copy2(src, dst, *a, **kw)

    monkeypatch.setattr(va.shutil, "copy2", flaky)
    _mock_run(monkeypatch, stdout=json.dumps(_RESULT_JSON), returncode=0)

    agent.main(va.AgentLog())  # must not raise

    assert log_path.is_file()  # the trial's primary log is intact
    err = capsys.readouterr().err
    assert "s1.jsonl" in err  # the warning names the file
    dest = tmp_path / "artifacts" / "claude-session"
    assert (dest / "b" / "s2.jsonl").is_file()
    assert not (dest / "a" / "s1.jsonl").exists()
