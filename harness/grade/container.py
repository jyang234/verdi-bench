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
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

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

# The docker grader's results transport [F-H1]: the image entrypoint prints
# exactly ONE fenced JSON document on stdout, and the host scores nothing from
# the agent-writable /workspace — an agent atexit hook or background thread
# that rewrites a results file after the grader wrote it forges nothing.
# Versioned sentinels: a transport change bumps the version so old and new
# images are never silently cross-read.
#
# V1→V2 [F-H1 follow-up]: the fence is now NONCE-AUTHENTICATED. The host mints a
# fresh, unpredictable per-grade nonce, injects it into the container as
# ``VERDI_FENCE_NONCE``, and accepts only a fence whose marker carries exactly
# that nonce. This closes the residual the plain stdout transport left open:
# because agent code runs in the SAME container as the grader, it can write to
# the captured stdout too, so a well-timed agent could suppress the grader's
# genuine fence (e.g. redirect fd 1) and print its own single forged block —
# and the old static, publicly-known markers made that block indistinguishable
# from a real one. A nonce the agent cannot read defeats that: a forged marker
# carries the wrong nonce (or none), so it never matches and the channel reads
# absent/ambiguous → fail-closed. The nonce is only secret if the grader image
# scrubs ``VERDI_FENCE_NONCE`` from the environment of any agent-executing
# subprocess (holdout tests import the solution); that grader-image discipline
# is the other half of this defense and is documented in docs/deep-dive.md §2.4.
# The version bump forces a clear fail-closed migration: a pre-V2 image emits
# markers the host never matches and gets the "rebuild your grader image"
# refusal below, rather than a silent cross-read.
NONCE_ENV = "VERDI_FENCE_NONCE"
_HOLDOUT_TAG = "VERDI_HOLDOUT_RESULTS_V2"
# Distinct tag for containerized plugin graders [F-H1 A.4] — plugins execute
# rules over the agent-controlled workspace, so their results were equally
# forgeable in-run. A holdout block can never be mistaken for a plugin block.
_PLUGIN_TAG = "VERDI_PLUGIN_RESULTS_V2"


def _fence_pair(tag: str, nonce: Optional[str]) -> tuple[str, str]:
    """The (begin, end) marker pair for ``tag``, optionally nonce-authenticated.

    With a nonce, the token is bracketed between ``:`` and the trailing dashes
    (``…_BEGIN:<nonce>-----``) so a longer forged guess cannot prefix-match the
    expected marker under ``str.count``. Without a nonce the bare markers are
    used — the local (no-daemon, ADVISORY) path and direct parser unit tests,
    neither of which runs an untrusted container.
    """
    suffix = f":{nonce}" if nonce else ""
    return (f"-----{tag}_BEGIN{suffix}-----", f"-----{tag}_END{suffix}-----")


def holdout_fence(nonce: Optional[str] = None) -> tuple[str, str]:
    """The holdout results (begin, end) markers for a given per-grade nonce."""
    return _fence_pair(_HOLDOUT_TAG, nonce)


def plugin_fence(nonce: Optional[str] = None) -> tuple[str, str]:
    """The plugin results (begin, end) markers for a given per-grade nonce."""
    return _fence_pair(_PLUGIN_TAG, nonce)


# Bare (un-nonced) markers, kept as module constants for the local path and for
# tests that exercise the parser directly.
RESULTS_FENCE_BEGIN, RESULTS_FENCE_END = holdout_fence(None)
PLUGIN_FENCE_BEGIN, PLUGIN_FENCE_END = plugin_fence(None)

# Filename the containerized plugin runner writes its assertion list to [PRA-M6].
# Same fresh-copy discipline: any agent-written copy is removed before the run.
PLUGIN_RESULTS = "plugin_results.json"


class GradingContainerError(RuntimeError):
    """The grader ran but failed (nonzero exit, no results) → a **terminal**
    cant_grade(container_failure): re-running won't change the outcome.

    "The grader ran" is a real precondition, not a given: a *down* daemon makes
    ``docker run`` exit 1 without the grader ever running, which would be
    misclassified here as terminal. The pre-flight daemon probe (``preflight``)
    catches that case up front and routes it to the transient
    :class:`GraderUnavailableError` instead [GR-8/GR-11]."""


class GraderUnavailableError(GradingContainerError):
    """The grader could not be run at all — daemon/config/OS error or exit 125 →
    a **transient** cant_grade(grader_unavailable) that a later attempt may
    resolve [GR-11]. Subclass of GradingContainerError so callers that don't care
    still catch it."""


@dataclass
class HoldoutRun:
    raw_output: dict
    exit_status: int = 0


