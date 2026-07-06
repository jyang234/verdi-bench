"""Offline image compliance check [refactor 03 §4].

``verify(image_ref)`` runs the image the way the run engine will — hardened,
non-root, ``--network none``, a synthetic read-only ``/verdi/request.json`` OUTSIDE
the workspace, a tight timeout — and asserts the harbor contract holds: the image
ran, wrote ``artifacts/agent_log.json``, that log parses under the DECLARED format
(reusing the real :mod:`harness.adapters.generic` parsers — the same pure-function
check, never a re-implementation), exit semantics are honored, and nothing was
written outside ``/workspace``. It validates **plumbing, not intelligence**: no
provider keys, no network, no LLM calls. Docker mechanics go through
:class:`harness.hermetic.DockerClient`; the shared hardened ``docker run`` recipe
comes from :class:`harness.hermetic.HardenedCommand`.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Optional

from harness.hermetic import DAEMON_ERROR_EXIT, TIMEOUT_EXIT, DockerClient, HardenedCommand

from .spec import ComplianceCheck, ComplianceReport

# The synthetic request `verify` mounts: enough to exercise read_request without a
# real task, and NO keys — a compliant agent that cannot reach a model must still
# fail visibly and leave a scorable log (the whole point of the offline check).
# ``payload.cli_timeout_s`` is a short bound a CLI-driving agent honors, so a stack
# CLI that HANGS offline (rather than failing fast) still times out inside its own
# fail-visible wrapper and writes a scorable log well within the run timeout below.
SYNTHETIC_REQUEST = {
    "schema_version": 1,
    "prompt": "verify: this is a plumbing check, no model call is expected",
    "arm": "verify",
    "model": "none/none",
    "payload": {"cli_timeout_s": 20},
}
REQUEST_MOUNT = "/verdi/request.json"
VERIFY_TIMEOUT_S = 120


def _parse_generic(log: dict) -> None:
    """Push the log through the REAL generic parsers; raise on a declared violation."""
    from harness.adapters.generic import (
        normalize_generic,
        normalize_generic_reasoning,
        normalize_generic_trajectory,
    )

    normalize_generic(log)
    normalize_generic_trajectory(log)
    normalize_generic_reasoning(log)


def _parse_native(log: dict, platform: str) -> None:
    """Push the log through the platform's native adapter; raise on malformed input."""
    from harness.adapters import get_adapter

    adapter = get_adapter(platform)
    adapter.normalize(log)
    adapter.normalize_trajectory(log)


