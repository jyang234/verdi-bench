# 02 — EVAL-4 Implementation Plan: Run stage — seam over Harbor, adapters, hermetic trials, cost guard

**Read with:** `00-EVAL-1-master-plan.md`, `Eval4.spec.md`, `Eval4.decisions.ndjson`. **Requires:** EVAL-3 merged (ledger, `assert_lock`, `derive_schedule`). EVAL-8 slice A (public corpus import) should land in parallel so real tasks exist to run.

## 1. Gate status

**CLEAR.** All five decisions RESOLVED: D001 hermetic pre-baked egress (model APIs via metering proxy only; task deps pre-baked; other egress logged + flagged); D002 no silent retries, infra failures re-run as *new* trials, timeout is an outcome (30m default, per-task override); D003 pinned per-trial CPU/mem quotas + contention caveat unless serial; D004 telemetry from agent-native logs, unmeasurable = null never estimated, key-pattern redaction at artifact capture; D005 Harbor + agent binaries version-pinned in images, digests in trial provenance. Inherited EVAL-1-D001/D005/D006/D007 all RESOLVED. **This story can go to Opus immediately.**

## 2. Objective

A trial is a sealed event: pinned image in, one prompt in, artifacts and a normalized `TrialRecord` out; every deviation (timeout, infra failure, egress attempt, ceiling stop) recorded as data, never handled as exception. This is where insulation is won or lost.

## 3. Phase-0 spikes — do these before writing the seam

The spec carries three build-time verification items from discovery. Each is a timeboxed spike (≤ half-day) with a **documented fallback that is not a compromise**; record the outcome in the story ledger as an Observation event:

1. **Harbor agent-install network behavior** — do Harbor task images pull at trial time? Fallback: pre-bake agent binaries in our own image layer at corpus-build time (this is the D001/D005 posture anyway — the spike only determines whether Harbor fights it).
2. **Proxy interposition inside Harbor containers** — can the metering proxy be set as the container's only egress (env `HTTP(S)_PROXY` + default-deny network policy)? Fallback: sidecar proxy container sharing a netns with the trial container.
3. **Native telemetry depth** — what do claude-code and codex logs actually expose (tokens in/out/cache, cost, tool calls)? Fallback: adapter log parsing; anything unparseable is `null` + flagged [D004] — never proxy-estimated (the proxy meters as a *cross-check signal* only, per spec).

## 4. Module layout & public symbols

```
harness/run/seam.py            run_trial            # (task, arm, workspace) -> TrialRecord
harness/run/interleave.py      schedule             # executes EVAL-3's derive_schedule output
harness/run/budget.py          cost_guard
harness/run/egress.py          proxy_config
harness/adapters/base.py       TrialRecord
harness/adapters/claude_code.py ClaudeCodeAdapter
harness/adapters/codex.py      CodexAdapter
```

Internal `[plan choice]`: `harness/run/engines/harbor.py` and `harness/run/engines/fake.py` — the two implementations behind the seam; `harness/run/redact.py` (key-pattern redaction at artifact capture, sharing the *pattern-list* mechanism with `harness/blind/` but as a separate secrets list — see master plan §7.4).

## 5. Data contracts

**5.1 `TrialRecord` (pydantic)** [AC-2]: `trial_id`, `task_id`, `arm`, `repetition`, `outcome ∈ {completed, timeout, infra_failed, not_started_cost_ceiling}`, `exit_status`, telemetry `{tokens_in, tokens_out, tokens_cache, cost, wall_time_s, tool_calls}` — every field `Optional`, `null` ⇒ paired entry in `telemetry_nulls: [field,...]` (flagged, not imputed) [D004]; `flags: {egress_violation: bool, contention_caveat: bool, ...}`; `provenance: {image_digest, agent_binary_version, harbor_version, engine, tier: "ADVISORY", executed_at, quotas: {cpus, mem}}` [D003, D005, AC-9]; `artifacts_path`.

**5.2 Ledger events added** (constructors in `events.py`): `trial` (embeds the TrialRecord), `trial_infra_failed`, `run_stopped_cost_ceiling` (with accumulated figure) [AC-5, AC-7], `executed_order` (the realized interleave) [AC-4].

**5.3 Seam contract** [AC-1]: `run_trial(task, arm, workspace) -> TrialRecord`, engine chosen by config. One shared **contract test suite** (pytest parametrized over engines) that both `HarborEngine` and `FakeEngine` must pass — the fake is also the fixture backbone for every downstream story. Import-linter contract: only `harness/run/engines/harbor.py` may import Harbor (`test_ac1_engine_isolated`).

## 6. Implementation sequence

**M1 — Seam + fake engine.** Define the contract suite first (timeouts, artifact layout, record shape, failure modes), implement `FakeEngine` to pass it. Tests: `test_ac1_seam_contract` (parametrized), `test_ac1_engine_isolated`.