class GradeRunner(Protocol):
    def run_holdouts(
        self, cmd: list[str], workspace: Path, holdouts_dir: str, nonce: Optional[str] = None
    ) -> HoldoutRun: ...


def _extract_fence(stdout: str, begin: str, end: str) -> tuple[str, Optional[str]]:
    """One fenced body from container stdout: ``("ok", body)``, or a fail-closed
    status — ``"absent"`` (no fence at all) / ``"ambiguous"`` (more than one
    fence, e.g. agent code printing its own forged block, or inverted markers).
    """
    begins, ends = stdout.count(begin), stdout.count(end)
    if begins == 0 and ends == 0:
        return "absent", None
    if begins != 1 or ends != 1:
        return "ambiguous", None
    start = stdout.index(begin) + len(begin)
    stop = stdout.index(end)
    if stop < start:
        return "ambiguous", None
    return "ok", stdout[start:stop]


def parse_fenced_stdout(
    stdout: str, exit_status: int = 0, *, nonce: Optional[str] = None
) -> HoldoutRun:
    """Extract the grader's fenced holdout results from stdout [F-H1].

    Fail-closed by construction: zero fences → the grader produced no results
    (terminal ``container_failure``, the old missing-file outcome); an
    ambiguous channel or unparseable JSON inside the fence → the malformed
    marker, so the parser flags ``cant_grade(malformed_holdout_output)``. An
    ambiguous channel is never scored.

    ``nonce`` authenticates the fence [F-H1 follow-up]: only a marker carrying
    the per-grade nonce is recognized, so a forged block written by agent code
    that cannot read the nonce reads as absent (wrong/no nonce) rather than
    being scored. ``None`` uses the bare markers (local/ADVISORY path and
    direct parser tests).
    """
    begin, end = holdout_fence(nonce)
    status, body = _extract_fence(stdout, begin, end)
    if status == "absent":
        raise GradingContainerError(
            "grader emitted no fenced holdout results on stdout (expected one "
            f"{RESULTS_FENCE_BEGIN!r} block — a grader image predating the V1 "
            "stdout transport must be rebuilt; see docs/usage-guide.md)"
        )
    if status != "ok":
        return HoldoutRun({"__malformed__": True}, exit_status)
    try:
        raw = json.loads(body or "")
    except json.JSONDecodeError:
        return HoldoutRun({"__malformed__": True}, exit_status)
    return HoldoutRun(raw, exit_status)


class DockerGradeRunner:
    """Runs holdouts in a fresh network-less container via the docker CLI.

    Uses :class:`GradingContainer`'s default (safe) path: a throwaway copy of the
    trial workspace with any pre-existing results file removed, so the container
    produces its own output and cannot mutate ledgered evidence [GR-1/GR-3].
    """

    grader_name = "docker"
    runs_plugins_in_container = True  # PRA-M6: plugins run network-less, not in-process

    def preflight(self) -> None:
        """Probe the docker daemon before a grade batch [GR-8/GR-11].

        A down daemon makes ``docker run`` exit 1 — indistinguishable from a
        grader that ran and failed — so a single outage would otherwise
        quarantine healthy task versions with *terminal* container_failure
        events. Probing ``docker version`` up front classifies daemon-down as a
        transient :class:`GraderUnavailableError` before any grading is
        attempted. A daemon/OS/config error or nonzero exit fails the probe."""
        try:
            proc = subprocess.run(
                ["docker", "version"], capture_output=True, text=True, timeout=30
            )
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
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        except (OSError, subprocess.SubprocessError) as e:
            # Could not run the grader at all — transient infra failure [GR-11].
            raise GraderUnavailableError(str(e)) from e
        # exit 125 is a docker daemon/config error (grader never ran) → transient.
        if proc.returncode == 125:
            raise GraderUnavailableError("docker daemon/config error (exit 125)")
        # Any other nonzero exit means the grader RAN and failed — not that holdout
        # tests failed (those are per-assertion in the results file at exit 0).
        # Terminal: refuse rather than scoring a stale/partial workspace file, and
        # do not retry a deterministic failure forever [GR-2/GR-11].
        if proc.returncode != 0:
            detail = proc.stderr.strip()
            raise GradingContainerError(
                f"grader container exited {proc.returncode}"
                + (f": {detail}" if detail else "")
            )
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


