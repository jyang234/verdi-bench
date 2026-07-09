# Trial images and the compatibility contract

The single **normative** statement of what a verdi trial image must obey
[refactor 03 §1]. A maintained base image bakes in every concern here; official
images extend it per stack; `bench images verify` proves compliance without
reading engine source. Companion docs point here rather than restating it.

The product goal: **a test image should be trivial to create and trust.** With
`verdi-base` + `verdi_agent`, writing an image means writing *agent logic only*.

## The compatibility contract (§1)

Every trial image must obey the following. The right column names where each
obligation is baked in or enforced — not a second contract to keep in sync.

| Obligation | Baked in / enforced by |
|---|---|
| Read `/verdi/request.json` (`schema_version`, `prompt`, `arm`, `model`, `payload`) — read-only, OUTSIDE the graded `/workspace` | `verdi_agent.read_request()`; the file is a typed `TrialRequestFile` (A1); `bench images verify` mounts a synthetic request read-only |
| Write the solution into `/workspace`; telemetry to `/workspace/artifacts/agent_log.json` in the generic v1/v2 format (or a registered native format) | `verdi_agent.AgentLog`; `verify` parses the log under the declared format with the real `harness.adapters.generic` parsers |
| Egress ONLY via `HTTP(S)_PROXY`, with per-trial basic-auth on the `CONNECT` (the trial id is the userinfo; stdlib will not add it) | `verdi_agent.post_json()`; the metering proxy attributes egress per-trial |
| Survive an arbitrary `uid:gid`, `--cap-drop ALL`, `--security-opt no-new-privileges`, pids/mem quotas, `--pull=never`, and digest pinning | `verdi-base` posture (non-root `USER`, `HOME=/tmp`, world-writable `/workspace`); the run engine's hardened `docker run`; `verify` runs the same shape |
| Exit-code semantics: a nonzero agent exit is still a **completed** (scorable) trial; 124/125 are runner-reserved | `verdi_agent.run_visible()` exits 1 with a scorable log; `verify` checks the agent does not usurp 124/125 |
| Fail visibly but leave a scorable `agent_log.json` | `verdi_agent.run_visible()` |
| Write **nothing outside `/workspace`** | `verify` asserts the read-only `/verdi` mount is intact and `/workspace` was the only writable surface |
| Multi-agent: a v2 log with the closed `agent` role vocabulary, `telemetry_by_model` keyed by declared models, and `reasoning` entries | `verdi_agent.AgentLog` (`agent=` / `by_model=` / `reasoning()`); the harness generic parsers |
| (Grader images) run holdouts and print exactly one nonce-fenced V2 result block on stdout | `harness/grade` + `docs/usage-guide.md` §grading (out of scope for a trial image) |

## Workspace layout

| Path | Mode | Purpose |
|---|---|---|
| `/workspace` | rw, bind-mounted, `--workdir` | the graded workspace — write the solution here |
| `/workspace/artifacts/agent_log.json` | rw | telemetry in the verdi generic v1/v2 format (or a registered native format) |
| `/verdi/request.json` | **read-only**, outside `/workspace` | the task + arm config (a typed `TrialRequestFile`, A1) |

## `request.json`, typed and versioned (A1)

`harness/run/request.py::TrialRequestFile` — `{schema_version: 1, prompt, arm,
model, payload}`. `schema_version` is **additive**: an existing image `json.loads`
the file and picks the keys it needs, so nothing breaks;
`verdi_agent.read_request()` tolerates its absence (a pre-A1 engine). Changing the
field set is a `schema_version` bump with a compatibility story.

## `verdi_agent` — the in-image SDK

A single stdlib-only file (`images/base/verdi_agent.py`), on the base's
`PYTHONPATH`, version-stamped:

- `read_request()` — typed accessors over `/verdi/request.json`.
- `post_json(host, path, headers, body)` — the CONNECT-tunnel +
  `Proxy-Authorization` dance, done once, correctly.
- `AgentLog` — the generic v1/v2 log writer (`message` / `tool_call` /
  `file_edit` / `test_run` / `reasoning`; `finish(...)` writes the log). It emits
  v2 automatically when per-model telemetry or sub-agent roles are used.
- `run_visible(main)` — runs `main(log)` so a trial is always scorable: any error
  still writes a fail-visible log and exits 1.

The generic log format itself is the FROZEN contract in `docs/adapters.md`;
`verdi_agent` is a writer for it, never a new format.

## Declaring a task's environment (EnvironmentSpec, A3)

Optional, additive `tasks.yaml` fields — ordinary task fields, sha-covered by the
lock, written pre-lock only. The canonical model is
`harness.images.spec.EnvironmentSpec`:

