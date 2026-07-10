#!/usr/bin/env python3
"""``claude-code-groundwork`` trial agent — the payload-gated groundwork fork [integration plan §4, A2].

A fork of the official ``anthropic-claude-code`` agent. It drives the SAME pinned
``claude`` CLI over the task inside ``/workspace`` and persists the SAME native
``artifacts/agent_log.json`` (the CLI's ``--output-format json`` result) via
:mod:`verdi_agent` — but it reads
``/verdi/request.json`` and, **iff** ``payload.tools`` includes ``"groundwork"``,
arms the treatment: it exposes the pinned ``flowmap``/``groundwork`` toolchain on
``PATH``, installs the ``groundwork-workflow`` skill, and points the CLI at an MCP
server that serves the workspace's call graph as read-only tools (the ground ->
edit -> verify loop, integration plan §4). When the payload ALSO declares
``workflow: ground_verify`` (the rung-2 "instructed" treatment), the CLI argv
additionally carries ``--append-system-prompt=<pre-registered text>`` — the
byte-stable :data:`WORKFLOW_SYSTEM_PROMPTS` entry instructing the ground ->
edit -> verify loop; without the key, rung 1 is availability only. When the payload
declares ``workflow: ground_verify_enforced`` (the rung-3 "enforced" treatment) the
argv is IDENTICAL to rung 2 (the same ``--append-system-prompt`` text, reused
verbatim), and enforcement is realized purely in arm-time FILESYSTEM: an
``$HOME/.claude/settings.json`` Stop hook runs the groundwork merge gate
(``groundwork review``) in-loop and re-drives the model on a BLOCK — bounded, never
trapping (see :data:`ENFORCEMENT_HOOK_PY`). When the payload declares
``workflow: placebo_gate`` it is the mechanism-decomposition PLACEBO
(``docs/design/mechanism-decomposition-program.md``, piece 1): rung 3's exact
Stop-hook machinery and byte-identical argv, but the hook runs NO gate and blocks
with one static content-free reason (see :data:`PLACEBO_HOOK_PY`), isolating the
gate's findings content from the forcing function. A payload that instead declares
``system_prompt_extra: <key>`` is the PROMPT-ONLY treatment (``docs/design/mechanism-decomposition-program.md``,
piece 2): a DISABLED plan carrying exactly one ``--append-system-prompt=<pre-registered text>``
token — no tools, no MCP config, no hook, no filesystem writes — so the arm is otherwise
byte-for-byte the control. With any other
payload it does none of that and behaves byte-for-byte like the shipped official
agent — **one image, both arms**, the asymmetry realized only here
(``docs/usage-guide.md`` §9).

Trust/hygiene invariants this entrypoint upholds (review findings, integration
plan §2/§4, D7):

* **Nothing groundwork-branded is written loose into ``/workspace``.** The MCP
  config and the skill are installed under ``$HOME`` (``/tmp`` in verdi-base),
  loaded by the CLI from outside the graded tree via an absolute
  ``--mcp-config`` path and the user-scope ``$HOME/.claude/skills`` dir. The ONLY
  path the entrypoint touches under ``/workspace`` is the ``artifacts/`` directory
  — the MCP call-log + captured-session-transcript destination, which the judge
  diff already excludes. An entrypoint-written workspace file would surface in the
  judged diff as a treatment-arm asymmetry, so it is forbidden by construction.
* **For same-model arms the CLI argv differs by exactly the payload-gated
  tokens:** ``--mcp-config=<path>`` and, when the arm declares a workflow,
  ``--append-system-prompt=<pre-registered text>``. Every arm also carries
  ``--model=<id>`` taken from its OWN pre-registered arm spec (the model is a
  declared arm property, never an agent choice); within a bare-vs-grounded pair
  the model id is identical, so the remaining argv delta is exactly those
  payload-gated tokens — one for rung-1 availability, both for the rung-2
  instructed treatment. Control is the shipped argv (plus its ``--model``). The
  base CLI — and thus its native ``--output-format json`` result log — is
  identical across same-model arms, so the ``claude_code`` adapter parses both
  the same way; the treatment's tool residue lives in
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

from verdi_agent import (
    WORKSPACE,
    AgentLog,
    capture_claude_session_transcripts,
    read_request,
    run_visible,
)

# The shipped invocation — IDENTICAL to images/official/anthropic-claude-code so
# the control arm is byte-for-byte the official agent. Non-interactive print mode;
# --permission-mode bypassPermissions because print mode cannot answer a permission
# prompt and acceptEdits auto-accepts EDITS only — it silently DENIED every
# non-edit tool, INCLUDING this image's MCP tools and the Skill (the 2026-07-08
# recon measured 234 denials across 160 trials; the treatment's tooling never
# executed — the groundwork MCP log carried only the server-init handshake). Bypass
# is correct here because the hermetic trial container IS the sandbox — cap-drop,
# internal network, metered-proxy-only egress — the posture the CLI's own help
# recommends the mode for. --output-format json makes the CLI emit its native
# result object on stdout, persisted verbatim as the native log. The flag spelling
# is version-coupled — confirm against the pinned CLI.
CLI = ["claude", "--print", "--permission-mode", "bypassPermissions", "--output-format", "json"]

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

# The rung-2 "instructed" treatment text, keyed by ``payload.workflow`` value.
# This text is PART OF THE PRE-REGISTERED TREATMENT DEFINITION and must stay
# byte-stable in the image: it is delivered verbatim as one
# ``--append-system-prompt=<text>`` token, so any edit changes the treatment
# under study and belongs to a new pre-registration, never a casual rewrite
# (revised 2026-07-08, pre-lock, after the manipulation pilot: literal tool ids;
# verification via the `groundwork verify` BINARY in Bash — the MCP surface is
# read-only per the vendored skill).
# INVARIANT: process-only — it names tools and sequence, never any task property,
# trap, or expected answer.
WORKFLOW_SYSTEM_PROMPTS = {
    "ground_verify": (
        "This workspace is governed by a structural policy (`policy.json`) enforced by the "
        "groundwork merge gate; structurally non-compliant changes are rejected. The following "
        "workflow is mandatory. (1) BEFORE editing any Go function, call the "
        "`mcp__groundwork__ground` tool on that function's fully-qualified name and treat its "
        "binding rules as constraints on your edit; if unsure whether an edit is local, check "
        "blast radius with `mcp__groundwork__reach`. (2) AFTER your edits, regenerate the graph "
        "by running `flowmap graph` in Bash, then call `mcp__groundwork__reload`. (3) Run "
        "`groundwork verify` in Bash and fix every finding. Do not conclude while "
        "`groundwork verify` reports anything other than STRUCTURALLY-CLEAR."
    ),
}

# The rung-3 "enforced" workflow value. Its ``--append-system-prompt`` text is rung
# 2's ``ground_verify`` entry VERBATIM (deliberate: a rung3-vs-rung2 contrast then
# isolates the enforcement Stop hook ALONE — no new prompt text), so the argv delta
# vs rung 2 is NONE; enforcement is realized purely in arm-time filesystem.
ENFORCED_WORKFLOW = "ground_verify_enforced"

# The mechanism-decomposition PLACEBO workflow [design:
# docs/design/mechanism-decomposition-program.md, piece 1]. Same payload shape,
# same rung-2 prompt text, same Stop-hook machinery as rung 3 — but the hook is
# :data:`PLACEBO_HOOK_PY`: no gate run, no graph/policy inputs, one static
# content-free block reason. The rung3-vs-placebo contrast isolates the gate's
# FINDINGS CONTENT from the forcing function itself.
PLACEBO_WORKFLOW = "placebo_gate"

# Each known ``payload.workflow`` value → the :data:`WORKFLOW_SYSTEM_PROMPTS` key whose
# text it delivers as ``--append-system-prompt``. Membership here is the known-workflow
# set (an unknown value is refused loudly). The enforced rung reuses the ``ground_verify``
# entry rather than duplicating the byte-stable text.
WORKFLOW_PROMPT_KEY = {
    "ground_verify": "ground_verify",
    ENFORCED_WORKFLOW: "ground_verify",
    PLACEBO_WORKFLOW: "ground_verify",
}

# Prompt-only treatments, keyed by ``payload.system_prompt_extra`` [design:
# docs/design/mechanism-decomposition-program.md, piece 2]. Delivered as one
# ``--append-system-prompt=<text>`` token with NO tools, NO MCP config, NO hook,
# NO filesystem writes — the arm is otherwise byte-for-byte the control. These
# texts are PRE-REGISTERED TREATMENT DEFINITIONS: byte-stable, process-only
# (they may point at an agent-visible file; they never name a tool, a workflow
# step, a trap, or an expected answer). Registry membership is the known set —
# an unknown value is refused loudly, and combining a prompt-only treatment
# with tools/workflow is refused (it would blur which treatment ran).
SYSTEM_PROMPT_EXTRAS = {
    "policy_pointer": (
        "This repository declares structural policy in `policy.json`; "
        "your change must honor it."
    ),
}

# The rung-3 enforcement Stop-hook script — a BYTE-STABLE, python3 stdlib-only module,
# PART OF THE PRE-REGISTERED RUNG-3 TREATMENT DEFINITION: any edit here changes the
# treatment under study and belongs to a NEW pre-registration, never a casual rewrite.
# apply_plan writes it to ``$HOME/verdi-enforce/stop_hook.py`` and registers it in
# ``$HOME/.claude/settings.json``; the pinned CLI runs it on every Stop attempt in
# --print mode. It derives its own directory from ``__file__`` (so it is home-relative
# yet byte-identical across runs) and reads its tamper-proof gate inputs from there.
# INVARIANT: it carries NO gate-satisfaction hint or anti-gaming instruction — the
# gate-gaming rate is a MEASUREMENT, not something the treatment nudges.
ENFORCEMENT_HOOK_PY = r'''#!/usr/bin/env python3
"""verdi rung-3 enforcement Stop hook — PART OF THE PRE-REGISTERED RUNG-3 TREATMENT
DEFINITION; any edit here is a NEW pre-registration, never a casual change.

