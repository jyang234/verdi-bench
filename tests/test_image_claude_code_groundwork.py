"""The payload-gated ``claude-code-groundwork`` trial image [integration plan §4, A2].

The unit tests import the image's PURE gating functions (loaded the same way the
container does — with ``images/base`` on ``sys.path`` so ``import verdi_agent``
resolves) and prove, WITHOUT a container:

* the payload gating truth table (enabled / disabled / absent payload / a ``tools``
  list without ``groundwork`` / a non-list ``tools`` — fail-closed);
* the generated MCP config content + that every launch path is absolute;
* the "nothing under ``/workspace`` except ``artifacts/``" rule — both declaratively
  (the plan's write destinations) and by applying the plan against tmp fake
  home + workspace dirs;
* the control path writes nothing and reproduces the shipped official CLI argv;
* the treatment argv is the control argv + EXACTLY the ``--mcp-config`` addition.

The ``docker``-marked smoke test at the bottom builds the real image (prebuilt
fallback) and proves the baked binaries run, the skill is staged, the CLI is
installed, and arming vs control behaves correctly end to end.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_IMG = _ROOT / "images" / "reference" / "claude-code-groundwork"
_AGENT = _IMG / "agent.py"
_OFFICIAL_AGENT = _ROOT / "images" / "official" / "anthropic-claude-code" / "agent.py"
# The rebased agents `import verdi_agent` (the stdlib-only in-image SDK at
# images/base, on the image's PYTHONPATH=/opt/verdi). Put that dir on sys.path so
# exec_module resolves the import here, mirroring the container [refactor 03 §3].
_BASE = _ROOT / "images" / "base"

# The shipped official invocation this image forks; the control arm must reproduce
# it byte-for-byte. Kept here so a drift in either agent's CLI fails a test.
# bypassPermissions (bug #5): print mode can answer no permission prompt, and
# acceptEdits auto-accepted EDITS only — every non-edit tool (incl. this image's
# MCP tools) was silently denied; the hermetic container is the sandbox.
# --output-format json makes the CLI emit its native result object on stdout, which
# both agents persist verbatim as the native agent_log.json [refactor 03 §3].
_SHIPPED_CLI = ["claude", "--print", "--permission-mode", "bypassPermissions", "--output-format", "json"]
# The treatment rungs: availability (rung 1 — tool armed, no instruction); the §6
# instructed payload (rung 2 — availability + the ground_verify workflow, which
# adds the pre-registered --append-system-prompt token); and the ENFORCED payload
# (rung 3 — rung 2 PLUS an enforcement Stop hook, argv-identical to rung 2, the
# enforcement realized purely in arm-time filesystem).
_AVAILABILITY = {"tools": ["groundwork"]}
_TREATMENT = {"tools": ["groundwork"], "workflow": "ground_verify"}
_ENFORCED = {"tools": ["groundwork"], "workflow": "ground_verify_enforced"}
_PLACEBO = {"tools": ["groundwork"], "workflow": "placebo_gate"}
_POINTER = {"system_prompt_extra": "policy_pointer"}


def _load(module_name: str, path: Path):
    if str(_BASE) not in sys.path:
        sys.path.insert(0, str(_BASE))
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so the frozen dataclass (under `from __future__ import
    # annotations`) can resolve its own module namespace on 3.11 — the container
    # runs agent.py as __main__, which is always in sys.modules; a dynamic load is
    # not, so we add it here.
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)  # import-safe: main() is guarded, gating fns pure
    return mod


agent = _load("_ccg_agent", _AGENT)


# --- payload gating truth table --------------------------------------------
def test_groundwork_enabled_truth_table():
    on = agent.groundwork_enabled
    # enabled: groundwork present in a tools list (with or without siblings)
    assert on({"tools": ["groundwork"]}) is True
    assert on(_TREATMENT) is True
    assert on({"tools": ["grep", "groundwork", "read"]}) is True
    # disabled: absent payload, no tools key, empty/other tools
    assert on({}) is False
    assert on({"workflow": "ground_verify"}) is False
    assert on({"tools": []}) is False
    assert on({"tools": ["grep", "read"]}) is False
    # fail-closed: a non-list `tools` (e.g. the bare string) never arms, even
    # though "groundwork" in "groundwork" is truthy for a str.
    assert on({"tools": "groundwork"}) is False
    assert on({"tools": None}) is False


def test_plan_disabled_is_the_empty_plan():
    plan = agent.plan_groundwork({})
    assert plan.enabled is False
    assert plan.symlinks == () and plan.files == () and plan.copies == ()
    assert plan.mkdirs == () and plan.cli_extra_args == ()
    assert plan.path_bin_dir == "" and plan.mcp_config_path == ""


def test_plan_enabled_has_every_arming_step():
    plan = agent.plan_groundwork(_TREATMENT)
    assert plan.enabled is True
    assert len(plan.symlinks) == 2  # flowmap + groundwork
    assert plan.path_bin_dir and len(plan.files) == 1 and len(plan.copies) == 1
    # equals form: the CLI's --mcp-config is variadic, so a space-separated value
    # would swallow the trailing positional prompt (pilot run 2026-07-07)
    assert plan.cli_extra_args[0].startswith("--mcp-config=")


# --- MCP config content + absolute paths -----------------------------------
def test_mcp_config_content_and_absolute_paths():
    # real defaults ($HOME=/tmp, workspace=/workspace, staging=/opt/groundwork).
    # Rung-1 (availability) payload: its cli_extra_args are pinned UNCHANGED below
    # — the exact one-token tuple rung 1 has always had.
    plan = agent.plan_groundwork(_AVAILABILITY)
    (config_path, config_text), = plan.files
    # the config file is written under $HOME, never /workspace
    assert config_path == "/tmp/groundwork.mcp.json"
    cfg = json.loads(config_text)
    server = cfg["mcpServers"]["groundwork"]
    assert server["type"] == "stdio"
    assert server["command"] == "/opt/groundwork/bin/groundwork"
    # the exact launch contract of cmd/groundwork/mcp.go (integration plan §4)
    assert server["args"] == [
        "mcp",
        "/workspace/graph.json",
        "--policy",
        "/workspace/policy.json",
        "--log",
        "/workspace/artifacts/groundwork-mcp.jsonl",
    ]
    # every path the launch names is absolute
    for tok in (server["command"], *(a for a in server["args"] if a.startswith("/"))):
        assert os.path.isabs(tok), tok
    # the --mcp-config arg points at the written config, by ABSOLUTE path, in
    # equals form (a two-token form lets the variadic flag eat the prompt)
    assert plan.cli_extra_args == ("--mcp-config=/tmp/groundwork.mcp.json",)
    assert os.path.isabs(plan.cli_extra_args[0].split("=", 1)[1])
    assert plan.mcp_config_path == config_path


def test_mcp_config_render_is_byte_stable():
    """The config is a pure function of its inputs — no run-to-run variation."""
    make = lambda: agent.render_mcp_config(  # noqa: E731
        agent.mcp_server_config(
            groundwork_bin="/opt/groundwork/bin/groundwork",
            graph="/workspace/graph.json",
            policy="/workspace/policy.json",
            log="/workspace/artifacts/groundwork-mcp.jsonl",
        )
    )
    assert make() == make()
    assert make().endswith("\n")


# --- the /workspace rule: declarative + applied ----------------------------
def test_plan_declares_no_workspace_write_except_artifacts():
    """Every filesystem DESTINATION the plan will touch is under $HOME, EXCEPT the
    single ``/workspace/artifacts`` mkdir (the judge-excluded MCP-log dir, D7) — for
    BOTH the instructed (rung 2) and enforced (rung 3) plans. The enforced plan's
    extra writes (settings.json, the Stop hook, the preserved base/policy, rounds)
    are ALL under $HOME; only its file-COPY sources read /workspace (reads, not
    writes — like the symlink targets), pinned separately below."""
    ws, home = "/workspace", "/tmp"
    for payload in (_TREATMENT, _ENFORCED):
        plan = agent.plan_groundwork(payload, home=home, workspace=ws)
        dests = [
            *plan.mkdirs,
            *(link for _, link in plan.symlinks),
            *(path for path, _ in plan.files),
            *(dst for _, dst in plan.copies),
            *(dst for _, dst in plan.file_copies),  # rung-3 single-file copies
        ]
        under_ws = [d for d in dests if d == ws or d.startswith(ws + os.sep)]
        assert under_ws == [os.path.join(ws, "artifacts")], (payload, under_ws)
        # the plan names that one workspace destination explicitly
        assert plan.artifacts_dir == os.path.join(ws, "artifacts")
        # and the symlink TARGETS (reads, not writes) point at the off-PATH staging dir
        assert all(t.startswith("/opt/groundwork/bin/") for t, _ in plan.symlinks)
    # the enforced plan's file-copy SOURCES are the two pristine /workspace assets —
    # reads (the base graph + graded policy), preserved tamper-proof under $HOME.
    enforced = agent.plan_groundwork(_ENFORCED, home=home, workspace=ws)
    assert {src for src, _ in enforced.file_copies} == {
        "/workspace/graph.json", "/workspace/policy.json"
    }


def test_apply_writes_only_artifacts_under_workspace(tmp_path):
    home = tmp_path / "home"
    ws = tmp_path / "ws"
    staging_bin = tmp_path / "opt" / "bin"
    staging_skill = tmp_path / "opt" / "skills" / "groundwork-workflow"
    for d in (home, ws, staging_bin, staging_skill):
        d.mkdir(parents=True)
    # fake staged sources so the symlinks + skill copy have real targets
    (staging_bin / "flowmap").write_text("#!/bin/true\n", encoding="utf-8")
    (staging_bin / "groundwork").write_text("#!/bin/true\n", encoding="utf-8")
    (staging_skill / "SKILL.md").write_text(
        "---\nname: groundwork-workflow\n---\n", encoding="utf-8"
    )

    plan = agent.plan_groundwork(
        _TREATMENT,
        home=str(home),
        workspace=str(ws),
        staging_bin=str(staging_bin),
        staging_skill=str(staging_skill),
    )
    agent.apply_plan(plan)

    # /workspace: ONLY artifacts/ exists (recursively), and it's an empty dir the
    # MCP server will populate — no groundwork-branded file loose in the diff.
    assert {str(p.relative_to(ws)) for p in ws.rglob("*")} == {"artifacts"}
    assert (ws / "artifacts").is_dir()

    # $HOME carries the config + skill + the on-PATH symlinks
    assert (home / "groundwork.mcp.json").is_file()
    assert (home / ".claude" / "skills" / "groundwork-workflow" / "SKILL.md").is_file()
    for b in ("flowmap", "groundwork"):
        link = home / ".local" / "bin" / b
        assert link.is_symlink()
        assert os.readlink(link) == str(staging_bin / b)


def test_apply_is_idempotent(tmp_path):
    """Re-arming (a re-run) replaces links/copies without error."""
    home, ws = tmp_path / "home", tmp_path / "ws"
    staging_bin = tmp_path / "opt" / "bin"
    staging_skill = tmp_path / "opt" / "skills" / "groundwork-workflow"
    for d in (home, ws, staging_bin, staging_skill):
        d.mkdir(parents=True)
    (staging_bin / "flowmap").write_text("x", encoding="utf-8")
    (staging_bin / "groundwork").write_text("x", encoding="utf-8")
    (staging_skill / "SKILL.md").write_text("---\nname: groundwork-workflow\n---\n", encoding="utf-8")
    plan = agent.plan_groundwork(
        _TREATMENT, home=str(home), workspace=str(ws),
        staging_bin=str(staging_bin), staging_skill=str(staging_skill),
    )
    agent.apply_plan(plan)
    agent.apply_plan(plan)  # must not raise
    assert (home / ".claude" / "skills" / "groundwork-workflow" / "SKILL.md").is_file()


# --- control: zero writes + the shipped argv/env ---------------------------
def test_control_apply_writes_nothing(tmp_path):
    home, ws = tmp_path / "home", tmp_path / "ws"
    home.mkdir()
    ws.mkdir()
    plan = agent.plan_groundwork({}, home=str(home), workspace=str(ws))
    agent.apply_plan(plan)
    assert list(home.iterdir()) == []
    assert list(ws.iterdir()) == []


def test_control_argv_and_env_are_the_shipped_official():
    plan = agent.plan_groundwork({})
    # empty model_id (the keyless `bench images verify` case) → the shipped official
    # argv, byte-for-byte, with no --model token.
    assert agent.cli_argv("do the task", plan, "") == [*_SHIPPED_CLI, "do the task"]
    base = {"PATH": "/usr/local/go/bin:/usr/bin", "HOME": "/tmp"}
    assert agent.cli_env(base, plan) == base  # unmodified copy


def test_cli_constant_matches_the_official_image():
    """The base CLI is byte-identical to the shipped anthropic-claude-code agent —
    the control arm forks nothing about the invocation [integration plan §4]."""
    official = _load("_official_cc_agent", _OFFICIAL_AGENT)
    assert agent.CLI == official.CLI == _SHIPPED_CLI


# --- treatment argv = control argv + only --mcp-config ---------------------
def test_treatment_argv_is_control_plus_only_mcp_config():
    control = agent.plan_groundwork({})
    treatment = agent.plan_groundwork(_AVAILABILITY)  # rung 1: tool only, no workflow
    # SAME request → the SAME arm-declared model in both arms (a bare-vs-grounded
    # pair shares its model id), so the ONLY argv delta is the treatment's one
    # --mcp-config token — the rung-1 arm-insulation pin, --model present in both.
    model_id = "claude-haiku-4-5-20251001"
    control_argv = agent.cli_argv("PROMPT", control, model_id)
    treatment_argv = agent.cli_argv("PROMPT", treatment, model_id)
    # the delta is EXACTLY one --mcp-config=<path> token, inserted before the
    # prompt. Equals form is load-bearing: the CLI flag is variadic, so the
    # two-token space form swallowed the trailing prompt as a second config
    # path (every grounded trial of the 2026-07-07 pilot died on it).
    assert treatment_argv == (
        control_argv[:-1] + ["--mcp-config=/tmp/groundwork.mcp.json"] + control_argv[-1:]
    )
    # both arms carry the identical --model; removing the one --mcp-config delta
    # reproduces the control argv exactly.
    assert control_argv == [*_SHIPPED_CLI, f"--model={model_id}", "PROMPT"]
    assert treatment_argv == [
        *_SHIPPED_CLI, f"--model={model_id}", "--mcp-config=/tmp/groundwork.mcp.json", "PROMPT"
    ]
    stripped = list(treatment_argv)
    stripped.remove("--mcp-config=/tmp/groundwork.mcp.json")
    assert stripped == control_argv


def test_treatment_argv_places_model_after_cli_before_mcp_config():
    """--model= comes from the arm's own declared model and sits right after the
    shared CLI tokens, BEFORE the treatment's --mcp-config and the trailing prompt
    (rung-1 payload; the rung-2 ordering is pinned separately below)."""
    treatment = agent.plan_groundwork(_AVAILABILITY)
    argv = agent.cli_argv("PROMPT", treatment, "claude-haiku-4-5-20251001")
    assert argv == [
        *_SHIPPED_CLI, "--model=claude-haiku-4-5-20251001",
        "--mcp-config=/tmp/groundwork.mcp.json", "PROMPT",
    ]
    assert argv.index("--model=claude-haiku-4-5-20251001") < argv.index(
        "--mcp-config=/tmp/groundwork.mcp.json"
    )


# --- rung 2: the instructed (ground_verify) workflow -------------------------
def test_ground_verify_plan_appends_system_prompt_after_mcp_config():
    """The instructed rung's cli_extra_args are EXACTLY the two payload-gated
    tokens, mcp-config first, both equals-form, and cli_argv renders
    [*CLI, --model=, --mcp-config=, --append-system-prompt=, prompt]."""
    plan = agent.plan_groundwork(_TREATMENT)
    text = agent.WORKFLOW_SYSTEM_PROMPTS["ground_verify"]
    assert plan.cli_extra_args == (
        "--mcp-config=/tmp/groundwork.mcp.json",
        f"--append-system-prompt={text}",
    )
    argv = agent.cli_argv("PROMPT", plan, "claude-haiku-4-5-20251001")
    assert argv == [
        *_SHIPPED_CLI, "--model=claude-haiku-4-5-20251001",
        "--mcp-config=/tmp/groundwork.mcp.json",
        f"--append-system-prompt={text}", "PROMPT",
    ]


def test_ground_verify_side_effects_identical_to_availability():
    """The instructed rung differs from availability by ONLY the argv token: the
    on-disk arming (symlinks/mkdirs/files/copies) is identical, so the workspace
    rule and MCP config need no per-rung re-proof."""
    rung1 = agent.plan_groundwork(_AVAILABILITY)
    rung2 = agent.plan_groundwork(_TREATMENT)
    assert (rung1.symlinks, rung1.mkdirs, rung1.files, rung1.copies) == (
        rung2.symlinks, rung2.mkdirs, rung2.files, rung2.copies
    )
    assert rung2.cli_extra_args[0] == rung1.cli_extra_args[0]
    assert len(rung2.cli_extra_args) == 2 and len(rung1.cli_extra_args) == 1


def test_append_system_prompt_carries_registered_text_verbatim():
    """The token's value is the WORKFLOW_SYSTEM_PROMPTS entry byte-for-byte — the
    text is part of the pre-registered treatment definition, so this pin carries a
    FULL second copy: any constant edit fails here and must be deliberate (a new
    pre-registration, per the constant's own comment)."""
    # The 2026-07-08 revision (pre-lock manipulation-pilot iteration): names the
    # literal MCP tool identifiers a small model pattern-matches on, and routes
    # verification through the `groundwork verify` BINARY in Bash — the MCP surface
    # is read-only per the vendored skill; the old text misnamed verify as a tool.
    expected = (
        "This workspace is governed by a structural policy (`policy.json`) enforced by the "
        "groundwork merge gate; structurally non-compliant changes are rejected. The following "
        "workflow is mandatory. (1) BEFORE editing any Go function, call the "
        "`mcp__groundwork__ground` tool on that function's fully-qualified name and treat its "
        "binding rules as constraints on your edit; if unsure whether an edit is local, check "
        "blast radius with `mcp__groundwork__reach`. (2) AFTER your edits, regenerate the graph "
        "by running `flowmap graph` in Bash, then call `mcp__groundwork__reload`. (3) Run "
        "`groundwork verify` in Bash and fix every finding. Do not conclude while "
        "`groundwork verify` reports anything other than STRUCTURALLY-CLEAR."
    )
    assert agent.WORKFLOW_SYSTEM_PROMPTS["ground_verify"] == expected
    plan = agent.plan_groundwork(_TREATMENT)
    (tok,) = [t for t in plan.cli_extra_args if t.startswith("--append-system-prompt=")]
    assert tok.split("=", 1)[1] == expected  # delivered verbatim, byte-for-byte


def test_unknown_workflow_raises_naming_it():
    """A workflow value outside the registered set is an invalid arm payload —
    refused loudly, never run inert (silent-inert would fake a control)."""
    with pytest.raises(ValueError) as ei:
        agent.plan_groundwork({"tools": ["groundwork"], "workflow": "vibe_check"})
    assert "vibe_check" in str(ei.value) and "ground_verify" in str(ei.value)


def test_workflow_without_groundwork_tool_raises():
    """An instructed workflow with no tool to use is an invalid arm payload."""
    for payload in (
        {"workflow": "ground_verify"},
        {"tools": [], "workflow": "ground_verify"},
        {"tools": ["grep"], "workflow": "ground_verify"},
    ):
        with pytest.raises(ValueError):
            agent.plan_groundwork(payload)


def test_argv_omits_model_flag_when_model_empty_in_both_arms():
    """Empty model_id (keyless verify) omits --model in control AND treatment; the
    treatment then differs from the shipped argv by only --mcp-config."""
    for payload in ({}, _TREATMENT):
        argv = agent.cli_argv("PROMPT", agent.plan_groundwork(payload), "")
        assert not any(a.startswith("--model") for a in argv)
    assert agent.cli_argv("PROMPT", agent.plan_groundwork({}), "") == [*_SHIPPED_CLI, "PROMPT"]


def test_treatment_env_prepends_bin_dir_to_path():
    plan = agent.plan_groundwork(_TREATMENT)
    env = agent.cli_env({"PATH": "/usr/bin"}, plan)
    assert env["PATH"] == "/tmp/.local/bin" + os.pathsep + "/usr/bin"


# --- rung 3: the enforced (ground_verify_enforced) treatment -----------------
# Rung 3 = rung 2 PLUS an enforcement Stop hook, realized PURELY in arm-time
# filesystem (a $HOME/.claude/settings.json Stop hook + its script and preserved,
# tamper-proof gate inputs under $HOME). The argv delta vs rung 2 is NONE: the
# --append-system-prompt text is rung 2's ground_verify entry VERBATIM, so a
# rung3-vs-rung2 contrast isolates the enforcement hook alone.
def test_enforced_plan_arms_stop_hook_under_home_and_argv_equals_rung2(tmp_path):
    home = tmp_path / "home"
    ws = tmp_path / "ws"
    staging_bin = tmp_path / "opt" / "bin"
    staging_skill = tmp_path / "opt" / "skills" / "groundwork-workflow"
    for d in (home, ws, staging_bin, staging_skill):
        d.mkdir(parents=True)
    (staging_bin / "flowmap").write_text("#!/bin/true\n", encoding="utf-8")
    (staging_bin / "groundwork").write_text("#!/bin/true\n", encoding="utf-8")
    (staging_skill / "SKILL.md").write_text(
        "---\nname: groundwork-workflow\n---\n", encoding="utf-8")
    # the pristine BASE graph + graded policy the workspace ships (the copy sources;
    # apply_plan runs before the CLI, so /workspace is still pristine here).
    (ws / "graph.json").write_text('{"base":true}\n', encoding="utf-8")
    (ws / "policy.json").write_text('{"substrate":"vta"}\n', encoding="utf-8")

    kw = dict(home=str(home), workspace=str(ws),
              staging_bin=str(staging_bin), staging_skill=str(staging_skill))
    plan = agent.plan_groundwork(_ENFORCED, **kw)
    rung2 = agent.plan_groundwork(_TREATMENT, **kw)
    # argv delta vs rung 2 is NONE — the enforced arm's CLI args are byte-identical.
    assert plan.cli_extra_args == rung2.cli_extra_args

    agent.apply_plan(plan)

    # /workspace: apply added ONLY artifacts/ (graph.json + policy.json pre-existed as
    # the copy sources) — nothing groundwork-branded lands loose in the graded tree.
    assert {str(p.relative_to(ws)) for p in ws.rglob("*")} == {
        "graph.json", "policy.json", "artifacts"}

    enforce = home / "verdi-enforce"
    # the pristine base graph + policy are preserved byte-for-byte under $HOME so the
    # in-loop gate cannot be defeated by editing /workspace/policy.json.
    assert (enforce / "base.graph.json").read_text(encoding="utf-8") == '{"base":true}\n'
    assert (enforce / "policy.json").read_text(encoding="utf-8") == '{"substrate":"vta"}\n'
    # the round counter starts at 0, and the byte-stable hook script is staged verbatim.
    assert (enforce / "rounds").read_text(encoding="utf-8") == "0"
    assert (enforce / "stop_hook.py").read_text(encoding="utf-8") == agent.ENFORCEMENT_HOOK_PY
    # settings.json registers the Stop hook at the LITERAL staged-script path (the CLI
    # auto-reads $HOME/.claude/settings.json in --print mode).
    settings = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    (stop_matcher,) = settings["hooks"]["Stop"]
    (hook,) = stop_matcher["hooks"]
    assert hook["type"] == "command"
    assert hook["command"] == f"python3 {enforce / 'stop_hook.py'}"


def test_lower_rungs_arm_no_stop_hook_or_settings():
    """INSULATION PIN: bare, availability (rung 1), and ground_verify (rung 2) plans
    declare NO settings.json, NO stop_hook.py, and NO file copies — enforcement is
    rung-3-only, so a rung3-vs-rung2 contrast isolates the Stop hook alone."""
    for payload in ({}, _AVAILABILITY, _TREATMENT):
        plan = agent.plan_groundwork(payload)
        assert plan.file_copies == ()
        dests = [p for p, _ in plan.files] + [dst for _, dst in plan.file_copies]
        assert not any(
            os.path.basename(d) in ("settings.json", "stop_hook.py") for d in dests
        ), (payload, dests)


def test_enforced_workflow_without_groundwork_tool_raises():
    """An enforced workflow with no tool to use is an invalid arm payload — refused
    loudly (consistent with the rung-2 unknown/tool-less handling)."""
    for payload in (
        {"workflow": "ground_verify_enforced"},
        {"tools": [], "workflow": "ground_verify_enforced"},
        {"tools": ["grep"], "workflow": "ground_verify_enforced"},
    ):
        with pytest.raises(ValueError):
            agent.plan_groundwork(payload)


def test_enforcement_hook_is_valid_python():
    """The pre-registered Stop-hook script compiles — it is written to disk and run
    by ``python3`` in the trial container, so a syntax error would silently disable
    enforcement on every enforced trial."""
    compile(agent.ENFORCEMENT_HOOK_PY, "<enforcement_hook>", "exec")


# --- placebo_gate: the mechanism-decomposition control ----------------------
# [design: docs/design/mechanism-decomposition-program.md, piece 1] The placebo
# arm is byte-identical to rung 3 EXCEPT the hook: same tools, same rung-2
# prompt token, same rounds/settings machinery — but the hook runs NO gate and
# blocks with ONE static, content-free reason. Any rescue it produces is
# attributable to forced re-review alone.


def test_placebo_plan_arms_static_hook_without_gate_inputs():
    plan = agent.plan_groundwork(_PLACEBO, home="/h", workspace="/w")
    files = dict(plan.files)
    assert files["/h/verdi-enforce/rounds"] == "0"
    assert files["/h/verdi-enforce/stop_hook.py"] == agent.PLACEBO_HOOK_PY
    assert "/h/.claude/settings.json" in files
    # the placebo reads no gate inputs: no tamper-proof base graph/policy copies
    assert plan.file_copies == ()


def test_placebo_argv_is_byte_identical_to_rung2_and_rung3():
    args = {
        wf: agent.plan_groundwork(p, home="/h", workspace="/w").cli_extra_args
        for wf, p in (("r2", _TREATMENT), ("r3", _ENFORCED), ("placebo", _PLACEBO))
    }
    assert args["placebo"] == args["r2"] == args["r3"]


def test_placebo_hook_is_valid_python_and_runs_no_gate():
    compile(agent.PLACEBO_HOOK_PY, "stop_hook.py", "exec")
    # structurally incapable of consulting the gate or the map
    assert "subprocess" not in agent.PLACEBO_HOOK_PY
    assert "/opt/groundwork/bin" not in agent.PLACEBO_HOOK_PY
    assert "flowmap" not in agent.PLACEBO_HOOK_PY
    assert "policy.json" not in agent.PLACEBO_HOOK_PY
    assert "graph" not in agent.PLACEBO_HOOK_PY


def test_placebo_block_reason_is_static_and_content_free():
    assert (
        "Review your changes for policy compliance before finishing."
        in agent.PLACEBO_HOOK_PY
    )
    # never leaks rule names, findings vocabulary, or verdict strings
    for token in ("must_not_reach", "no_concurrent_reach", "must_pass_through",
                  "io_budget", "STRUCTURALLY", "new_violations"):
        assert token not in agent.PLACEBO_HOOK_PY


def test_placebo_without_groundwork_tool_raises():
    with pytest.raises(ValueError, match="workflow"):
        agent.plan_groundwork({"workflow": "placebo_gate"})


# --- policy_pointer: the cheapest-possible treatment -------------------------
# [design: docs/design/mechanism-decomposition-program.md, piece 2] Prompt-only:
# one appended system-prompt line pointing at policy.json — no tools, no MCP
# config, no hook, no filesystem writes. A rung below availability on the
# integration ladder ("rung 1.5" if it works, a null that strengthens the
# forcing-function story if it doesn't).


def test_pointer_argv_is_control_plus_exactly_one_prompt_token():
    plan = agent.plan_groundwork(_POINTER, home="/h", workspace="/w")
    control = agent.plan_groundwork({}, home="/h", workspace="/w")
    argv = agent.cli_argv("do the task", plan, "m1")
    control_argv = agent.cli_argv("do the task", control, "m1")
    delta = [t for t in argv if t not in control_argv]
    assert delta == [
        "--append-system-prompt=" + agent.SYSTEM_PROMPT_EXTRAS["policy_pointer"]
    ]
    assert not any(t.startswith("--mcp-config") for t in argv)


def test_pointer_plan_is_otherwise_disabled_and_writes_nothing(tmp_path):
    home, workspace = tmp_path / "home", tmp_path / "workspace"
    workspace.mkdir()
    plan = agent.plan_groundwork(_POINTER, home=str(home), workspace=str(workspace))
    assert plan.enabled is False
    assert plan.files == () and plan.copies == () and plan.file_copies == ()
    assert plan.symlinks == () and plan.mkdirs == ()
    agent.apply_plan(plan)  # must be a no-op
    assert not home.exists()
    assert list(workspace.iterdir()) == []
    # env untouched: no bin dir prepended
    assert agent.cli_env({"PATH": "/usr/bin"}, plan) == {"PATH": "/usr/bin"}


def test_pointer_text_is_pinned_and_process_only():
    text = agent.SYSTEM_PROMPT_EXTRAS["policy_pointer"]
    assert text == (
        "This repository declares structural policy in `policy.json`; "
        "your change must honor it."
    )
    # names no tool, no workflow step, no task property
    for token in ("mcp__", "groundwork", "flowmap", "verify"):
        assert token not in text


def test_pointer_combined_with_tools_or_workflow_raises():
    with pytest.raises(ValueError, match="system_prompt_extra"):
        agent.plan_groundwork(
            {"system_prompt_extra": "policy_pointer", "tools": ["groundwork"]}
        )
    with pytest.raises(ValueError):
        agent.plan_groundwork(
            {"system_prompt_extra": "policy_pointer", "workflow": "ground_verify"}
        )


def test_unknown_system_prompt_extra_raises():
    with pytest.raises(ValueError, match="unknown system_prompt_extra"):
        agent.plan_groundwork({"system_prompt_extra": "not-a-registered-extra"})


# --- main(): the CLI runs in JSON mode and persists the native result --------
# The control arm is exercised here (payload {} → apply_plan is a no-op, no staged
# binaries needed); the JSON-emission path is identical across arms, so this pins
# the shared behavior. subprocess.run is mocked — the external boundary — and the
# SDK's artifact paths are redirected off /workspace.
_RESULT_JSON = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "Refactored reach() and its callers.",
    "total_cost_usd": 0.0711,
    "usage": {
        "input_tokens": 2048,
        "output_tokens": 512,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 256,
    },
    "num_turns": 9,
    "duration_ms": 12500,
    "session_id": "sess-gw-1",
}


