"""Docker command mechanics — the one owner of ``docker`` subprocess calls [refactor 04 §1].

Docker argv construction and daemon interaction were hand-rolled in three places
(``harbor.py`` trials, ``grade/container.py`` graders + plugins, with duplicated
daemon probes and exit-125 semantics). This module promotes harbor's
``CommandRunner`` seam into one shared client so every owner runs argv, probes the
daemon, and builds the hardened ``docker run`` recipe from a single place.

Placement [refactor 04 §1]: ``hermetic`` names neither ``harbor`` nor any engine,
so the AST seam sweep (``tests/test_eval4_seam.py``) and the harbor-confinement
import contract stay green. NB: peers import ``DockerClient`` from the fully
qualified ``harness.hermetic.docker`` path — a relative ``from .docker import ...``
would read to that same AST sweep as the bare module name ``docker`` and be
flagged, so intra-package imports of this module use the absolute path.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

# docker's own exit codes, keyed on by both owners [harbor RunOutput mapping +
# grade GraderUnavailableError]. Docker returns 125 for a daemon/config error
# *before* the container runs; harbor stamps 124 on a container it killed after a
# timeout. Naming them here kills the duplicated ``== 125`` literals.
DAEMON_ERROR_EXIT = 125
TIMEOUT_EXIT = 124


class DockerClient:
    """Injectable seam over the ``docker`` CLI — promotes harbor's ``CommandRunner``.

    ``run`` is the single place a ``docker`` argv is handed to :func:`subprocess.run`;
    it propagates :class:`subprocess.TimeoutExpired` and :class:`OSError` unchanged
    so each owner maps them into its own failure vocabulary (harbor's kill-on-timeout
    ``RunOutput``; grade's transient ``GraderUnavailableError``).
    """

    def run(
        self,
        argv: list[str],
        *,
        timeout_s: Optional[int] = None,
        env: Optional[dict] = None,
        text: bool = True,
    ) -> subprocess.CompletedProcess:
        """Run ``argv`` capturing stdout/stderr; return the completed process.

        ``env`` (when given) is layered over the process environment, so a caller
        passes only the delta (e.g. provider keys) rather than the whole environ;
        an absent/empty ``env`` inherits the parent environment unchanged. Never
        raises on a nonzero exit (``check=False``) — a nonzero return is data the
        caller classifies, not an error to swallow.
        """
        child_env = {**os.environ, **env} if env else None
        return subprocess.run(
            argv, capture_output=True, text=text, timeout=timeout_s, env=child_env, check=False
        )

    def daemon_available(self) -> bool:
        """Probe ``docker version`` — ``True`` iff the daemon answers [GR-8, RN-14].

        The single daemon-availability probe both the grader pre-flight and the
        managed metering proxy consult before doing work that a dead daemon would
        turn into a misleading failure.
        """
        try:
            proc = self.run(["docker", "version"], timeout_s=30)
        except (OSError, subprocess.SubprocessError):
            return False
        return proc.returncode == 0


class HardenedCommand:
    """Fluent builder for a hardened ``docker run`` argv — the shared recipe, once.

    The cap-drop / no-new-privileges / non-root / ``--pull=never`` / ``mem=swap`` /
    read-only-mount / workdir spellings live here so harbor's trial shape
    (``harbor.py:236-294``) and grade's grader + plugin shapes
    (``grade/container.py:305-344``) draw them from one place. The builder appends
    in call order, so each owner keeps its exact, byte-pinned token sequence — the
    argv-identity tests (eval4 + eval5 container/plugin suites) stay green.
    """

    def __init__(self) -> None:
        self._argv: list[str] = ["docker", "run"]

    def rm(self) -> "HardenedCommand":
        """``--rm``: never reuse a container (a fresh one per trial/grade)."""
        self._argv.append("--rm")
        return self

    def detach(self) -> "HardenedCommand":
        """``-d``: run detached — the managed metering proxy's long-lived shape."""
        self._argv.append("-d")
        return self

    def pull_never(self) -> "HardenedCommand":
        """``--pull=never``: run only the pre-baked, digest-pinned image [RN-12, D005]."""
        self._argv.append("--pull=never")
        return self

    def name(self, container_name: str) -> "HardenedCommand":
        """``--name``: a deterministic name so a timed-out container is killable [RN-10]."""
        self._argv += ["--name", container_name]
        return self

    def user(self) -> "HardenedCommand":
        """``--user uid:gid``: run as the invoking user so written files are
        harness-owned and redactable [RN-7]. A no-op where ``os.getuid`` is absent
        (non-POSIX), matching the callers' existing guard."""
        if hasattr(os, "getuid"):
            self._argv += ["--user", f"{os.getuid()}:{os.getgid()}"]
        return self

    def harden(self, *, pids_limit: Optional[int] = None) -> "HardenedCommand":
        """The shared security recipe: drop all capabilities and forbid privilege
        escalation [PRA-L9]; optionally cap process count (harbor's trial shape
        pins ``--pids-limit`` against a fork bomb; grade's shapes do not)."""
        self._argv += ["--cap-drop", "ALL", "--security-opt", "no-new-privileges"]
        if pids_limit is not None:
            self._argv += ["--pids-limit", str(pids_limit)]
        return self

    def cpus(self, cpus) -> "HardenedCommand":
        """``--cpus``: the pinned CPU quota [D003]."""
        self._argv += ["--cpus", str(cpus)]
        return self

    def memory(self, mem) -> "HardenedCommand":
        """``--memory`` with swap pinned to the same limit, so the quota is a hard
        ceiling and default swap headroom cannot silently loosen it [PRA-L9, D003]."""
        self._argv += ["--memory", str(mem), "--memory-swap", str(mem)]
        return self

    def network(self, name: str) -> "HardenedCommand":
        """``--network``: the metered network for a proxied trial, else ``none``."""
        self._argv += ["--network", name]
        return self

    def env(self, name: str) -> "HardenedCommand":
        """``--env NAME``: inject a value from the CLI process environment (the
        value never appears on the argv, so it is not visible in ``ps`` [AC-8])."""
        self._argv += ["--env", name]
        return self

    def env_kv(self, key: str, value: str) -> "HardenedCommand":
        """``--env KEY=VALUE`` (proxy URLs — not secrets)."""
        self._argv += ["--env", f"{key}={value}"]
        return self

    def e_env(self, key: str, value: str) -> "HardenedCommand":
        """``-e KEY=VALUE`` — grade's spelling for the per-grade fence nonce."""
        self._argv += ["-e", f"{key}={value}"]
        return self

    def volume(self, host, container: str, *, ro: bool = False) -> "HardenedCommand":
        """``--volume HOST:CONTAINER[:ro]`` with the host path resolved absolute."""
        spec = f"{Path(host).resolve()}:{container}" + (":ro" if ro else "")
        self._argv += ["--volume", spec]
        return self

    def workdir(self, path: str) -> "HardenedCommand":
        """``--workdir``."""
        self._argv += ["--workdir", path]
        return self

    def image(self, image: str) -> "HardenedCommand":
        """The image ref — appended last of the ``docker run`` flags."""
        self._argv.append(image)
        return self

    def arg(self, *tokens) -> "HardenedCommand":
        """Trailing container command tokens (e.g. the plugin entrypoint)."""
        self._argv += [str(t) for t in tokens]
        return self

    def build(self) -> list[str]:
        """The assembled argv (a fresh list; the builder is not reused after)."""
        return list(self._argv)
