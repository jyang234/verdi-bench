# Adapters and the verdi normalized log format

How any test subject — an agent CLI, a custom harness, a tool suite, a
multi-agent workflow — plugs into verdi-bench's telemetry and trajectory
interfaces. Companion to `docs/deep-dive.md` §7; the code seams are
`harness/adapters/` [EVAL-4 AC-2] and `harness/run/trajectory.py`
[EVAL-12 AC-1]. For the full trial-image contract (workspace layout,
`request.json`, egress, `verdi_agent`), see `docs/images.md` §1 — the normative
statement; this document is the FROZEN log-format half of it.

## What an adapter is

A trial produces two measurement surfaces beyond its graded workspace:

- **Telemetry** — per-trial aggregates (`tokens_in`, `tokens_out`,
  `tokens_cache`, `cost`, `wall_time_s`, `tool_calls`), normalized into the
  `Telemetry` model embedded in every `TrialRecord`.
- **Trajectory** — the ordered steps of the trial (`TrajectoryStep`:
  `tool_call` / `file_edit` / `test_run` / `message`), persisted as a
  sha-ledgered artifact and consumed by the forensic detectors.

An adapter maps a platform's `artifacts/agent_log.json` onto those two
interfaces. The honesty rules are non-negotiable [EVAL-4-D004, §7.8]:

- A field the platform cannot measure is `None` — flagged in
  `telemetry_nulls`, **never** imputed, estimated, or proxied.
- A trial with no trajectory content returns `None` — honest absence,
  distinct from an empty step list.
- A *present but corrupt* log fails the trial closed
  (`trial_infra_failed`); it never silently becomes "no telemetry"
  [RN-17].

## Three integration tiers

### Tier 1 — zero code: emit the normalized format (`platform: generic`)

If your test subject can write its own log, write it directly in the verdi
normalized log format (below) and declare `platform: generic` in the arm.
No harness-side code, no registration — the `generic` adapter ships with the
instrument. This is the baseline path for custom harnesses, wrapped
open-source models, and agentic workflows.

### Tier 2 — subclass with overrides

`Adapter` (in `harness/adapters/base.py`) is a working adapter out of the
box: its default `normalize` / `normalize_trajectory` parse the normalized
format. Subclass it, set `platform`, and override only what your platform
measures differently — e.g. keep the default telemetry parsing but derive
trajectory steps from a native event stream. Register the instance in
`_ADAPTERS` (`harness/adapters/__init__.py`).

### Tier 3 — full native adapter

For a platform with its own log format (the `claude_code` and `codex`
adapters are the templates, ~100 lines each): override both methods, parse
the native log, and let every unmeasurable field stay `None`. The
null-honesty tests will hold you to it.

Whatever the tier, plan-time validation refuses to lock an experiment whose
arm names a platform with no registered adapter, so a typo'd or unshipped
platform fails before any spend, not mid-run.

## The verdi normalized log format, v1

Written by the trial to `artifacts/agent_log.json` inside `/workspace`.

```json
{
  "verdi_log_version": 1,
  "telemetry": {
    "tokens_in": 1200,
    "tokens_out": 340,
    "tokens_cache": 800,
    "cost": 0.42,
    "wall_time_s": 61.5,
    "tool_calls": 7
  },
  "trajectory": [
    {"kind": "message", "command": ""},
    {"kind": "tool_call", "relative_ts": 1.5, "tokens": 100, "cost": 0.01,
     "command": "ls"},
    {"kind": "file_edit", "files_touched": ["a.py"], "command": ""},
    {"kind": "test_run", "exit_code": 0, "command": "pytest -q"}
  ]
}
```

### `verdi_log_version` (required to engage)

- **Absent** → the log never claimed the format. All telemetry is null,
  the trajectory is absent — honest, not an error. This makes
  `platform: generic` safe even when a harness emits some other log.
- **Present and `1`** → the parser engages, and the rest of the document is
  a self-attestation.
- **Present and anything else** → refused loudly (`GenericLogError`); the
  parser never guesses at another version's semantics.

### `telemetry` (optional object)

Validated through the `Telemetry` model itself — the schema every
`TrialRecord` embeds is the format's single source of truth.

