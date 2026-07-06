#!/usr/bin/env python3
"""Official ``anthropic-claude-code`` trial agent — drives the Claude Code CLI [refactor 03 §3].

Reads ``/verdi/request.json``, invokes the pre-installed, version-pinned
``claude`` CLI non-interactively over the task inside the graded ``/workspace``
(the CLI edits files there and reaches ``api.anthropic.com`` through the injected
metering proxy via ``HTTP(S)_PROXY``), then emits ``artifacts/agent_log.json`` in
the verdi **generic** format via :mod:`verdi_agent`. Fail-visible: if the CLI is
absent / unauthenticated / offline (the ``bench images verify`` plumbing case),
:func:`verdi_agent.run_visible` still leaves a scorable log and exits nonzero.

Honesty note [refactor 03 §3]: the real CLI is exercised only WITH keys and
network. ``verify`` proves the plumbing — request in, scorable generic log out —
never intelligence. Native-format emission (``platform: claude_code``) is the
alternative the harness adapter already supports; see README.md. The generic
translation here is deliberately minimal (a whole-trial record), never a fabricated
native session — verdi's honesty rules forbid inventing telemetry the CLI did not
report.
"""

from __future__ import annotations

import os
import subprocess

from verdi_agent import WORKSPACE, AgentLog, read_request, run_visible

# Non-interactive print mode; edits are auto-accepted so a batch trial never
# blocks on a permission prompt. The exact flag spelling is version-coupled —
# confirm it against the pinned CLI version (README.md).
CLI = ["claude", "--print", "--permission-mode", "acceptEdits"]


def main(log: AgentLog) -> None:
    req = read_request()
    # The CLI authenticates from ANTHROPIC_API_KEY (harbor injects it as an
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
    log.message(f"[{req.arm}/{req.model_id}] claude-code exit {proc.returncode}")
    log.test_run(" ".join(CLI), detail=detail, exit_code=proc.returncode)
    # Telemetry stays null: the print-mode CLI does not self-report tokens/cost in
    # a stable machine form, and verdi never guesses [docs/adapters.md, D004]. An
    # operator who wants per-trial telemetry runs the arm as platform: claude_code
    # (native adapter) or parses the CLI's --output-format json.
    log.finish()
    if proc.returncode != 0:
        raise RuntimeError(f"claude-code CLI exited {proc.returncode}: {detail!r}")


if __name__ == "__main__":
    run_visible(main)
