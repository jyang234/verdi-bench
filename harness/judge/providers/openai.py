"""OpenAI provider client (pinned via the fully-versioned model id)."""

from __future__ import annotations

from .base import Provider
from ._http import post_json, require_key


class OpenAIProvider(Provider):
    def complete(self, model_id: str, messages: list[dict], temperature: float) -> str:
        model = model_id.split("/", 1)[1]
        body = {"model": model, "temperature": temperature, "messages": messages}
        resp = post_json(
            "https://api.openai.com/v1/chat/completions",
            body,
            {"authorization": f"Bearer {require_key('OPENAI_API_KEY')}"},
        )
        return resp["choices"][0]["message"]["content"]  # pragma: no cover - real path