**M2 — Harbor engine (hermetic).** Pre-baked image build path (agents + task deps at corpus-build time; digests captured); container launch with pinned quotas [D003] (`test_ac6_quota_applied` via container inspect), no ambient network — egress only through the metering proxy. Reuse the existing Squid/devcontainer architecture as the proxy (spec says it drops in): allowlist = model API hosts; every other attempt ⇒ proxy log line + `egress_violation` flag on the record [AC-3] (`test_ac3_egress_flagged`, `test_ac3_image_digest_provenance`). Provider keys env-injected at trial start; never in image layers or ledger [AC-8] (`test_ac8_no_keys_in_images`: scan layers of a fixture image).

**M3 — Lifecycle.** Timeout as outcome: 30m default, per-task override; deadline → SIGTERM grace → SIGKILL → `outcome=timeout` (`test_ac5_timeout_outcome`). Infra failure (docker daemon error, container OOM before agent start, etc.) ⇒ `trial_infra_failed` event and, when rerun, a **new trial id** — mutation of an existing trial is unrepresentable (ids are write-once; the ledger is append-only anyway) (`test_ac5_no_silent_retry`, `test_ac5_infra_rerun_new_trial`). Contention caveat: any run with concurrency > 1 stamps `contention_caveat=true` on all its records [D003] (`test_ac6_contention_flag`).

**M4 — Adapters + redaction.** `ClaudeCodeAdapter` / `CodexAdapter`: parse agent-native logs (per Phase-0 spike 3) → normalized `TrialRecord` telemetry; absent field ⇒ `null` + `telemetry_nulls` entry, never estimated (`test_ac2_*_normalization`, `test_ac2_null_not_estimated`, from fixture logs of each agent). Artifact capture pipeline runs `redact.py` over transcripts/logs: known key patterns (provider key regexes, `sk-`/`AKIA`-style, plus configured extras) scrubbed before anything is written to `artifacts/<trial>/` (`test_ac8_redaction`: fixture transcript echoing a key). Note downstream dependency: EVAL-9 AC-4 assumes redaction happened **here**, upstream of every scorer.

**M5 — Interleave + cost guard + insulation proofs.** `schedule` consumes EVAL-3's `derive_schedule` output; the scheduler API takes only the derived order — arm-blocked execution is unrepresentable through it; executed order ledgered [AC-4] (`test_ac4_interleave_from_seed`, `test_ac4_executed_order_ledgered`). `cost_guard`: accumulate `cost` across records; before each trial start, refuse if past `cost_ceiling` and append `run_stopped_cost_ceiling` [AC-7, EVAL-1-D007] (`test_ac7_ceiling_stops`, `test_ac7_stop_ledgered`). Insulation property tests [AC-9]: canary strings seeded into holdouts must never appear in the trial container filesystem or any prompt payload (`test_ac9_holdout_canaries_absent` — reuses the canary corpus from `harness/blind/`); every local record carries `tier=ADVISORY` (`test_ac9_advisory_stamp`).

**M6 — CLI.** `bench run <experiment-dir>`: calls `assert_lock` (EVAL-3) first; end-to-end two-arm fixture experiment on local Docker producing chained trial events + artifacts ready for EVAL-5/EVAL-2.

## 7. Test plan summary

| AC | Tests | Notes |
|---|---|---|
| AC-1 | seam_contract (×2 engines), engine_isolated | import-linter + parametrized suite |
| AC-2 | claude_code_normalization, codex_normalization, null_not_estimated | fixture logs |
| AC-3 | egress_flagged, image_digest_provenance | live proxy log assertion |
| AC-4 | interleave_from_seed, executed_order_ledgered | vs EVAL-3 schedule |
| AC-5 | timeout_outcome, no_silent_retry, infra_rerun_new_trial | fault injection |
| AC-6 | quota_applied, contention_flag | container inspect |
| AC-7 | ceiling_stops, stop_ledgered | mid-run crossing fixture |
| AC-8 | redaction, no_keys_in_images | layer scan + transcript fixture |
| AC-9 | holdout_canaries_absent, advisory_stamp | hypothesis property over payloads |

## 8. Constraints checklist at merge

- No module outside the seam implementation imports the engine ✓ (M1 lint)
- Hermetic: pre-baked pinned images, model-API-only egress, violations flagged not tolerated ✓ (M2)
- Silent retries unrepresentable; every re-run a new ledgered trial ✓ (M3)
- Harbor + agent versions pinned; digests in provenance ✓ (M2)

## 9. Definition of done

`bench run` executes a two-arm fixture end-to-end on local Docker: chained trial events, ADVISORY-stamped records, redacted artifacts, executed-order event, ceiling enforcement demonstrated; contract suite green against both engines; Phase-0 spike outcomes ledgered.

## 10. Risks / watch items

- Docker-dependent tests need a CI runner with Docker; mark them and keep the fake-engine suite as the fast path.
- Proxy metering vs adapter telemetry will disagree slightly — that's the *cross-check signal*, not an error; surface the delta in the record, don't reconcile it.
- Out of scope, resist creep: OpenCode adapter, cloud sandboxes, TRUSTED tier.
