"""Managed metering-proxy lifecycle [refactor 04 §1].

:class:`MeteringProxy` replaces the 7-raw-docker-step lifecycle the shakedown
scripts hand-rolled (and were already diverging on) with one context manager:
``__enter__`` stands up the metered + egress networks and the packaged CONNECT
proxy with the resolved allowlist **injected**, waits for readiness by *probing*
(no ``sleep`` guess), and yields a :class:`~harness.run.types.ProxyConfig`;
``__exit__`` always tears the whole thing down.

The proxy is the stdlib ``_proxy_container.py``, mounted read-only into a pinned
``python:3.12-alpine`` and run in place — no image build. Its JSONL contract and
trial-id-as-userinfo auth are frozen [refactor 04 §1, §6].
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

# Absolute imports (not ``from .docker import ...``): the AST seam sweep
# (tests/test_eval4_seam.py) flags a bare module name ``docker`` in an import.
from harness.hermetic.docker import DockerClient, HardenedCommand
from harness.hermetic.network import (
    EGRESS_NETWORK,
    METERED_NETWORK,
    connect_network,
    create_network,
    remove_network,
)
from harness.run.types import ProxyConfig

# The pinned base image the packaged proxy runs in — a multi-arch manifest-list
# digest [D005]. Overridable so a CI/other-platform runner can re-pin without a
# code change (the ``VERDI_GRADER_IMAGE`` pattern).
PROXY_BASE_IMAGE = os.environ.get(
    "VERDI_PROXY_IMAGE",
    "python:3.12-alpine@sha256:6d43704baacd1bfbe7c295d7f13079d5d8104ed33568873133f8fc69980419df",
)

# The managed proxy's container name — resolvable by name on the metered network,
# so a trial's ``HTTP(S)_PROXY=http://<trial-id>@verdi-metering-proxy:3128`` finds
# it. Matches deploy/metering-proxy/docker-compose.yml's ``container_name``.
MANAGED_PROXY_NAME = "verdi-metering-proxy"
PROXY_PORT = 3128
_CONTAINER_LOG = "/var/log/verdi/verdi.jsonl"
_READINESS_TIMEOUT_S = 30

# Probe readiness by *connecting* to the proxy port from inside the container,
# retrying until it accepts — bounded by the host-side ``docker exec`` timeout, so
# a proxy that never binds fails loudly instead of a fixed ``sleep`` guessing it is
# up. No ``sleep``: the retry is a shell ``until`` loop, not a timed wait.
_READY_PROBE = (
    "until python3 -c "
    "'import socket; socket.create_connection((\"127.0.0.1\", %d), 1)' 2>/dev/null; "
    "do :; done"
) % PROXY_PORT


class MeteringProxyError(RuntimeError):
    """The managed metering proxy could not be stood up (no daemon, image, or
    the proxy never became ready). Fail loudly — a run that proceeded without a
    working proxy would spend and egress unattributed [PRA-H4]."""


class MeteringProxy:
    """Context manager owning the metered proxy's whole lifecycle [refactor 04 §1]."""

    def __init__(
        self,
        allow: list[str],
        *,
        log_path: Optional[Path] = None,
        image: str = PROXY_BASE_IMAGE,
        docker: Optional[DockerClient] = None,
        name: str = MANAGED_PROXY_NAME,
    ) -> None:
        self._allow = list(allow)
        self._image = image
        self._docker = docker or DockerClient()
        self._name = name
        self._proxy_src = Path(__file__).resolve().parent / "_proxy_container.py"
        # Resolve where the JSONL log lands. An explicit path is honored as-is (its
        # parent dir is the mount); an absent one gets a managed temp dir removed on
        # teardown. Either way the *directory* is bind-mounted so the container can
        # create/append the file.
        self._owns_logdir = log_path is None
        if log_path is None:
            self._logdir = Path(tempfile.mkdtemp(prefix="verdi-metering-"))
            self._logfile = self._logdir / "verdi.jsonl"
        else:
            self._logfile = Path(log_path)
            self._logdir = self._logfile.parent

    @classmethod
    def managed(
        cls, allow: list[str], *, log_path: Optional[Path] = None, image: str = PROXY_BASE_IMAGE
    ) -> "MeteringProxy":
        """Build a managed proxy over ``allow`` (see the class docstring)."""
        return cls(allow, log_path=log_path, image=image)

    # --- context manager ------------------------------------------------------
    def __enter__(self) -> ProxyConfig:
        try:
            return self.start()
        except BaseException:
            # A partial stand-up (networks made, proxy crashed) must not leak.
            self.stop()
            raise

    def __exit__(self, *exc) -> None:
        self.stop()

    # --- lifecycle ------------------------------------------------------------
    def start(self) -> ProxyConfig:
        """Stand up networks + proxy, wait for readiness, return the ProxyConfig."""
        if not self._docker.daemon_available():
            raise MeteringProxyError(
                "docker daemon is unavailable — cannot stand up the managed "
                "metering proxy; run without proxy.managed or start docker"
            )
        # Provision the log dir and pre-create the file so a zero-egress trial still
        # finds a (configured, present) log rather than tripping PRA-H4.
        self._logdir.mkdir(parents=True, exist_ok=True)
        self._logfile.touch(exist_ok=True)
        # A stale container from a crashed prior run would collide on the name.
        self._remove_container()
        create_network(self._docker, METERED_NETWORK, internal=True)
        create_network(self._docker, EGRESS_NETWORK, internal=False)
        cmd = (
            HardenedCommand()
            .detach()
            .name(self._name)
            .network(METERED_NETWORK)
            .harden()
            .user()
            .env_kv("VERDI_PROXY_ALLOW", ",".join(self._allow))
            .env_kv("PROXY_LOG", _CONTAINER_LOG)
            .volume(self._proxy_src, "/verdi/proxy.py", ro=True)
            .volume(self._logdir, os.path.dirname(_CONTAINER_LOG))
            .image(self._image)
            .arg("python3", "/verdi/proxy.py")
            .build()
        )
        proc = self._docker.run(cmd, timeout_s=120)
        if proc.returncode != 0:
            raise MeteringProxyError(
                f"could not start the metering proxy container: {proc.stderr.strip()}"
            )
        # The proxy (and only the proxy) bridges to egress to reach the model APIs.
        connect_network(self._docker, EGRESS_NETWORK, self._name)
        self._await_ready()
        return ProxyConfig(
            allowlist=list(self._allow),
            proxy_url=f"http://{self._name}:{PROXY_PORT}",
            log_path=str(self._logfile),
        )

    def stop(self) -> None:
        """Tear down proxy + networks; always safe to call (idempotent, loud-free)."""
        self._remove_container()
        remove_network(self._docker, EGRESS_NETWORK)
        remove_network(self._docker, METERED_NETWORK)
        if self._owns_logdir:
            shutil.rmtree(self._logdir, ignore_errors=True)

    # --- internals ------------------------------------------------------------
    def _await_ready(self) -> None:
        try:
            proc = self._docker.run(
                ["docker", "exec", self._name, "sh", "-c", _READY_PROBE],
                timeout_s=_READINESS_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as e:
            raise MeteringProxyError(
                f"metering proxy {self._name!r} did not accept connections within "
                f"{_READINESS_TIMEOUT_S}s:\n{self._container_logs()}"
            ) from e
        if proc.returncode != 0:
            raise MeteringProxyError(
                f"metering proxy {self._name!r} failed to become ready "
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


def teardown_managed(docker: Optional[DockerClient] = None, *, name: str = MANAGED_PROXY_NAME) -> None:
    """Remove the managed proxy container + its networks by their known names.

    The ``bench proxy down`` verb's worker — a standalone teardown that does not
    need the originating :class:`MeteringProxy` object (the names are constants).
    """
    docker = docker or DockerClient()
    try:
        docker.run(["docker", "rm", "-f", name], timeout_s=30)
    except (OSError, subprocess.SubprocessError):
        pass
    remove_network(docker, EGRESS_NETWORK)
    remove_network(docker, METERED_NETWORK)
