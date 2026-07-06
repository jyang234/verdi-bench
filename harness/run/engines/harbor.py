"""Harbor engine [EVAL-4 §M2] — hermetic, pinned, network-insulated trials.

**Harbor is the only ENGINE, and the only module that may name harbor** [AC-1,
import-linter contract + the AST seam sweep]. All docker *mechanics* now live in
:mod:`harness.hermetic` (A6, refactor 04 §1): this module builds its trial argv
through :class:`~harness.hermetic.docker.HardenedCommand` and runs it through a
:class:`~harness.hermetic.docker.DockerClient`, so "who talks to Docker" is the
hermetic layer — the old "only this module talks to Docker" claim was already
false via ``grade/container.py`` and is re-scoped honestly here.

Hermetic posture [D001, D005]:
* Pinned image ref (digest captured into provenance).
* Pinned CPU/mem quotas [D003].
* No ambient network — egress only through the metering proxy (default-deny
  network + ``HTTP(S)_PROXY`` pointing at the allowlisted proxy). Every other
  attempt is a proxy log line + ``egress_violation`` on the record [AC-3].
* Provider keys injected as env at trial start — never baked into image layers
  or written to the ledger [AC-8].

Actual daemon calls sit behind an injectable ``runner`` (the ``CommandRunner``
seam, now backed by ``DockerClient``) so command construction and result mapping
are unit-testable without a live Docker; the true container-inspect assertions
are ``@pytest.mark.docker``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

from ...adapters.base import Outcome, Quotas
from ...hermetic.docker import (
    DAEMON_ERROR_EXIT,
    TIMEOUT_EXIT,
    DockerClient,
    HardenedCommand,
)
from ...hermetic.network import METERED_NETWORK
from ...hermetic.network import ensure_metered_network as _ensure_metered_network
from ..types import EngineResult, TrialRequest

HARBOR_VERSION = "harbor-pinned-0.1.0"  # version-pinned in images [D005]

# The trial-image contract [RN-4, EVAL-4-D-8; the normative statement is
# docs/images.md §1]: the harness writes the task prompt and arm configuration to a
# host file (a typed TrialRequestFile, A1) and bind-mounts it READ-ONLY at this
# path, OUTSIDE /workspace (so it never pollutes the graded workspace copy). A
# pre-baked trial image's entrypoint reads it to learn its task and which arm it is.
TRIAL_REQUEST_MOUNT = "/verdi/request.json"

# METERED_NETWORK is imported from harness.hermetic.network — the single owner of
# the constant [refactor 04 §1]; the string never changes [refactor 04 §6]. Kept
# re-exported here so `from ...engines.harbor import METERED_NETWORK` still works.


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


class TelemetryCorruptError(RuntimeError):
    """The agent's native telemetry log was present but not valid JSON [RN-17].

    Distinct from an absent log (legitimately no telemetry): corruption must
    fail the trial closed, never silently become "no telemetry"."""


class ProxyLogMissingError(RuntimeError):
    """A configured metering-proxy log file is absent [PRA-H4].

    The proxy is dead or misconfigured; treating this as "no egress, no cost, no
    violation" is a silent fail-open of the cost guard and the egress fence, so
    the scan raises and the trial fails infra_failed(proxy_log_missing)."""


@dataclass
class RunOutput:
    exit_status: int
    daemon_error: bool = False
    timed_out: bool = False
    # PRA-M7: True when the timeout kill/reap could not be confirmed, so a
    # possibly-still-live container may still be writing into the mounted
    # workspace — redaction must NOT be trusted; the trial fails infra_failed.
    kill_failed: bool = False


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

    def resolve_pinned(self, image: str) -> Optional[tuple[str, str]]: ...

    def ensure_metered_network(self) -> None: ...


class DockerCliRunner:
    """Default runner: builds argv here, runs it through :class:`DockerClient`.

    The ``CommandRunner`` seam's production implementation. Docker mechanics — the
    subprocess call, the daemon/exit-code semantics, the metered network — are
    delegated to :mod:`harness.hermetic`, so this class is just harbor's mapping of
    a container run into a :class:`RunOutput` [refactor 04 §1]."""

    def __init__(self, docker: Optional[DockerClient] = None) -> None:
        self._docker = docker or DockerClient()

    def ensure_metered_network(self) -> None:
        """Create the restricted metering network if it's absent [RN-11] — the
        hermetic network owner does the work; harbor triggers it before a proxied
        trial joins ``--network verdi-metered``."""
        _ensure_metered_network(self._docker)

    def _kill(self, name: Optional[str]) -> bool:
        """Kill and reap a container by name [RN-10]. Returns True iff the
        container is confirmed no longer running afterward; a False means it may
        still be live, so the caller fails the trial closed rather than redact a
        workspace still being written to [PRA-M7].

        Correctness note: trial containers run with ``--rm``, so ``docker kill``
        triggers auto-removal and a following ``docker kill``/``docker wait`` can
        legitimately exit nonzero *because the container is already gone* — which
        is SUCCESS, not failure. So we do not trust those exit codes; we send the
        kill (best-effort, ``--rm`` may already be reaping), reap, then CONFIRM
        the final state with ``docker inspect``: gone (nonzero) or present-but-
        not-Running is a confirmed kill; present-and-Running is the only failure.
        """
        if not name:
            return True  # no container to kill (never started)
        for args in (["docker", "kill", name], ["docker", "wait", name]):
            try:
                self._docker.run(args, timeout_s=30)
            except (OSError, subprocess.SubprocessError):
                pass  # exit code is unreliable under --rm; the inspect below decides
        try:
            probe = self._docker.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", name], timeout_s=30
            )
        except (OSError, subprocess.SubprocessError):
            return False  # cannot confirm the container is dead → fail closed
        if probe.returncode != 0:
            return True  # container is gone (--rm reaped it): confirmed not running
        return probe.stdout.strip() == "false"  # present but not Running ⇒ killed

    def resolve_pinned(self, image: str) -> Optional[tuple[str, str]]:
        """The runnable IMMUTABLE ref and its digest, or None (refused).

        F-M-I2: the same immutable ref is recorded in provenance AND handed to
        ``docker run`` — resolving a digest by ``inspect`` but running the tag
        left a TOCTOU window where a repointed tag executed one image while
        provenance recorded another.
        """
        if "@sha256:" in image:
            return image, image.split("@", 1)[1]
        # a registry image carries a RepoDigest (the manifest digest) — a
        # runnable repo@sha256:... ref
        repo = self._inspect_format(image, "{{index .RepoDigests 0}}")
        if repo and "@" in repo:
            return repo, repo.split("@", 1)[1]
        # a local/CI image (built, never pushed) has no RepoDigest — pin instead
        # to its content-addressed image Id (itself runnable), which identifies
        # the exact image in provenance and satisfies D005 [RN-12]. An absent
        # image resolves to None and is refused.
        idv = self._inspect_format(image, "{{.Id}}")
        return (idv, idv) if idv and idv.startswith("sha256:") else None

    def _inspect_format(self, image: str, fmt: str) -> Optional[str]:
        try:
            out = self._docker.run(
                ["docker", "inspect", "--format", fmt, image], timeout_s=30
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    def run_container(
        self, cmd: list[str], timeout_s: int, env: Optional[dict] = None
    ) -> RunOutput:
        # Provider key VALUES are layered into the child environment by DockerClient
        # (never on the argv), so `docker run --env KEY` picks them up without
        # exposing them in the host process table [AC-8].
        try:
            proc = self._docker.run(cmd, timeout_s=timeout_s, env=env)
        except subprocess.TimeoutExpired:
            # Kill the CONTAINER, not just the docker CLI: the CLI dying on timeout
            # leaves the container running and writing into the still-mounted
            # workspace AFTER redaction. Kill by name and reap it before returning,
            # so redaction sees a final, static workspace [RN-10]. If the kill/reap
            # cannot be confirmed the workspace is not safe to redact, so surface
            # that as kill_failed [PRA-M7].
            killed = self._kill(_name_from_cmd(cmd))
            return RunOutput(exit_status=TIMEOUT_EXIT, timed_out=True, kill_failed=not killed)
        except (OSError, FileNotFoundError):
            return RunOutput(exit_status=DAEMON_ERROR_EXIT, daemon_error=True)
        # docker returns 125 for daemon/config errors before the container runs
        if proc.returncode == DAEMON_ERROR_EXIT:
            return RunOutput(exit_status=DAEMON_ERROR_EXIT, daemon_error=True)
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
        # The shared hardened recipe [refactor 04 §1]: --pull=never so a trial runs
        # only the pre-baked, digest-pinned image [RN-12, D005]; a deterministic
        # --name so a timed-out container is killable [RN-10]; --user so files the
        # trial writes into the bind-mounted workspace are harness-owned and
        # redactable [RN-7]; and the PRA-L9 cap-drop/no-new-privileges/pids-limit
        # hardening — a benchmark trial is untrusted agent code.
        hc = (
            HardenedCommand()
            .rm()
            .pull_never()
            .name(_container_name(request.trial_id))
            .user()
            .harden(pids_limit=512)
        )
        # pinned quotas [D003]; --memory pins swap to the same ceiling so default
        # swap headroom cannot silently loosen the symmetric per-arm memory cap.
        if q.cpus is not None:
            hc.cpus(q.cpus)
        if q.mem is not None:
            hc.memory(q.mem)
        # network insulation: default-deny; egress only via the metering proxy
        if request.proxy is not None and request.proxy.proxy_url:
            # inject the trial id as the proxy-auth credential so the metering
            # proxy attributes every request to this trial [RN-11, D-10].
            proxy_url = _with_trial_auth(request.proxy.proxy_url, request.trial_id)
            hc.env_kv("HTTP_PROXY", proxy_url).env_kv("HTTPS_PROXY", proxy_url)
            # a restricted docker network that only reaches the proxy [RN-11]
            hc.network(METERED_NETWORK)
        else:
            hc.network("none")
        # provider keys injected as env — never in image layers or ledger [AC-8],
        # and never as `KEY=VALUE` on the argv (visible in `ps`/proc). Pass only
        # the NAME on the command; docker reads the value from the CLI process
        # environment, which run_container populates from request.provider_keys.
        for k in (request.provider_keys or {}):
            hc.env(k)
        # workspace mount; then the trial request (prompt + arm config) delivered
        # READ-ONLY, outside the workspace so it never enters the graded copy
        # [RN-4, D-8] — added after the workspace volume so a workspace-first parser
        # still finds /workspace.
        hc.volume(request.workspace, "/workspace")
        if request_file is not None:
            hc.volume(request_file, TRIAL_REQUEST_MOUNT, ro=True)
        hc.workdir("/workspace").image(image)
        return hc.build()

    def run(self, request: TrialRequest) -> EngineResult:
        image = request.image
        pinned = self._runner.resolve_pinned(image)
        artifacts = Path(request.workspace) / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)

        if pinned is None:
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
        pinned_ref, digest = pinned

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
            # F-M-I2: run the resolved immutable ref, never the mutable tag.
            cmd = self.build_run_command(request, pinned_ref, request_file)
            result = self._runner.run_container(
                cmd, request.timeout_s, env=request.provider_keys or {}
            )
        finally:
            shutil.rmtree(req_dir, ignore_errors=True)

        failure_reason: Optional[str] = None
        if result.kill_failed:
            # PRA-M7: the timeout kill could not be confirmed, so a live container
            # may still be writing the workspace — do not trust redaction; fail
            # the trial closed with a specific reason rather than reporting a plain
            # timeout whose (possibly unredacted) artifacts we would then capture.
            outcome = Outcome.infra_failed
            failure_reason = "kill_failed"
        elif result.daemon_error:
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

        try:
            native_log = self._read_native_log(artifacts)
        except TelemetryCorruptError:
            # RN-17: corrupt telemetry fails the trial closed rather than
            # silently becoming "no telemetry". Only a completed trial is
            # downgraded — a daemon/timeout failure keeps its more specific reason.
            native_log = {}
            if outcome == Outcome.completed:
                outcome = Outcome.infra_failed
                failure_reason = "telemetry_corrupt"
        try:
            egress_attempts, egress_violation, metered_cost = self._scan_proxy_log(request)
        except ProxyLogMissingError:
            # PRA-H4: fail closed — a missing proxy log means we cannot vouch for
            # egress confinement or metered cost for this trial. Only downgrade a
            # would-be-completed trial; a daemon/timeout/kill failure keeps its
            # more specific reason.
            egress_attempts, egress_violation, metered_cost = [], False, None
            if outcome == Outcome.completed:
                outcome = Outcome.infra_failed
                failure_reason = "proxy_log_missing"

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
            # cost the metering proxy attributed to this trial — feeds the cost
            # guard when the arm can't self-report [RN-2]. None until the proxy
            # emits per-request cost in its JSONL.
            proxy_metered_cost=metered_cost,
        )

    @staticmethod
    def _read_native_log(artifacts: Path) -> dict:
        """Parse the agent's native telemetry log.

        An **absent** log is legitimate (the arm may emit none) and reads as
        ``{}``. A **present but corrupt** log is not — silently mapping it to
        ``{}`` would launder corrupt telemetry into "no telemetry", so raise
        :class:`TelemetryCorruptError` and let the caller fail the trial closed
        [RN-17]."""
        log = artifacts / "agent_log.json"
        if not log.exists():
            return {}
        try:
            return json.loads(log.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise TelemetryCorruptError(f"{log}: {e}") from e

    @staticmethod
    def _trial_request_payload(request: TrialRequest) -> dict:
        """The trial-image contract payload [RN-4, D-8, A1] — what a pre-baked
        image's entrypoint reads from ``/verdi/request.json``: its prompt and which
        arm it is (name, model, config). Built through the typed
        :class:`~harness.run.request.TrialRequestFile` so the file carries a
        ``schema_version`` (A1, additive: the existing prompt/arm/model/payload keys
        are unchanged). The prompt is holdout-free by construction (the seam refuses
        a canary in any request channel before the engine runs)."""
        from ..request import TrialRequestFile

        return TrialRequestFile(
            prompt=request.prompt,
            arm=request.arm.name,
            model=request.arm.model,
            payload=request.arm.payload or {},
        ).model_dump(mode="json")

    @staticmethod
    def _agent_version(request: TrialRequest) -> Optional[str]:
        return (request.arm.payload or {}).get("agent_binary_version")

    @staticmethod
    def _scan_proxy_log(request: TrialRequest) -> tuple[list[str], bool, Optional[float]]:
        """Parse the metering proxy's structured JSONL, keyed on trial [RN-11].

        Each line is ``{"trial","host","decision":"allow|deny"[,"cost"]}``; only
        lines for this trial count (per-trial attribution via the injected proxy
        credential). Any ``deny`` is an egress violation, and a per-line ``cost``
        (when the proxy meters it) sums into the trial's metered cost so a
        null-telemetry-cost arm is still enforceable on the real path [RN-2].

        A line that is not a JSON object is skipped without crashing (a bare
        ``42``/``null``/``[...]`` must not abort the whole run); unparseable lines
        are skipped — the metering proxy is expected to emit valid JSONL, so a
        malformed line is an operational fault of the proxy, not this trial's.

        PRA-H4: a *configured but absent* log is NOT treated as "no egress, no
        cost, no violation" — that silent fail-open let a null-telemetry arm spend
        invisibly against the ceiling and shed egress-violation evidence when the
        proxy was dead or its path was wrong. A configured proxy whose log file is
        missing raises :class:`ProxyLogMissingError`, and the trial fails closed
        infra_failed(proxy_log_missing)."""
        if request.proxy is None or not request.proxy.log_path:
            return [], False, None
        p = Path(request.proxy.log_path)
        if not p.exists():
            raise ProxyLogMissingError(
                f"proxy log {p} is configured but absent — the metering proxy is "
                "dead or misconfigured; refusing to treat this as zero egress/cost "
                "[PRA-H4]"
            )
        attempts: list[str] = []
        violation = False
        metered: Optional[float] = None
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict) or rec.get("trial") != request.trial_id:
                continue
            host = rec.get("host")
            if host:
                attempts.append(host)
            if rec.get("decision") == "deny":
                violation = True
            cost = rec.get("cost")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                metered = (metered or 0.0) + float(cost)
        return attempts, violation, metered