- `files: {relative/path: contents}` — staged into `/workspace` before the trial
  by BOTH engines (a fixture tree, a scaffold). Escape-confined.
- `env: {NAME: VALUE}` — injected into the container by Harbor AFTER the
  provider-key env and never overriding it. **Never secrets** — provider keys flow
  only through `run.config.yaml` / per-arm key names (operational, never locked).
- `extra_hosts: [host, ...]` — per-task egress hosts merged into the *derived*
  proxy allowlist for ALL arms, so a task can reach an extra endpoint without
  breaking the per-arm "declare for every arm or none" symmetry (the extension is
  task-scoped and applied uniformly). Inert unless the spec already pre-registers
  egress hosts (the derived-allowlist regime).

## The image tree

```
images/
  base/            verdi-base + verdi_agent.py + README (the contract, per-image)
  grader/          the grading-container image (nonce-fenced holdout transport)
  official/
    generic-llm/            single-turn chat (anthropic/openai/google), generic
    anthropic-claude-code/  drives the pinned Claude Code CLI
    openai-codex/           drives the pinned OpenAI Codex CLI
  reference/
    multi-agent/            a worked multi-agent workflow (v2 log)
    claude-code-groundwork/ Claude Code + the verdi-go structural toolchain,
                            payload-gated treatments (bare vs grounded arms)
```

## `bench images`

```bash
bench images list                      # the official registry: name → tag
bench images build <name|path> [--pin] # build (FROM verdi-base first), pin to sha256
bench images verify <ref> [--format native --platform <p>]
```

`verify` runs the image with `--network none`, a synthetic read-only
`/verdi/request.json`, a tight timeout, and a non-root `uid:gid`, then asserts the
contract above: the image ran, wrote `agent_log.json`, the log parses under the
declared format, exit semantics are honored, and nothing was written outside
`/workspace`. It validates **plumbing, not intelligence** — no provider keys, no
network, no LLM calls. A non-compliant image fails loudly with the named check.

## Emitting OTLP spans (optional) [refactor 09 §4/§6]

An OTel-native stack (LangChain/LangSmith, pydantic-ai, Logfire, OpenLLMetry, a
hand-instrumented SDK) does **not** have to flatten its span tree into
`agent_log.json` by hand: it can emit OTLP inside the trial container and let the
harness capture the spans as a redacted, sha-ledgered `artifacts/otlp_spans.json`
artifact — with no byte leaving the hermetic boundary. verdi-bench builds the
mailbox, never the instrumentation.

When (and only when) a trace collector is configured for the run
(`otlp.managed: true`, or an explicit `otlp.endpoint`, in `run.config.yaml` — see
`docs/usage-guide.md` §6), the engine injects three **standard** OTel environment
variables the image MAY read through any OTLP-HTTP exporter — no verdi-specific
code:

| Env var | Injected value | Purpose |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | the collector's OTLP/HTTP endpoint (managed: `http://verdi-trace-collector:4318`) | where an OTel SDK POSTs `/v1/traces` |
| `OTEL_EXPORTER_OTLP_HEADERS` | `x-verdi-trial=<trial_id>` | per-trial attribution — the collector's analog of the proxy's trial-id credential; the header every SDK forwards |
| `NO_PROXY` | the collector host, **appended** to any operator-supplied `NO_PROXY` | keeps span posts off the metering proxy, so egress metering stays unpolluted |

**Spans are always optional; compliance never requires them.** OTLP config rides
these standard env vars, *not* `/verdi/request.json` — the frozen A1 request
contract is untouched, so an image that ignores OTel sees nothing new. Critically,
`bench images verify` runs `--network none` with **no `OTEL_*` env** at all: OTel
SDKs buffer and drop unexported spans by design, so a span-emitting image verifies
exactly like any other. Point an exporter at the injected endpoint and you get
spans when a collector is live, and a clean, compliant image when one is not.

The image only *emits* spans; the harness extracts this trial's slice post-run
into `artifacts/otlp_spans.json` (an image never writes that file). That artifact
is projected into the trajectory + flight-recorder surfaces by the `platform:
otlp` adapter — see `docs/adapters.md` §"The `otlp` platform" for the normative
span→trajectory mapping and how to select it in an arm.

## Companion docs

- `docs/adapters.md` — the FROZEN generic log format v1/v2 (this is the writer's
  contract).
- `images/base/README.md` — the same §1 table, per-image, for an author reading
  the image directory.
- `images/reference/multi-agent/README.md` — a worked multi-agent example.
- `deploy/metering-proxy/README.md` — the external metering-proxy deployment
  reference for the egress obligation above (the harness-native path is the
  managed proxy; that README points back here).
