"""Anthropic provider client (pinned via the fully-versioned model id)."""

from __future__ import annotations

from .base import MAX_OUTPUT_TOKENS, Provider, ProviderError, ProviderRefusal
from ._http import post_json, require_key


def _content(resp: dict) -> str:
    """Extract the completion text, failing closed on an error-shaped 200 [JD-3].

    An error body must raise ``ProviderError`` (→ provider_error), not silently
    yield ``""`` that a downstream parser then misclassifies as ``parse``.
    """
    if resp.get("type") == "error" or "error" in resp:
        raise ProviderError(f"anthropic error response: {resp.get('error', resp)}")
    if resp.get("stop_reason") == "refusal":
        raise ProviderRefusal("model refused")
    content = resp.get("content")
    if not isinstance(content, list):
        raise ProviderError(f"unexpected anthropic response shape: {resp}")
    return "".join(b.get("text", "") for b in content if b.get("type") == "text")


class AnthropicProvider(Provider):
    def complete(self, model_id: str, messages: list[dict], temperature: float) -> str:
        model = model_id.split("/", 1)[1]
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        turns = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]
        body = {
            "model": model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "temperature": temperature,
            "system": system,
            "messages": turns,
        }
        resp = post_json(
            "https://api.anthropic.com/v1/messages",
            body,
            {"x-api-key": require_key("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01"},
        )
        return _content(resp)
