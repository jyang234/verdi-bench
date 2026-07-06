"""The shared throwaway-grading-container discipline [refactor 05 §2].

The audit found this discipline triple-copied across ``grade/container.py``: the
holdout path and the plugin path each re-implemented the fresh-copy + stale
removal AND the ``docker run`` exit-code classification. Both are the *same*
fail-closed protection over an untrusted, agent-controlled workspace, so they
live here once and both paths call in.

- :func:`fresh_workspace_copy` — copy the agent's final workspace to a throwaway
  dir with the grader's own results file removed, so the container produces its
  own output and can never mutate the ledgered trial evidence [GR-1/GR-3].
- :func:`run_grading_container` — run a hardened grading argv through the
  hermetic :class:`~harness.hermetic.docker.DockerClient` and map its exit into
  the grade error vocabulary: a grader that *could not be run* (OS error / exit
  125) is a transient :class:`GraderUnavailableError`; a grader that ran and
  *failed* (any other nonzero exit) is a terminal :class:`GradingContainerError`
  [GR-2/GR-11]. Distinguishing the two is what keeps a docker outage retryable
  while a broken grader is not re-attempted forever.

Contains **no LLM client** — grading's determinism is its authority (the
``grade-has-no-llm-clients`` import contract). The docker argv itself is built by
the hermetic layer ([refactor 04 §1]); this module never spells a ``docker`` flag.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..hermetic.docker import DAEMON_ERROR_EXIT, DockerClient
from .fence import GraderUnavailableError, GradingContainerError

# Wall-clock ceiling on a single grading container [GR-2]. A grade that hangs
# must not stall the batch; the same bound both paths (holdout + plugin) used.
GRADE_TIMEOUT_S = 1800


def _remove_stale(entry: Path) -> None:
    """Delete a pre-existing grader-results entry of ANY type from the copy.

    The workspace is agent-controlled, so the results name may be a file, a
    symlink, or a directory planted to break a naive ``unlink`` (SEC5). Removing
    it stops an agent-written results file from masquerading as grader output
    [GR-1]."""
    if entry.is_symlink() or entry.is_file():
        entry.unlink()
    elif entry.is_dir():
        shutil.rmtree(entry)


@contextmanager
def fresh_workspace_copy(
    workspace, *, stale_name: str, prefix: str, purpose: str = ""
) -> Iterator[Path]:
    """Grade a throwaway copy of ``workspace`` with ``stale_name`` removed.

    The copy protects the ledgered trial evidence from a rw container mount
    [GR-3], and deleting any pre-existing results file in the copy stops an
    agent-written results file from masquerading as grader output [GR-1]. The
    original workspace is never mounted.

    The workspace is agent-controlled, so preparation is hardened: symlinks are
    copied as links (never followed — no escape/disk-exhaustion), and a results
    entry of any type (file/dir/symlink) is removed. Any preparation error
    becomes a :class:`GradingContainerError` so a hostile workspace fails *this*
    trial closed rather than aborting the whole grade batch [SEC].

    Yields the workspace copy (``<tmp>/workspace``); a caller that needs a
    sibling mount (e.g. the plugin path's ``task.json``) writes it beside the
    copy under ``copy.parent``. The throwaway tree is always removed on exit —
    best-effort, so a cleanup error never clobbers an already-computed grade.
    """
    tmp = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        copy = tmp / "workspace"
        try:
            shutil.copytree(workspace, copy, symlinks=True)
            _remove_stale(copy / stale_name)
        except OSError as e:
            raise GradingContainerError(
                f"could not prepare a clean workspace copy{purpose}: {e}"
            ) from e
        yield copy
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_grading_container(
    docker: DockerClient, cmd: list[str], *, noun: str
) -> subprocess.CompletedProcess:
    """Run a hardened grading argv and classify its exit fail-closed [GR-2/GR-11].

    Returns the completed process on exit 0 (the caller then scores ONLY the
    fenced stdout — never a file from the agent-writable copy). Otherwise raises:

    - :class:`GraderUnavailableError` (transient, regradeable) when the grader
      *could not be run at all* — an OS/subprocess error, or docker's exit 125
      (a daemon/config error *before* the container runs).
    - :class:`GradingContainerError` (terminal) when the grader RAN and exited
      nonzero — not that holdout tests failed (those are per-assertion in the
      fenced results at exit 0), so refuse rather than scoring a stale/partial
      workspace, and do not retry a deterministic failure forever.

    ``noun`` names the container in the terminal message (``grader``/``plugin``).
    """
    try:
        proc = docker.run(cmd, timeout_s=GRADE_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as e:
        # Could not run the grader at all — transient infra failure [GR-11].
        raise GraderUnavailableError(str(e)) from e
    # exit 125 is a docker daemon/config error (grader never ran) → transient.
    if proc.returncode == DAEMON_ERROR_EXIT:
        raise GraderUnavailableError("docker daemon/config error (exit 125)")
    if proc.returncode != 0:
        detail = proc.stderr.strip()
        raise GradingContainerError(
            f"{noun} container exited {proc.returncode}"
            + (f": {detail}" if detail else "")
        )
    return proc