| field         | type    | meaning                                   |
|---------------|---------|-------------------------------------------|
| `tokens_in`   | int     | prompt tokens consumed, whole trial       |
| `tokens_out`  | int     | completion tokens produced, whole trial   |
| `tokens_cache`| int     | cache-read tokens, whole trial            |
| `cost`        | float   | self-reported spend (USD), whole trial    |
| `wall_time_s` | float   | wall-clock seconds, whole trial           |
| `tool_calls`  | int     | tool invocations, whole trial             |

- Omit (or set `null`) any field you cannot measure — it lands in
  `telemetry_nulls` as data. Never guess.
- Omitting the whole block means "nothing measured": all nulls, still legal.
- An **unknown key is refused loudly** — in a declared log, a typo'd
  `token_in` must not launder into "unmeasured".

### `trajectory` (optional array)

Each element validates as a `TrajectoryStep` — the format *is* the shared
schema (`harness/run/trajectory.py`, schema v3):

| field           | type        | meaning                                          |
|-----------------|-------------|--------------------------------------------------|
| `kind`          | enum, req.  | `tool_call` \| `file_edit` \| `test_run` \| `message` |
| `relative_ts`   | float       | seconds since trial start                        |
| `tokens`        | int         | tokens attributable to this step                 |
| `cost`          | float       | cost attributable to this step                   |
| `files_touched` | list[str]   | files this step modified                         |
| `exit_code`     | int         | exit status, when the step ran a command         |
| `command`       | str         | the shell command; `""` = measured-not-a-command; `null` = unmeasurable |
| `agent`         | str         | closed-vocabulary role label (see below); `null` = unattributed |
| `detail`        | str         | additive v3 field: per-step forensic content (e.g. an edit's before/after); `null` = unmeasurable |

- Omit the `trajectory` key entirely for an honestly absent trajectory
  (`None`) — distinct from `[]`, an empty-but-measured one.
- A non-list value or a malformed step (unknown `kind`, unknown field) is
  refused loudly with its index.
- Only label a step `test_run` when your harness *knows* it ran tests;
  inferring it from command text is estimation, not measurement.

### `reasoning` (optional list, any version) [EVAL-24]

The **flight recorder**: the chain of thought by which the arm reached its
answer, as an ordered list of entries. Optional at any declared version
(absent = no reasoning, honest — distinct from `[]`); logs without it parse
unchanged.

```json
{
  "verdi_log_version": 1,
  "reasoning": [
    {"content": "plan: solve add first, then the palindrome task", "agent": "planner",
     "relative_ts": 1.5, "turn": 0},
    {"content": "add(a, b) returns a + b; handled the overflow case", "agent": "worker-1",
     "tokens": 90, "relative_ts": 9.0, "turn": 1}
  ]
}
```

Each entry carries `content` (str, required — the reasoning text) and the
optional, null-honest `tokens` (int) and `cost` (float). An optional `agent`
role [EVAL-24] attributes the reasoning to a sub-agent of a multi-agent
workflow, over the **same** closed role vocabulary as trajectory steps
(`planner`/`worker-2`/…); `null` = unattributed (single-agent reasoning). An
out-of-vocabulary label is refused loudly, exactly like a step's `agent`.

Two optional **v3 linkage** fields [flight-recorder charter] let the operator
process view interleave thought with action into one timeline: `relative_ts`
(float — seconds since trial start, the trajectory-step clock) and `turn`
(int — the 0-based index of the trajectory step this reasoning belongs to,
the stack's own declaration, never inferred by verdi). Both null = unlinked;
unlinked reasoning still renders, in capture order, labeled as such. A
negative `turn` is a malformed declaration and is refused loudly. Older logs
(and recorders written before v3) read back with both fields null forever —
no reader may require them.

Reasoning persists as a **separate**
artifact (`artifacts/flight_recorder.json`) bound to the chain by an additive
`flight_recorder_sha`, and is **operator-tier** — it feeds the read-only
compare view, the per-trial process view, and the blinded advisory forensic
review, and is invisible by construction to the judge, the deterministic
grade, and the official fence. A malformed `reasoning` block is refused
loudly (`GenericLogError`), like any declared block.

### Failure semantics, end to end

| condition                                        | result                                     |
|--------------------------------------------------|--------------------------------------------|
| no `agent_log.json`                              | all-null telemetry, absent trajectory      |
| `agent_log.json` is not valid JSON               | trial fails closed: `telemetry_corrupt` [RN-17] |
| valid JSON, no `verdi_log_version`               | all-null telemetry, absent trajectory      |
| declared but unsupported version                 | `GenericLogError` → trial fails closed     |
| declared, unknown top-level key (incl. a typo'd block name or a v2 feature under a v1 declaration) | `GenericLogError` → trial fails closed |
| declared, structural violation inside a block    | `GenericLogError` → trial fails closed     |

## Format v2 — multi-agent attribution [EVAL-21]

`verdi_log_version: 2` is a superset of v1 adding self-reported
attribution. Attribution is the arm's *testimony* — the instrument cannot
see inside the hermetic container — so it is exploratory cross-check data
only: it rides the trial record's flags, never the authoritative telemetry
stream, and no official gate reads it.

### `agent` on trajectory steps

A closed role vocabulary, so identity leakage is **unrepresentable**
rather than scrubbed: labels match `role(-ordinal)?` where role is one of
`planner`, `executor`, `orchestrator`, `router`, `critic`, `reviewer`,
`tester`, `researcher`, `worker`, and the ordinal distinguishes instances
(`worker-1`, `worker-2`). Anything else (`llama-planner`, free text) is
refused loudly at parse. `null` = unattributed. Extending the vocabulary
requires a schema-version bump.

### `telemetry_by_model` (v2, optional object)

Per-model telemetry blocks, keyed **strictly by the models the locked spec
declared** (the arm's primary `model` plus its `aux_models`) — a key naming
an undeclared model is refused loudly: attribution to a model the
pre-registration never mentioned is a contradiction, not data. Each value
is a Telemetry-shaped block with the usual null honesty.

```json
{
  "verdi_log_version": 2,
  "telemetry": {"cost": 0.42, "tokens_out": 340},
  "telemetry_by_model": {
    "meta/llama-3-70b-instruct-20240620": {"cost": 0.30, "tokens_out": 300},
    "qwen/qwen2-coder-32b-20240901":      {"cost": 0.12, "tokens_out": 40}
  },
  "trajectory": [
    {"kind": "message", "command": "", "agent": "planner"},
    {"kind": "test_run", "exit_code": 0, "command": "pytest -q",
     "agent": "worker-1"}
  ]
}
```

The whole-trial `telemetry` block remains the sole authoritative stream.
When by-model blocks sum differently from the totals, the mismatch is
surfaced on the record as a `by_model_delta` flag — never reconciled in
either direction.

### Versioning

This format is a public seam. v1 parses unchanged forever; any field
addition or semantic change requires a `verdi_log_version` bump with a
compatibility story, per the contract rules in `CLAUDE.md`. Note the
trajectory table is coupled to `TRAJECTORY_SCHEMA_VERSION` — a new step
field lands in both (in particular, `agent` validates by step schema, which
is at **v3** — the current version, carrying the additive `detail` field —
so it is accepted in any declared log; the v2 *log* version — a separate
versioning axis from the step schema — is load-bearing for
`telemetry_by_model`) [F-L15].

## The `otlp` platform — spans instead of a log [refactor 10]

An OTel-native stack need not flatten its span tree into `agent_log.json`: it
emits OTLP inside the trial container (`docs/images.md` §"Emitting OTLP spans"),
the harness captures the redacted, sha-ledgered `artifacts/otlp_spans.json`
([refactor 09](design/refactor/09-otlp-trace-capture.md)), and the **registered
`otlp` platform** (`harness/adapters/otlp.py`, `platform = "otlp"`) projects that
artifact into the trajectory. It is a native adapter
(`speaks_generic_format = False`): it reads `otlp_spans.json`, **not**
`agent_log.json`, and its whole-trial `telemetry` is honestly null — spans feed
the trajectory + flight recorder, not the authoritative telemetry stream.

The projection maps **into existing fields only** — the FROZEN trajectory v3 and
flight-recorder v3 records — so it needs **no** schema-version bump: the byte
recipes, the closed role vocabulary, and the `kind` enum are untouched.

### Selecting it

- Declare `platform: otlp` on the arm in the pre-registration — **before the
  lock**. Source selection is pre-registered, never data-dependent (**D-10-1**:
  there is no silent fallback from an absent log to spans, and no merge of the
  two sources in v1 — which bytes were graded must not be a runtime accident).
- It **requires a collector**: a run whose `platform: otlp` arm has no OTLP
  collector configured is refused at run start, before any trial executes
  (`_assert_otlp_coherence` → `OtlpCoherenceError`), naming both settings — never
  a trial-time missing-artifact surprise. Configure `otlp.managed: true` or an
  explicit `otlp.endpoint` in `run.config.yaml` (`docs/usage-guide.md` §6).
- Input artifact: the redacted on-disk `artifacts/otlp_spans.json`, honoring the
  dual-source invariant (trajectory from redacted on-disk bytes).

### The projection — the normative mapping (`OTLP_MAPPING_VERSION = 1`)

The mapping is a **closed whitelist**: an attribute crosses into a trajectory or
flight-recorder field only if a rule below names it. Everything else — most
critically `gen_ai.request.model`, `gen_ai.system`, `service.*`, and **all
resource attributes** — is dropped on the floor, so vendor/model/arm identity
cannot survive into the judge-adjacent trajectory (the same design intent as the
closed role vocabulary). OTLP-JSON wraps every value in an `AnyValue` and encodes
int64 as a *string*; the reader unwraps that shape read-only, and an unrecognized
shape is dropped (`null`), never guessed.

**Span selection.** Only spans carrying a `gen_ai.*` **or** `verdi.*` attribute
are considered. HTTP-client, DB, and other infrastructure spans are
trajectory-altitude noise — ignored here, still present in the raw artifact.

**Ordering (deterministic).** Selected spans sort by
`(start_time_unix_nano, span_id)` — span id as the tie-break. `t0` is the minimum
`start_time_unix_nano` of the selected set; `relative_ts = (start − t0) / 1e9`
rounded to milliseconds. **All timing derives from span data; the harness
contributes no clock** — a shuffled batch order yields byte-identical output.

**`TrajectoryStep` mapping** (`kind` enum closed:
`tool_call | file_edit | test_run | message`):

| Trajectory field | Source | Rule |
|---|---|---|
| `kind = message` | `gen_ai.operation.name` ∈ {`chat`, `text_completion`, `generate_content`} | one step per LLM-call span |
| `kind = tool_call` | `gen_ai.operation.name = execute_tool`, or `gen_ai.tool.name` present | the default tool classification |
| `kind = file_edit` | a tool step whose `gen_ai.tool.name` ∈ `_FILE_EDIT_TOOLS` (below) | mechanical lookup — an unknown tool stays a generic `tool_call` |
| `kind = test_run` | explicit only: `verdi.test_run = true`, or a `verdi.command` whose first token is a frozen test runner | **never** inferred from span names; classification precedence within the tool family is `test_run` > `file_edit` > `tool_call` |
| `relative_ts` | span start (as above) | |
| `tokens` | `gen_ai.usage.input_tokens` **+** `gen_ai.usage.output_tokens` | **both halves required** — a total with an unmeasured half is unmeasurable, so it is `null`, never imputed by treating the absent half as 0 |
| `cost` | `verdi.cost_usd` only | OTel has no standard cost attribute; absent → `null` |
| `files_touched` | `verdi.files` (string list) first; else, for a `file_edit`, a path parsed from the whitelisted `gen_ai.tool.arguments` JSON (`file_path`/`path`/`filename`) | else `null` |
| `exit_code` | `verdi.exit_code` | `test_run`/`tool_call` steps only; absent → `null` |
| `command` | `verdi.command` for tool-family steps; `""` for a `message` (measured — a message is not a shell command) | else `null` |
| `detail` | completion text (`gen_ai.content.completion`) for `message`; tool name + `gen_ai.tool.arguments` for `tool_call`/`file_edit` | post-redaction, whitelisted sources only — no non-whitelisted attribute reaches `detail` |
| `agent` | **`verdi.agent` only**, validated by the closed role vocabulary | present-but-invalid → fail closed (`spans_corrupt`); absent → `None`. **Never** derived from `service.name` or a resource attribute — that would be an identity leak by construction |

**`ReasoningEntry` mapping** (flight recorder — operator-tier, never the judge
packet):

| Field | Source |
|---|---|
| `content` | the `gen_ai.content.reasoning` attribute, or a span **event** named `gen_ai.reasoning` / `verdi.reasoning` (its `content` attribute) |
| `tokens` / `cost` | `gen_ai.usage.*` (both halves) / `verdi.cost_usd`, same rules as steps |
| `agent` | `verdi.agent`, same closed-vocabulary rule as steps |
| `relative_ts` | the carrying span's start, same clock |
| `turn` | the trajectory-step index of the reasoning span's **nearest selected ancestor** (parent-span-id chain, cycle-guarded); no selected ancestor → `None` |

**The closed `_FILE_EDIT_TOOLS` set** (byte-affecting, golden-pinned — extended
only via an `OTLP_MAPPING_VERSION` bump): `Edit`, `Write`, `MultiEdit`,
`NotebookEdit` (claude-code parity), plus the common OTel-emitted `write_file`,
`edit_file`, `create_file`, `str_replace_editor`.

**The `verdi.*` attribute vocabulary** a stack may set to enrich the projection
(all optional; each drops to `null`/unattributed when absent):

| Attribute | Effect |
|---|---|
| `verdi.agent` | the closed-vocabulary role label — the **only** source of a step's / entry's `agent` |
| `verdi.cost_usd` | the **only** source of a step's / entry's `cost` |
| `verdi.files` | `files_touched` (string list), taken before any tool-argument path parse |
| `verdi.exit_code` | `exit_code` for a `test_run`/`tool_call` step |
| `verdi.test_run` | `= true` forces the `test_run` classification |
| `verdi.command` | the step `command` for a tool-family step, and a test-runner classification signal |
| `verdi.reasoning` | a span-event name whose `content` becomes a `ReasoningEntry` |

### Versioning and the golden discipline

`OTLP_MAPPING_VERSION = 1` lives in `adapters/otlp.py`. It is **not** stored in
the frozen trajectory/flight-recorder records (no field addition, no byte
change); the mapping is pinned instead by **golden fixture pairs** — committed
`otlp_spans.json` fixtures → byte-exact `trajectory.json` / `flight_recorder.json`
outputs. Any change to a rule above breaks a golden and **must** bump the constant
and regenerate the goldens in the same reviewed commit.

### Failure semantics

Consistent with the generic parser's honesty split (a declared source that lies
fails closed; honest absence is `None`):

| Condition | Result |
|---|---|
| `platform: otlp` declared, no collector configured for the run | run refused at start (`OtlpCoherenceError`) — before any trial |
| `otlp_spans.json` unparseable, its wrapper invalid, or a mapping violation (e.g. `verdi.agent` outside the closed vocabulary) | `SpanMappingError` → trial fails closed `trial_infra_failed(spans_corrupt)` |
| artifact present, **zero selected spans** (empty batches, or nothing GenAI-shaped) | honest absence: no trajectory artifact, no `trajectory_sha`; the trial completes |
| reasoning sources absent, steps present | trajectory persists; the flight recorder is honestly absent (the two artifacts are independently optional) |

## Multi-agent workflows as a test subject

A multi-agent harness is a single arm: one container, one prompt in, one
workspace of artifacts out. Guidance for the normalized log:

- **Telemetry is whole-trial**: sum tokens/cost/tool calls across all
  agents. If your orchestrator cannot attribute a field across agents,
  leave it null rather than reporting a partial sum as a total. Per-model
  splits go in `telemetry_by_model` (v2) — declared models only.
- **Trajectory is one ordered list**: serialize concurrent agents' steps
  into a single order (by timestamp, via `relative_ts`), attributing each
  step with a closed-vocabulary `agent` role label (v2). Do not encode
  agent identity anywhere else (e.g. `command`).
- **Spawning a subagent** is a `tool_call` step.
- The whole fleet shares the trial's pinned CPU/memory quotas, its single
  timeout, and the metering proxy — egress from any agent is attributed to
  the trial.