Registered in $HOME/.claude/settings.json, the pinned claude CLI runs this on every
Stop attempt in --print mode. It regenerates the branch call graph from /workspace
and runs the groundwork merge gate (`groundwork review`) against the tamper-proof
base graph + policy preserved beside this script (under $HOME, so editing
/workspace/policy.json cannot defeat the gate). On a BLOCK it prints
{"decision":"block","reason":...} on stdout (exit 0), which blocks completion and
re-drives the model with the gate's findings; a per-session counter bounds the
re-drives to MAX_ROUNDS. It NEVER traps the session: an exhausted budget, a
non-compiling workspace, a broken gate, or ANY unexpected error all ALLOW
completion (exit 0). One JSON line per gate evaluation is appended to the
judge-excluded /workspace/artifacts/groundwork-enforce.jsonl.

No gate-satisfaction hint or anti-gaming instruction lives here: the gate-gaming
rate is a MEASUREMENT.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROUNDS = os.path.join(HERE, "rounds")
POLICY = os.path.join(HERE, "policy.json")
BASE_GRAPH = os.path.join(HERE, "base.graph.json")
BRANCH_GRAPH = os.path.join(HERE, "branch.graph.json")

WORKSPACE = "/workspace"
FLOWMAP = "/opt/groundwork/bin/flowmap"
GROUNDWORK = "/opt/groundwork/bin/groundwork"
ENFORCE_LOG = "/workspace/artifacts/groundwork-enforce.jsonl"
MAX_ROUNDS = 3


