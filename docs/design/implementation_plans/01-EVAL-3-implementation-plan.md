# 01 — EVAL-3 Implementation Plan: Experiment schemas, hash-chained ledger, plan stage

**Read with:** `00-EVAL-1-master-plan.md`, `Eval3.spec.md`, `Eval3.decisions.ndjson`.
**Builds:** first — every other story writes into this substrate.

## 1. Gate status — read before building

- **RESOLVED:** D001 underpowered policy = warn + ledgered acknowledgment; D002 opacity v1 = tamper-**evident** (same-user writes, chain verification; dedicated-UID resistance deferred to TRUSTED tier); D003 ledger = ndjson + sha256 `prev_hash`; D004 lock = sha256 genesis event, mutation refuses run; D005 seed at plan, deterministic interleave; D006 fixed primary-metric vocabulary, composites banned.
- **OPEN (gate is formally blocked by both):**
  - **D007** power-model variance source. Working assumption = recommendation: calibration-run variance feeds `mde_check`; pre-calibration experiments carry an `assumption_based_mde` flag in ledger and findings. Build the flagged-fallback path now (calibration data won't exist until EVAL-8 slice A runs anyway); the variance-source is an injected provider (§M6) so the resolution is a small diff.
  - **D008** lock-integrity hardening: external head-hash anchoring at checkpoints + actor attestation on the genesis event. Working assumption = recommendation (`anchor-plus-attestation-v1`). Implement as an optional `anchors` subsystem (§M3) that is on by default but cleanly severable if D008 resolves to defer.
- Inherited: EVAL-1-D001 (verdi-bench) and EVAL-1-D007 (cost ceiling required) — both RESOLVED. No inherited blockers.

## 2. Objective

A locked experiment is a cryptographic commitment — what will be measured, how it's decided, what it may cost, in what order trials run — fixed before the first token is spent; every subsequent event hash-chained to it and stamped with instrument identity; underpowered designs unable to masquerade as evidence.

## 3. Module layout & public symbols

Spec touchpoints (must exist under these names):

```
harness/schema/experiment.py   ExperimentSpec
harness/ledger/chain.py        append_event, verify_chain
harness/ledger/events.py       (typed constructors)
harness/plan/lock.py           lock_experiment
harness/plan/power.py          mde_check
harness/cli.py                 cmd_plan   (+ verify-chain verb [AC-3])
```

Internal additions `[plan choice]`: `harness/schema/metrics.py` (closed `PrimaryMetric` enum — imported later by EVAL-9's negative tests), `harness/plan/interleave.py` (`derive_schedule(seed, trials)` — the pure function EVAL-4 executes), `harness/ledger/anchors.py` (D008), `harness/version.py` (from M0).

## 4. Data contracts

**4.1 `experiment.yaml` → `ExperimentSpec` (pydantic v2, `extra="forbid"`)** [AC-1]

Required fields: `arms` (list; each `{name, platform, model, payload}`), `corpus` (ref: corpus id + version, per EVAL-8's manifest addressing), `repetitions: int > 0`, `primary_metric: PrimaryMetric` — enum `{holdout_pass_rate, judge_preference, cost_per_task, wall_time}` [D006], `decision_rule` (string DSL v1: threshold + direction on the primary, e.g. `"delta_holdout_pass_rate > 0"` `[plan choice: keep it a validated string; no expression engine]`), `judge` block (validated against EVAL-2's judge-config schema; until EVAL-2 lands, validate shape only: `model` must be `provider/versioned-id`, alias ids rejected here at plan time per EVAL-2 AC-5), `seed: int`, `cost_ceiling` (currency amount, **required**; missing ⇒ named error) [EVAL-1-D007], optional `hypothesized_effect` (consumed by `mde_check`), optional `fractional_scoring: bool=false` (pre-registration hook for EVAL-5 AC-3).

Rejections with named errors: composite/unknown primary metric [AC-1], missing cost ceiling [AC-1], alias judge id.

**4.2 Ledger event envelope** [AC-3, AC-6]

```json
{"event": "<type>", "prev_hash": "<64-hex>", "provenance": {"ts": "...", "actor": "...", "experiment_id": "...", "instrument": {"version": "...", "git_sha": "..."}}, ...payload}
```

Canonicalization `[plan choice — pin it, everything depends on it]`: serialize with `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`; one event per line, `\n` terminated; `prev_hash` = sha256 hex of the previous **line's exact bytes excluding the trailing newline**; genesis event uses `prev_hash = "0"*64`. Document this in `chain.py`'s docstring; `verify_chain` and `append_event` share one `canonical_line()` helper so they cannot drift.

**4.3 Genesis / lock event** [AC-2, D004, D008]

`experiment_locked` payload: `spec_sha256` (over the yaml file's exact bytes), `spec_path`, `seed`, `mde` result block, plus D008 attestation: `{attested_by, method}` `[assumption: D008 rec]`.

**4.4 Event types shipped by this story** (constructors in `events.py`): `experiment_locked`, `acknowledged_underpowered`, `chain_anchor` (D008), plus the constructor framework other stories extend (registration decorator; unknown event types rejected). Constructors are the **only** write path — enforce with an import-linter contract: nothing outside `harness/ledger/` calls `chain.append_event` directly `[plan choice implementing the "typed constructors are the only write path" spec statement]`.

## 5. Implementation sequence

**M1 — Schema.** `ExperimentSpec` + `PrimaryMetric` enum + fixtures (valid, composite-metric, missing-ceiling, alias-judge). Tests: `test_ac1_schema_valid`, `test_ac1_composite_metric_rejected`, `test_ac1_missing_cost_ceiling_rejected`.

**M2 — Chain core.** `canonical_line`, `append_event(path, event) -> event | raise`, `verify_chain(path) -> ok | first_broken_link`. Atomicity `[plan choice]`: open `O_APPEND`; acquire `flock` (exclusive) for the append critical section (serializes same-host writers — sufficient for tamper-*evident* v1 per D002); encode full line, single `os.write`, `fsync`; any exception ⇒ raise, and because the write is a single syscall of the whole line, no partial line survives normal failure paths. Fault-inject via an injectable writer to prove exception ⇒ no partial line (`test_ac7_append_atomic`). `verify_chain` walks the file, recomputes each link, reports **first** broken link with line number, distinguishes rewrite/deletion/reorder in its message where determinable; clean file ⇒ exit 0. Tests: `test_ac3_chain_append`, `test_ac3_tamper_detected` (mutate a middle line; delete a line; swap two lines).

**M3 — Events + provenance + anchors.** `events.py` constructor framework; provenance auto-stamped from `harness/version.py` + injected clock/actor; events missing provenance fail schema (`test_ac6_event_provenance_stamped`). D008 `anchors.py`: `anchor_head(ledger, out)` writes `{head_hash, height, ts}` to an external location (a sibling anchors file outside the experiment dir for v1; interface takes a destination so "external" can later mean git-notes/remote) `[assumption: D008 rec]`; `bench anchor` verb; `verify_chain --against-anchor` cross-checks.

**M4 — Lock.** `lock_experiment(spec_path, ledger)` = validate (M1) → `mde_check` (M5) → sha256 the yaml bytes → append `experiment_locked` genesis. Provide `assert_lock(spec_path, ledger)` used by **every** later stage entrypoint: recompute the sha, refuse on mismatch printing recorded vs computed hashes [AC-2]. EVAL-4/5/2/6/7/9 plans all call this — make it one obvious helper. Tests: `test_ac2_lock_genesis`, `test_ac2_mutation_refused` (mutate yaml post-lock; assert run/grade/analyze-shaped entrypoints refuse — stub entrypoints for the not-yet-built stages so the property is pinned now).

**M5 — Interleave derivation.** `derive_schedule(seed, trial_set)` — pure, reproducible: namespaced sub-seed (`sha256(seed||"interleave")`), Fisher–Yates over the full `(task, arm, repetition)` trial list `[plan choice]`. Same locked plan ⇒ identical schedule; different seed ⇒ different recorded order. Tests: `test_ac5_seed_recorded`, `test_ac5_interleave_deterministic`.

**M6 — Power.** `mde_check(spec, variance_source) -> {mde, method, flags}`. Seeded simulation under the paired-binary model [spec: method detailed at build — this is the detail `[plan choice]`]: model per-task paired outcomes with per-arm success probability `p` and within-task correlation `ρ`; sweep effect sizes; MDE = smallest delta detected at 80% power / α=0.05 two-sided under the same paired-bootstrap decision procedure EVAL-6 will use (share the resampler once EVAL-6 lands; a local copy is acceptable now with a TODO to unify). `variance_source` is injected [D007]: `AssumedVariance(p, ρ)` ⇒ result flagged `assumption_based_mde` (flag propagates into the lock event and later into findings via EVAL-6); `CalibrationVariance(ledger_ref)` reads real calibration-run variance once EVAL-8 slice A has produced one. If `hypothesized_effect < mde`: refuse lock unless `--acknowledge-underpowered`, which appends `acknowledged_underpowered` [D001, AC-4]. Tests: `test_ac4_mde_computed`, `test_ac4_underpowered_requires_ack`, `test_ac4_ack_ledgered`.

**M7 — CLI + one-event property.** `bench plan <experiment.yaml> --ledger <path> [--acknowledge-underpowered]`; `bench verify-chain <ledger>` (nonzero exit naming first broken link) [AC-3]; `bench anchor` (D008). Hypothesis property test: for every stage entrypoint registered so far, one invocation ⇒ exactly one appended event, success or failure (`test_ac7_one_event_per_operation`) — expose the entrypoint registry so later stories' verbs are automatically swept into this property.

## 6. Test plan summary

| AC | Tests | Type |
|---|---|---|
| AC-1 | test_ac1_schema_valid / composite_metric_rejected / missing_cost_ceiling_rejected | unit + fixtures |
| AC-2 | test_ac2_lock_genesis, test_ac2_mutation_refused | fixture, cross-stage |
| AC-3 | test_ac3_chain_append, test_ac3_tamper_detected, test_ac3_verify_cli | unit + CLI |
| AC-4 | test_ac4_mde_computed / underpowered_requires_ack / ack_ledgered | seeded sim + CLI |
| AC-5 | test_ac5_seed_recorded, test_ac5_interleave_deterministic | property |
| AC-6 | test_ac6_event_provenance_stamped | schema |
| AC-7 | test_ac7_append_atomic, test_ac7_one_event_per_operation | fault-injection + hypothesis |

## 7. Constraints checklist at merge

- ndjson + sha256 `prev_hash`, one chain per ledger file ✓ (M2)
- Genesis lock; primary metric + decision rule immutable post-lock ✓ (M4)
- Underpowered ⇒ only with ledgered ack carried into findings ✓ (M6; findings side lands with EVAL-6 AC-3)
- Opacity v1 is tamper-EVIDENT, not tamper-proof — do **not** build UID separation; do document the boundary in the module docstring ✓ (D002)

## 8. Definition of done

`bench plan` and `bench verify-chain` functional against fixture experiments; ledger module importable by sibling stories with the constructor-extension pattern documented; full AC suite green including tamper and atomicity property tests; D007/D008 seams documented so their resolutions are config-sized diffs. Formal story closure additionally requires D007 and D008 RESOLVED in the ledger.

## 9. Risks / watch items

- **Canonicalization drift** is the classic failure: one helper, shared by append and verify, golden-file tested.
- **mde_check realism**: assumed variance will be wrong until calibration data exists — that's exactly why D007's flag must ride into findings; don't quietly drop it.
- **Stub entrypoints** for not-yet-built stages (M4) must be replaced, not duplicated, as stories land — leave `TODO(EVAL-4)`-style markers keyed to tickets.
