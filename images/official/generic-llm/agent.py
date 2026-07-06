#!/usr/bin/env python3
"""Official ``generic-llm`` trial agent — single-turn chat, platform ``generic`` [refactor 03 §3].

The promotion of the shakedown harbor agent (``scripts/shakedown/assets/harbor/
agent.py``) onto :mod:`verdi_agent`: it reads its task + identity from
``/verdi/request.json``, calls the arm's model ONCE (anthropic / openai / google)
through the injected metering proxy, writes ``solution.py`` into the graded
``/workspace``, and emits ``artifacts/agent_log.json`` in the verdi generic v1
format. Fail-visible: :func:`verdi_agent.run_visible` guarantees a scorable log
even when the model call raises.

The ~35 lines of CONNECT-tunnel + ``Proxy-Authorization`` code the old asset
hand-rolled are gone — ``verdi_agent.post_json`` owns that dance now, so this
file is agent logic only.
"""

from __future__ import annotations

import os
import re

from verdi_agent import WORKSPACE, AgentLog, post_json, read_request, run_visible

SYSTEM = (
    "You are a coding agent. Reply with ONLY the raw contents of a Python file "
    "named solution.py that solves the task. No prose, no markdown fences."
)


def _strip_fences(text: str) -> str:
    """Strip a ```python ... ``` fence if the model wrapped its code in one."""
    t = (text or "").strip()
    m = re.search(r"```(?:python)?\n(.*?)```", t, re.DOTALL)
    return (m.group(1) if m else t).strip()


def _call(provider: str, model_id: str, user: str) -> tuple[str, int | None, int | None]:
    """One chat completion → ``(text, tokens_in, tokens_out)`` for the arm's provider.

    Egress rides ``verdi_agent.post_json`` (the metering proxy + per-trial
    credential). Provider keys come from the allowlisted ``--env`` names harbor
    injects at trial start — never baked into the image.
    """
    if provider == "anthropic":
        resp = post_json(
            "api.anthropic.com",
            "/v1/messages",
            {
                "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                "anthropic-version": "2023-06-01",
            },
            {
                "model": model_id,
                "max_tokens": 1024,
                "temperature": 0,
                "system": SYSTEM,
                "messages": [{"role": "user", "content": user}],
            },
        )
        text = "".join(b.get("text", "") for b in resp["content"] if b.get("type") == "text")
        u = resp.get("usage", {}) or {}
        return text, u.get("input_tokens"), u.get("output_tokens")

    if provider == "openai":
        resp = post_json(
            "api.openai.com",
            "/v1/chat/completions",
            {"authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            {
                "model": model_id,
                "max_tokens": 1024,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": user},
                ],
            },
        )
        text = resp["choices"][0]["message"]["content"]
        u = resp.get("usage", {}) or {}
        return text, u.get("prompt_tokens"), u.get("completion_tokens")

    if provider == "google":
        resp = post_json(
            "generativelanguage.googleapis.com",
            f"/v1beta/models/{model_id}:generateContent",
            {"x-goog-api-key": os.environ["GOOGLE_API_KEY"]},
            {
                "systemInstruction": {"parts": [{"text": SYSTEM}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"maxOutputTokens": 1024, "temperature": 0},
            },
        )
        parts = resp["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts)
        u = resp.get("usageMetadata", {}) or {}
        return text, u.get("promptTokenCount"), u.get("candidatesTokenCount")

    raise RuntimeError(
        f"unknown provider {provider!r}; generic-llm supports anthropic/openai/google"
    )


def main(log: AgentLog) -> None:
    req = read_request()
    user = f"Task: {req.prompt}"
    text, tokens_in, tokens_out = _call(req.provider, req.model_id, user)
    code = _strip_fences(text)
    (WORKSPACE / "solution.py").write_text(code + "\n", encoding="utf-8")
    log.message(f"[{req.arm}/{req.model_id}] {user[:160]}")
    log.file_edit(["solution.py"], detail=code[:400])
    log.test_run("python -c 'import solution'", detail="wrote solution.py")
    log.finish(tokens_in=tokens_in, tokens_out=tokens_out)
    print(f"generic-llm[{req.arm}] wrote solution.py ({len(code)} bytes), "
          f"tokens in/out={tokens_in}/{tokens_out}")


if __name__ == "__main__":
    run_visible(main)