def verify(
    image_ref: str,
    *,
    expected_format: str = "generic",
    platform: Optional[str] = None,
    docker: Optional[DockerClient] = None,
    timeout_s: int = VERIFY_TIMEOUT_S,
) -> ComplianceReport:
    """Run ``image_ref`` under the harbor posture and report compliance [refactor 03 §4]."""
    docker = docker or DockerClient()
    checks: list[ComplianceCheck] = []
    if expected_format == "native" and not platform:
        checks.append(
            ComplianceCheck(
                name="declared_format",
                ok=False,
                detail="expected_format=native requires a platform (the native adapter to parse under)",
            )
        )
        return ComplianceReport(
            image_ref=image_ref, expected_format=expected_format, checks=checks
        )

    with tempfile.TemporaryDirectory(prefix="verdi-verify-") as tmp:
        root = Path(tmp)
        workspace = root / "workspace"
        workspace.mkdir()
        req_dir = root / "verdi"
        req_dir.mkdir()
        req_file = req_dir / "request.json"
        req_bytes = json.dumps(SYNTHETIC_REQUEST).encode("utf-8")
        req_file.write_bytes(req_bytes)

        # The shared hardened recipe, the same shape the engine gives a trial:
        # --pull=never (a local, pre-built image), non-root --user, cap-drop /
        # no-new-privileges / pids-limit, --network none, /workspace rw, the request
        # read-only OUTSIDE the workspace.
        argv = (
            HardenedCommand()
            .rm()
            .pull_never()
            .user()
            .harden(pids_limit=512)
            .network("none")
            .volume(workspace, "/workspace")
            .volume(req_file, REQUEST_MOUNT, ro=True)
            .workdir("/workspace")
            .image(image_ref)
            .build()
        )

        try:
            proc = docker.run(argv, timeout_s=timeout_s)
        except Exception as e:  # DockerClient propagates TimeoutExpired / OSError
            reason = (
                f"timed out after {timeout_s}s"
                if type(e).__name__ == "TimeoutExpired"
                else f"could not run ({type(e).__name__}: {e})"
            )
            checks.append(ComplianceCheck(name="image_ran", ok=False, detail=reason))
            return ComplianceReport(
                image_ref=image_ref, expected_format=expected_format, checks=checks
            )

        exit_status = proc.returncode
        # 1. the container actually ran (docker did not return a daemon/config error).
        checks.append(
            ComplianceCheck(
                name="image_ran",
                ok=exit_status != DAEMON_ERROR_EXIT,
                detail=(
                    f"docker daemon/config error (exit {DAEMON_ERROR_EXIT}); "
                    + (proc.stderr or "").strip()[-400:]
                    if exit_status == DAEMON_ERROR_EXIT
                    else f"exited {exit_status}"
                ),
            )
        )

        # 2. the agent wrote artifacts/agent_log.json under /workspace.
        log_path = workspace / "artifacts" / "agent_log.json"
        wrote_log = log_path.exists()
        checks.append(
            ComplianceCheck(
                name="wrote_agent_log",
                ok=wrote_log,
                detail=(
                    "artifacts/agent_log.json"
                    if wrote_log
                    else "no artifacts/agent_log.json under /workspace — a compliant "
                    "image writes a scorable log even on failure (run_visible)"
                ),
            )
        )

        # 3. the log parses under the DECLARED format (the real harness parsers).
        if wrote_log:
            try:
                data = json.loads(log_path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError(f"agent_log.json is a {type(data).__name__}, not an object")
                if expected_format == "native":
                    _parse_native(data, platform or "")
                else:
                    _parse_generic(data)
                parse_ok, parse_detail = True, f"parses as {expected_format}"
            except Exception as e:  # a declared-but-malformed log is corrupt, not compliant
                parse_ok = False
                parse_detail = f"{type(e).__name__}: {e}"
            checks.append(
                ComplianceCheck(name="log_parses", ok=parse_ok, detail=parse_detail)
            )

        # 4. exit semantics: a nonzero agent exit is still a completed, scorable
        # trial — but the agent must NOT usurp the runner-reserved 124/125 codes.
        reserved = exit_status in (TIMEOUT_EXIT, DAEMON_ERROR_EXIT)
        checks.append(
            ComplianceCheck(
                name="exit_semantics",
                ok=not reserved,
                detail=(
                    f"agent used runner-reserved exit {exit_status} (124=timeout, "
                    f"125=daemon); those are the runner's, not the agent's"
                    if reserved
                    else f"exit {exit_status} is a completed (scorable) trial"
                ),
            )
        )

        # 5. nothing written outside /workspace: the read-only /verdi mount is intact
        # (the agent neither tampered with its task nor had any writable host surface
        # but /workspace — the sole rw mount by construction).
        req_intact = req_file.read_bytes() == req_bytes and [
            p.name for p in req_dir.iterdir()
        ] == ["request.json"]
        checks.append(
            ComplianceCheck(
                name="no_writes_outside_workspace",
                ok=req_intact,
                detail=(
                    "read-only /verdi mount intact; /workspace was the only writable surface"
                    if req_intact
                    else "the read-only /verdi/request.json was modified or a stray file "
                    "appeared beside it — the image wrote outside /workspace"
                ),
            )
        )

    return ComplianceReport(
        image_ref=image_ref, expected_format=expected_format, checks=checks
    )
