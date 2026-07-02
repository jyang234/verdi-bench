"""Google (Gemini) provider client (pinned via the fully-versioned model id)."""

from __future__ import annotations

from .base import Provider
from ._http import post_json, require_key


class GoogleProvider(Provider):
    def complete(self, model_id: str, messages: list[dict], temperature: float) -> str:
        model = model_id.split("/", 1)[1]
        contents = [
            {"role": "user" if m["role"] != "assistant" else "model", "parts": [{"text": m["content"]}]}
            for m in messages
        ]
        key = require_key("GOOGLE_API_KEY")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        resp = post_json(url, {"contents": contents, "generationConfig": {"temperature": temperature}}, {})
        cand = resp["candidates"][0]  # pragma: no cover - real path
        return "".join(p.get("text", "") for p in cand["content"]["parts"])
