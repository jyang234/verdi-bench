#!/usr/bin/env python3
"""``claude-code-groundwork`` trial agent — the payload-gated groundwork fork [integration plan §4, A2].

A fork of the official ``anthropic-claude-code`` agent. It drives the SAME pinned
``claude`` CLI over the task inside ``/workspace`` and emits the SAME generic
``artifacts/agent_log.json`` via :mod:`verdi_agent` — but it reads
``/verdi/request.json`` and, **iff** ``payload.tools`` includes ``"groundwork"``,
arms the treatment: it exposes the pinned ``flowmap``/``groundwork`` toolchain on
``PATH``, installs the ``groundwork-workflow`` skill, and points the CLI at an MCP
server that serves the workspace's call graph as read-only tools (the ground ->
edit -> verify loop, integration plan §4). With any other payload it does none of
that and behaves byte-for-byte like the shipped official agent — **one image, both
arms**, the asymmetry realized only here (``docs/usage-guide.md`` §9).

Trust/hygiene invariants this entrypoint upholds (review findings, integration
plan §2/§4, D7):

* **Nothing groundwork-branded is written loose into ``/workspace``.** The MCP
  config and the skill are installed under ``$HOME`` (``/tmp`` in verdi-base),
  loaded by the CLI from outside the graded tree via an absolute
  ``--mcp-config`` path and the user-scope ``$HOME/.claude/skills`` dir. The ONLY
  path the entrypoint touches under ``/workspace`` is the ``artifacts/`` directory
  — the MCP call-log destination, which the judge diff already excludes. An
  entrypoint-written workspace file would surface in the judged diff as a
  treatment-arm asymmetry, so it is forbidden by construction.
* **The CLI argv differs across arms by exactly ``--mcp-config <path>``.** Control
  is the shipped argv; treatment is the shipped argv plus that one flag. The
  ``log.test_run`` telemetry line is identical across arms so the ``claude_code``
  adapter parses both the same way — the treatment's tool residue lives in
  ``artifacts/groundwork-mcp.jsonl``, never in the parsed log.

The gating DECISIONS are the pure, importable functions below
(:func:`groundwork_enabled`, :func:`mcp_server_config`, :func:`plan_groundwork`,
:func:`cli_argv`, :func:`cli_env`); the side effects are :func:`apply_plan` and
:func:`main`. The no-docker unit tests exercise the pure plan + a tmp-dir apply,
so the "nothing under /workspace except artifacts/" rule and the argv delta are
proven without a container (``tests/test_image_claude_code_groundwork.py``).

Honesty note: like the official image, the real CLI is exercised only WITH keys
and network; ``bench images verify`` proves plumbing (request in -> scorable log
out), never intelligence. The pinned CLI version MUST support user-scope skills
and ``--mcp-config`` (README "honesty notes") — the treatment arm's whole value
depends on it, and it is NOT re-verified by the offline check.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Mapping

from verdi_agent import WORKSPACE, AgentLog, read_request, run_visible

# The shipped invocation — IDENTICAL to images/official/anthropic-claude-code so
# the control arm is byte-for-byte the official agent. Non-interactive print mode;
# edits auto-accepted so a batch trial never blocks on a permission prompt (this
# permission mode also governs whether the model may call the MCP tools without a
# prompt). The flag spelling is version-coupled — confirm against the pinned CLI.
CLI = ["claude", "--print", "--permission-mode", "acceptEdits"]

# Where the Dockerfile stages the toolchain + skill — DELIBERATELY off PATH, so a
# control arm that never runs the treatment branch cannot see them. The treatment
# branch symlinks the binaries onto a writable PATH dir and copies the skill into
# $HOME scope at runtime.
STAGING_BIN = "/opt/groundwork/bin"  # holds flowmap + groundwork (not on PATH)
STAGING_SKILL = "/opt/groundwork/skills/groundwork-workflow"
BINARIES = ("flowmap", "groundwork")
SKILL_NAME = "groundwork-workflow"

# The MCP server the CLI spawns (stdio) when the treatment is armed. graph.json /
# policy.json are TASK workspace files (integration plan §5); the call log is
# routed to the judge-excluded artifacts dir (D7). These are real CONTAINER paths
# baked into the config the CLI reads at runtime.
GRAPH_NAME = "graph.json"
POLICY_NAME = "policy.json"
MCP_LOG_NAME = "groundwork-mcp.jsonl"
MCP_SERVER_NAME = "groundwork"


def groundwork_enabled(payload: Mapping) -> bool:
    """Is the groundwork treatment armed for this arm? [integration plan §4/§6].

    True iff ``payload.tools`` is a list containing ``"groundwork"``. Every other
    shape — no payload, no ``tools`` key, a ``tools`` list without ``groundwork``,
    or a non-list ``tools`` — is control (fail-closed: only the explicit opt-in
    arms the tool, never a truthy accident)."""
    tools = payload.get("tools")
    return isinstance(tools, list) and "groundwork" in tools


def mcp_server_config(
    *,
    groundwork_bin: str,
    graph: str,
    policy: str,
    log: str,
) -> dict:
    """The ``--mcp-config`` JSON for the stdio groundwork server [integration plan §4].

    Mirrors the launch contract of ``cmd/groundwork/mcp.go``:
    ``groundwork mcp <graph.json> --policy <policy.json> --log <calls.jsonl>``
    (stdio; the CLI spawns it). ``command`` is the ABSOLUTE staging path so the
    launch does not depend on the runtime PATH."""
    return {
        "mcpServers": {
            MCP_SERVER_NAME: {
                "type": "stdio",
                "command": groundwork_bin,
                "args": ["mcp", graph, "--policy", policy, "--log", log],
            }
        }
    }


def render_mcp_config(config: dict) -> str:
    """Serialize the MCP config deterministically (sorted keys, trailing newline).

    Byte-stable across runs: the same payload yields the same config file, so a
    treatment arm never introduces run-to-run variation through its own config."""
    return json.dumps(config, indent=2, sort_keys=True) + "\n"


@dataclass(frozen=True)
class GroundworkPlan:
    """A declarative description of every side effect arming groundwork requires.

    Pure output of :func:`plan_groundwork`; :func:`apply_plan` is the only thing
    that touches the filesystem. Keeping the plan declarative lets a test assert —
    without a container — that NO write lands under ``/workspace`` except the
    ``artifacts/`` mkdir, and that the argv delta is exactly ``--mcp-config``.

    * ``symlinks`` — ``(target, link)`` pairs exposing the staged binaries on PATH.
    * ``path_bin_dir`` — the writable dir holding those links, prepended to PATH.
    * ``mkdirs`` — directories to ensure (all under ``$HOME`` except the one
      allowed ``/workspace/artifacts`` destination for the MCP log).
    * ``files`` — ``(abspath, text)`` files to write (the MCP config, under HOME).
    * ``copies`` — ``(src, dst)`` trees to copy (the skill, into HOME scope).
    * ``cli_extra_args`` — CLI args inserted before the prompt (``--mcp-config``).
    """

    enabled: bool
    symlinks: tuple[tuple[str, str], ...] = ()
    path_bin_dir: str = ""
    mkdirs: tuple[str, ...] = ()
    files: tuple[tuple[str, str], ...] = ()
    copies: tuple[tuple[str, str], ...] = ()
    cli_extra_args: tuple[str, ...] = ()
    mcp_config_path: str = ""
    artifacts_dir: str = ""


def plan_groundwork(
    payload: Mapping,
    *,
    home: str = "/tmp",
    workspace: str = "/workspace",
    staging_bin: str = STAGING_BIN,
    staging_skill: str = STAGING_SKILL,
) -> GroundworkPlan:
    """Compute the arming plan for ``payload`` — PURE (no filesystem writes).

    Control (``groundwork`` not in ``payload.tools``) returns the empty, disabled
    plan: no symlinks, no config, no skill, no extra CLI args — so :func:`apply_plan`
    is a no-op and :func:`cli_argv`/:func:`cli_env` reproduce the shipped agent
    exactly. Treatment computes every path from ``home``/``workspace``/staging so a
    test can redirect them at tmp dirs and assert the /workspace rule.

    /WORKSPACE RULE (review finding, integration plan §4): every write destination
    is under ``home`` EXCEPT ``<workspace>/artifacts`` (the MCP call-log dir, D7,
    judge-excluded). No other path under ``workspace`` appears in the plan."""
    if not groundwork_enabled(payload):
        return GroundworkPlan(enabled=False)

    bin_dir = os.path.join(home, ".local", "bin")
    symlinks = tuple(
        (os.path.join(staging_bin, b), os.path.join(bin_dir, b)) for b in BINARIES
    )

    artifacts = os.path.join(workspace, "artifacts")
    config = mcp_server_config(
        groundwork_bin=os.path.join(staging_bin, "groundwork"),
        graph=os.path.join(workspace, GRAPH_NAME),
        policy=os.path.join(workspace, POLICY_NAME),
        log=os.path.join(artifacts, MCP_LOG_NAME),
    )
    config_path = os.path.join(home, "groundwork.mcp.json")

    skill_dst = os.path.join(home, ".claude", "skills", SKILL_NAME)

    return GroundworkPlan(
        enabled=True,
        symlinks=symlinks,
        path_bin_dir=bin_dir,
        # bin_dir + the skill parent live under $HOME; artifacts is the sole
        # /workspace destination (created so the MCP server can open its log).
        mkdirs=(bin_dir, os.path.dirname(skill_dst), artifacts),
        files=((config_path, render_mcp_config(config)),),
        copies=((staging_skill, skill_dst),),
        cli_extra_args=("--mcp-config", config_path),
        mcp_config_path=config_path,
        artifacts_dir=artifacts,
    )


def apply_plan(plan: GroundworkPlan) -> None:
    """Realize ``plan`` on disk — the ONLY side-effecting step [integration plan §4].

    A no-op for the disabled (control) plan, so the control arm writes nothing.
    Idempotent for the enabled plan (safe to re-run): existing links/skill copies
    are replaced. Fails loudly if a staged source is missing — an unarmed
    treatment must never masquerade as armed."""
    if not plan.enabled:
        return
    for d in plan.mkdirs:
        os.makedirs(d, exist_ok=True)
    for target, link in plan.symlinks:
        if os.path.islink(link) or os.path.exists(link):
            os.remove(link)
        os.symlink(target, link)
    for path, text in plan.files:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    for src, dst in plan.copies:
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst)


def cli_argv(prompt: str, plan: GroundworkPlan) -> list[str]:
    """The ``claude`` argv for this arm [integration plan §4].

    Control: ``[*CLI, prompt]`` — byte-for-byte the shipped official agent.
    Treatment: ``[*CLI, --mcp-config <path>, prompt]`` — the shipped argv plus
    EXACTLY that one flag (the flags precede the trailing positional prompt)."""
    return [*CLI, *plan.cli_extra_args, prompt]


def cli_env(base_env: Mapping[str, str], plan: GroundworkPlan) -> dict:
    """The environment for the CLI subprocess [integration plan §4].

    Control: an unmodified copy of ``base_env``. Treatment: the same, with the
    symlink bin dir prepended to PATH so the model can invoke ``flowmap``/
    ``groundwork`` by bare name (regenerate ``graph.json`` after edits, then
    ``reload``)."""
    env = dict(base_env)
    if plan.enabled and plan.path_bin_dir:
        env["PATH"] = plan.path_bin_dir + os.pathsep + env.get("PATH", "")
    return env


def main(log: AgentLog) -> None:
    req = read_request()
    # Gate on the arm's payload; arm the treatment iff explicitly requested. apply
    # is a no-op for control, so control is the shipped agent's exact behavior. The
    # HOME-scoped writes go under the real $HOME (verdi-base fixes it to /tmp; harbor
    # does not override it) — NEVER /workspace; the config's internal graph/policy/
    # log paths stay the fixed container paths (/workspace, /opt/groundwork/bin).
    plan = plan_groundwork(req.payload, home=os.environ.get("HOME") or "/tmp")
    apply_plan(plan)

    argv = cli_argv(req.prompt, plan)
    env = cli_env(os.environ, plan)
    # The CLI authenticates from ANTHROPIC_API_KEY (harbor injects it as an
    # allowlisted --env) and tunnels egress through HTTP(S)_PROXY automatically;
    # when armed, it also spawns the stdio groundwork MCP server from --mcp-config.
    proc = subprocess.run(
        argv,
        cwd=str(WORKSPACE),
        env=env,
        capture_output=True,
        text=True,
        timeout=int(req.payload.get("cli_timeout_s", 1500)),
    )
    detail = (proc.stdout or proc.stderr or "").strip()[:400]
    log.message(f"[{req.arm}/{req.model_id}] claude-code exit {proc.returncode}")
    # The telemetry line is the SHIPPED base CLI, identical across arms, so the
    # claude_code adapter parses control and treatment the same way; the
    # treatment's tool usage is recorded in artifacts/groundwork-mcp.jsonl, not
    # here [integration plan §4, D7]. Telemetry stays null (the print-mode CLI does
    # not self-report tokens/cost in a stable machine form; verdi never guesses).
    log.test_run(" ".join(CLI), detail=detail, exit_code=proc.returncode)
    log.finish()
    if proc.returncode != 0:
        raise RuntimeError(f"claude-code CLI exited {proc.returncode}: {detail!r}")


if __name__ == "__main__":
    run_visible(main)
