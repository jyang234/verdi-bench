# 03 — Test images & environments (Phase 3)

The product goal: **a test image should be trivial to create and trust.** A
maintained base image bakes in every compatibility concern; official images
extend it per stack; a task's environment is declared, not discovered; and
`bench images verify` proves compliance without reading engine source.

Today the image contract is folklore. To build a working trial image an
author must consult four documents (`harness/run/engines/harbor.py`
internals, `docs/adapters.md`, `images/multi-agent-reference/README.md`,
`deploy/metering-proxy/README.md`) and two reference agents, then hand-roll
~35 lines of proxy-CONNECT-auth code that stdlib won't do for them — already
copy-pasted between `scripts/shakedown/assets/harbor/agent.py:26-60` and
`images/multi-agent-reference/agent.py:58-104`.

**DECISIONS required:** A1 (`schema_version` in request.json), A3 (additive
tasks.yaml environment fields).

## 1. The compatibility contract, made explicit

What a trial image must obey (collected by the audit; to be published as
`docs/images.md` and enforced by `bench images verify`):

| Obligation | Today defined in |
|---|---|
| Read `/verdi/request.json` (`prompt`, `arm`, `model`, `payload`) — read-only, outside the graded workspace | private method `harbor.py:420-430` |
| Write solution into `/workspace`; telemetry to `/workspace/artifacts/agent_log.json` in generic v1/v2 or a registered native format | `docs/adapters.md:61-134`; path re-derived in ≥6 places |
| Egress via `HTTP(S)_PROXY` with **per-trial basic-auth on CONNECT** (trial id as userinfo; stdlib won't add it) | reference agents, hand-rolled |
| Survive arbitrary `uid:gid`, cap-drop, no-new-privileges, pids/mem quotas, `--pull=never`, digest pinning | `harbor.py:245-293` |
| Exit-code semantics: nonzero agent exit is still a completed (scorable) trial; 124/125 runner-reserved | `harbor.py:218-224, 354-358` |
| Fail visibly but leave a scorable `agent_log.json` | convention in both reference agents |
| Multi-agent: v2 log with closed `agent` role vocabulary, `telemetry_by_model` keyed by declared models, v3 reasoning entries | `trajectory.py:40-50`, `generic.py:180-217`, `flight_recorder.py:73-136` |
| (Grader images) run holdouts in a subprocess and print exactly one nonce-fenced V2 result block on stdout | `grade/container.py:36-65`, `docs/usage-guide.md:157-187` |

## 2. `verdi_agent.py` — the in-image SDK

A single-file, **stdlib-only** helper (the existing hard constraint: no pip,
no network at build time — `scripts/shakedown/assets/harbor/Dockerfile`),
version-stamped, shipped into every base-image build context and importable
by any agent:

```python
# images/base/verdi_agent.py  (stdlib only, ~150 lines)
def read_request() -> Request: ...          # /verdi/request.json, typed accessors
def post_json(host, path, headers, body): ...  # the CONNECT-tunnel + Proxy-Authorization
                                               # dance, done once, correctly
class AgentLog:                             # generic-format v1/v2 writer
    def message(self, text, *, agent=None): ...
    def file_edit(self, files, detail="", *, agent=None): ...
    def test_run(self, command, detail="", *, agent=None): ...
    def reasoning(self, content, *, agent=None, turn=None): ...   # flight-recorder feed
    def finish(self, *, cost=None, tokens_in=None, tokens_out=None,
               by_model=None): ...          # writes artifacts/agent_log.json
def run_visible(main) -> None: ...          # try/except wrapper: any error still
                                            # writes a scorable log and exits 1
```

This deletes the duplicated tunnel/log code from both reference agents and
makes "write an agent" mean *agent logic only*. The generic log format is a
frozen contract ("v1 parses unchanged forever", `docs/adapters.md:244-252`);
`verdi_agent` is a writer for it, not a new format.

## 3. Image tree

```
images/
  base/                       # verdi-base: python:3.12-slim (or alpine), verdi_agent.py,
    Dockerfile                # non-root user, PYTHONDONTWRITEBYTECODE=1, workspace
    verdi_agent.py            # layout, entrypoint convention documented in-image
    README.md                 # the contract of §1, normative
  official/
    generic-llm/              # today's shakedown agent, promoted: single-turn
      Dockerfile  agent.py    # anthropic/openai/google chat loop via verdi_agent
    anthropic-claude-code/    # installs the claude-code CLI; adapter platform
      Dockerfile  agent.py    # `claude_code` native log emission
    openai-codex/             # installs the codex CLI; platform `codex`
      Dockerfile  agent.py
  reference/
    multi-agent/              # moved from images/multi-agent-reference
      Dockerfile  agent.py  README.md
  grader/                     # generic grading entrypoint image for declared
    Dockerfile                # holdout kinds ([05] §1) — python + pytest,
                              # runs harness-shipped run_holdouts under the fence
```

Notes:

- Official stack images pin their tool versions explicitly (a stack version
  is part of the arm's identity — put it in `payload` and the image tag).
  Model ids in examples are **date-versioned only** (alias ids are refused
  at plan time, `harness/schema/judge_config.py:26-59`).
- The multi-agent reference moves under `reference/` — path referenced only
  by `scripts/shakedown/harbor_multiagent.py:27` and its own README; update
  both in the same commit.
- The metering proxy also becomes an image (promotion of
  `scripts/shakedown/assets/harbor/proxy.py` — see [04](04-run-engine.md)
  §1); the Squid config under `deploy/metering-proxy/` stays as the
  external-deployment reference with its documented caveat.

## 4. `harness/images` subsystem

```python
# harness/images/spec.py
class ImageSpec(BaseModel):                # extra="forbid"
    ref: str                               # tag or digest-pinned ref
    build_context: Path | None = None      # None ⇒ pull/local-resolve only
    expected_format: Literal["generic", "native"] = "generic"
    platform: str | None = None            # adapter platform when native

class EnvironmentSpec(BaseModel):          # per-task additions (A3: additive
    files: dict[str, str] = {}             # tasks.yaml keys, optional, hashed
    env: dict[str, str] = {}               # like any task field — pre-lock only)
    extra_hosts: list[str] = []            # merged into the proxy allowlist path

# harness/images/build.py — build/pin/inspect via harness.hermetic (never
#   naming harbor; the AST seam sweep stays green)
def build(spec: ImageSpec) -> PinnedImage: ...      # returns sha256 digest
def resolve_digest(ref: str) -> str: ...
# harness/images/verify.py — offline compliance check:
def verify(image_ref: str) -> ComplianceReport: ...
```

`verify` runs the container with `--network none`, a synthetic
`/verdi/request.json`, and a tight timeout, then asserts: agent_log.json
exists and parses under the declared format (reusing
`harness/adapters/generic.py` parsers — the same pure-function check the
multi-agent README already prototypes at
`images/multi-agent-reference/README.md:58-64`), exit semantics honored,
nothing written outside `/workspace`. No LLM calls, no keys — it validates
plumbing, not intelligence.

CLI: `bench images build <name|path> [--pin]`, `bench images verify <ref>`,
`bench images list` (the official registry: name → build context). New
subsystem ⇒ add to `.importlinter` source lists (A5 mechanics).

### request.json, typed and versioned (**A1**)

Promote `_trial_request_payload` (`harbor.py:420-430`) to a public
`TrialRequestFile` pydantic model in `harness/run/request.py` with
`schema_version: 1`. Additive key — existing images `json.loads` and pick
keys, so nothing breaks; the docker-marked request test
(`tests/test_eval4_harbor_request.py:117-140`) extends to assert the
version. `verdi_agent.read_request()` becomes the reference consumer.
Requires explicit approval because every existing trial image reads this
file: the migration story is "additive field + verify covers both".

## 5. Environments for a task

Declaring an environment must not breach hermeticity or the lock:

- `EnvironmentSpec` fields are ordinary task fields — sha-covered by the
  task commitment like everything else (additive, **A3**), materialized by
  the engine *before* the trial starts (files staged into `/workspace`,
  env vars injected — never secrets; provider keys keep flowing only
  through `run.config.yaml` / per-arm key names, which are operational and
  never locked).
- `extra_hosts` feeds the *derived* allowlist exactly the way
  `arm.model_hosts`/`infra_hosts` already do (`harness/run/egress.py`),
  keeping the "declare for every arm or none" symmetry rule intact.
- The fake engine honors `files` (materializes them) and ignores the rest —
  keeping L1 hermetic tests meaningful.

## 6. Migration

1. Land `harness/hermetic` first ([04](04-run-engine.md) §1 — build/verify
   shell out through it).
2. `images/base` + `verdi_agent.py` + `bench images build/verify`; port
   `generic-llm` from the shakedown agent; `bench images verify` green on
   it and on the multi-agent reference.
3. Official `anthropic-claude-code` / `openai-codex` images (each is:
   Dockerfile extending base, agent.py invoking the stack CLI, emitting via
   `verdi_agent` or the native adapter format).
4. Convert `scripts/shakedown/harbor.py` / `harbor_multiagent.py` to
   `bench images build` + SDK; delete `scripts/shakedown/assets/harbor/`
   (Dockerfile + agent absorbed; proxy promoted in [04](04-run-engine.md)).
5. `docs/images.md` (the §1 contract, normative) + usage-guide §6 rewrite.

## 7. Constraints & invariants

- Digest pinning, `--pull=never`, ro request mount, cap-drop, key
  redaction: load-bearing product claims covered by docker-marked CI tests —
  the images subsystem *consumes* the engine, never re-implements trial
  execution.
- `images/` build contexts are plain docker contexts — no dependency on the
  harness package inside images (only the copied-in `verdi_agent.py`),
  keeping trial containers hermetic.
- The generic log format and the holdout fence transport are frozen; both
  get writers/entrypoints here, never format changes.

## 8. Acceptance

- A new stack image = `images/official/<name>/{Dockerfile,agent.py}`
  extending base, plus `bench images verify` passing — nothing else.
- Shakedown L6 contains zero `docker` CLI invocations.
- `docs/images.md` is the single normative statement of §1; the four
  scattered partial descriptions link to it.
