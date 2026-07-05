"""EVAL-2 AC-8 — provider clients fail closed on error-shaped bodies and timeouts.

The real provider clients extract content from a 200 response; an error-shaped or
safety-blocked body must map to a provider exception (so the judge client records
CANT_JUDGE with the right reason) rather than raising a bare KeyError/IndexError
that escapes with no event (JD-3), and a connect-phase timeout must classify as a
timeout, not a generic provider error (JD-13).
"""

from __future__ import annotations

import urllib.error

import pytest

from harness.judge.providers._http import _classify_urlerror
from harness.judge.providers.anthropic import _content as anthropic_content
from harness.judge.providers.base import ProviderError, ProviderRefusal, ProviderTimeout
from harness.judge.providers.google import _content as google_content
from harness.judge.providers.openai import _content as openai_content


# --- openai --------------------------------------------------------------
def test_openai_happy_path():
    resp = {"choices": [{"message": {"content": "verdict text"}}]}
    assert openai_content(resp) == "verdict text"


def test_openai_error_shaped_body_raises_provider_error():
    with pytest.raises(ProviderError):
        openai_content({"error": {"message": "rate limited", "type": "rate_limit"}})


def test_openai_empty_choices_raises_provider_error():
    with pytest.raises(ProviderError):
        openai_content({"choices": []})


def test_openai_context_length_exceeded_is_context_overflow():
    """PR-9: OpenAI's context_length_exceeded maps to the distinct
    ProviderContextOverflow (→ context_overflow), not a generic provider_error."""
    from harness.judge.providers.base import (
        ProviderContextOverflow,
        provider_failure_reason,
    )

    with pytest.raises(ProviderContextOverflow) as exc:
        openai_content({"error": {"code": "context_length_exceeded", "message": "too long"}})
    assert provider_failure_reason(exc.value) == "context_overflow"


# --- google --------------------------------------------------------------
def test_google_happy_path():
    resp = {"candidates": [{"content": {"parts": [{"text": "verdict text"}]}}]}
    assert google_content(resp) == "verdict text"


def test_google_safety_block_raises_provider_error():
    # a safety-blocked response has no candidates (only promptFeedback)
    with pytest.raises(ProviderError):
        google_content({"promptFeedback": {"blockReason": "SAFETY"}})


def test_google_error_shaped_body_raises_provider_error():
    with pytest.raises(ProviderError):
        google_content({"error": {"code": 400, "message": "bad request"}})


def test_google_key_travels_in_header_not_url(monkeypatch):
    """JD-10: the Google key must ride an x-goog-api-key header, never the URL
    query string — a key in the request line leaks through any proxy/access log."""
    import harness.judge.providers.google as google_mod

    captured = {}

    def fake_post_json(url, body, headers):
        captured["url"] = url
        captured["headers"] = headers
        return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    monkeypatch.setattr(google_mod, "require_key", lambda name: "SECRET-KEY")
    monkeypatch.setattr(google_mod, "post_json", fake_post_json)

    out = google_mod.GoogleProvider().complete("google/gemini-1.5-pro-002", [{"role": "user", "content": "hi"}], 0.0)
    assert out == "ok"
    assert "SECRET-KEY" not in captured["url"]
    assert "key=" not in captured["url"]
    assert captured["headers"]["x-goog-api-key"] == "SECRET-KEY"


# --- anthropic -----------------------------------------------------------
def test_anthropic_happy_path():
    resp = {"content": [{"type": "text", "text": "verdict text"}]}
    assert anthropic_content(resp) == "verdict text"


def test_anthropic_refusal_raises_refusal():
    with pytest.raises(ProviderRefusal):
        anthropic_content({"stop_reason": "refusal", "content": []})


def test_anthropic_error_shaped_body_raises_provider_error():
    # today anthropic's .get() chain returns "" here and misclassifies as parse
    with pytest.raises(ProviderError):
        anthropic_content({"type": "error", "error": {"message": "overloaded"}})


# --- _http timeout classification (JD-13) --------------------------------
def test_connect_timeout_classifies_as_timeout():
    err = urllib.error.URLError(reason=TimeoutError("timed out"))
    assert isinstance(_classify_urlerror(err), ProviderTimeout)


def test_non_timeout_urlerror_classifies_as_provider_error():
    err = urllib.error.URLError(reason=ConnectionRefusedError("refused"))
    classified = _classify_urlerror(err)
    assert isinstance(classified, ProviderError)
    assert not isinstance(classified, ProviderTimeout)