class _FakeReq:
    prompt = "do the task"
    arm = "armA"
    model_id = "claude-x"
    payload: dict = {}  # control: apply_plan is a no-op, no staged toolchain needed


@pytest.fixture
def gw_env(monkeypatch, tmp_path):
    """Redirect the SDK artifact paths at a tmp dir + stub the request read."""
    import verdi_agent as va

    artifacts = tmp_path / "artifacts"
    log_path = artifacts / "agent_log.json"
    monkeypatch.setattr(va, "ARTIFACTS", artifacts)
    monkeypatch.setattr(va, "AGENT_LOG_PATH", log_path)
    monkeypatch.setattr(agent, "read_request", lambda: _FakeReq())
    # Hermetic HOME: session-transcript capture reads $HOME/.claude/projects, and
    # these tests must never see (or copy) the developer's real ~/.claude. (main's
    # plan_groundwork also reads HOME; the control payload keeps it a no-op.)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return va, log_path


def _mock_run(monkeypatch, *, stdout, returncode, stderr=""):
    captured = {}

    def run(argv, **kw):
        captured["argv"] = argv
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(agent.subprocess, "run", run)
    return captured


def test_main_exit0_valid_json_persists_native_and_adapter_reads_it(gw_env, monkeypatch):
    from harness.adapters.claude_code import ClaudeCodeAdapter

    va, log_path = gw_env
    raw = json.dumps(_RESULT_JSON)
    captured = _mock_run(monkeypatch, stdout=raw + "\n", returncode=0)

    agent.main(va.AgentLog())

    content = log_path.read_text(encoding="utf-8")
    assert content == raw  # exactly the (stripped) stdout, verbatim
    t = ClaudeCodeAdapter().normalize(json.loads(content))
    assert t.cost == 0.0711
    assert (t.tokens_in, t.tokens_out, t.tokens_cache) == (2048, 512, 256)
    assert t.wall_time_s == 12.5
    # control-arm argv = the shipped CLI + the arm model (equals form) + prompt
    # (_FakeReq.model_id == "claude-x").
    assert captured["argv"] == [*_SHIPPED_CLI, "--model=claude-x", "do the task"]


