#!/usr/bin/env python3
"""Official ``anthropic-claude-code`` trial agent — drives the Claude Code CLI [refactor 03 §3].

Reads ``/verdi/request.json``, invokes the pre-installed, version-pinned
``claude`` CLI non-interactively over the task inside the graded ``/workspace``
(the CLI edits files there and reaches ``api.anthropic.com`` through the injected
metering proxy via ``HTTP(S)_PROXY``), and persists the CLI's own
``--output-format json`` result object VERBATIM as the native
``artifacts/agent_log.json``. The arm runs as ``platform: claude_code``, so the
harness adapter measures tokens/cost/wall-time from that native report — no verdi
translation, nothing guessed [docs/adapters.md, EVAL-4 AC-2].

The verdi **generic** log remains the FALLBACK for the plumbing-failure path only:
if the CLI is absent / unauthenticated / offline (the ``bench images verify`` case)
it may die before emitting its JSON contract, and :func:`verdi_agent.run_visible`
still leaves a scorable generic log and exits nonzero.

Honesty note [refactor 03 §3]: the real CLI is exercised only WITH keys and
network. ``verify`` proves the plumbing — request in, scorable log out — never
intelligence. The native log is the CLI's OWN report, never a fabricated session; a
CLI that exits 0 without that report gets no log rather than invented telemetry.
"""

from __future__ import annotations

import json
import os
import subprocess

from verdi_agent import (
    WORKSPACE,
    AgentLog,
    capture_claude_session_transcripts,
    read_request,
    run_visible,
)

# Non-interactive print mode; --permission-mode bypassPermissions because print
# mode cannot answer a permission prompt and acceptEdits auto-accepts EDITS only —
# it silently DENIED every non-edit tool (builds, tests, Skill, MCP; the 2026-07-08
# recon measured 234 denials across 160 trials and the treatment arm's tooling
# never executed). Bypass is correct here because the hermetic trial container IS
# the sandbox — cap-drop, internal network, metered-proxy-only egress — the posture
# the CLI's own help recommends the mode for. --output-format json makes the CLI
# emit its native result object (tokens/cost/duration) on stdout, persisted
# verbatim as the native log. The exact flag spelling is version-coupled — confirm
# against the pinned CLI (README.md).
CLI = ["claude", "--print", "--permission-mode", "bypassPermissions", "--output-format", "json"]


def main(log: AgentLog) -> None:
    req = read_request()
    # Deliver the ARM's declared model to the CLI. req.model_id is the id with its
    # provider prefix stripped (``claude-…`` from ``anthropic/claude-…``); pin it
    # with --model so the CLI does not fall back to its built-in default (which
    # silently ran one model for every arm). Equals form is load-bearing: a
    # space-form flag can swallow the trailing positional prompt (the failure the
    # groundwork --mcp-config hit in the 2026-07-07 pilot). An empty model_id (the
    # keyless `bench images verify` request) omits the flag → today's argv preserved.
    model_flag = [f"--model={req.model_id}"] if req.model_id else []
    # The CLI authenticates from ANTHROPIC_API_KEY (harbor injects it as an
    # allowlisted --env) and tunnels egress through HTTP(S)_PROXY automatically.
    try:
        proc = subprocess.run(
            [*CLI, *model_flag, req.prompt],
            cwd=str(WORKSPACE),
            env=os.environ,
            capture_output=True,
            text=True,
            timeout=int(req.payload.get("cli_timeout_s", 1500)),
        )
    finally:
        # Preserve the CLI's session transcripts ($HOME/.claude/projects/**/*.jsonl)
        # BEFORE any parse/raise branch so every exit path below carries them; the
        # finally exists for the inner cli_timeout_s raise (TimeoutExpired) — the
        # forensically richest window — which then propagates unchanged. Two
        # constraints make this safe: artifacts/ is excluded from the judged diff
        # (the groundwork-mcp.jsonl precedent — a transcript can never surface as a
        # judged asymmetry), and the capture is unconditional, identical on every arm.
        capture_claude_session_transcripts()
    raw = (proc.stdout or "").strip()
    try:
        parsed = json.loads(raw)
        is_object = isinstance(parsed, dict)
    except json.JSONDecodeError:
        is_object = False
    if is_object:
        # platform: claude_code — persist the CLI's OWN result object verbatim; the
        # adapter measures tokens/cost/wall-time from it. Per-step tokens and any
        # field the report omits stay null — never guessed [docs/adapters.md, D004].
        log.finish_native(raw)
        if proc.returncode != 0:
            result = parsed.get("result")
            tail = (result if isinstance(result, str) else (proc.stderr or "")).strip()[-400:]
            raise RuntimeError(f"claude-code CLI exited {proc.returncode}: {tail!r}")
        return
    if proc.returncode == 0:
        # Exited 0 without the JSON result contract: refuse to fabricate a log.
        # run_visible leaves the generic error log; the claude_code adapter reads
        # null telemetry from it — the honest state, not invented numbers.
        raise RuntimeError(
            "claude-code CLI exited 0 without its --output-format json result "
            f"contract (stdout head {raw[:200]!r})"
        )
    # Non-JSON nonzero exit — the keyless `bench images verify` plumbing path (the
    # CLI may die before emitting JSON). Keep a scorable GENERIC log, as before.
    detail = (proc.stdout or proc.stderr or "").strip()[:400]
    log.message(f"[{req.arm}/{req.model_id}] claude-code exit {proc.returncode}")
    log.test_run(" ".join(CLI), detail=detail, exit_code=proc.returncode)
    log.finish()
    raise RuntimeError(f"claude-code CLI exited {proc.returncode}: {detail!r}")


if __name__ == "__main__":
    run_visible(main)