class GradingContainer:
    def __init__(self, runner: Optional[GradeRunner] = None, *, image: Optional[str] = None):
        self._runner = runner or DockerGradeRunner()
        self._image = image or os.environ.get("VERDI_GRADER_IMAGE", DEFAULT_GRADER_IMAGE)

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
        cmd = ["docker", "run", "--rm", "--network", "none"]
        cmd += ["--cap-drop", "ALL", "--security-opt", "no-new-privileges"]
        if nonce:
            cmd += ["-e", f"{NONCE_ENV}={nonce}"]
        if hasattr(os, "getuid"):
            cmd += ["--user", f"{os.getuid()}:{os.getgid()}"]
        cmd += ["--volume", f"{Path(workspace).resolve()}:/workspace"]
        if holdouts_dir:
            # holdouts bind-mounted READ-ONLY [AC-1]
            cmd += ["--volume", f"{Path(holdouts_dir).resolve()}:/holdouts:ro"]
        cmd += ["--workdir", "/workspace", self._image]
        return cmd

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
        cmd = ["docker", "run", "--rm", "--network", "none"]
        cmd += ["--cap-drop", "ALL", "--security-opt", "no-new-privileges"]
        if nonce:
            cmd += ["-e", f"{NONCE_ENV}={nonce}"]
        cmd += ["--volume", f"{Path(workspace).resolve()}:/workspace"]
        if task_file is not None:
            cmd += ["--volume", f"{Path(task_file).resolve()}:/verdi/task.json:ro"]
        cmd += ["--workdir", "/workspace", self._image]
        cmd += ["python", "-m", "harness.grade.run_plugin", *[str(p) for p in plugin_ids]]
        return cmd

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

        tmp = Path(tempfile.mkdtemp(prefix="verdi-plugin-"))
        try:
            copy = tmp / "workspace"
            try:
                shutil.copytree(workspace, copy, symlinks=True)
                stale = copy / PLUGIN_RESULTS
                if stale.is_symlink() or stale.is_file():
                    stale.unlink()
                elif stale.is_dir():
                    shutil.rmtree(stale)
            except OSError as e:
                raise GradingContainerError(
                    f"could not prepare a clean workspace copy for plugins: {e}"
                ) from e
            # the GradeTask travels into the container read-only at /verdi/task.json
            task_file = tmp / "task.json"
            task_file.write_text(json.dumps({
                "id": getattr(task, "id", "t"),
                "task_sha": getattr(task, "task_sha", ""),
                "holdouts_dir": getattr(task, "holdouts_dir", ""),
                "fake_plugin_output": getattr(task, "fake_plugin_output", {}) or {},
            }), encoding="utf-8")
            # Per-grade nonce authenticates the plugin fence too [F-H1 follow-up].
            nonce = secrets.token_hex(16)
            cmd = self.build_plugin_command(copy, plugin_ids, task_file, nonce)
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            except (OSError, subprocess.SubprocessError) as e:
                raise GraderUnavailableError(str(e)) from e
            if proc.returncode == 125:
                raise GraderUnavailableError("docker daemon/config error (exit 125)")
            if proc.returncode != 0:
                raise GradingContainerError(
                    f"plugin container exited {proc.returncode}"
                    + (f": {proc.stderr.strip()}" if proc.stderr.strip() else "")
                )
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
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

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
        """Grade a throwaway copy of the workspace.

        The copy protects the ledgered trial evidence from a rw container mount
        [GR-3], and deleting any pre-existing results file in the copy stops an
        agent-written ``holdout_results.json`` from masquerading as grader
        output [GR-1]. The original workspace is never mounted.

        The workspace is agent-controlled, so preparation is hardened: symlinks
        are copied as links (never followed — no escape/disk-exhaustion), and a
        results entry of any type (file/dir/symlink) is removed. Any preparation
        error becomes a GradingContainerError so a hostile workspace fails *this*
        trial closed rather than aborting the whole grade batch [SEC].
        """
        tmp = Path(tempfile.mkdtemp(prefix="verdi-grade-"))
        try:
            copy = tmp / "workspace"
            try:
                shutil.copytree(workspace, copy, symlinks=True)
                stale = copy / HOLDOUT_RESULTS
                if stale.is_symlink() or stale.is_file():
                    stale.unlink()
                elif stale.is_dir():
                    shutil.rmtree(stale)
            except OSError as e:
                raise GradingContainerError(
                    f"could not prepare a clean workspace copy: {e}"
                ) from e
            cmd = self.build_grade_command(copy, holdouts_dir, nonce)
            return self._runner.run_holdouts(cmd, copy, holdouts_dir, nonce)
        finally:
            # Best-effort cleanup of the throwaway copy: a cleanup error must not
            # clobber an already-computed grade [determinism/fail-loudly intent].
            shutil.rmtree(tmp, ignore_errors=True)