def test_main_exit3_valid_json_persists_native_and_raises(gw_env, monkeypatch):
    va, log_path = gw_env
    err = {**_RESULT_JSON, "is_error": True, "subtype": "error_during_execution",
           "result": "fatal: broke near the end"}
    raw = json.dumps(err)
    _mock_run(monkeypatch, stdout=raw, returncode=3, stderr="stderr tail")

    with pytest.raises(RuntimeError) as ei:
        agent.main(va.AgentLog())
    assert "3" in str(ei.value)
    assert log_path.read_text(encoding="utf-8") == raw
    assert json.loads(log_path.read_text(encoding="utf-8"))["is_error"] is True


def test_main_exit0_non_json_refuses_to_fabricate(gw_env, monkeypatch):
    va, log_path = gw_env
    _mock_run(monkeypatch, stdout="hello, not json at all", returncode=0)

    with pytest.raises(RuntimeError) as ei:
        agent.main(va.AgentLog())
    assert "json result" in str(ei.value).lower()
    assert not log_path.exists()  # no fabricated native log


def test_main_exit3_non_json_writes_generic_log_with_test_run(gw_env, monkeypatch):
    """The keyless plumbing path is unchanged: a scorable generic log with test_run."""
    va, log_path = gw_env
    _mock_run(monkeypatch, stdout="CLI crashed: no auth", returncode=3)

    with pytest.raises(RuntimeError):
        agent.main(va.AgentLog())
    written = json.loads(log_path.read_text(encoding="utf-8"))
    assert written["verdi_log_version"] == 1
    kinds = [s["kind"] for s in written["trajectory"]]
    assert "test_run" in kinds and "message" in kinds
    (test_step,) = [s for s in written["trajectory"] if s["kind"] == "test_run"]
    assert test_step["exit_code"] == 3


