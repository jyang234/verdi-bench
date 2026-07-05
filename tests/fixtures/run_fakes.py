"""Fakes for exercising the run seam without a live Docker daemon."""

from __future__ import annotations

import json
from pathlib import Path

from harness.run.engines.harbor import RunOutput


def _workspace_from_cmd(cmd: list[str]) -> Path:
    for i, tok in enumerate(cmd):
        if tok == "--volume":
            host = cmd[i + 1].split(":")[0]
            return Path(host)
    raise AssertionError("no --volume in docker command")


class FakeDockerRunner:
    """A CommandRunner that simulates a container writing its native log."""

    def __init__(
        self,
        *,
        native_log: dict | None = None,
        daemon_error: bool = False,
        timed_out: bool = False,
        exit_status: int = 0,
        digest: str = "sha256:" + "a" * 64,
        corrupt_log: bool = False,
    ):
        self.native_log = native_log or {}
        self.daemon_error = daemon_error
        self.timed_out = timed_out
        self.exit_status = exit_status
        self.digest = digest
        self.corrupt_log = corrupt_log
        self.last_cmd: list[str] | None = None
        self.metered_network_ensured = False

    def ensure_metered_network(self) -> None:
        self.metered_network_ensured = True

    def resolve_pinned(self, image: str):
        if "@sha256:" in image:
            return image, image.split("@", 1)[1]
        if self.digest is None:
            return None  # unresolvable image: refused upstream [RN-12]
        # tag path: the fake pins to its content-addressed Id, like docker's
        # local-image fallback — the Id itself is the runnable ref [F-M-I2]
        return self.digest, self.digest

    def run_container(self, cmd: list[str], timeout_s: int, env=None) -> RunOutput:
        self.last_cmd = cmd
        self.last_env = env
        ws = _workspace_from_cmd(cmd)
        artifacts = ws / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        log_text = "{not valid json" if self.corrupt_log else json.dumps(self.native_log)
        (artifacts / "agent_log.json").write_text(log_text, encoding="utf-8")
        if self.daemon_error:
            return RunOutput(exit_status=125, daemon_error=True)
        if self.timed_out:
            return RunOutput(exit_status=124, timed_out=True)
        return RunOutput(exit_status=self.exit_status)
