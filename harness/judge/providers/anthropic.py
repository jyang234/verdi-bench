"""Anthropic provider client (pinned via the fully-versioned model id)."""

from __future__ import annotations

from .base import Provider, ProviderRefusal
from ._http import post_json, require_key


class AnthropicProvider(Provider):
    def complete(self, model_id: str, messages: list[dict], temperature: float) -> str:
        model = model_id.split("/", 1)[1]
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        turns = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]
        body = {
            "model": model,
            "max_tokens": 2048,
            "temperature": temperature,
            "system": system,
            "messages": turns,
        }
        resp = post_json(
            "https://api.anthropic.com/v1/messages",
            body,
            {"x-api-key": require_key("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01"},
        )
        if resp.get("stop_reason") == "refusal":  # pragma: no cover - real path
            raise ProviderRefusal("model refused")
        parts = [b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text"]
        return "".join(parts)
