#!/usr/bin/env python3
"""Official ``openai-codex`` trial agent — drives the OpenAI Codex CLI [refactor 03 §3].

Reads ``/verdi/request.json``, invokes the pre-installed, version-pinned ``codex``
CLI non-interactively over the task inside the graded ``/workspace`` (the CLI
edits files there and reaches ``api.openai.com`` through the injected metering
proxy via ``HTTP(S)_PROXY``), then emits ``artifacts/agent_log.json`` in the verdi
**generic** format via :mod:`verdi_agent`. Fail-visible: if the CLI is absent /
unauthenticated / offline (the ``bench images verify`` plumbing case),
:func:`verdi_agent.run_visible` still leaves a scorable log and exits nonzero.

Honesty note [refactor 03 §3]: the real CLI is exercised only WITH keys and
network. ``verify`` proves the plumbing — request in, scorable generic log out —
never intelligence. Native-format emission (``platform: codex``) is the
alternative the harness adapter already supports; see README.md. Telemetry stays
null rather than guessed [docs/adapters.md, D004].
"""

from __future__ import annotations

import os
import subprocess

from verdi_agent import WORKSPACE, AgentLog, read_request, run_visible

# Non-interactive exec mode with sandbox handling delegated to harbor's container
# hardening. The exact flag spelling is version-coupled — confirm it against the
# pinned CLI version (README.md).
CLI = ["codex", "exec", "--skip-git-repo-check"]


def main(log: AgentLog) -> None:
    req = read_request()
    # The CLI authenticates from OPENAI_API_KEY (harbor injects it as an
    # allowlisted --env) and tunnels egress through HTTP(S)_PROXY automatically.
    proc = subprocess.run(
        [*CLI, req.prompt],
        cwd=str(WORKSPACE),
        env=os.environ,
        capture_output=True,
        text=True,
        timeout=int(req.payload.get("cli_timeout_s", 1500)),
    )
    detail = (proc.stdout or proc.stderr or "").strip()[:400]
    log.message(f"[{req.arm}/{req.model_id}] codex exit {proc.returncode}")
    log.test_run(" ".join(CLI), detail=detail, exit_code=proc.returncode)
    log.finish()
    if proc.returncode != 0:
        raise RuntimeError(f"codex CLI exited {proc.returncode}: {detail!r}")


if __name__ == "__main__":
    run_visible(main)
