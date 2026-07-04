"""Google (Gemini) provider client (pinned via the fully-versioned model id)."""

from __future__ import annotations

from .base import Provider, ProviderError
from ._http import post_json, require_key


def _content(resp: dict) -> str:
    """Extract the completion text, failing closed on an error/safety-blocked 200
    [JD-3]. A safety block returns no ``candidates`` (only ``promptFeedback``);
    that and any error body must raise ``ProviderError`` (→ provider_error) rather
    than a bare ``KeyError``/``IndexError`` that escapes with no verdict event.
    """
    if "error" in resp:
        raise ProviderError(f"google error response: {resp['error']}")
    try:
        cand = resp["candidates"][0]
        return "".join(p.get("text", "") for p in cand["content"]["parts"])
    except (KeyError, IndexError, TypeError) as e:
        raise ProviderError(f"unexpected google response shape (safety block?): {resp}") from e


class GoogleProvider(Provider):
    def complete(self, model_id: str, messages: list[dict], temperature: float) -> str:
        model = model_id.split("/", 1)[1]
        contents = [
            {"role": "user" if m["role"] != "assistant" else "model", "parts": [{"text": m["content"]}]}
            for m in messages
        ]
        key = require_key("GOOGLE_API_KEY")
        # JD-10: pass the key in a header, never in the URL query string — a key in
        # the request line leaks through any proxy/access log. Mirrors the
        # x-api-key / Authorization headers of the sibling providers.
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        resp = post_json(
            url,
            {"contents": contents, "generationConfig": {"temperature": temperature}},
            {"x-goog-api-key": key},
        )
        return _content(resp)
