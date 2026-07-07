"""Subprocess mechanics for the real groundwork grader plugin [integration plan §3].

Splits the flowmap/groundwork shell-out — binary discovery, asset resolution,
branch-graph regeneration, and ``review --json`` parsing — out of the plugin's
verdict-mapping *policy* (:mod:`.groundwork`), so each has a single
responsibility: this module only RUNS the tools and returns the parsed review
artifact; the plugin decides how a verdict becomes an assertion.

Trust boundary [integration plan §2, decision D2]. The branch graph is
**regenerated from the workspace copy** here — an agent-supplied ``graph.json``
in the workspace is never consumed, because "graph integrity is the single point
of failure: an agent-supplied graph forges any verdict" (verdi-go doctrine). And
the policy + base graph are resolved **only from the read-only holdouts side**
(``/holdouts/groundwork/`` in the grade container, else ``task.holdouts_dir`` for
the local runner) — **NEVER from /workspace**, which is agent-authored: a
policy or base graph read from the workspace would let the graded party choose
its own grader inputs. :func:`resolve_assets` enforces this structurally (it only
ever looks under :func:`holdouts_root`), and a decoy planted in the workspace is
ignored by construction.

Error taxonomy — every failure RAISES (the plugin turns any exception into a
terminal ``cant_grade(plugin_error)`` upstream; a silent empty vector is never a
grade). :class:`GroundworkUnavailableError` for "could not run at all" (binary or
asset missing); :class:`GroundworkShellError` for a tool that ran and failed
(workspace won't type-check, ``groundwork`` operational exit 2, unexpected exit,
malformed JSON). Each carries precise detail so the terminal grade is diagnosable.

Contains **no LLM client** — grading's determinism is its authority (the
``grade-has-no-llm-clients`` import contract).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import NamedTuple, Optional

from ..fence import NONCE_ENV

# The read-only holdouts mount inside the grade container [integration plan §2].
# A module constant (not a literal) so a hermetic test can point it at a tmp dir
# to exercise the container-vs-local resolution branch without a real /holdouts.
CONTAINER_HOLDOUTS = Path("/holdouts")

# The per-task subdirectory the groundwork assets live under, on the holdouts
# side. ``/holdouts`` is already the per-task mount (DockerGradeRunner mounts
# ``task.holdouts_dir`` there), so assets are at ``<root>/groundwork/…``.
ASSETS_SUBDIR = "groundwork"
POLICY_NAME = "policy.json"
BASE_GRAPH_NAME = "base.graph.json"

# Binary discovery: PATH by default, env override for tests/local pinning.
FLOWMAP_BIN_ENV = "VERDI_FLOWMAP_BIN"
GROUNDWORK_BIN_ENV = "VERDI_GROUNDWORK_BIN"
FLOWMAP_DEFAULT = "flowmap"
GROUNDWORK_DEFAULT = "groundwork"

# Wall-clock ceilings. flowmap type-checks the whole module (seconds on the
# stdlib-only v0 corpus); groundwork consumes only JSON (sub-second). Bounds so a
# hang fails the grade rather than stalling the batch.
_FLOWMAP_TIMEOUT_S = 600
_GROUNDWORK_TIMEOUT_S = 120
# The `version` subcommand just prints a build stamp (no analysis, no I/O). A tight
# ceiling so a hung version probe degrades provenance to "unknown" quickly rather
# than stalling a grade whose verdict already computed.
_VERSION_TIMEOUT_S = 30

# groundwork's exit-code contract (cmd/groundwork/main.go): 0 clean, 1 a computed
# verdict failed the gate (BLOCK), 2 operational error. flowmap exits non-zero on
# any load/type-check failure.
_GROUNDWORK_OPERATIONAL_EXIT = 2


class GroundworkShellError(RuntimeError):
    """A real-path flowmap/groundwork invocation ran and failed operationally —
    the workspace would not type-check, ``groundwork`` returned its operational
    exit 2, an unexpected exit, or unparseable JSON. Flows to
    ``cant_grade(plugin_error)``; the message carries the precise cause."""


class GroundworkUnavailableError(GroundworkShellError):
    """The real groundwork tooling could not be run at all: a binary is missing
    from PATH / the env override, or a required grading asset (policy / base
    graph) is absent from the holdouts side. Fail closed — grading a task that
    declares the plugin must never silently no-op [F-M-O1]."""


def _resolve_binary(env_var: str, default_name: str) -> str:
    """The flowmap/groundwork binary: ``env_var`` override if set, else PATH.

    A set-but-missing override is an error (a misconfigured pin must fail loud,
    not silently fall back to PATH and grade with the wrong build)."""
    override = os.environ.get(env_var)
    if override:
        p = Path(override)
        if not p.is_file():
            raise GroundworkUnavailableError(
                f"{env_var}={override!r} does not point at a file; the groundwork "
                f"toolchain pin is misconfigured"
            )
        return str(p)
    found = shutil.which(default_name)
    if not found:
        raise GroundworkUnavailableError(
            f"{default_name!r} not found on PATH and {env_var} is unset; the grader "
            f"image must bundle the pinned groundwork toolchain (integration plan §3)"
        )
    return found


def holdouts_root(task) -> Path:
    """The read-only holdouts root: the ``/holdouts`` container mount when it
    exists, else ``task.holdouts_dir`` (the local runner) [integration plan §2].

    Never the workspace. The container mount wins when present so an in-container
    grade always reads the trusted, read-only copy — even if a stale
    ``holdouts_dir`` string travelled in the task.json."""
    if CONTAINER_HOLDOUTS.is_dir():
        return CONTAINER_HOLDOUTS
    if task.holdouts_dir:
        return Path(task.holdouts_dir)
    raise GroundworkUnavailableError(
        f"no holdouts available for task {getattr(task, 'id', '?')!r}: neither the "
        f"{CONTAINER_HOLDOUTS} mount nor task.holdouts_dir is set, so the trusted "
        f"policy/base-graph cannot be located (they are NEVER read from /workspace)"
    )


def resolve_assets(task) -> tuple[Path, Path]:
    """Locate ``policy.json`` and ``base.graph.json`` under the holdouts side ONLY.

    Returns ``(policy_path, base_graph_path)`` under ``<holdouts_root>/groundwork/``.
    Raises :class:`GroundworkUnavailableError` naming the missing file. The paths
    are resolved exclusively from :func:`holdouts_root` — a ``policy.json`` or
    ``base.graph.json`` planted in the agent-authored /workspace is structurally
    ignored, which is the point [integration plan §2]."""
    assets = holdouts_root(task) / ASSETS_SUBDIR
    policy = assets / POLICY_NAME
    base_graph = assets / BASE_GRAPH_NAME
    for path, what in ((policy, "policy"), (base_graph, "base graph")):
        if not path.is_file():
            raise GroundworkUnavailableError(
                f"groundwork {what} asset missing at {path} (resolved from the "
                f"read-only holdouts side, never /workspace); the task must commit "
                f"it under holdouts/{ASSETS_SUBDIR}/"
            )
    return policy, base_graph


def _subprocess_env(gocache: Path) -> dict:
    """The child environment: inherit, drop the fence nonce (deep-dive §2.4), and
    point GOCACHE at a writable dir so flowmap's type-checker works even when the
    container HOME is unset [integration plan §3]."""
    env = os.environ.copy()
    env.pop(NONCE_ENV, None)
    env["GOCACHE"] = str(gocache)
    return env


def regenerate_branch_graph(workspace, stamp: str, out_dir: Path, env: dict) -> Path:
    """``flowmap graph [--stamp <stamp>] <workspace>`` → ``out_dir/branch.graph.json``.

    The graph is written OUTSIDE the workspace (``out_dir`` is a throwaway temp
    dir) so nothing groundwork-derived lands in the graded tree. flowmap's flags
    MUST precede the directory argument. A non-zero exit is a load/type-check
    failure — a workspace that does not compile — surfaced as a
    :class:`GroundworkShellError` whose detail says compile-failure and carries a
    stderr tail (functional holdouts separately catch a non-compiling workspace;
    the distinction is preserved in the assertion detail) [integration plan §3]."""
    flowmap = _resolve_binary(FLOWMAP_BIN_ENV, FLOWMAP_DEFAULT)
    branch = out_dir / "branch.graph.json"
    argv = [flowmap, "graph"]
    if stamp:
        argv += ["--stamp", stamp]
    argv.append(str(workspace))
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=_FLOWMAP_TIMEOUT_S, env=env,
        )
    except subprocess.TimeoutExpired as e:
        raise GroundworkShellError(
            f"flowmap graph timed out after {_FLOWMAP_TIMEOUT_S}s on {workspace}"
        ) from e
    except OSError as e:
        raise GroundworkUnavailableError(f"could not exec flowmap ({flowmap}): {e}") from e
    if proc.returncode != 0:
        raise GroundworkShellError(
            "flowmap graph compile-failure (the workspace did not type-check); "
            f"exit {proc.returncode}: {_stderr_tail(proc.stderr)}"
        )
    branch.write_text(proc.stdout, encoding="utf-8")
    return branch


def run_review(policy: Path, base_graph: Path, branch_graph: Path, env: dict) -> dict:
    """``groundwork review <policy> <base> <branch> --json`` → parsed artifact.

    Exit 0 (clean: STRUCTURALLY-CLEAR / NO-STRUCTURAL-SIGNAL) or 1 (BLOCK) both
    print the canonical artifact to stdout — parse it. Exit 2 is groundwork's
    OPERATIONAL error (unreadable input, bad flags) → raise. Any other exit, or
    unparseable JSON, → raise. A gate FAIL (exit 1) is NOT an error: it means the
    branch violated the policy, which the mapper reports as failed assertions
    [integration plan §3]."""
    groundwork = _resolve_binary(GROUNDWORK_BIN_ENV, GROUNDWORK_DEFAULT)
    argv = [groundwork, "review", str(policy), str(base_graph), str(branch_graph), "--json"]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=_GROUNDWORK_TIMEOUT_S, env=env,
        )
    except subprocess.TimeoutExpired as e:
        raise GroundworkShellError(
            f"groundwork review timed out after {_GROUNDWORK_TIMEOUT_S}s"
        ) from e
    except OSError as e:
        raise GroundworkUnavailableError(
            f"could not exec groundwork ({groundwork}): {e}"
        ) from e
    if proc.returncode == _GROUNDWORK_OPERATIONAL_EXIT:
        raise GroundworkShellError(
            "groundwork review operational failure (exit 2 — unreadable inputs or "
            f"bad flags): {_stderr_tail(proc.stderr)}"
        )
    if proc.returncode not in (0, 1):
        raise GroundworkShellError(
            f"groundwork review unexpected exit {proc.returncode}: "
            f"{_stderr_tail(proc.stderr)}"
        )
    try:
        artifact = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise GroundworkShellError(
            f"groundwork review emitted malformed JSON (exit {proc.returncode}): {e}"
        ) from e
    if not isinstance(artifact, dict):
        raise GroundworkShellError(
            f"groundwork review JSON is not an object: {type(artifact).__name__}"
        )
    return artifact


class Toolchain(NamedTuple):
    """The flowmap+groundwork build identity behind a real-path grade, for grade
    PROVENANCE [integration plan §10 P0]. On success ``flowmap`` / ``groundwork``
    each hold the tool's verbatim one-line ``version`` output (``flowmap <v>`` /
    ``groundwork <v>``); on any failure both are ``None`` and ``error`` says why.

    Provenance is BEST-EFFORT disclosure: a failed version probe degrades to
    ``unknown`` and NEVER fails the grade — the verdict is the load-bearing output
    and is already computed by the time versions are captured
    (:func:`review_artifact`)."""

    flowmap: Optional[str]
    groundwork: Optional[str]
    error: Optional[str] = None


def _capture_version(env_var: str, default_name: str, env: dict) -> str:
    """``<bin> version`` → the tool's verbatim single-line identity (``<tool> <v>``;
    both tools print exactly one version line — cmd/flowmap/main.go,
    cmd/groundwork/main.go). Raises on any failure; :func:`capture_toolchain`
    catches it and degrades to ``unknown`` — a version probe never fails a grade."""
    binary = _resolve_binary(env_var, default_name)
    proc = subprocess.run(
        [binary, "version"], capture_output=True, text=True,
        timeout=_VERSION_TIMEOUT_S, env=env,
    )
    if proc.returncode != 0:
        raise GroundworkShellError(
            f"{default_name} version exited {proc.returncode}: {_stderr_tail(proc.stderr)}"
        )
    line = proc.stdout.strip()
    if not line:
        raise GroundworkShellError(f"{default_name} version printed no output")
    return line.splitlines()[0].strip()


def capture_toolchain(env: dict) -> Toolchain:
    """Best-effort flowmap+groundwork build identity for grade provenance.

    Probes ``version`` on the SAME binaries the grade used: :func:`_resolve_binary`
    is a pure function of the environment, which does not change for the grade's
    duration, so re-resolving here yields exactly the flowmap/groundwork that built
    the branch graph and computed the review. ANY failure — a binary that vanished,
    a non-zero version exit, a timeout, an exec error — is folded into
    ``Toolchain.error`` and NEVER re-raised: provenance is disclosure and the verdict
    does not depend on it [integration plan §10 P0]."""
    try:
        flowmap = _capture_version(FLOWMAP_BIN_ENV, FLOWMAP_DEFAULT, env)
        groundwork = _capture_version(GROUNDWORK_BIN_ENV, GROUNDWORK_DEFAULT, env)
    except (GroundworkShellError, OSError, subprocess.SubprocessError) as e:
        return Toolchain(flowmap=None, groundwork=None, error=str(e))
    return Toolchain(flowmap=flowmap, groundwork=groundwork)


def review_artifact(workspace, task) -> tuple[dict, Toolchain]:
    """The full real-path pipeline → ``(artifact, toolchain)``.

    Resolves the trusted assets from the holdouts side, regenerates the branch
    graph from the workspace into a throwaway temp dir OUTSIDE the workspace, runs
    ``groundwork review``, and — once the verdict is computed — captures the
    flowmap+groundwork build identity for grade provenance (best-effort; a version
    probe never fails the grade). The temp dir (branch graph + a writable GOCACHE)
    is always removed. Every REVIEW failure raises (→ ``cant_grade(plugin_error)``)."""
    policy, base_graph = resolve_assets(task)
    stamp = getattr(task, "task_sha", "") or ""
    tmp = Path(tempfile.mkdtemp(prefix="verdi-groundwork-"))
    try:
        gocache = tmp / "gocache"
        gocache.mkdir()
        env = _subprocess_env(gocache)
        branch = regenerate_branch_graph(workspace, stamp, tmp, env)
        artifact = run_review(policy, base_graph, branch, env)
        toolchain = capture_toolchain(env)
        return artifact, toolchain
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _stderr_tail(stderr: Optional[str], limit: int = 800) -> str:
    """A bounded, single-line-ish tail of a tool's stderr for an error detail."""
    text = (stderr or "").strip()
    return text[-limit:] if text else "(no stderr)"
