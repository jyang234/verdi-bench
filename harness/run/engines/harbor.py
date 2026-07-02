"""Harbor engine [EVAL-4 §M2] — hermetic, pinned, network-insulated trials.

**This is the only module that may import/talk to Harbor/Docker** [AC-1,
import-linter contract]. Everything else speaks the engine seam.

Hermetic posture [D001, D005]:
* Pinned image ref (digest captured into provenance).
* Pinned CPU/mem quotas [D003].
* No ambient network — egress only through the metering proxy (default-deny
  network + ``HTTP(S)_PROXY`` pointing at the allowlisted proxy). Every other
  attempt is a proxy log line + ``egress_violation`` on the record [AC-3].
* Provider keys injected as env at trial start — never baked into image layers
  or written to the ledger [AC-8].

Actual daemon calls sit behind an injectable ``runner`` so command construction
and result mapping are unit-testable without a live Docker; the true
container-inspect assertions are ``@pytest.mark.docker``.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

from ...adapters.base import Outcome, Quotas
from ..types import EngineResult, TrialRequest

HARBOR_VERSION = "harbor-pinned-0.1.0"  # version-pinned in images [D005]


@dataclass
class RunOutput:
    exit_status: int
    daemon_error: bool = False
    timed_out: bool = False


class CommandRunner(Protocol):
    def run_container(self, cmd: list[str], timeout_s: int) -> RunOutput: ...

    def resolve_digest(self, image: str) -> Optional[str]: ...


class DockerCliRunner:
    """Default runner shelling out to the ``docker`` CLI."""

    def resolve_digest(self, image: str) -> Optional[str]:
        if "@sha256:" in image:
            return image.split("@", 1)[1]
        try:
            out = subprocess.run(
                ["docker", "inspect", "--format", "{{index .RepoDigests 0}}", image],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        digest = out.stdout.strip()
        return digest.split("@", 1)[1] if "@" in digest else None

    def run_container(self, cmd: list[str], timeout_s: int) -> RunOutput:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return RunOutput(exit_status=124, timed_out=True)
        except (OSError, FileNotFoundError):
            return RunOutput(exit_status=125, daemon_error=True)
        # docker returns 125 for daemon/config errors before the container runs
        if proc.returncode == 125:
            return RunOutput(exit_status=125, daemon_error=True)
        return RunOutput(exit_status=proc.returncode)


class HarborEngine:
    name = "harbor"

    def __init__(
        self, runner: Optional[CommandRunner] = None, harbor_version: str = HARBOR_VERSION
    ):
        self._runner = runner or DockerCliRunner()
        self.harbor_version = harbor_version

    def build_run_command(self, request: TrialRequest, image: str) -> list[str]:
        """Pure construction of the ``docker run`` argv — hermetic flags,
        quotas, proxy-only egress, env-injected keys. Unit-tested directly."""
        q: Quotas = request.quotas or Quotas()
        cmd = ["docker", "run", "--rm"]
        # pinned quotas [D003]
        if q.cpus is not None:
            cmd += ["--cpus", str(q.cpus)]
        if q.mem is not None:
            cmd += ["--memory", str(q.mem)]
        # network insulation: default-deny; egress only via the metering proxy
        if request.proxy is not None and request.proxy.proxy_url:
            cmd += ["--env", f"HTTP_PROXY={request.proxy.proxy_url}"]
            cmd += ["--env", f"HTTPS_PROXY={request.proxy.proxy_url}"]
            # a restricted docker network that only reaches the proxy
            cmd += ["--network", "verdi-metered"]
        else:
            cmd += ["--network", "none"]
        # provider keys injected as env — never in image layers or ledger [AC-8]
        for k, v in (request.provider_keys or {}).items():
            cmd += ["--env", f"{k}={v}"]
        # workspace mount
        cmd += ["--volume", f"{Path(request.workspace).resolve()}:/workspace"]
        cmd += ["--workdir", "/workspace"]
        cmd += [image]
        return cmd

    def run(self, request: TrialRequest) -> EngineResult:
        image = request.image
        digest = self._runner.resolve_digest(image)
        artifacts = Path(request.workspace) / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)

        cmd = self.build_run_command(request, image)
        result = self._runner.run_container(cmd, request.timeout_s)

        if result.daemon_error:
            outcome = Outcome.infra_failed
        elif result.timed_out:
            outcome = Outcome.timeout
        else:
            outcome = Outcome.completed if result.exit_status == 0 else Outcome.completed
            # a nonzero agent exit is still a completed *trial* (the agent ran);
            # grading decides pass/fail. infra vs timeout are the only non-completions.

        native_log = self._read_native_log(artifacts)
        egress_attempts, egress_violation = self._scan_proxy_log(request)

        return EngineResult(
            outcome=outcome,
            native_log=native_log,
            artifacts_dir=artifacts,
            exit_status=result.exit_status,
            image_digest=digest,
            agent_binary_version=self._agent_version(request),
            harbor_version=self.harbor_version,
            engine=self.name,
            quotas=request.quotas or Quotas(),
            egress_violation=egress_violation,
            egress_attempts=egress_attempts,
            executed_at=request.ts,
        )

    @staticmethod
    def _read_native_log(artifacts: Path) -> dict:
        log = artifacts / "agent_log.json"
        if log.exists():
            try:
                return json.loads(log.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}
        return {}

    @staticmethod
    def _agent_version(request: TrialRequest) -> Optional[str]:
        return (request.arm.payload or {}).get("agent_binary_version")

    @staticmethod
    def _scan_proxy_log(request: TrialRequest) -> tuple[list[str], bool]:
        if request.proxy is None or not request.proxy.log_path:
            return [], False
        p = Path(request.proxy.log_path)
        if not p.exists():
            return [], False
        attempts: list[str] = []
        violation = False
        for line in p.read_text(encoding="utf-8").splitlines():
            if f"trial={request.trial_id}" in line:
                parts = line.split()
                if len(parts) >= 2:
                    attempts.append(parts[1])
                if line.startswith("DENY"):
                    violation = True
        return attempts, violation