# --- session-transcript capture (flight-recorder evidence; symmetric) --------
# Same behavior as the official agent (shared verdi_agent helper): every
# $HOME/.claude/projects/**/*.jsonl lands under artifacts/claude-session/ with the
# projects-relative path preserved, on EVERY exit path, identically across arms.
_TRANSCRIPTS = {"a/s1.jsonl": '{"type":"user"}\n', "b/s2.jsonl": '{"type":"assistant"}\n'}


def _plant_transcripts(tmp_path):
    projects = tmp_path / "home" / ".claude" / "projects"
    for rel, text in _TRANSCRIPTS.items():
        p = projects / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def test_session_transcripts_captured_on_success(gw_env, monkeypatch, tmp_path):
    va, _ = gw_env
    _plant_transcripts(tmp_path)
    _mock_run(monkeypatch, stdout=json.dumps(_RESULT_JSON), returncode=0)

    agent.main(va.AgentLog())

    dest = tmp_path / "artifacts" / "claude-session"
    for rel, text in _TRANSCRIPTS.items():
        assert (dest / rel).read_text(encoding="utf-8") == text  # slug dirs preserved


def test_session_transcripts_captured_on_nonjson_failure_path(gw_env, monkeypatch, tmp_path):
    """Capture precedes the parse/raise branching: the keyless nonzero-exit
    non-JSON fallback path still carries the transcripts."""
    va, _ = gw_env
    _plant_transcripts(tmp_path)
    _mock_run(monkeypatch, stdout="CLI crashed: no auth", returncode=3)

    with pytest.raises(RuntimeError):
        agent.main(va.AgentLog())
    dest = tmp_path / "artifacts" / "claude-session"
    for rel in _TRANSCRIPTS:
        assert (dest / rel).is_file()


