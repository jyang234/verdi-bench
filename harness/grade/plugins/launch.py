"""Containerized grader-plugin launch [refactor 05 §2, PRA-M6].

The recipe for running grader plugins in a fresh, ``--network none`` container —
the plugin-tier counterpart to the holdout grader — lives beside the ``plugins/``
package it launches. It consumes the shared seams: the hermetic hardened-argv
builder ([refactor 04 §1]), the throwaway-copy + exit-classification discipline
(:mod:`harness.grade.isolation`), and the nonce-authenticated fence transport
(:mod:`harness.grade.fence`). :class:`~harness.grade.runners.GradingContainer`'s
``build_plugin_command`` / ``run_plugins`` are thin callers of the two functions
here.

Plugins previously ran in-process on the harness host — with full network and
filesystem access — against the agent-controlled workspace; a plugin that shells
out over malicious build/config files was a host-side code-execution surface the
holdout tier does not have. Running them under the same ``--network none``
fresh-copy discipline as holdouts (plus ``--cap-drop ALL`` and no-new-privileges)
closes that asymmetry.

Contains **no LLM client** — grading's determinism is its authority (the
``grade-has-no-llm-clients`` import contract).
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Optional

from ...hermetic.docker import HardenedCommand
from ..fence import NONCE_ENV, GradingContainerError, _extract_fence, plugin_fence
from ..isolation import fresh_workspace_copy, run_grading_container
from ..types import Assertion

# Filename the containerized plugin runner writes its assertion list to [PRA-M6].
# Same fresh-copy discipline: any agent-written copy is removed before the run.
PLUGIN_RESULTS = "plugin_results.json"


def build_plugin_command(
    image: str,
    workspace: Path,
    plugin_ids: list,
    task_file: Optional[Path] = None,
    nonce: Optional[str] = None,
    holdouts_dir: Optional[str] = None,
) -> list[str]:
    """Fresh, NETWORK-LESS container argv for grader plugins [PRA-M6].

    The same ``--network none`` + ``--cap-drop ALL`` + no-new-privileges recipe as
    the holdout grader (no ``--user``: the plugin entrypoint keeps its prior
    identity), built through the hermetic layer [refactor 04 §1]. The grader
    image's plugin entrypoint (``python3 -m harness.grade.run_plugin`` — the
    reference grader image ships ``python3`` only, no ``python`` alias) reads the
    ids and the read-only task mount, and writes :data:`PLUGIN_RESULTS`.

    ``nonce`` (present on the production path) is injected as ``VERDI_FENCE_NONCE``
    so the entrypoint can stamp it into its fence marker [F-H1 follow-up].

    ``holdouts_dir`` (when set) is bind-mounted **read-only at /holdouts**, the
    same trusted mount the holdout grader gets [integration plan §2]. The
    groundwork plugin resolves its policy + base graph from ``/holdouts/groundwork/``
    ONLY — never from the agent-authored ``/workspace`` — so a graded party cannot
    substitute its own grader inputs. The trailing ``python -m ...`` command tokens
    stay LAST (the argv-identity tests pin ``cmd[-4:]``); the mount rides the flags.
    """
    hc = HardenedCommand().rm().network("none").harden()
    if nonce:
        hc.e_env(NONCE_ENV, nonce)
    hc.volume(workspace, "/workspace")
    if task_file is not None:
        hc.volume(task_file, "/verdi/task.json", ro=True)
    if holdouts_dir:
        hc.volume(holdouts_dir, "/holdouts", ro=True)
    hc.workdir("/workspace").image(image)
    hc.arg("python3", "-m", "harness.grade.run_plugin", *[str(p) for p in plugin_ids])
    return hc.build()


def run_plugins_in_container(docker, image: str, workspace: Path, plugin_ids: list, task) -> list:
    """Run declared plugins in a fresh-copy, network-less container [PRA-M6].

    Grades a throwaway copy of the workspace (evidence protection + stale removal
    via the shared isolation helper), delivers the ``GradeTask`` read-only at
    ``/verdi/task.json``, and scores ONLY the entrypoint's nonce-authenticated
    fenced stdout [F-H1 A.4] — never a file from the agent-writable copy. Every
    failure mode is terminal here (it flows to cant_grade(plugin_error) in
    grade_trial), so no malformed marker is emitted.
    """
    # Same fresh-copy + exit-classification discipline as the holdout path,
    # via the shared isolation helpers [refactor 05 §2].
    with fresh_workspace_copy(
        workspace, stale_name=PLUGIN_RESULTS, prefix="verdi-plugin-",
        purpose=" for plugins",
    ) as copy:
        # the GradeTask travels into the container read-only at /verdi/task.json,
        # written beside the workspace copy under the same throwaway tree.
        task_file = copy.parent / "task.json"
        holdouts_dir = getattr(task, "holdouts_dir", "") or ""
        # In-container the trusted holdouts are bind-mounted at /holdouts, so the
        # task the plugin reads names that CONTAINER path — never the host path —
        # so a plugin resolving grader assets (groundwork's policy/base graph) hits
        # the read-only mount, not a stale host string [integration plan §2].
        task_file.write_text(json.dumps({
            "id": getattr(task, "id", "t"),
            "task_sha": getattr(task, "task_sha", ""),
            "holdouts_dir": "/holdouts" if holdouts_dir else "",
            "fake_plugin_output": getattr(task, "fake_plugin_output", {}) or {},
        }), encoding="utf-8")
        # Per-grade nonce authenticates the plugin fence too [F-H1 follow-up].
        nonce = secrets.token_hex(16)
        cmd = build_plugin_command(
            image, copy, plugin_ids, task_file, nonce,
            holdouts_dir=holdouts_dir or None,
        )
        proc = run_grading_container(docker, cmd, noun="plugin")
        # F-H1 A.4: same trusted channel as holdouts — never a file from the
        # agent-writable copy, and nonce-authenticated so an agent-forged block
        # is rejected.
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