def test_m_j4_transient_http_faults_retry_bounded(monkeypatch):
    """F-M-J4: a single 429/5xx/timeout previously failed the whole call (and
    the batch) closed. Retryable faults now back off (fixed 2s/4s) and succeed;
    non-retryable client errors still fail immediately with zero sleeps."""
    import io
    import urllib.error
    import urllib.request

    import harness.judge.providers._http as http_mod
    from harness.judge.providers._http import post_json

    sleeps: list[float] = []
    monkeypatch.setattr(http_mod.time, "sleep", sleeps.append)

    calls = {"n": 0}

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def flaky(req, timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError(req.full_url, 429, "rate limited", {}, None)
        return _Resp(b'{"ok": true}')

    monkeypatch.setattr(urllib.request, "urlopen", flaky)
    assert post_json("https://x.test/v1", {}, {}) == {"ok": True}
    assert calls["n"] == 3 and sleeps == [2.0, 4.0]

    # a non-retryable 400 fails immediately, no backoff
    sleeps.clear()
    calls["n"] = 0

    def bad_request(req, timeout):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 400, "bad request", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", bad_request)
    with pytest.raises(ProviderError):
        post_json("https://x.test/v1", {}, {})
    assert calls["n"] == 1 and sleeps == []


def test_m_j4_exhausted_retries_fail_closed(monkeypatch):
    """Persistent 429s exhaust the bounded attempts and fail closed."""
    import urllib.error
    import urllib.request

    import harness.judge.providers._http as http_mod
    from harness.judge.providers._http import RETRY_ATTEMPTS, post_json

    sleeps: list[float] = []
    monkeypatch.setattr(http_mod.time, "sleep", sleeps.append)
    calls = {"n": 0}

    def always_429(req, timeout):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 429, "rate limited", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", always_429)
    with pytest.raises(ProviderError, match="429"):
        post_json("https://x.test/v1", {}, {})
    assert calls["n"] == RETRY_ATTEMPTS


def test_m_j4_uniform_output_cap_across_providers(monkeypatch):
    """F-M-J4: Anthropic hardcoded max_tokens=2048 while OpenAI/Google set no
    cap — a truncated verdict JSON became CANT_JUDGE(parse) on one vendor only.
    All three now send the shared MAX_OUTPUT_TOKENS."""
    import harness.judge.providers.anthropic as a_mod
    import harness.judge.providers.google as g_mod
    import harness.judge.providers.openai as o_mod
    from harness.judge.providers.base import MAX_OUTPUT_TOKENS

    bodies: dict[str, dict] = {}

    def capture(name, reply):
        def _post(url, payload, headers, **kw):
            bodies[name] = payload
            return reply
        return _post

    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    monkeypatch.setattr(a_mod, "post_json",
                        capture("anthropic", {"content": [{"type": "text", "text": "x"}]}))
    monkeypatch.setattr(o_mod, "post_json",
                        capture("openai", {"choices": [{"message": {"content": "x"}}]}))
    monkeypatch.setattr(g_mod, "post_json",
                        capture("google", {"candidates": [{"content": {"parts": [{"text": "x"}]}}]}))

    a_mod.AnthropicProvider().complete("anthropic/m", [{"role": "user", "content": "p"}], 0.0)
    o_mod.OpenAIProvider().complete("openai/m", [{"role": "user", "content": "p"}], 0.0)
    g_mod.GoogleProvider().complete("google/m", [{"role": "user", "content": "p"}], 0.0)

    assert bodies["anthropic"]["max_tokens"] == MAX_OUTPUT_TOKENS
    assert bodies["openai"]["max_tokens"] == MAX_OUTPUT_TOKENS
    assert bodies["google"]["generationConfig"]["maxOutputTokens"] == MAX_OUTPUT_TOKENS


def test_m_j4_parse_is_transient_for_reruns():
    """F-M-J4: a truncated/garbled reply is a property of one provider call,
    not of the packet — a parse CANT_JUDGE is re-attempted on re-run instead of
    permanently excluding the comparison (a missing-data channel)."""
    from harness.judge.schema import TRANSIENT_CANT_JUDGE
    from harness.process.score import TRANSIENT_CANT_SCORE

    assert "parse" in TRANSIENT_CANT_JUDGE
    assert "parse" in TRANSIENT_CANT_SCORE
    assert "identity_leak" not in TRANSIENT_CANT_JUDGE  # blinding stays terminal


def test_m_j3_provider_usage_extracted_and_normalized(monkeypatch):
    """F-M-J3: every provider previously discarded the response's usage block.
    Each now records normalized {input_tokens, output_tokens} on last_usage;
    an unreported usage stays None — honest absence, never zero-imputed."""
    import harness.judge.providers.anthropic as a_mod
    import harness.judge.providers.google as g_mod
    import harness.judge.providers.openai as o_mod
    from harness.judge.providers.base import normalize_usage

    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("GOOGLE_API_KEY", "k")

    monkeypatch.setattr(a_mod, "post_json", lambda *a, **k: {
        "content": [{"type": "text", "text": "x"}],
        "usage": {"input_tokens": 10, "output_tokens": 3},
    })
    p = a_mod.AnthropicProvider()
    p.complete("anthropic/m", [{"role": "user", "content": "p"}], 0.0)
    assert p.last_usage == {"input_tokens": 10, "output_tokens": 3}

    monkeypatch.setattr(o_mod, "post_json", lambda *a, **k: {
        "choices": [{"message": {"content": "x"}}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 2},
    })
    p = o_mod.OpenAIProvider()
    p.complete("openai/m", [{"role": "user", "content": "p"}], 0.0)
    assert p.last_usage == {"input_tokens": 7, "output_tokens": 2}

    monkeypatch.setattr(g_mod, "post_json", lambda *a, **k: {
        "candidates": [{"content": {"parts": [{"text": "x"}]}}],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 1},
    })
    p = g_mod.GoogleProvider()
    p.complete("google/m", [{"role": "user", "content": "p"}], 0.0)
    assert p.last_usage == {"input_tokens": 5, "output_tokens": 1}

    # unreported usage is honest absence
    monkeypatch.setattr(o_mod, "post_json", lambda *a, **k: {
        "choices": [{"message": {"content": "x"}}],
    })
    p = o_mod.OpenAIProvider()
    p.complete("openai/m", [{"role": "user", "content": "p"}], 0.0)
    assert p.last_usage is None
    assert normalize_usage(None, 5) is None and normalize_usage(3, None) is None
