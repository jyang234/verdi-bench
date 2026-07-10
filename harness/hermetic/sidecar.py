"""``ManagedSidecar`` — the one managed-container lifecycle [refactor 11 §G2].

The docker *command* machinery is already single-sourced (``DockerClient``,
``HardenedCommand``, the ``network.py`` constants, a shared pinned base image).
What duplicated was the managed-lifecycle scaffolding *around* it: the metering
proxy [refactor 04 §1] and the OTLP trace collector [refactor 09 §3] carried
near-identical, member-for-member copies of ``__enter__``/``__exit__``, the
readiness *probe* (never a fixed timer), container removal, log fetch, and the
log-dir/basename resolution. Two copies was survivable; a third sidecar (a future
artifact-syncer, log-shipper, …) must not make it three.

:class:`ManagedSidecar` owns that skeleton once as a template method:

    ``start`` = daemon-or-refuse → provision the log dir → remove any stale
    container → ``_stand_up`` (the subclass's ``HardenedCommand`` recipe) →
    ``_await_ready`` (probe, bounded by the exec timeout) → ``_config`` (the
    subclass's yielded config)

    ``stop`` = remove the container → ``_teardown_networks`` (which networks the
    subclass created) → ``_pre_teardown`` (log cleanup; the collector's D-09-1
    delete overrides the default owned-temp-dir sweep)

Each subclass keeps only its deliberate divergences: the metering proxy stands up
a dual network (metered + egress) and a CONNECT allowlist; the collector attaches
to ``METERED_NETWORK`` only (no egress — span data physically cannot leave the
host) and deletes the raw envelope log on teardown [D-09-1]. The behavior is a
pure hoist — the hermetic and OTLP suites plus the live docker e2es are the pin.
"""

from __future__ import annotations

import itertools
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import ClassVar, Optional

# Absolute import (not ``from .docker import ...``): the AST seam sweep
# (tests/test_eval4_seam.py) flags a bare module name ``docker`` in an import.
from harness.hermetic.docker import DockerClient

# The ownership label every managed sidecar container carries (incident 2026-07-10):
# ``--label {SIDECAR_LABEL}=<kind>`` (kind = the subclass's ``_LABEL_VALUE``), so a
# teardown sweeps by ownership — ``docker ps --filter label=…`` then ``rm -f`` each —
# instead of by a shared name. Before this, a fixed global container name let ANY
# lifecycle actor operate on a name it did not own: a concurrent e2e cleanup removed
# a live harbor run's proxy (21/24 trials invalidated), and two concurrent runs would
# kill each other's proxy via ``start()``'s stale-sweep.
SIDECAR_LABEL = "verdi.managed-sidecar"

# Deterministic per-process name suffix — pid + a monotonic counter, NO randomness or
# wall-clock (the determinism directive). Two default sidecars in one process diverge
# on the counter; two processes diverge on the pid. The container name is never
# ledgered or hash-chained (it only rides runtime env, e.g. HTTP_PROXY), so a
# pid-varying value cannot perturb any instrument output.
_INSTANCE_COUNTER = itertools.count(1)


def _instance_suffix() -> str:
    """A unique, deterministic suffix for a managed sidecar's default name."""
    return f"{os.getpid()}-{next(_INSTANCE_COUNTER)}"

# The readiness *probe*, single-sourced: connect to the sidecar's port from inside
# the container, retrying until it accepts — bounded by the host-side ``docker
# exec`` timeout, so a sidecar that never binds fails loudly instead of a fixed
# timer guessing it is up. The retry is a shell ``until`` loop, not a timed wait.
# ``%d`` is the subclass ``port``; the rendered string is byte-identical to the
# per-module ``_READY_PROBE`` constants it replaces [refactor 11 §G2].
_READY_PROBE_TEMPLATE = (
    "until python3 -c "
    "'import socket; socket.create_connection((\"127.0.0.1\", %d), 1)' 2>/dev/null; "
    "do :; done"
)
_READINESS_TIMEOUT_S = 30


