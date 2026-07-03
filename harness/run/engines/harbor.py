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
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

from ...adapters.base import Outcome, Quotas
from ..types import EngineResult, TrialRequest

HARBOR_VERSION = "harbor-pinned-0.1.0"  # version-pinned in images [D005]

# The trial-image contract [RN-4, EVAL-4-D-8]: the harness writes the task prompt
# and arm configuration to a host file and bind-mounts it READ-ONLY at this path,
# OUTSIDE /workspace (so it never pollutes the graded workspace copy). A pre-baked
# trial image's entrypoint reads it to learn its task and which arm it is.
TRIAL_REQUEST_MOUNT = "/verdi/request.json"

# The restricted docker network a proxied trial joins — it reaches only the
# metering proxy, nothing else [RN-11, D001].
METERED_NETWORK = "verdi-metered"


def _container_name(trial_id: str) -> str:
    """Deterministic container name for a trial, so a timed-out container is
    killable by name [RN-10]."""
    return f"verdi-{trial_id}"


def _with_trial_auth(proxy_url: str, trial_id: str) -> str:
    """Insert the trial id as the proxy-auth username so the metering proxy
    attributes egress to this trial [RN-11, D-10]. A URL that already carries
    userinfo is left as-is."""
    if "://" not in proxy_url:
        return proxy_url
    scheme, rest = proxy_url.split("://", 1)
    if "@" in rest:
        return proxy_url
    return f"{scheme}://{trial_id}@{rest}"


@dataclass
class RunOutput:
    exit_status: int
    daemon_error: bool = False
    timed_out: bool = False


def _name_from_cmd(cmd: list[str]) -> Optional[str]:
    if "--name" in cmd:
        i = cmd.index("--name")
        if i + 1 < len(cmd):
            return cmd[i + 1]
    return None


class CommandRunner(Protocol):
    def run_container(
        self, cmd: list[str], timeout_s: int, env: Optional[dict] = None
    ) -> RunOutput: ...

    def resolve_digest(self, image: str) -> Optional[str]: ...

    def ensure_metered_network(self) -> None: ...


