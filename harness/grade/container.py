"""Grading-container launcher [EVAL-5 §M1].

A thin, **network-less** specialization of EVAL-4's container plumbing. Trial
containers are never reused — grading runs in a fresh container per trial with a
copy of the trial's final workspace and the holdouts bind-mounted **read-only**.

Like the Harbor engine, the docker-run command is built purely (unit-testable);
daemon calls sit behind an injectable runner so the network/readonly assertions
can be checked without a live daemon (true container-inspect is docker-marked).
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
from pathlib import Path
from typing import Optional, Protocol

from ..hermetic.docker import DockerClient, HardenedCommand

# The grader image. Configurable via env so a deployment pins a real digest
# (``verdi-bench/grader@sha256:…``); the default tag is a placeholder a real
# install overrides — never the all-zeros non-digest it used to be [GR-4].
DEFAULT_GRADER_IMAGE = "verdi-bench/grader:latest"

# Filename the LOCAL (no-daemon, ADVISORY) runner reads its results from inside
# the workspace. The docker path no longer reads any workspace file [F-H1] —
# results ride the fenced stdout transport below — but any pre-existing copy is
# still removed from the fresh grading copy [GR-1], and the name stays excluded
# from the judge/scanner solution walks.
HOLDOUT_RESULTS = "holdout_results.json"

# The versioned fenced-stdout grader transport now lives in ``grade/fence.py``
# [refactor 05 §2] — the one owner of the FROZEN tag/nonce/output-shape bytes.
# These names are imported (and re-exported by this façade for back-compat) so
# the runner + orchestration code below reads them unchanged.
from .fence import (  # noqa: F401 — re-exported for external importers + tests
    NONCE_ENV,
    PLUGIN_FENCE_BEGIN,
    PLUGIN_FENCE_END,
    RESULTS_FENCE_BEGIN,
    RESULTS_FENCE_END,
    GraderUnavailableError,
    GradingContainerError,
    HoldoutRun,
    _extract_fence,
    holdout_fence,
    parse_fenced_stdout,
    plugin_fence,
)
from .isolation import fresh_workspace_copy, run_grading_container

# Filename the containerized plugin runner writes its assertion list to [PRA-M6].
# Same fresh-copy discipline: any agent-written copy is removed before the run.
PLUGIN_RESULTS = "plugin_results.json"


class GradeRunner(Protocol):
    def run_holdouts(
        self, cmd: list[str], workspace: Path, holdouts_dir: str, nonce: Optional[str] = None
    ) -> HoldoutRun: ...


class DockerGradeRunner:
    """Runs holdouts in a fresh network-less container via the docker CLI.

    Uses :class:`GradingContainer`'s default (safe) path: a throwaway copy of the
    trial workspace with any pre-existing results file removed, so the container
    produces its own output and cannot mutate ledgered evidence [GR-1/GR-3].
    """

    grader_name = "docker"
    runs_plugins_in_container = True  # PRA-M6: plugins run network-less, not in-process

    def __init__(self, docker: Optional[DockerClient] = None) -> None:
        self._docker = docker or DockerClient()

    def preflight(self) -> None:
        """Probe the docker daemon before a grade batch [GR-8/GR-11].

        A down daemon makes ``docker run`` exit 1 — indistinguishable from a
        grader that ran and failed — so a single outage would otherwise
        quarantine healthy task versions with *terminal* container_failure
        events. Probing ``docker version`` (through the hermetic DockerClient) up
        front classifies daemon-down as a transient
        :class:`GraderUnavailableError` before any grading is attempted. A
        daemon/OS/config error or nonzero exit fails the probe."""
        try:
            proc = self._docker.run(["docker", "version"], timeout_s=30)
        except (OSError, subprocess.SubprocessError) as e:
            raise GraderUnavailableError(f"docker daemon probe failed: {e}") from e
        if proc.returncode != 0:
            detail = proc.stderr.strip()
            raise GraderUnavailableError(
                f"docker daemon unavailable (docker version exit {proc.returncode})"
                + (f": {detail}" if detail else "")
            )

    def run_holdouts(
        self, cmd: list[str], workspace: Path, holdouts_dir: str, nonce: Optional[str] = None
    ) -> HoldoutRun:
        # Run the grader + classify its exit through the shared isolation helper
        # (transient grader_unavailable vs terminal container_failure) [GR-2/GR-11].
        proc = run_grading_container(self._docker, cmd, noun="grader")
        # F-H1: score ONLY the fenced stdout channel — never a file from the
        # agent-writable /workspace, which agent code executing at grade time
        # can rewrite after the grader does. The per-grade nonce authenticates
        # the fence so an agent-forged block on the same stdout is rejected.
        # Malformed output still flows to cant_grade(malformed) [GR-6].
        return parse_fenced_stdout(proc.stdout, proc.returncode, nonce=nonce)


class LocalGradeRunner:
    """No-daemon runner: reads a pre-placed ``holdout_results.json`` from the
    workspace. Used by the fake/end-to-end path so grading is exercisable without
    Docker (the real DockerGradeRunner is docker-marked).

    ``grades_in_place`` opts out of the fresh-copy isolation: it must read the
    pre-placed file in the original workspace. It is a read-only path (it never
    mounts or writes the workspace), so it does not mutate evidence. Because it
    scores a file the harness/agent placed rather than a container's own output,
    its grades are stamped ``grader_name = "local"`` (ADVISORY) so they are
    distinguishable from a trusted container grade.
    """

    grades_in_place = True
    grader_name = "local"

    def preflight(self) -> None:
        """No daemon to probe — the no-daemon path is always available."""

    def run_holdouts(
        self, cmd: list[str], workspace: Path, holdouts_dir: str, nonce: Optional[str] = None
    ) -> HoldoutRun:
        # nonce is unused: this path reads a pre-placed file, not a fenced
        # stdout channel, and is ADVISORY (grader_name="local") by construction.
        results = Path(workspace) / HOLDOUT_RESULTS
        if not results.exists():
            raise GradingContainerError("no holdout_results.json in workspace")
        try:
            return HoldoutRun(json.loads(results.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            # surface as a HoldoutRun with a marker so the parser flags malformed
            return HoldoutRun({"__malformed__": True})


class LocalExecutingGradeRunner:
    """No-daemon runner that EXECUTES a declared holdout [refactor 05 §1].

    Where :class:`LocalGradeRunner` *reads* a pre-placed ``holdout_results.json``,
    this runner *runs* the declared :class:`~harness.grade.holdouts.Holdout` from
    ``holdouts_dir`` in a host subprocess — no Docker required — and packs the
    executed assertions into the same wire shape the deterministic parser reads.

    It executes on the host with no container isolation and scores the harness's
    own execution rather than a trusted grader image, so its ``grader_name`` is
    the non-``"docker"`` ``"local-exec"`` — analyze already banners such a grade
    ADVISORY with zero code change (``analyze/report.py`` keys advisory on a
    ``grader`` field present and ≠ ``"docker"``).

    ``grades_in_place`` is True: it runs the holdout against the workspace it is
    given (the fresh-copy discipline is docker's evidence protection; this host
    path only READS the workspace — a nonce-authenticated fenced channel is not
    involved — so it opts out like :class:`LocalGradeRunner`, and no per-grade
    nonce is minted).
    """

    grades_in_place = True
    grader_name = "local-exec"

    def preflight(self) -> None:
        """No daemon to probe — the no-daemon path is always available."""

    def run_holdouts(
        self, cmd: list[str], workspace: Path, holdouts_dir: str, nonce: Optional[str] = None
    ) -> HoldoutRun:
        # Lazy import: holdouts.py imports NONCE_ENV from THIS module, so a
        # top-level import here would be a cycle (mirrors run_plugins' lazy
        # ``from .plugins import get_plugin``).
        from .holdouts import assertions_to_raw, load_declared_holdout

        holdout = load_declared_holdout(holdouts_dir)
        if holdout is None:
            # A holdout.json with no ``kind`` (or no file) is opaque/bespoke — it
            # needs a benchmark grader image (--runner docker), not this generic
            # executor. Fail closed rather than silently score nothing [GR-6].
            raise GradingContainerError(
                f"no declared holdout (holdout.json with a 'kind') in {holdouts_dir!r}; "
                "the local-exec runner executes declared holdouts — an opaque/bespoke "
                "holdout is graded by its own image via --runner docker"
            )
        return HoldoutRun(assertions_to_raw(holdout.execute(workspace)))


class GradingContainer:
    def __init__(
        self, runner: Optional[GradeRunner] = None, *, image: Optional[str] = None,
        docker: Optional[DockerClient] = None,
    ):
        self._runner = runner or DockerGradeRunner()
        self._image = image or os.environ.get("VERDI_GRADER_IMAGE", DEFAULT_GRADER_IMAGE)
        # The plugin-container run's own docker mechanic [refactor 04 §1]; the
        # holdout path runs through the injected GradeRunner instead.
        self._docker = docker or DockerClient()

    def preflight(self) -> None:
        """Delegate the daemon probe to the runner (no-op for daemon-less runners).

        Called once at the start of a grade batch [GR-8/GR-11]. A runner without
        a ``preflight`` is treated as always-available."""
        probe = getattr(self._runner, "preflight", None)
        if probe is not None:
            probe()

    def build_grade_command(
        self, workspace: Path, holdouts_dir: str, nonce: Optional[str] = None
    ) -> list[str]:
        """Fresh, network-less container; holdouts read-only; results on stdout.

        Hardened to parity with the plugin command [F-H1]: capabilities
        dropped, no privilege escalation, and non-root — the grader writes
        nothing the host reads (results ride the fenced stdout transport), so
        the container needs no privileged authority over /workspace.

        ``nonce`` (present on the production path) is injected as
        ``VERDI_FENCE_NONCE`` so the grader can stamp it into its fence marker
        and the host can authenticate the channel [F-H1 follow-up]."""
        # Same shared hardened recipe as harbor's trial [refactor 04 §1], minus the
        # quotas/pull-pin: a fresh, network-less container with caps dropped, no
        # privilege escalation, non-root [F-H1] — the grader writes nothing the host
        # reads (results ride the fenced stdout transport).
        hc = HardenedCommand().rm().network("none").harden()
        if nonce:
            hc.e_env(NONCE_ENV, nonce)
        hc.user()
        hc.volume(workspace, "/workspace")
        if holdouts_dir:
            # holdouts bind-mounted READ-ONLY [AC-1]
            hc.volume(holdouts_dir, "/holdouts", ro=True)
        hc.workdir("/workspace").image(self._image)
        return hc.build()

    def build_plugin_command(
        self,
        workspace: Path,
        plugin_ids: list,
        task_file: Optional[Path] = None,
        nonce: Optional[str] = None,
    ) -> list[str]:
        """Fresh, NETWORK-LESS container for grader plugins [PRA-M6].

        Plugins previously ran in-process on the harness host — with full network
        and filesystem access — against the agent-controlled workspace; a plugin
        that shells out over malicious build/config files was a host-side
        code-execution surface the holdout tier does not have. Running them under
        the same ``--network none`` fresh-copy discipline as holdouts (plus
        ``--cap-drop ALL`` and no-new-privileges) closes that asymmetry. The
        grader image's plugin entrypoint (``python -m harness.grade.run_plugin``)
        reads the ids and the read-only task mount, and writes PLUGIN_RESULTS."""
        # Same network-less hardened recipe as the holdout grader (no --user: the
        # plugin entrypoint keeps its prior identity) [PRA-M6, refactor 04 §1].
        hc = HardenedCommand().rm().network("none").harden()
        if nonce:
            hc.e_env(NONCE_ENV, nonce)
        hc.volume(workspace, "/workspace")
        if task_file is not None:
            hc.volume(task_file, "/verdi/task.json", ro=True)
        hc.workdir("/workspace").image(self._image)
        hc.arg("python", "-m", "harness.grade.run_plugin", *[str(p) for p in plugin_ids])
        return hc.build()

    @property
    def grader_name(self) -> str:
        """Identity of the grader used, recorded in the grade event so a local
        (ADVISORY) grade is distinguishable from a trusted container grade [SEC]."""
        return getattr(self._runner, "grader_name", "unknown")

    def run_plugins(self, workspace: Path, plugin_ids: list, task) -> list:
        """Run declared grader plugins and return their assertions [PRA-M6].

        Docker path: plugins run in a fresh-copy, ``--network none`` container
        (the same isolation holdouts get), so a plugin cannot reach the network or
        the host. No-daemon LocalGradeRunner path: plugins run in-process — an
        explicit ADVISORY fallback with no sandbox, used only for the fake/test
        path and distinguishable by ``grader_name = "local"``.
        """
        from .plugins import get_plugin
        from .types import Assertion

        if not plugin_ids:
            return []
        if getattr(self._runner, "runs_plugins_in_container", False):
            # docker path: network-less container over a throwaway copy.
            return self._run_plugins_in_container(Path(workspace), plugin_ids, task)
        # no-daemon ADVISORY path (LocalGradeRunner / test fakes): in-process, no
        # isolation — used only where there is no daemon; grades are stamped
        # grader_name="local" so they are distinguishable from a trusted grade.
        out: list = []
        for pid in plugin_ids:
            out.extend(get_plugin(pid).grade(Path(workspace), task))
        return out

    def _run_plugins_in_container(self, workspace: Path, plugin_ids: list, task) -> list:
        from .types import Assertion

        # Same fresh-copy + exit-classification discipline as the holdout path,
        # via the shared isolation helpers [refactor 05 §2].
        with fresh_workspace_copy(
            workspace, stale_name=PLUGIN_RESULTS, prefix="verdi-plugin-",
            purpose=" for plugins",
        ) as copy:
            # the GradeTask travels into the container read-only at /verdi/task.json,
            # written beside the workspace copy under the same throwaway tree.
            task_file = copy.parent / "task.json"
            task_file.write_text(json.dumps({
                "id": getattr(task, "id", "t"),
                "task_sha": getattr(task, "task_sha", ""),
                "holdouts_dir": getattr(task, "holdouts_dir", ""),
                "fake_plugin_output": getattr(task, "fake_plugin_output", {}) or {},
            }), encoding="utf-8")
            # Per-grade nonce authenticates the plugin fence too [F-H1 follow-up].
            nonce = secrets.token_hex(16)
            cmd = self.build_plugin_command(copy, plugin_ids, task_file, nonce)
            proc = run_grading_container(self._docker, cmd, noun="plugin")
            # F-H1 A.4: same trusted channel as holdouts — never a file from the
            # agent-writable copy, and nonce-authenticated so an agent-forged
            # block is rejected. Every failure mode is terminal here (it flows
            # to cant_grade(plugin_error) in grade_trial), so no marker needed.
            pbegin, pend = plugin_fence(nonce)
            status, body = _extract_fence(proc.stdout, pbegin, pend)
            if status == "absent":
                raise GradingContainerError(
                    "plugin container emitted no fenced results on stdout (an "
                    "image predating the V1 transport must be rebuilt)"
                )
            if status != "ok":
                raise GradingContainerError(
                    "ambiguous plugin results channel: multiple or inverted fences"
                )
            try:
                raw = json.loads(body or "")
            except json.JSONDecodeError as e:
                raise GradingContainerError(f"malformed fenced plugin results: {e}") from e
            return [Assertion(**a) for a in raw]

    def run(self, workspace: Path, holdouts_dir: str) -> HoldoutRun:
        workspace = Path(workspace)
        # Fail SAFE: grade a throwaway copy by DEFAULT. Only a runner that must
        # read a pre-placed file in the original workspace (the no-daemon
        # LocalGradeRunner) opts out via ``grades_in_place`` — a future container
        # runner therefore cannot silently lose evidence protection [GR-1/GR-3].
        if getattr(self._runner, "grades_in_place", False):
            # Local/ADVISORY path reads a pre-placed file — no container, no
            # fenced channel, so no nonce.
            cmd = self.build_grade_command(workspace, holdouts_dir)
            return self._runner.run_holdouts(cmd, workspace, holdouts_dir, None)
        # A fresh, unpredictable per-grade nonce authenticates the fenced stdout
        # channel [F-H1 follow-up]. It MUST be unpredictable (an agent that could
        # predict it could forge the fence), so it is drawn from the OS CSPRNG,
        # not a seed — an unpredictability seam, like the uuid4 trial id. It is
        # ephemeral transport auth: it never enters a ledgered or rendered field,
        # so determinism of every graded/recorded output is unaffected.
        nonce = secrets.token_hex(16)
        return self._run_on_fresh_copy(workspace, holdouts_dir, nonce)

    def _run_on_fresh_copy(
        self, workspace: Path, holdouts_dir: str, nonce: Optional[str] = None
    ) -> HoldoutRun:
        """Grade a throwaway copy of the workspace [GR-1/GR-3].

        The fresh-copy discipline (evidence protection, symlink no-follow, and
        stale-results removal) lives in the shared
        :func:`~harness.grade.isolation.fresh_workspace_copy`; this builds the
        grade command against the copy and hands it to the runner.
        """
        with fresh_workspace_copy(
            workspace, stale_name=HOLDOUT_RESULTS, prefix="verdi-grade-",
        ) as copy:
            cmd = self.build_grade_command(copy, holdouts_dir, nonce)
            return self._runner.run_holdouts(cmd, copy, holdouts_dir, nonce)
