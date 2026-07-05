#!/usr/bin/env python3
"""A real LLM trial agent for the verdi harbor path (stdlib only).

Reads its task + identity from the read-only /verdi/request.json, calls the real
model API (egress tunnels through the injected metering proxy automatically —
urllib honors HTTP(S)_PROXY), writes the solution into the graded /workspace, and
emits artifacts/agent_log.json in the verdi generic log format (platform: generic).
Fails visibly: any error still writes an agent_log so the trial is scorable/absent-honest.
"""
from __future__ import annotations

import base64
import http.client
import json
import os
import pathlib
import re
import sys
import urllib.parse

WS = pathlib.Path("/workspace")
ART = WS / "artifacts"
ART.mkdir(parents=True, exist_ok=True)


def post_json(host, path, headers, body):
    """POST JSON to https://host/path. If HTTP(S)_PROXY is set, CONNECT-tunnel
    through it and send the trial-id credential (userinfo) as Proxy-Authorization
    — the harbor metering proxy requires per-trial auth, and stdlib urllib will
    not add it on a CONNECT, so we do it explicitly."""
    data = json.dumps(body).encode()
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if proxy:
        pu = urllib.parse.urlparse(proxy)
        conn = http.client.HTTPSConnection(pu.hostname, pu.port or 3128, timeout=120)
        tunnel_headers = {}
        if pu.username is not None:
            cred = base64.b64encode(f"{pu.username}:{pu.password or ''}".encode()).decode()
            tunnel_headers["Proxy-Authorization"] = "Basic " + cred
        conn.set_tunnel(host, 443, headers=tunnel_headers)
    else:
        conn = http.client.HTTPSConnection(host, 443, timeout=120)
    conn.request("POST", path, body=data, headers={**headers, "content-type": "application/json"})
    resp = conn.getresponse()
    raw = resp.read()
    if resp.status >= 400:
        raise RuntimeError(f"HTTP {resp.status}: {raw[:200]!r}")
    return json.loads(raw)


def _write_log(trajectory, telemetry):
    (ART / "agent_log.json").write_text(json.dumps({
        "verdi_log_version": 1, "telemetry": telemetry, "trajectory": trajectory,
    }), encoding="utf-8")


def _strip_fences(text):
    t = text.strip()
    m = re.search(r"```(?:python)?\n(.*?)```", t, re.DOTALL)
    return (m.group(1) if m else t).strip()


def main():
    req = json.loads(pathlib.Path("/verdi/request.json").read_text())
    prompt, model, arm = req["prompt"], req["model"], req.get("arm", "?")
    provider, model_id = model.split("/", 1)
    system = ("You are a coding agent. Reply with ONLY the raw contents of a Python "
              "file named solution.py that solves the task. No prose, no markdown fences.")
    user = f"Task: {prompt}"

    try:
        if provider == "anthropic":
            body = {"model": model_id, "max_tokens": 1024, "temperature": 0,
                    "system": system, "messages": [{"role": "user", "content": user}]}
            resp = post_json("api.anthropic.com", "/v1/messages",
                             {"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                              "anthropic-version": "2023-06-01"}, body)
            text = "".join(b.get("text", "") for b in resp["content"] if b.get("type") == "text")
            u = resp.get("usage", {}); intok, outtok = u.get("input_tokens"), u.get("output_tokens")
        elif provider == "openai":
            body = {"model": model_id, "max_tokens": 1024, "temperature": 0,
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": user}]}
            resp = post_json("api.openai.com", "/v1/chat/completions",
                             {"authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"}, body)
            text = resp["choices"][0]["message"]["content"]
            u = resp.get("usage", {}); intok, outtok = u.get("prompt_tokens"), u.get("completion_tokens")
        else:
            raise RuntimeError(f"unknown provider {provider!r}")

        code = _strip_fences(text)
        (WS / "solution.py").write_text(code + "\n", encoding="utf-8")
        _write_log(
            trajectory=[
                {"kind": "message", "detail": f"[{arm}/{model_id}] {user[:160]}"},
                {"kind": "file_edit", "files_touched": ["solution.py"], "detail": code[:400]},
                {"kind": "test_run", "command": "python -c 'import solution'", "detail": "wrote solution.py"},
            ],
            telemetry={"cost": None, "tokens_in": intok, "tokens_out": outtok},
        )
        print(f"agent[{arm}] wrote solution.py ({len(code)} bytes), tokens in/out={intok}/{outtok}")
    except Exception as e:  # fail VISIBLY but still leave a scorable log
        _write_log(trajectory=[{"kind": "message", "detail": f"agent error: {type(e).__name__}: {e}"}],
                   telemetry={"cost": None, "input_tokens": None, "output_tokens": None})
        print(f"agent[{arm}] FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