def tail(text, limit=800):
    text = (text or "").strip()
    return text[-limit:] if text else "(no stderr)"


def log(record):
    os.makedirs(os.path.dirname(ENFORCE_LOG), exist_ok=True)
    with open(ENFORCE_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def block(reason):
    # JSON on stdout + exit 0 blocks completion and re-drives the model with `reason`.
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


def load_artifact(stdout):
    try:
        data = json.loads(stdout)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def findings_of(artifact):
    verdict = str(artifact.get("verdict", ""))
    lines = [verdict] if verdict else []
    for v in artifact.get("new_violations") or []:
        rule = str(v.get("rule") or "").strip()
        summary = str(v.get("summary") or "").strip()
        frm, to = str(v.get("from") or ""), str(v.get("to") or "")
        edge = (frm + " -> " + to) if to else frm
        head = ("[" + rule + "] ") if rule else ""
        if summary and edge:
            detail = summary + " (" + edge + ")"
        else:
            detail = summary or edge or "(no detail)"
        lines.append("- " + head + detail)
    return "\n".join(lines)


def main():
    current = int(open(ROUNDS, encoding="utf-8").read().strip())
    if current >= MAX_ROUNDS:
        log({"round": current, "decision": "exhausted"})
        sys.exit(0)

    graph = subprocess.run([FLOWMAP, "graph"], cwd=WORKSPACE,
                           capture_output=True, text=True)
    if graph.returncode != 0:
        detail = tail(graph.stderr)
        with open(ROUNDS, "w", encoding="utf-8") as fh:
            fh.write(str(current + 1))
        log({"round": current, "decision": "build_error", "detail": detail})
        block("The workspace does not compile; groundwork cannot run until it "
              "builds:\n" + detail)

    with open(BRANCH_GRAPH, "w", encoding="utf-8") as fh:
        fh.write(graph.stdout)

    gate = subprocess.run(
        [GROUNDWORK, "review", POLICY, BASE_GRAPH, BRANCH_GRAPH, "--json"],
        capture_output=True, text=True)
    if gate.returncode == 0:
        verdict = str(load_artifact(gate.stdout).get("verdict", ""))
        log({"round": current, "verdict": verdict, "decision": "clean"})
        sys.exit(0)
    if gate.returncode == 1:
        artifact = load_artifact(gate.stdout)
        verdict = str(artifact.get("verdict", ""))
        findings = findings_of(artifact)
        with open(ROUNDS, "w", encoding="utf-8") as fh:
            fh.write(str(current + 1))
        log({"round": current, "verdict": verdict, "decision": "block",
             "findings": findings})
        block("The structural gate BLOCKED your change. Fix these and "
              "continue:\n" + findings)
    # exit 2 or any other → operational gate error: never trap against a broken gate.
    log({"round": current, "decision": "gate_error", "detail": tail(gate.stderr)})
    sys.exit(0)


try:
    main()
except SystemExit:
    raise
except Exception as exc:  # never crash the session: allow on any unexpected error
    try:
        log({"round": -1, "decision": "hook_error", "detail": repr(exc)[-800:]})
    except Exception:
        pass
    sys.exit(0)
'''

# The placebo Stop-hook script — BYTE-STABLE, PART OF THE PRE-REGISTERED PLACEBO
# TREATMENT DEFINITION: any edit here is a NEW pre-registration. Identical
# machinery to ENFORCEMENT_HOOK_PY (rounds counter, MAX_ROUNDS, enforce log,
# never traps) minus the gate: no subprocess, no graph, no policy — it blocks
# every Stop attempt with ONE static, content-free reason until the budget is
# exhausted, then allows. INVARIANT: the reason must never name a rule, a
# finding, a verdict string, or any task property — content-free is the point.
PLACEBO_HOOK_PY = r'''#!/usr/bin/env python3
"""verdi placebo-gate Stop hook — PART OF THE PRE-REGISTERED PLACEBO TREATMENT
DEFINITION; any edit here is a NEW pre-registration, never a casual change.

The mechanism-decomposition control for rung-3 enforcement: the SAME bounded
Stop-hook machinery (rounds counter, MAX_ROUNDS, enforce log, never traps),
but it consults NO gate and reads NO inputs. It blocks every Stop attempt with
one STATIC, content-free reason until the budget is exhausted, then allows.
Any rescue this treatment produces is attributable to forced re-review alone,
never to gate findings.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROUNDS = os.path.join(HERE, "rounds")
ENFORCE_LOG = "/workspace/artifacts/groundwork-enforce.jsonl"
MAX_ROUNDS = 3
REASON = "Review your changes for policy compliance before finishing."


def log(record):
    os.makedirs(os.path.dirname(ENFORCE_LOG), exist_ok=True)
    with open(ENFORCE_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def main():
    current = int(open(ROUNDS, encoding="utf-8").read().strip())
    if current >= MAX_ROUNDS:
        log({"round": current, "decision": "exhausted"})
        sys.exit(0)
    with open(ROUNDS, "w", encoding="utf-8") as fh:
        fh.write(str(current + 1))
    log({"round": current, "decision": "block", "findings": REASON})
    print(json.dumps({"decision": "block", "reason": REASON}))
    sys.exit(0)


try:
    main()
except SystemExit:
    raise
except Exception as exc:  # never crash the session: allow on any unexpected error
    try:
        log({"round": -1, "decision": "hook_error", "detail": repr(exc)[-800:]})
    except Exception:
        pass
    sys.exit(0)
'''


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


def render_settings(hook_script: str) -> str:
    """The ``$HOME/.claude/settings.json`` registering the rung-3 enforcement Stop hook.

    Byte-stable (sorted keys, trailing newline). The hook command is
    ``python3 <hook_script>`` at a LITERAL absolute path (never ``$HOME``), so the CLI
    — which auto-reads this file in ``--print`` mode — invokes the pre-registered gate
    hook on every Stop attempt [rung-3 treatment]."""
    settings = {
        "hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": f"python3 {hook_script}"}]}]
        }
    }
    return json.dumps(settings, indent=2, sort_keys=True) + "\n"


@dataclass(frozen=True)
class GroundworkPlan:
    """A declarative description of every side effect arming groundwork requires.

    Pure output of :func:`plan_groundwork`; :func:`apply_plan` is the only thing
    that touches the filesystem. Keeping the plan declarative lets a test assert —
    without a container — that NO write lands under ``/workspace`` except the
    ``artifacts/`` mkdir (the rung-3 enforced arm's extra writes are ALL under
    ``$HOME``), and that the argv delta is exactly ``--mcp-config`` (rung 1). A
    disabled plan carries no argv delta EXCEPT a registered ``system_prompt_extra``,
    which is a disabled plan carrying exactly one ``--append-system-prompt`` token
    (``docs/design/mechanism-decomposition-program.md``, piece 2).

    * ``symlinks`` — ``(target, link)`` pairs exposing the staged binaries on PATH.
    * ``path_bin_dir`` — the writable dir holding those links, prepended to PATH.
    * ``mkdirs`` — directories to ensure (all under ``$HOME`` except the one
      allowed ``/workspace/artifacts`` destination for the MCP log).
    * ``files`` — ``(abspath, text)`` files to write: the MCP config (under HOME)
      always; plus, when the arm is ENFORCED (rung 3), the Stop-hook script, its
      ``rounds`` counter, and ``$HOME/.claude/settings.json`` registering the hook.
    * ``copies`` — ``(src, dst)`` TREES to copy (the skill, into HOME scope).
    * ``file_copies`` — ``(src, dst)`` single FILES to copy: the rung-3 enforced arm
      preserves the pristine ``/workspace`` base graph + policy under ``$HOME`` so the
      in-loop gate is tamper-proof. Empty for every other arm.
    * ``cli_extra_args`` — CLI args inserted before the prompt: ``--mcp-config``
      always; plus ``--append-system-prompt`` when the arm declares a workflow.
    """

    enabled: bool
    symlinks: tuple[tuple[str, str], ...] = ()
    path_bin_dir: str = ""
    mkdirs: tuple[str, ...] = ()
    files: tuple[tuple[str, str], ...] = ()
    copies: tuple[tuple[str, str], ...] = ()
    file_copies: tuple[tuple[str, str], ...] = ()
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
    exactly, EXCEPT a payload declaring a registered ``system_prompt_extra``, which is
    a disabled plan carrying exactly one ``--append-system-prompt`` token — the
    prompt-only treatment, exclusive of tools/workflow (``docs/design/mechanism-decomposition-program.md``,
    piece 2). Treatment computes every path from ``home``/``workspace``/staging so a
    test can redirect them at tmp dirs and assert the /workspace rule. A treatment
    payload WITHOUT a ``workflow`` key is rung-1 availability (one
    ``--mcp-config`` token, byte-identical to the pre-rung-2 plan); with
    ``workflow: ground_verify`` it is the rung-2 instructed treatment (the
    ``--append-system-prompt`` token added). With ``workflow:
    ground_verify_enforced`` it is the rung-3 ENFORCED treatment: the argv is
    byte-identical to rung 2 (the same ``--append-system-prompt`` text, reused
    verbatim), PLUS arm-time-only enforcement writes under ``$HOME`` — the Stop-hook
    script, its ``rounds`` counter, ``$HOME/.claude/settings.json`` registering the
    hook, and the pristine base graph + policy preserved tamper-proof (see
    :data:`ENFORCEMENT_HOOK_PY`). With ``workflow: placebo_gate`` it is the
    mechanism-decomposition PLACEBO (``docs/design/mechanism-decomposition-program.md``,
    piece 1): the SAME arm-time Stop-hook writes and byte-identical argv as rung 3, but
    the hook is :data:`PLACEBO_HOOK_PY` (no gate, no inputs) and NO base graph/policy is
    copied. An invalid ``workflow`` — unknown value, or declared
    without the groundwork tool — raises rather than running inert: a silently-inert
    instruction would fake a control arm (the instrument-bug class bug #5 belonged to).

    /WORKSPACE RULE (review finding, integration plan §4): every write destination
    is under ``home`` EXCEPT ``<workspace>/artifacts`` (the MCP call-log dir, D7,
    judge-excluded). No other path under ``workspace`` appears in the plan — the
    rung-3 enforced arm's extra writes are ALL under ``home``; only its file-copy
    SOURCES read the pristine ``/workspace`` base graph + policy (reads, not writes)."""
    enabled = groundwork_enabled(payload)
    has_workflow = "workflow" in payload
    workflow = payload.get("workflow")
    if has_workflow:
        if not enabled:
            raise ValueError(
                f"payload declares workflow {workflow!r} but tools does not include "
                "'groundwork' — an instructed workflow with no tool to use is an "
                "invalid arm payload"
            )
        if workflow not in WORKFLOW_PROMPT_KEY:
            raise ValueError(
                f"unknown workflow {workflow!r}; known workflows: "
                f"{sorted(WORKFLOW_PROMPT_KEY)}"
            )
    extra = payload.get("system_prompt_extra")
    if extra is not None:
        if enabled or has_workflow:
            raise ValueError(
                f"payload declares system_prompt_extra {extra!r} alongside "
                "groundwork tools/workflow — prompt-only treatments are "
                "exclusive by definition; a combined arm would blur which "
                "treatment ran"
            )
        if extra not in SYSTEM_PROMPT_EXTRAS:
            raise ValueError(
                f"unknown system_prompt_extra {extra!r}; known extras: "
                f"{sorted(SYSTEM_PROMPT_EXTRAS)}"
            )
        return GroundworkPlan(
            enabled=False,
            cli_extra_args=(
                f"--append-system-prompt={SYSTEM_PROMPT_EXTRAS[extra]}",
            ),
        )
    if not enabled:
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

    # equals form is load-bearing on BOTH tokens: a space-separated value can
    # swallow the trailing positional prompt (--mcp-config is variadic — every
    # grounded trial of the first pilot run failed on exactly this; the same
    # hazard applies to --append-system-prompt's free-text value).
    extra_args = [f"--mcp-config={config_path}"]
    if has_workflow:
        # The enforced rung reuses rung 2's text verbatim (WORKFLOW_PROMPT_KEY maps
        # it to the ``ground_verify`` entry), so its argv is byte-identical to rung 2.
        extra_args.append(
            f"--append-system-prompt={WORKFLOW_SYSTEM_PROMPTS[WORKFLOW_PROMPT_KEY[workflow]]}"
        )

    # bin_dir + the skill parent live under $HOME; artifacts is the sole /workspace
    # destination (created so the MCP server can open its log).
    mkdirs = (bin_dir, os.path.dirname(skill_dst), artifacts)
    files = ((config_path, render_mcp_config(config)),)
    file_copies: tuple[tuple[str, str], ...] = ()
    if workflow in (ENFORCED_WORKFLOW, PLACEBO_WORKFLOW):
        # rung 3 / placebo = rung 2 + a Stop hook, realized PURELY in arm-time
        # filesystem (all under $HOME; argv is byte-identical to rung 2). The
        # ENFORCED hook needs the pristine BASE graph + graded policy preserved
        # tamper-proof beside it; the PLACEBO hook consults nothing, so it gets
        # no copies — an unread input staged anyway would blur the contrast.
        enforce_dir = os.path.join(home, "verdi-enforce")
        hook_script = os.path.join(enforce_dir, "stop_hook.py")
        settings_path = os.path.join(home, ".claude", "settings.json")
        hook_source = (
            ENFORCEMENT_HOOK_PY if workflow == ENFORCED_WORKFLOW else PLACEBO_HOOK_PY
        )
        mkdirs = (*mkdirs, enforce_dir)
        files = (
            *files,
            (os.path.join(enforce_dir, "rounds"), "0"),
            (hook_script, hook_source),
            (settings_path, render_settings(hook_script)),
        )
        if workflow == ENFORCED_WORKFLOW:
            file_copies = (
                (os.path.join(workspace, GRAPH_NAME), os.path.join(enforce_dir, "base.graph.json")),
                (os.path.join(workspace, POLICY_NAME), os.path.join(enforce_dir, "policy.json")),
            )

    return GroundworkPlan(
        enabled=True,
        symlinks=symlinks,
        path_bin_dir=bin_dir,
        mkdirs=mkdirs,
        files=files,
        copies=((staging_skill, skill_dst),),
        file_copies=file_copies,
        cli_extra_args=tuple(extra_args),
        mcp_config_path=config_path,
        artifacts_dir=artifacts,
    )


def apply_plan(plan: GroundworkPlan) -> None:
    """Realize ``plan`` on disk — the ONLY side-effecting step [integration plan §4].

    A no-op for the disabled (control) plan, so the control arm writes nothing.
    Idempotent for the enabled plan (safe to re-run): existing links/skill copies
    are replaced, file writes and single-file copies (the rung-3 preserved base
    graph/policy) overwrite in place. Fails loudly if a staged source is missing —
    an unarmed treatment must never masquerade as armed."""
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
    for src, dst in plan.file_copies:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
    for src, dst in plan.copies:
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst)