class DockerCliRunner:
    """Default runner shelling out to the ``docker`` CLI."""

    def ensure_metered_network(self) -> None:
        """Create the restricted metering network if it's absent [RN-11].

        ``--internal`` gives the trial no direct external connectivity — only the
        proxy (attached to this network) can reach model APIs. Best-effort: if
        docker is unreachable the trial itself fails closed as a daemon_error, so
        this does not mask that."""
        try:
            inspect = subprocess.run(
                ["docker", "network", "inspect", METERED_NETWORK],
                capture_output=True, timeout=30, check=False,
            )
            if inspect.returncode == 0:
                return
            subprocess.run(
                ["docker", "network", "create", "--internal", METERED_NETWORK],
                capture_output=True, timeout=30, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return

    @staticmethod
    def _kill(name: Optional[str]) -> None:
        """Kill and reap a container by name [RN-10]. Best-effort."""
        if not name:
            return
        for args in (["docker", "kill", name], ["docker", "wait", name]):
            try:
                subprocess.run(args, capture_output=True, timeout=30, check=False)
            except (OSError, subprocess.SubprocessError):
                pass

    def resolve_digest(self, image: str) -> Optional[str]:
        if "@sha256:" in image:
            return image.split("@", 1)[1]
        # a registry image carries a RepoDigest (the manifest digest)
        repo = self._inspect_format(image, "{{index .RepoDigests 0}}")
        if repo and "@" in repo:
            return repo.split("@", 1)[1]
        # a local/CI image (built, never pushed) has no RepoDigest — pin instead to
        # its content-addressed image Id, which still identifies the exact image in
        # provenance and satisfies D005 [RN-12]. An absent image resolves to None
        # and is refused.
        idv = self._inspect_format(image, "{{.Id}}")
        return idv if idv and idv.startswith("sha256:") else None

    @staticmethod
    def _inspect_format(image: str, fmt: str) -> Optional[str]:
        try:
            out = subprocess.run(
                ["docker", "inspect", "--format", fmt, image],
                capture_output=True, text=True, timeout=30, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    def run_container(
        self, cmd: list[str], timeout_s: int, env: Optional[dict] = None
    ) -> RunOutput:
        # Provider key VALUES are passed through the child environment (never on
        # the argv), so `docker run --env KEY` picks them up without exposing
        # them in the host process table.
        child_env = {**os.environ, **(env or {})}
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout_s, env=child_env
            )
        except subprocess.TimeoutExpired:
            # Kill the CONTAINER, not just the docker CLI: the CLI dying on timeout
            # leaves the container running and writing into the still-mounted
            # workspace AFTER redaction. Kill by name and reap it before returning,
            # so redaction sees a final, static workspace [RN-10].
            self._kill(_name_from_cmd(cmd))
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

    def build_run_command(
        self, request: TrialRequest, image: str, request_file: Optional[Path] = None
    ) -> list[str]:
        """Pure construction of the ``docker run`` argv — hermetic flags,
        quotas, proxy-only egress, env-injected keys, and the read-only trial
        request mount [RN-4]. Unit-tested directly."""
        q: Quotas = request.quotas or Quotas()
        # --pull=never: a trial must run the pre-baked, digest-pinned image; never
        # silently pull an unpinned tag at trial time [RN-12, D005].
        cmd = ["docker", "run", "--rm", "--pull=never"]
        # a deterministic name so a timed-out container is killable by name [RN-10]
        cmd += ["--name", _container_name(request.trial_id)]
        # pinned quotas [D003]
        if q.cpus is not None:
            cmd += ["--cpus", str(q.cpus)]
        if q.mem is not None:
            cmd += ["--memory", str(q.mem)]
        # network insulation: default-deny; egress only via the metering proxy
        if request.proxy is not None and request.proxy.proxy_url:
            # inject the trial id as the proxy-auth credential so the metering
            # proxy attributes every request to this trial [RN-11, D-10].
            proxy_url = _with_trial_auth(request.proxy.proxy_url, request.trial_id)
            cmd += ["--env", f"HTTP_PROXY={proxy_url}"]
            cmd += ["--env", f"HTTPS_PROXY={proxy_url}"]
            # a restricted docker network that only reaches the proxy [RN-11]
            cmd += ["--network", METERED_NETWORK]
        else:
            cmd += ["--network", "none"]
        # provider keys injected as env — never in image layers or ledger [AC-8],
        # and never as `KEY=VALUE` on the argv (visible in `ps`/proc). Pass only
        # the NAME on the command; docker reads the value from the CLI process
        # environment, which run_container populates from request.provider_keys.
        for k in (request.provider_keys or {}):
            cmd += ["--env", k]
        # workspace mount
        cmd += ["--volume", f"{Path(request.workspace).resolve()}:/workspace"]
        # trial request (prompt + arm config) delivered READ-ONLY, outside the
        # workspace so it never enters the graded copy [RN-4, D-8]. Added after the
        # workspace volume so a workspace-first parser still finds /workspace.
        if request_file is not None:
            cmd += ["--volume", f"{Path(request_file).resolve()}:{TRIAL_REQUEST_MOUNT}:ro"]
        cmd += ["--workdir", "/workspace"]
        cmd += [image]
        return cmd

    def run(self, request: TrialRequest) -> EngineResult:
        image = request.image
        digest = self._runner.resolve_digest(image)
        artifacts = Path(request.workspace) / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)

        if digest is None:
            # D005/RN-12: a trial must run a digest-pinned image. A tag-only or
            # otherwise unresolvable image fails the trial closed (infra_failed,
            # a real reason) rather than silently running an unpinned tag.
            return EngineResult(
                outcome=Outcome.infra_failed,
                native_log={},
                artifacts_dir=artifacts,
                image_digest=None,
                engine=self.name,
                quotas=request.quotas or Quotas(),
                executed_at=request.ts,
                failure_reason="unpinned_image",
            )

        if request.proxy is not None:
            # ensure the restricted metering network exists before a trial joins
            # it — it was referenced by --network but never created [RN-11].
            self._runner.ensure_metered_network()

        # Write the trial request to a host temp file (outside the workspace) and
        # bind-mount it read-only; clean it up once the container has exited [RN-4].
        req_dir = Path(tempfile.mkdtemp(prefix="verdi-req-"))
        try:
            request_file = req_dir / "request.json"
            request_file.write_text(
                json.dumps(self._trial_request_payload(request)), encoding="utf-8"
            )
            cmd = self.build_run_command(request, image, request_file)
            result = self._runner.run_container(
                cmd, request.timeout_s, env=request.provider_keys or {}
            )
        finally:
            shutil.rmtree(req_dir, ignore_errors=True)

        failure_reason: Optional[str] = None
        if result.daemon_error:
            outcome = Outcome.infra_failed
            # the docker daemon/config error (exit 125) — a real reason the
            # scheduler can ledger, not the fake-only placeholder [RN-14]
            failure_reason = "daemon_error"
        elif result.timed_out:
            outcome = Outcome.timeout
        else:
            # a nonzero agent exit is still a completed *trial* (the agent ran);
            # grading decides pass/fail. infra vs timeout are the only
            # non-completions, so a completed run is unconditionally `completed`.
            outcome = Outcome.completed

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
            failure_reason=failure_reason,
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
    def _trial_request_payload(request: TrialRequest) -> dict:
        """The trial-image contract payload [RN-4, D-8] — what a pre-baked image's
        entrypoint reads from ``/verdi/request.json``: its prompt and which arm it
        is (name, model, config). The prompt is holdout-free by construction (the
        seam refuses a canary in any request channel before the engine runs)."""
        return {
            "prompt": request.prompt,
            "arm": request.arm.name,
            "model": request.arm.model,
            "payload": request.arm.payload or {},
        }

    @staticmethod
    def _agent_version(request: TrialRequest) -> Optional[str]:
        return (request.arm.payload or {}).get("agent_binary_version")

    @staticmethod
    def _scan_proxy_log(request: TrialRequest) -> tuple[list[str], bool]:
        """Parse the metering proxy's structured JSONL, keyed on trial [RN-11].

        Each line is ``{"trial","host","decision":"allow|deny"}``; only lines for
        this trial count (per-trial attribution via the injected proxy credential),
        and any ``deny`` is an egress violation. A malformed line is skipped, not
        silently treated as clean traffic."""
        if request.proxy is None or not request.proxy.log_path:
            return [], False
        p = Path(request.proxy.log_path)
        if not p.exists():
            return [], False
        attempts: list[str] = []
        violation = False
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("trial") != request.trial_id:
                continue
            host = rec.get("host")
            if host:
                attempts.append(host)
            if rec.get("decision") == "deny":
                violation = True
        return attempts, violation
