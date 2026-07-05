# Adapters and the verdi normalized log format

How any test subject — an agent CLI, a custom harness, a tool suite, a
multi-agent workflow — plugs into verdi-bench's telemetry and trajectory
interfaces. Companion to `docs/deep-dive.md` §7; the code seams are
`harness/adapters/` [EVAL-4 AC-2] and `harness/run/trajectory.py`
[EVAL-12 AC-1].

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