def remove_managed_container(name: str, client: DockerClient) -> None:
    """Best-effort ``docker rm -f <name>`` — the container-removal step shared by
    every managed sidecar's ``stop`` and the ``--name`` escape hatch of the
    standalone ``bench <sidecar> down`` operator verbs.

    Loud-free by design: a teardown that crashed because the container was already
    gone would leak the *other* resources the caller still means to remove.
    """
    try:
        client.run(["docker", "rm", "-f", name], timeout_s=30)
    except (OSError, subprocess.SubprocessError):
        pass


def remove_labeled_sidecars(label_value: str, client: DockerClient) -> None:
    """Best-effort removal of EVERY container labeled ``{SIDECAR_LABEL}=<label_value>``
    — the ownership sweep the default ``bench <sidecar> down`` uses so it cannot miss a
    suffixed live sidecar nor remove an unrelated bare-name container (incident
    2026-07-10). A label-filtered ``docker ps`` discovers the ids, then each is
    force-removed. Loud-free (a failed discover leaves the other teardown steps intact).
    """
    try:
        proc = client.run(
            ["docker", "ps", "-aq", "--filter", f"label={SIDECAR_LABEL}={label_value}"],
            timeout_s=30,
        )
    except (OSError, subprocess.SubprocessError):
        return
    if proc.returncode != 0:
        return
    for cid in proc.stdout.split():
        remove_managed_container(cid, client)