def test_session_transcripts_captured_on_inner_timeout(gw_env, monkeypatch, tmp_path):
    """The inner cli_timeout_s raise (subprocess.TimeoutExpired) still captures —
    a timed-out trial is the forensically richest window — and the exception
    propagates unchanged to run_visible."""
    va, _ = gw_env
    _plant_transcripts(tmp_path)

    def run(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(agent.subprocess, "run", run)

    with pytest.raises(subprocess.TimeoutExpired):
        agent.main(va.AgentLog())
    dest = tmp_path / "artifacts" / "claude-session"
    for rel in _TRANSCRIPTS:
        assert (dest / rel).is_file()


def test_no_transcripts_no_claude_session_dir(gw_env, monkeypatch, tmp_path):
    """Absent transcripts write nothing and create no dir — supplementary evidence,
    visibly absent downstream, never a failure."""
    va, _ = gw_env
    _mock_run(monkeypatch, stdout=json.dumps(_RESULT_JSON), returncode=0)

    agent.main(va.AgentLog())

    assert not (tmp_path / "artifacts" / "claude-session").exists()


# --- image-directory self-consistency (no docker) --------------------------
def test_vendored_skill_present_with_frontmatter_and_provenance():
    skill = _IMG / "skill" / "groundwork-workflow" / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    assert text.startswith("---\n") and "name: groundwork-workflow" in text
    prov = (_IMG / "skill" / "PROVENANCE").read_text(encoding="utf-8")
    assert "golang-code-graph" in prov and re.search(r"[0-9a-f]{40}", prov)


def test_dockerfile_reuses_pinning_pattern_and_stages_off_path():
    df = (_IMG / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM verdi-base" in df
    assert "golang:1.25.11-bookworm" in df
    # the grader's fail-closed empty-ref check + prebuilt fallback, reused
    assert "GROUNDWORK_REF must be set" in df
    assert "GROUNDWORK_PREBUILT" in df
    # binaries staged at a non-PATH location (agent.py exposes them per-arm)
    assert "/opt/groundwork/bin" in df


def test_dockerfile_pins_a_skills_capable_cli_version():
    """The pinned CLI must support user-scope skills; agent skills first auto-loaded
    from ~/.claude/skills in @anthropic-ai/claude-code 2.1.157 (changelog)."""
    df = (_IMG / "Dockerfile").read_text(encoding="utf-8")
    m = re.search(r"ARG CLAUDE_CODE_VERSION=(\d+)\.(\d+)\.(\d+)", df)
    assert m, "CLAUDE_CODE_VERSION must pin a concrete x.y.z default"
    assert tuple(int(g) for g in m.groups()) >= (2, 1, 157)


# --- docker-marked: a REAL build + arming smoke ----------------------------
from tests.fixtures.docker import DOCKER_AVAILABLE  # noqa: E402

_IMAGE_TAG = "verdi-bench/claude-code-groundwork-smoke:latest"
_FLOWMAP_BIN = os.environ.get("VERDI_FLOWMAP_BIN")
_GROUNDWORK_BIN = os.environ.get("VERDI_GROUNDWORK_BIN")
_BINARIES_SET = bool(_FLOWMAP_BIN and _GROUNDWORK_BIN)

_needs_daemon = pytest.mark.skipif(not DOCKER_AVAILABLE, reason="no docker daemon available")
_needs_binaries = pytest.mark.skipif(
    not _BINARIES_SET,
    reason="set VERDI_FLOWMAP_BIN and VERDI_GROUNDWORK_BIN (sibling-built) to bake "
    "the trial image via the prebuilt fallback (build is --pull for base images only)",
)


def _run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, **kw)


def _build_image(tmp_path: Path) -> None:
    """Build verdi-base, then the trial image via the prebuilt fallback: assemble a
    self-contained context (a copy of the image dir) with the sibling binaries in
    bin/, so no `go install` (network) is needed at build time."""
    base = _run(["docker", "build", "-t", "verdi-base", str(_ROOT / "images" / "base")])
    assert base.returncode == 0, f"verdi-base build failed:\n{base.stderr[-2000:]}"
    ctx = tmp_path / "ctx"
    shutil.copytree(_IMG, ctx)
    shutil.copy(_FLOWMAP_BIN, ctx / "bin" / "flowmap")
    shutil.copy(_GROUNDWORK_BIN, ctx / "bin" / "groundwork")
    proc = _run([
        "docker", "build", "--build-arg", "GROUNDWORK_PREBUILT=1",
        "-t", _IMAGE_TAG, str(ctx),
    ])
    assert proc.returncode == 0, f"trial image build failed:\n{proc.stderr[-3000:]}"


@_needs_daemon
@_needs_binaries
def test_docker_binaries_baked_runnable_off_path_and_cli_installed(tmp_path):
    _build_image(tmp_path)
    # the staged binaries run from their absolute staging path
    for b in ("flowmap", "groundwork"):
        p = _run(["docker", "run", "--rm", "--entrypoint", f"/opt/groundwork/bin/{b}", _IMAGE_TAG, "version"])
        assert p.returncode == 0 and b in p.stdout, p.stderr
    # but they are NOT on the default PATH (control asymmetry) — only Go is
    onpath = _run(["docker", "run", "--rm", "--entrypoint", "bash", _IMAGE_TAG, "-lc",
                   "command -v flowmap || echo NONE"])
    assert onpath.stdout.strip() == "NONE", onpath.stdout
    # the vendored skill is staged and the CLI is installed
    skill = _run(["docker", "run", "--rm", "--entrypoint", "cat", _IMAGE_TAG,
                  "/opt/groundwork/skills/groundwork-workflow/SKILL.md"])
    assert skill.returncode == 0 and "groundwork-workflow" in skill.stdout
    cli = _run(["docker", "run", "--rm", "--entrypoint", "bash", _IMAGE_TAG, "-lc", "command -v claude"])
    assert cli.returncode == 0 and cli.stdout.strip()


_ARM_SMOKE = r"""
import os, sys, shutil, subprocess
sys.path.insert(0, '/')
import agent
plan = agent.plan_groundwork({'tools': ['groundwork']})
agent.apply_plan(plan)
home = os.environ.get('HOME') or '/tmp'
assert os.path.isfile(home + '/groundwork.mcp.json'), 'no MCP config in $HOME'
assert os.path.isfile(home + '/.claude/skills/groundwork-workflow/SKILL.md'), 'skill not in $HOME scope'
for b in ('flowmap', 'groundwork'):
    link = home + '/.local/bin/' + b
    assert os.path.islink(link) and os.access(link, os.X_OK), 'binary not exposed: ' + b
# the exposed binaries are findable on the treatment PATH (unambiguous: explicit
# path arg), and run via their symlink (absolute path avoids subprocess's parent-
# PATH resolution quirk)
env = agent.cli_env(os.environ, plan)
assert shutil.which('flowmap', path=env['PATH']) == home + '/.local/bin/flowmap'
out = subprocess.run([home + '/.local/bin/flowmap', 'version'], capture_output=True, text=True)
assert out.returncode == 0 and 'flowmap' in out.stdout, out.stderr
print('ARMED_OK')
"""

_CONTROL_SMOKE = r"""
import os, sys, shutil
sys.path.insert(0, '/')
import agent
plan = agent.plan_groundwork({})
agent.apply_plan(plan)
assert plan.enabled is False
home = os.environ.get('HOME') or '/tmp'
assert not os.path.exists(home + '/groundwork.mcp.json')
assert not os.path.exists(home + '/.claude/skills/groundwork-workflow')
assert not os.path.exists(home + '/.local/bin/flowmap')
assert shutil.which('flowmap') is None and shutil.which('groundwork') is None
assert agent.cli_argv('P', plan, '') == agent.CLI + ['P']  # shipped argv (empty model)
assert '--mcp-config' not in agent.cli_argv('P', plan, '')
print('CONTROL_OK')
"""


@_needs_daemon
@_needs_binaries
def test_docker_treatment_arms_from_home_scope_and_control_does_not(tmp_path):
    _build_image(tmp_path)
    armed = _run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "python",
                  _IMAGE_TAG, "-c", _ARM_SMOKE])
    assert armed.returncode == 0 and "ARMED_OK" in armed.stdout, armed.stderr
    control = _run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "python",
                    _IMAGE_TAG, "-c", _CONTROL_SMOKE])
    assert control.returncode == 0 and "CONTROL_OK" in control.stdout, control.stderr
