# claude-code-groundwork (reference image)

The payload-gated **groundwork trial image** for the flagship ground -> edit ->
verify experiment [verdi-go integration plan §4, Track A2]. A fork of the official
[`anthropic-claude-code`](../../official/anthropic-claude-code) image: it drives
the same pinned Claude Code CLI and extends `verdi-base`, but it also bakes in the
pinned verdi-go toolchain (`flowmap` + `groundwork`) and the vendored
`groundwork-workflow` skill, and its `agent.py` arms them **only** when the arm's
`payload.tools` includes `"groundwork"`.

> **One image, both arms.** verdi has no "different container per arm" field by
> design — the asymmetry is realized inside one image, in the entrypoint
> (`docs/usage-guide.md` §9). A control arm runs this exact image and gets the
> shipped official agent, byte-for-byte: no binaries on PATH, no MCP server, no
> skill, and the unmodified CLI argv. See `docs/images.md` §1 for the contract.

## The payload gate

`agent.py` reads `/verdi/request.json` and computes
`groundwork_enabled = "groundwork" in (payload.tools or [])`.

| arm | `payload` | what the entrypoint does |
|---|---|---|
| control | `{}`, or `tools` without `groundwork` | nothing — shipped official agent; argv `claude --print --permission-mode bypassPermissions --output-format json --model=<model> <prompt>` |
| treatment rung 1 (availability) | `{tools: [groundwork]}` | arms the toolchain (below); argv gains **only** `--mcp-config=<path>` |
| treatment rung 2 (instructed) | `{tools: [groundwork], workflow: ground_verify}` | rung 1, plus argv gains `--append-system-prompt=<text>` — the byte-stable `WORKFLOW_SYSTEM_PROMPTS["ground_verify"]` instruction (part of the pre-registered treatment definition); an unknown `workflow`, or one without the tool, is refused loudly |

When armed, the entrypoint (and nothing else):

1. symlinks `/opt/groundwork/bin/{flowmap,groundwork}` into `$HOME/.local/bin` and
   prepends it to the CLI subprocess PATH (so the model can regenerate `graph.json`
   with `flowmap graph`, then `reload` the MCP server);
2. ensures `/workspace/artifacts/` exists (the MCP call-log destination, D7);
3. writes the MCP config to `$HOME/groundwork.mcp.json` and copies the skill to
   `$HOME/.claude/skills/groundwork-workflow/` — **under `$HOME` (`/tmp`), never
   `/workspace`**;
4. runs the CLI with `--mcp-config $HOME/groundwork.mcp.json`, which spawns
   `groundwork mcp /workspace/graph.json --policy /workspace/policy.json --log
   /workspace/artifacts/groundwork-mcp.jsonl` (stdio) and exposes its read-only
   tools as `mcp__groundwork__*`.

`graph.json` / `policy.json` are **task** workspace files (integration plan §5),
readable by both arms (9c parity); the treatment differs only in having the graph
*surfaced* as live tools plus the skill that teaches the loop.

### Why the writes go under `$HOME`, never `/workspace`

The judged diff is the agent's `/workspace` changes (minus `artifacts/`). An
entrypoint-written file loose in `/workspace` would show up in the treatment arm's
diff and nowhere in control — a judge-visible arm asymmetry (review finding). So
the entrypoint touches **exactly one** path under `/workspace`: the `artifacts/`
directory, which `harness/judge/assemble.py` already excludes. The CLI loads the
MCP config and the skill from `$HOME` scope instead:

- **MCP config** via an absolute `--mcp-config $HOME/groundwork.mcp.json` argument.
- **Skill** via the user-scope `$HOME/.claude/skills/groundwork-workflow/` dir,
  auto-discovered without any project-directory file.

Both are loaded from outside the working directory in non-interactive `--print`
mode; neither requires a file in `/workspace`.

## Build

