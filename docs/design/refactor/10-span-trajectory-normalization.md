# 10 — Span → trajectory normalization (Phase 3/4)

The product goal: **spans become graded-adjacent evidence without touching a
frozen byte.** Spec [09](09-otlp-trace-capture.md) lands a trial's OTLP spans
as `artifacts/otlp_spans.json`; this spec projects that artifact into the
existing trajectory v3 (`harness/run/trajectory.py`) and flight-recorder v3
(`harness/run/flight_recorder.py`) formats — deterministically, post-trial,
from redacted on-disk bytes, with the raw artifact preserved unchanged as
the replayable source.

The projection maps **into existing fields only**, so it requires **no
schema-version bump**: `TRAJECTORY_SCHEMA_VERSION = 3` and
`FLIGHT_RECORDER_SCHEMA_VERSION = 3` stay pinned by their literal test
assertions (`tests/test_eval12_trajectory.py:276-292`,
`tests/test_eval21_attribution.py:122`), and the canonical-bytes recipes and
closed role vocabulary are untouched ([00](00-refactor-master-plan.md) §8,
[04](04-run-engine.md) §6 invariants).

**DECISIONS:** D-10-1 (no fallback/merge between span-derived and
log-derived trajectories in v1: **accepted**), D-10-2 (file-edit tool-name
set: **minimal set accepted as specced in §2**), A12 (shared with spec 09:
`spans_corrupt` joins the `failure_reason` vocabulary) — all accepted
2026-07-06, recorded in `decisions.ndjson`.

## 1. Placement: a platform adapter, not a new pipeline

The normalizer is `harness/adapters/otlp.py` — a registered `Adapter`
(`harness/adapters/base.py`) with platform id `"otlp"`,
`speaks_generic_format = False`, sibling of `claude_code.py` / `codex.py`.
This is the healthiest extension path in the codebase (two files for a new
platform, `adapters/__init__.py:10-33`) and means **zero orchestration
change**: `seam.run_trial` already normalizes trajectory and reasoning
through the arm's adapter after redaction (`seam.py:199-252`).

Two deliberate deviations from the log-reading adapters:

