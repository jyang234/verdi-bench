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