Self-contained context (the skill is vendored under `skill/`, the prebuilt drop is
`bin/`), so the build never reaches into a verdi-go checkout. `verdi-base` must
exist first (`bench images build base`, or `docker build -t verdi-base
../../base`).

```bash
# default: ref-pinned install (build-time network required; the result is offline)
docker build -f images/reference/claude-code-groundwork/Dockerfile \
  --build-arg GROUNDWORK_REF=<tag|sha|pseudo-version> \
  -t verdi-bench/claude-code-groundwork:latest \
  images/reference/claude-code-groundwork
```

`GROUNDWORK_REF` has **no default** on purpose: byte-stable graph output holds per
`flowmap` *build* only, so a floating install is a determinism hole. Pin the SAME
ref used by the grader image and the task's committed base graph.

### Airgapped / prebuilt fallback

Build `flowmap` + `groundwork` from a pinned checkout with the **same Go version**
(1.25.11), drop them into `bin/`, and build with `--build-arg GROUNDWORK_PREBUILT=1`
(no `GROUNDWORK_REF` needed). The binaries in `bin/` are git-ignored — never
committed.

Like the grader image, this image takes a build-arg, so it is built with
`docker build` and is **not** in the `bench images` registry (which is arg-less);
it is a reference image resolved by path.

## Honesty notes

- **The pinned CLI version must support user-scope skills + `--mcp-config`.** The
  default `CLAUDE_CODE_VERSION` is `2.1.202`; user-scope `~/.claude/skills`
  auto-loading first shipped in `@anthropic-ai/claude-code` **2.1.157**, so pin
  `>= 2.1.157`. This is UNVERIFIED in an offline build and is **not** re-checked by
  `bench images verify` — confirm the pin and the CLI's flag spelling against your
  target before an official run. The treatment arm's entire mechanism (MCP tools +
  skill) depends on it; a too-old pin silently degrades treatment to control.
- **The build needs network** (apt + npm + `go install`); the RESULT is digest-
  pinned and harbor runs it offline with `--pull=never`.
- **`bench images verify` proves plumbing, not intelligence.** Offline, with no
  keys and `--network none`, the CLI cannot reach a model, so it fails and
  `run_visible` still writes a scorable `agent_log.json` — exactly what verify
  checks. Verify exercises the CONTROL path (its synthetic request carries no
  `groundwork` payload); the treatment arming is proven by
  `tests/test_image_claude_code_groundwork.py` (pure gating, no docker) and the
  docker-marked smoke test.
- **Telemetry comes from the CLI's own `--output-format json` result**, persisted
  verbatim as the native `agent_log.json`. The base CLI is identical across arms, so
  the `claude_code` adapter parses both the same way; the treatment's tool usage is
  recorded in `artifacts/groundwork-mcp.jsonl` (persisted, judge-excluded, D7), never
  in `agent_log.json`.
- **Add `groundwork` / `flowmap` to the experiment's arm canaries** (D6) so a
  treatment diff that mentions the tool in a comment cannot leak the arm to the
  judge unnoticed.

## Verified compliant (without docker)

`tests/test_image_claude_code_groundwork.py` imports this image's **pure** gating
functions and asserts the truth table, the MCP config content + absolute paths,
the "nothing under `/workspace` except `artifacts/`" rule (applied against tmp
fake home + workspace dirs), the zero-write control path, and the exact
treatment-vs-control argv delta — all without a container. A `docker`-marked smoke
test builds the image and checks the armed vs empty payload end to end.

## Contents

- `Dockerfile` — multi-stage: the pinned verdi-go toolchain (grader pinning
  pattern), then the `FROM verdi-base` trial image with the CLI + Go toolchain +
  staged binaries + skill.
- `agent.py` — the payload-gated entrypoint; pure gating functions + a
  side-effecting `main`.
- `skill/groundwork-workflow/` — the vendored skill; `skill/PROVENANCE` pins its
  source repo + commit.
- `bin/` — the airgapped prebuilt-binary drop zone (git-ignored contents).