- **Input artifact.** `normalize_trajectory` / `normalize_reasoning` read the
  redacted on-disk `artifacts/otlp_spans.json`, not `agent_log.json` —
  honoring the dual-source invariant ("trajectory from redacted on-disk
  bytes", `seam.py:171,208`, [04](04-run-engine.md) §3). When the
  `CaptureStage` protocol lands ([04](04-run-engine.md) §3), this becomes a
  stage configuration rather than an adapter special case; until then the
  adapter receives the artifacts dir the way `_redacted_native_log`
  (`seam.py:40-65`) resolves it.
- **Source selection is pre-registered, never data-dependent.**
  **DECISION D-10-1:** an arm declares `platform: otlp` in the spec (before
  the lock) or it does not — there is no silent fallback from an absent
  agent-log trajectory to spans, and no merging of the two sources in v1.
  Rationale: a data-dependent source switch would make "which bytes were
  graded" a runtime accident, which is exactly what pre-registration exists
  to prevent. Merge semantics, if ever wanted, are a future spec with their
  own approval.

Configuration coherence is validated **early**: an arm with
`platform: otlp` in a run whose config has no collector
(`request.otlp is None`) fails at plan/run validation with a message naming
both settings — not at trial time with a missing artifact.

## 2. The projection — `OTLP_MAPPING_VERSION = 1`

The mapping is a **closed whitelist projection**. Attributes cross into
trajectory fields only if a table row names them; everything else — most
critically `gen_ai.request.model`, `gen_ai.system`, `service.*`, and all
resource attributes — is dropped on the floor. The trajectory is
judge-adjacent; vendor and model identity must not survive the projection
(the same design intent as the role vocabulary's "cannot spell a model,
vendor, platform, or arm identity", `trajectory.py:34-39`).

**Span selection.** Only spans carrying GenAI semantic-convention
attributes (`gen_ai.*`) or verdi attributes (`verdi.*`) are considered.
HTTP-client, DB, and other infrastructure spans are ignored — they are noise
at trajectory altitude and remain available in the raw artifact.

**Ordering.** Selected spans sort by `(start_time_unix_nano, span_id)` —
span-id hex as the deterministic tie-break. `t0` = the minimum
`start_time_unix_nano` of the selected set; `relative_ts` =
`(start − t0) / 1e9` rounded to milliseconds. All timing derives from span
data; the harness contributes no clock (determinism directive).

**`TrajectoryStep` mapping** (`kind` enum is closed:
`tool_call | file_edit | test_run | message`, `trajectory.py:83`):

| Trajectory field | Source | Rule |
|---|---|---|
| `kind = message` | `gen_ai.operation.name` ∈ {`chat`, `text_completion`, `generate_content`} | one step per LLM-call span |
| `kind = tool_call` | `gen_ai.operation.name = execute_tool`, or `gen_ai.tool.name` present | |
| `kind = file_edit` | tool_call whose `gen_ai.tool.name` ∈ `_FILE_EDIT_TOOLS` | frozen module set, seeded from `claude_code.py:18` (`Edit`, `Write`, `MultiEdit`, `NotebookEdit`) plus common OTel-emitted names (`write_file`, `edit_file`, `create_file`, `str_replace_editor`); **DECISION D-10-2:** confirm the set — it is byte-affecting and golden-pinned |
| `kind = test_run` | explicit only: `verdi.test_run = true`, or a tool/exec command whose first token ∈ a frozen test-runner list (`pytest`, `go test` style tokens — the conservative `codex.py` `parsed_cmd == "test"` posture) | never inferred from span names |
| `relative_ts` | span start | as above |
| `tokens` | `gen_ai.usage.input_tokens + gen_ai.usage.output_tokens` | absent → `null` |
| `cost` | `verdi.cost_usd` only | OTel has no standard cost attribute; absent → `null` |
| `files_touched` | `verdi.files` (string list), else parseable path arguments of a file-edit tool | else `null` |
| `exit_code` | `verdi.exit_code` | test_run/tool_call only; absent → `null` |
| `command` | tool invocation command from whitelisted argument attributes | else `null` |
| `detail` | completion text (`message`), tool name + whitelisted args (`tool_call`/`file_edit`) | post-redaction text, same exposure class as `claude_code.py:46-117` details |
| `agent` | **`verdi.agent` attribute only**, validated by `validate_agent_label` (`trajectory.py:53-67`) | present-but-invalid → fail closed (§3); absent → `None` (unattributed). **Never** derived from `service.name` or resource attrs — that is an identity leak by construction |

**`ReasoningEntry` mapping** (flight recorder — operator-tier, never the
judge packet, `flight_recorder.py:1-16`):

| Field | Source |
|---|---|
| `content` | `gen_ai.content.reasoning` attribute, or span events named `gen_ai.reasoning` / `verdi.reasoning` |
| `tokens` / `cost` | `gen_ai.usage.*` on the carrying span / `verdi.cost_usd` |
| `agent` | `verdi.agent`, same rule as steps |
| `relative_ts` | carrying span start, same clock |
| `turn` | index of the trajectory step whose span is the reasoning span's **nearest ancestor** among selected spans (parent-span-id chain); no selected ancestor → `None` |

The `turn` linkage is the OTel parent/child tree mapped onto the existing v3
linkage fields (`flight_recorder.py:100-101,123-131`) — the codebase's own
precedent for cross-referencing reasoning to actions. Reasoning volume is
governed by the existing `DEFAULT_REASONING_BUDGET_BYTES` inside
`persist_flight_recorder` — no new budget mechanism.

**Persistence is reused, not reimplemented.** The adapter returns
`list[TrajectoryStep]` / `list[ReasoningEntry]`; `persist_trajectory` and
`persist_flight_recorder` own scrub → canonicalize → sha → readback exactly
as today (`trajectory.py:148-187`, `flight_recorder.py:174-211`). The
normalizer contains no serialization code.

## 3. Failure semantics

Consistent with the generic parser's honesty split (declared ⇒ strict,
undeclared ⇒ honest absence, `generic.py:32-39`):

| Condition | Behavior |
|---|---|
| `platform: otlp` declared, collector not configured | plan/run validation error (§1) — fail before any trial runs |
| `otlp_spans.json` unparseable, wrapper invalid, or a mapping violation (e.g. `verdi.agent` outside the closed vocabulary) | `SpanMappingError` → `trial_infra_failed` with new reason **`spans_corrupt`** (A12) — declared telemetry that lies fails the trial closed, the `TrajectoryCorruptError` discipline (`interleave.py:53-60`) |
| Artifact present, zero selected spans (empty batches or nothing GenAI-shaped) | honest absence: `normalize_trajectory` returns `None`, no `trajectory.json`, no `trajectory_sha` — the `claude_code.py` "no `messages` ⇒ `None`" posture |
| Reasoning sources absent, steps present | trajectory persists, flight recorder honestly absent — the artifacts are independently optional, as today |

## 4. Versioning and reproducibility

- `OTLP_MAPPING_VERSION = 1` lives in `adapters/otlp.py`. It is **not**
  stored in the frozen trajectory/flight-recorder records (no field
  addition, no byte change). Instead the mapping is pinned by **golden
  fixture pairs**: committed `otlp_spans.json` fixtures → byte-exact
  `trajectory.json` / `flight_recorder.json` outputs. Any mapping change
  breaks a golden and forces a version bump + fixture regeneration in the
  same reviewed commit — the [01](01-safety-nets.md) discipline applied to
  the projection.
- Fixture set (minimum): a LangChain/LangSmith-style export, a
  pydantic-ai-style export, a multi-agent trace with `verdi.agent` labels
  (`worker-1`, `critic-2`), a reasoning-bearing trace exercising `turn`
  linkage, and an adversarial fixture (§5).
- Blinding meta-fixture: an **adversarial fixture** laced with model ids,
  vendor names, and arm-name strings in *non-whitelisted* attributes; the
  test asserts none of those byte sequences appear in the emitted
  trajectory/flight-recorder bytes. This is the projection-is-a-whitelist
  property made executable.

## 5. Blinding and contract analysis

- **No new `Packet` / `ResponseArtifacts` field.** Span-derived trajectories
  reach the judge exactly as log-derived ones do today; the D5
  field-coverage meta-test ([05](05-grading-judging.md) §5) and the
  `arm_map`-never-in-packet assertion are unaffected and remain the guards.
- **Frozen bytes untouched:** trajectory v3 / flight-recorder v3 canonical
  recipes, the closed 9-role vocabulary + `-N` ordinal regex
  (`trajectory.py:40-46`), the `kind` enum, sha hoisting on the `trial`
  event, `TrialRecord` shape (spec 09's A13 field is the only addition, and
  it is spec 09's).
- **Structural contracts:** `adapters/otlp.py` imports `hermetic.otlp_decode`
  types read-only (or duplicates the thin wrapper model if the linter's
  layering forbids that edge — resolve at implementation against the A5
  contract set); it imports no LLM client, no protobuf (decoding already
  happened at capture); `grade`/`judge` never import it.
- **One-event property:** the normalizer emits no new ledger event — it
  feeds existing `trajectory_sha` / `flight_recorder_sha` fields on the
  `trial` event. Nothing to register.

## 6. Migration steps

1. Fixture corpus first: collect real OTLP-JSON exports (LangChain,
   pydantic-ai, hand-built multi-agent) into `tests/fixtures/otlp/`;
   hand-write the expected trajectory/flight-recorder goldens. Reproduce-
   before-fixing applied to greenfield: the goldens *are* the spec of the
   mapping, reviewed before the code exists.
2. `adapters/otlp.py`: selection, ordering, step mapping; registry entry.
3. Reasoning mapping + `turn` ancestor resolution.
4. Failure paths: `SpanMappingError`, `spans_corrupt` wiring in
   `interleave.py` (A12, shared commit with spec 09's vocabulary change),
   plan/run-time coherence validation.
5. Hypothesis property tests: arbitrary span forests → every emitted step
   validates under `TrajectoryStep` (`extra="forbid"` + role regex as the
   oracle); ordering invariant under input shuffling; the adversarial
   identity fixture.
6. Docker-marked e2e closing the loop with spec 09: real OTel SDK →
   collector → decode → normalize → `resolve_trajectory` returns `verified`.
7. Docs: `docs/adapters.md` gains the `otlp` platform section with the
   mapping table as the normative reference; `docs/images.md` cross-links
   ("emit spans instead of writing agent_log.json" path for OTel-native
   stacks).

## 7. Constraining tests

- Golden pairs byte-exact per fixture (mapping pinned; `OTLP_MAPPING_VERSION`
  bump discipline verified by a drift counter-test).
- Closed-vocabulary: `verdi.agent: "llama-planner"` → `spans_corrupt`;
  `worker-42` accepted; absent → unattributed (mirrors
  `tests/test_eval21_attribution.py:115-138`).
- Determinism: shuffled `batches` input → identical output bytes.
- Identity: adversarial fixture emits no model/vendor/arm byte sequences.
- Honest absence: empty batches → no trajectory artifact, trial completes.
- Cross-engine: fake engine's scripted spans produce a verified trajectory
  through the identical path (contract-suite row).
