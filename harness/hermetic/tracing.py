"""Managed OTLP trace-collector lifecycle [refactor 09 §3].

:class:`TraceCollector` mirrors :class:`~harness.hermetic.metering.MeteringProxy`
exactly — one context manager that stands the collector container up on the
metered network, waits for readiness by *probing* 4318 (never a fixed timer),
yields a :class:`CollectorConfig`, and always tears the container down — with one
deliberate difference: the collector attaches **only** to ``METERED_NETWORK``
(``--internal``) and never to the egress network. It has no outbound needs, so
span data physically cannot leave the host [refactor 09 §3, §6].

The collector is the stdlib ``_collector_container.py``, mounted read-only into a
pinned ``python:3.12-alpine`` and run in place — no image build. Its envelope
JSONL contract is frozen [refactor 09 §2].

**DECISION D-09-1** (raw-log retention): the shared host-side envelope log holds
raw (possibly secret/identity-bearing) span bodies that ``redact_artifacts``
cannot see inside base64/protobuf, so it cannot be made safe to keep. The
lifecycle therefore **deletes it on teardown** after each trial has already
extracted its per-trial slice into the redacted, sha-bound ``otlp_spans.json``;
``keep_raw=True`` retains it as an explicitly operator-tier file.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict

# Absolute imports (not ``from .docker import ...``): the AST seam sweep
# (tests/test_eval4_seam.py) flags a bare module name ``docker`` in an import.
from harness.hermetic.docker import DockerClient, HardenedCommand
from harness.hermetic.metering import PROXY_BASE_IMAGE
from harness.hermetic.network import METERED_NETWORK, create_network, remove_network

# The collector shares the proxy's pinned base image (the same ``python3`` runs
# both stdlib sidecars) so the digest lives in exactly one place [refactor 04 §1].
# Overridable so a CI/other-platform runner can re-pin without a code change.
COLLECTOR_BASE_IMAGE = os.environ.get("VERDI_COLLECTOR_IMAGE", PROXY_BASE_IMAGE)

# The managed collector's container name — resolvable by name on the metered
# network, so a trial's ``OTEL_EXPORTER_OTLP_ENDPOINT=http://verdi-trace-collector:4318``
# finds it. Single-constant discipline, like ``MANAGED_PROXY_NAME``/``METERED_NETWORK``.
MANAGED_COLLECTOR_NAME = "verdi-trace-collector"
COLLECTOR_PORT = 4318
_CONTAINER_LOG = "/var/log/verdi/otlp.jsonl"
_READINESS_TIMEOUT_S = 30

# Probe readiness by *connecting* to 4318 from inside the container, retrying
# until it accepts — bounded by the host-side ``docker exec`` timeout, so a
# collector that never binds fails loudly instead of a fixed timer guessing it up.
_READY_PROBE = (
    "until python3 -c "
    "'import socket; socket.create_connection((\"127.0.0.1\", %d), 1)' 2>/dev/null; "
    "do :; done"
) % COLLECTOR_PORT


class CollectorConfig(BaseModel):
    """What :meth:`TraceCollector.__enter__` yields — the endpoint a trial's OTel
    exporter targets and the host-side envelope JSONL the extraction reads."""

    model_config = ConfigDict(extra="forbid")
    endpoint: str
    log_path: str


class TraceCollectorError(RuntimeError):
    """The managed trace collector could not be stood up (no daemon, image, or it
    never became ready). Fail loudly — a run that proceeded believing capture was
    live while the collector was dead would lose spans silently [refactor 09 §3]."""


class TraceCollector:
    """Context manager owning the trace collector's whole lifecycle [refactor 09 §3]."""

    def __init__(
        self,
        *,
        log_path: Optional[Path] = None,
        keep_raw: bool = False,
        image: str = COLLECTOR_BASE_IMAGE,
        docker: Optional[DockerClient] = None,
        name: str = MANAGED_COLLECTOR_NAME,
    ) -> None:
        self._keep_raw = keep_raw
        self._image = image
        self._docker = docker or DockerClient()
        self._name = name
        self._collector_src = Path(__file__).resolve().parent / "_collector_container.py"
        # Resolve where the envelope JSONL lands. An explicit path is honored
        # as-is: its parent dir is the mount AND its basename rides into the
        # container via COLLECTOR_LOG, so the collector writes the operator's exact
        # filename — a custom basename must never fall open as a touched-but-empty
        # log beside otlp.jsonl (the 988af58 PROXY_LOG lesson). An absent path gets
        # a managed temp dir removed on teardown.
        self._owns_logdir = log_path is None
        if log_path is None:
            self._logdir = Path(tempfile.mkdtemp(prefix="verdi-otlp-"))
            self._logfile = self._logdir / "otlp.jsonl"
        else:
            self._logfile = Path(log_path)
            self._logdir = self._logfile.parent

    @classmethod
    def managed(
        cls,
        *,
        log_path: Optional[Path] = None,
        keep_raw: bool = False,
        image: str = COLLECTOR_BASE_IMAGE,
    ) -> "TraceCollector":
        """Build a managed collector (see the class docstring). ``keep_raw`` opts out
        of the D-09-1 delete-on-teardown of the raw envelope log."""
        return cls(log_path=log_path, keep_raw=keep_raw, image=image)

    # --- context manager ------------------------------------------------------
    def __enter__(self) -> CollectorConfig:
        try:
            return self.start()
        except BaseException:
            # A partial stand-up (network made, container crashed) must not leak.
            self.stop()
            raise

    def __exit__(self, *exc) -> None:
        self.stop()

    # --- lifecycle ------------------------------------------------------------
    def start(self) -> CollectorConfig:
        """Stand the collector up on the metered network, wait for readiness, and
        return the :class:`CollectorConfig`. Unlike the proxy it never connects to
        the egress network — the collector has no outbound needs [refactor 09 §3]."""
        if not self._docker.daemon_available():
            raise TraceCollectorError(
                "docker daemon is unavailable — cannot stand up the managed trace "
                "collector; run without otlp.managed or start docker"
            )
        # Provision the log dir and pre-create the file so a zero-span trial still
        # finds a (configured, present) envelope log rather than tripping the
        # span_log_missing fail-closed path [refactor 09 §2/§4].
        self._logdir.mkdir(parents=True, exist_ok=True)
        self._logfile.touch(exist_ok=True)
        # A stale container from a crashed prior run would collide on the name.
        self._remove_container()
        # The collector joins the SAME internal metered network the proxied trial
        # is on, reachable there by name; it never touches the egress network.
        create_network(self._docker, METERED_NETWORK, internal=True)
        cmd = (
            HardenedCommand()
            .detach()
            .name(self._name)
            .network(METERED_NETWORK)
            .harden()
            .user()
            # The basename rides into the container under the mounted dir (the
            # PROXY_LOG discipline), so a custom log path is honored exactly.
            .env_kv("COLLECTOR_LOG", f"{os.path.dirname(_CONTAINER_LOG)}/{self._logfile.name}")
            .volume(self._collector_src, "/verdi/collector.py", ro=True)
            .volume(self._logdir, os.path.dirname(_CONTAINER_LOG))
            .image(self._image)
            .arg("python3", "/verdi/collector.py")
            .build()
        )
        proc = self._docker.run(cmd, timeout_s=120)
        if proc.returncode != 0:
            raise TraceCollectorError(
                f"could not start the trace collector container: {proc.stderr.strip()}"
            )
        # NO egress connect — span data terminates on the host [refactor 09 §3, §6].
        self._await_ready()
        return CollectorConfig(
            endpoint=f"http://{self._name}:{COLLECTOR_PORT}",
            log_path=str(self._logfile),
        )

    def stop(self) -> None:
        """Tear down the collector + its network, then apply D-09-1 to the raw log.
        Always safe to call (idempotent, loud-free)."""
        self._remove_container()
        # Best-effort: if a managed metering proxy still holds the metered network,
        # this rm is a no-op and the proxy's own teardown removes it [refactor 09 §3].
        remove_network(self._docker, METERED_NETWORK)
        self._delete_envelope_log()

    def _delete_envelope_log(self) -> None:
        """D-09-1: the raw envelope log holds un-redactable secret/identity-bearing
        bodies, so it is deleted on teardown unless ``keep_raw`` retains it as an
        explicitly operator-tier file [refactor 09 §6]."""
        if self._keep_raw:
            return
        if self._owns_logdir:
            shutil.rmtree(self._logdir, ignore_errors=True)
        elif self._logfile.exists():
            self._logfile.unlink()

    # --- internals ------------------------------------------------------------
    def _await_ready(self) -> None:
        try:
            proc = self._docker.run(
                ["docker", "exec", self._name, "sh", "-c", _READY_PROBE],
                timeout_s=_READINESS_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as e:
            raise TraceCollectorError(
                f"trace collector {self._name!r} did not accept connections within "
                f"{_READINESS_TIMEOUT_S}s:\n{self._container_logs()}"
            ) from e
        if proc.returncode != 0:
            raise TraceCollectorError(
                f"trace collector {self._name!r} failed to become ready "
                f"(exit {proc.returncode}):\n{self._container_logs()}"
            )

    def _remove_container(self) -> None:
        try:
            self._docker.run(["docker", "rm", "-f", self._name], timeout_s=30)
        except (OSError, subprocess.SubprocessError):
            pass

    def _container_logs(self) -> str:
        try:
            proc = self._docker.run(["docker", "logs", self._name], timeout_s=15)
            return ((proc.stdout or "") + (proc.stderr or "")).strip() or "<no logs>"
        except (OSError, subprocess.SubprocessError):
            return "<logs unavailable>"


def teardown_managed(
    docker: Optional[DockerClient] = None,
    *,
    name: str = MANAGED_COLLECTOR_NAME,
    log_path: Optional[Path] = None,
    keep_raw: bool = False,
) -> None:
    """Remove the managed collector container + its metered network by their known
    names — the ``bench otlp down`` worker.

    A standalone teardown (the names are constants), so it does not need the
    originating :class:`TraceCollector`. It removes the metered network best-effort
    (a live metering proxy still holding it makes the rm a no-op), and applies the
    D-09-1 default: delete ``log_path`` unless ``keep_raw`` [refactor 09 §3, §6].
    """
    docker = docker or DockerClient()
    try:
        docker.run(["docker", "rm", "-f", name], timeout_s=30)
    except (OSError, subprocess.SubprocessError):
        pass
    remove_network(docker, METERED_NETWORK)
    if log_path is not None and not keep_raw:
        p = Path(log_path)
        if p.exists():
            p.unlink()