def cli_argv(prompt: str, plan: GroundworkPlan, model_id: str) -> list[str]:
    """The ``claude`` argv for this arm [integration plan §4].

    ``model_id`` is the arm's declared model with its provider prefix stripped
    (``req.model_id``). When non-empty it is delivered as a single ``--model=<id>``
    token placed right after the shared ``CLI`` tokens — BEFORE any ``--mcp-config``
    and the trailing prompt — so the CLI runs the arm's model rather than its
    built-in default. Empty ``model_id`` (the keyless ``bench images verify`` case)
    omits the flag, preserving the shipped argv.

    Control (model M): ``[*CLI, --model=M, prompt]`` — the shipped official argv +
    the arm's model.
    Rung-1 treatment (availability): ``[*CLI, --model=M, --mcp-config=<path>,
    prompt]`` — control plus EXACTLY the one ``--mcp-config`` token.
    Rung-2 treatment (``workflow: ground_verify``): ``[*CLI, --model=M,
    --mcp-config=<path>, --append-system-prompt=<pre-registered text>, prompt]``
    — rung 1 plus the one instructed-workflow token. A same-model bare/grounded
    pair therefore differs by exactly the payload-gated token(s).
    Rung-3 treatment (``workflow: ground_verify_enforced``): argv IDENTICAL to rung 2
    (the same ``--append-system-prompt`` text, reused verbatim) — enforcement is
    realized purely in arm-time filesystem (the Stop hook), never in argv."""
    # equals form on --model: a space-form flag can swallow the trailing positional
    # prompt (the same failure --mcp-config hit in the 2026-07-07 pilot).
    model = [f"--model={model_id}"] if model_id else []
    return [*CLI, *model, *plan.cli_extra_args, prompt]


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

    argv = cli_argv(req.prompt, plan, req.model_id)
    env = cli_env(os.environ, plan)
    # The CLI authenticates from ANTHROPIC_API_KEY (harbor injects it as an
    # allowlisted --env) and tunnels egress through HTTP(S)_PROXY automatically;
    # when armed, it also spawns the stdio groundwork MCP server from --mcp-config.
    try:
        proc = subprocess.run(
            argv,
            cwd=str(WORKSPACE),
            env=env,
            capture_output=True,
            text=True,
            timeout=int(req.payload.get("cli_timeout_s", 1500)),
        )
    finally:
        # Preserve the CLI's session transcripts ($HOME/.claude/projects/**/*.jsonl)
        # BEFORE any parse/raise branch so every exit path below carries them; the
        # finally exists for the inner cli_timeout_s raise (TimeoutExpired) — the
        # forensically richest window — which then propagates unchanged. Two
        # constraints make this safe: artifacts/ is excluded from the judged diff
        # (the groundwork-mcp.jsonl precedent — a transcript can never surface as a
        # judged asymmetry), and the capture is unconditional, identical on every arm.
        capture_claude_session_transcripts()
    raw = (proc.stdout or "").strip()
    try:
        parsed = json.loads(raw)
        is_object = isinstance(parsed, dict)
    except json.JSONDecodeError:
        is_object = False
    if is_object:
        # platform: claude_code — persist the CLI's OWN --output-format json result
        # verbatim; the adapter measures tokens/cost/wall-time from it. The base CLI
        # is identical across arms, so control and treatment parse the same way; the
        # treatment's tool usage stays in artifacts/groundwork-mcp.jsonl, never in
        # the native log [integration plan §4, D7]. Fields the report omits stay null
        # — never guessed [docs/adapters.md, D004].
        log.finish_native(raw)
        if proc.returncode != 0:
            result = parsed.get("result")
            tail = (result if isinstance(result, str) else (proc.stderr or "")).strip()[-400:]
            raise RuntimeError(f"claude-code CLI exited {proc.returncode}: {tail!r}")
        return
    if proc.returncode == 0:
        # Exited 0 without the JSON result contract: refuse to fabricate a log.
        raise RuntimeError(
            "claude-code CLI exited 0 without its --output-format json result "
            f"contract (stdout head {raw[:200]!r})"
        )
    # Non-JSON nonzero exit — the keyless `bench images verify` plumbing path (the
    # CLI may die before emitting JSON). Keep the narration + scorable GENERIC log,
    # exactly as before.
    detail = (proc.stdout or proc.stderr or "").strip()[:400]
    log.message(f"[{req.arm}/{req.model_id}] claude-code exit {proc.returncode}")
    log.test_run(" ".join(CLI), detail=detail, exit_code=proc.returncode)
    log.finish()
    raise RuntimeError(f"claude-code CLI exited {proc.returncode}: {detail!r}")


if __name__ == "__main__":
    run_visible(main)