class ManagedSidecar:
    """Template-method base for a managed docker sidecar [refactor 11 §G2].

    Subclasses declare ``port`` and set the ``_ERROR_CLS`` / ``_NOUN`` /
    ``_DAEMON_UNAVAILABLE_MESSAGE`` / ``_LOG_PREFIX`` / ``_DEFAULT_LOG_BASENAME``
    class attributes, and fill the divergent seams ``_stand_up`` (the
    ``HardenedCommand`` recipe + networks), ``_config`` (the yielded config), and
    ``_teardown_networks``; ``_pre_teardown`` is an optional log-cleanup hook whose
    default sweeps a managed temp dir. Everything else — the context-manager
    protocol, the readiness probe, container removal, and log fetch — is inherited,
    so a new sidecar gets the fail-loud lifecycle by construction.
    """

    # --- subclass-declared identity ------------------------------------------
    port: ClassVar[int]
    _ERROR_CLS: ClassVar[type[Exception]]
    _NOUN: ClassVar[str]
    _DAEMON_UNAVAILABLE_MESSAGE: ClassVar[str]
    _LOG_PREFIX: ClassVar[str]
    _DEFAULT_LOG_BASENAME: ClassVar[str]
    # The ownership-label value (the sidecar kind: "metering-proxy" / "otlp-collector"),
    # stamped as ``--label {SIDECAR_LABEL}=<_LABEL_VALUE>`` and filtered on by the
    # label sweep. A dedicated attribute — never parsed from ``_NOUN`` prose.
    _LABEL_VALUE: ClassVar[str]

    def __init__(
        self,
        *,
        log_path: Optional[Path],
        image: str,
        docker: Optional[DockerClient],
        name: str,
    ) -> None:
        self._image = image
        self._docker = docker or DockerClient()
        self._name = name
        self._ready_probe = _READY_PROBE_TEMPLATE % self.port
        # Resolve where the JSONL log lands. An explicit path is honored as-is: its
        # parent dir is the mount AND its basename rides into the container via the
        # subclass's log-env token, so the sidecar writes the operator's exact
        # filename — a custom basename must never fall open as a touched-but-empty
        # log beside the default (the 988af58 PROXY_LOG lesson). An absent path gets
        # a managed temp dir removed on teardown.
        self._owns_logdir = log_path is None
        if log_path is None:
            self._logdir = Path(tempfile.mkdtemp(prefix=self._LOG_PREFIX))
            self._logfile = self._logdir / self._DEFAULT_LOG_BASENAME
        else:
            self._logfile = Path(log_path)
            self._logdir = self._logfile.parent

    @property
    def name(self) -> str:
        """This instance's container name (default: the subclass constant prefix +
        a unique per-instance suffix). Read-only — the identity a caller needs for
        exact teardown or an in-network address, without reaching into ``_name``."""
        return self._name

    # --- context manager ------------------------------------------------------
    def __enter__(self):
        try:
            return self.start()
        except BaseException:
            # A partial stand-up (networks made, container crashed) must not leak.
            self.stop()
            raise

    def __exit__(self, *exc) -> None:
        self.stop()

    # --- lifecycle template ---------------------------------------------------
    def start(self):
        """Stand the sidecar up and return the subclass's config [refactor 11 §G2].

        Daemon-or-refuse, provision + pre-create the log so a zero-traffic trial
        still finds a configured, present log, remove any stale same-named
        container, run the subclass recipe, wait for readiness by *probing*, and
        yield the config."""
        if not self._docker.daemon_available():
            raise self._ERROR_CLS(self._DAEMON_UNAVAILABLE_MESSAGE)
        self._logdir.mkdir(parents=True, exist_ok=True)
        self._logfile.touch(exist_ok=True)
        # Self-heal an explicit-name reuse: with instance-unique default names a
        # collision is only ever with a crashed PRIOR run of this SAME name (an
        # operator-passed name, or a re-``start`` of one object), never another live
        # sidecar — so this sweep can no longer remove a name it does not own.
        self._remove_container()
        self._stand_up()
        self._await_ready()
        return self._config()

    def stop(self) -> None:
        """Tear the sidecar down; always safe to call (idempotent, loud-free)."""
        self._remove_container()
        self._teardown_networks()
        self._pre_teardown()

    # --- subclass seams -------------------------------------------------------
    def _stand_up(self) -> None:
        """Create the sidecar's network(s) and run its container — the divergent
        ``HardenedCommand`` recipe (proxy: dual-network + CONNECT allowlist;
        collector: metered-only). Raises the subclass error on a nonzero run."""
        raise NotImplementedError

    def _config(self):
        """Build the config ``start`` yields (``ProxyConfig`` / ``CollectorConfig``)."""
        raise NotImplementedError

    def _teardown_networks(self) -> None:
        """Remove the network(s) this sidecar created (proxy: egress + metered;
        collector: metered only — it must never name egress)."""
        raise NotImplementedError

    def _pre_teardown(self) -> None:
        """Post-container log cleanup. Default: sweep a managed temp dir. The
        collector overrides this with the D-09-1 delete of the raw envelope log
        (even an operator-provided path, unless ``keep_raw``)."""
        if self._owns_logdir:
            shutil.rmtree(self._logdir, ignore_errors=True)

    # --- shared internals -----------------------------------------------------
    def _await_ready(self) -> None:
        """Probe the sidecar's port from inside the container until it accepts,
        bounded by the exec timeout — a sidecar that never binds fails loudly with
        its container logs, never a fixed timer guessing it up [refactor 11 §G2]."""
        try:
            proc = self._docker.run(
                ["docker", "exec", self._name, "sh", "-c", self._ready_probe],
                timeout_s=_READINESS_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as e:
            raise self._ERROR_CLS(
                f"{self._NOUN} {self._name!r} did not accept connections within "
                f"{_READINESS_TIMEOUT_S}s:\n{self._container_logs()}"
            ) from e
        if proc.returncode != 0:
            raise self._ERROR_CLS(
                f"{self._NOUN} {self._name!r} failed to become ready "
                f"(exit {proc.returncode}):\n{self._container_logs()}"
            )

    def _remove_container(self) -> None:
        remove_managed_container(self._name, self._docker)

    def _container_logs(self) -> str:
        try:
            proc = self._docker.run(["docker", "logs", self._name], timeout_s=15)
            return ((proc.stdout or "") + (proc.stderr or "")).strip() or "<no logs>"
        except (OSError, subprocess.SubprocessError):
            return "<logs unavailable>"
