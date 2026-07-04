# 09 — EVAL-10 Implementation Plan: Contamination sentinel — training-set membership detection for tasks and solutions

**Read with:** `00-EVAL-1-master-plan.md`, `eval10.spec.md`, `eval10.decisions.ndjson`. **Requires:** EVAL-8 (manifest, admission hook, curation flow), EVAL-6 (fence, findings document, confound-disclosure posture), EVAL-2 (provider client seam), EVAL-4 (insulation alarm channel `HoldoutLeakError`), EVAL-3 (event registry, one-event property sweep). Independent of EVAL-11/EVAL-12. Origin: Phase-7 readiness assessment roadmap gap #2.

## 1. Gate status — all local decisions RESOLVED (2026-07-04)

| Decision | Resolution | Consequence here |
|---|---|---|
| D001 asymmetric disposition | **refuse-official** | New fence check + `AnalyzeError` subclass; exploratory still renders, watermarked, with the summary |
| D002 v1 probe set | **canary-plus-oracle-prefix** | Two probe techniques in `probe.py`; no logprob/perplexity paths (v2) |
| D003 overlap metric | **winnowing-fingerprints** | Token k-gram winnowing in `overlap.py`; threshold pre-registered in the locked spec, never tuned post-hoc |
| D004 flagged-task lifecycle | **disclose-plus-operator-quarantine** | No auto-removal anywhere in this story; disclosure is the default and quarantine stays the operator's ledgered act (flake-quarantine mechanics already refuse quarantined `(task_id, task_sha)` at schedule) |

Inherited EVAL-1-D001 RESOLVED. The spec graduates from `specs/proposed/` to `specs/` in the same commit as the first AC tests (per its own header), with the resolved decisions reflected (`open_decisions: []`).

## 2. Objective

The paired A/B design controls harness confounds, not memorization. Three independent channels — deterministic dating, planted canaries, and solution-overlap fingerprints — each honest about what it can prove, feed a per-arm contamination summary disclosed in every render. The single validity-breaking case, *asymmetric flagged* contamination, refuses the official render; everything else is disclosure, never suppression (EVAL-6 posture).

## 3. Module layout & public symbols

```
harness/contamination/dating.py    ContaminationStatus, cutoff_status
harness/contamination/canary.py    derive_canary, hash_canary, embed_canary
harness/contamination/overlap.py   winnow_fingerprints, solution_overlap
harness/contamination/summary.py   contamination_summary, asymmetric_flags
harness/contamination/probe.py     run_memory_probe
harness/contamination/cli.py       register → `bench contamination probe`
```

The spec's touchpoint `harness/cli.py:cmd_contamination_probe` is realized as `harness/contamination/cli.py:register` wired into the `_register_stage_commands` list — the established stage-CLI pattern every story since EVAL-4 uses (touchpoints are `[judgment]`-planned symbols). Deterministic tier = `dating`, `canary`, `overlap`, `summary`; the probe module (and the CLI that drives it) is the story's only LLM-touching surface [AC-6].

## 4. Data contracts

**4.1 Schema additions (additive, optional — old specs/manifests still validate):**
- `Arm.training_cutoff: Optional[str]` — RFC3339 date/timestamp of the arm model's training cutoff. Inside the locked spec bytes, so it is pre-registered by construction.
- `TaskEntry.created_at: Optional[str]` — RFC3339; stamped at staging from the merge request's `merged_at` (input data, not wall clock — determinism seam preserved).
- `TaskEntry.canary_sha256: Optional[str]` — hash of the derived canary; the value itself never enters the manifest.
- `ExperimentSpec.contamination: Optional[ContaminationConfig]` with `overlap_threshold` (0, 1] — locked with the spec [D003]. Absent block ⇒ module default `0.5`, fixed in code, still not post-hoc tunable.

**4.2 Tri-state** [AC-1]: `ContaminationStatus ∈ {clean_by_date, unknown, flagged}` per `(task, arm)`. `flagged` ⇐ positive AC-3/AC-4 detection (detection outranks dating); `clean_by_date` ⇐ both dates present ∧ `created_at > training_cutoff`; `unknown` otherwise — a missing date or a pre-cutoff creation never launders into clean. Malformed dates raise, they do not degrade to unknown (fail loudly).

**4.3 Canary** [AC-2]: `derive_canary(task_sha)` = `"VBCANARY-" + sha256("verdi-bench/contamination-canary/v1:" + task_sha)[:32]` — namespaced sub-hash, no randomness. `embed_canary` appends the inert marker `<!-- VBCANARY-… -->` to the prompt; re-embedding raises. Admission records `hash_canary(canary)` on the internal-manifest entry; the `task_admitted` event format is untouched (no ledger-contract change). This canary corpus is disjoint from the §7.4 blinding canaries but reuses the same scrub mechanism (`harness.review.scrub.assert_identity_free(text, canaries=…)`).

**4.4 `contamination_probe` event** [AC-3]: one event per probe run, additive verb.
`{"probe": {status: complete|cant_probe, reason: null|timeout|refusal|context_overflow|provider_error|canary_in_prompt, threshold, arms: {name: {model, outcomes: {task_id: flagged|negative|unprobed}, channels: {task_id: [canary_regurgitation|oracle_prefix|solution_overlap]}}}, canary_sha256: {task_id: hash}}}`.
`cant_probe` carries **no** outcomes — a failed probe is never a silent partial probe. Canary values are unrepresentable in the event (hash-only).

**4.5 Findings** [AC-5]: `FindingsDocument.contamination` = `{probe_status, per_arm: {arm: {clean_by_date, unknown, flagged, flagged_task_ids}}, asymmetric: [{task_id, flagged_arms, unflagged_arms}]}` — computed by `contamination_summary(ledger_path, spec, manifest)` (manifest optional; absent dates ⇒ unknown). Rendered in **both** renders as a `Contamination (disclosed, non-suppressing)` section, mirroring the confounds block. Asymmetry ⇒ `AsymmetricContaminationError` in the official fence, mapped into `CantAnalyzeReason`.

**Asymmetry definition (fail-closed):** a task is asymmetric when at least one arm is `flagged` and at least one is not. The spec's parenthetical names the flagged-vs-clean case; flagged-vs-unknown breaks pairing validity identically, so it refuses too — the VC fixtures (flagged/clean refuses; both-flagged and all-unknown render with caveat) hold either way.

## 5. Implementation sequence

**M1 — Dating + schema.** `cutoff_status` pure function; `training_cutoff` / `created_at` / `canary_sha256` fields; `stage_candidate(created_at=…)` plumbed from `mr.json`'s `merged_at`. Tests: `test_ac1_cutoff_tristate` (full date-combination table), `test_ac1_unknown_never_clean`.

**M2 — Canary.** `derive_canary` / `hash_canary` / `embed_canary`; `admit_task` derives + records the hash at admission; `bench corpus admit --candidate-json` embeds the marker into the stored candidate prompt. Tests: `test_ac2_canary_deterministic_embed`, `test_ac2_canary_never_published` (ledger bytes carry hash only; a render containing a canary value fails `assert_identity_free`).

**M3 — Overlap.** Token k-gram (k=5) rolling winnow (window=4), `hashlib`-based hashes (never Python `hash()` — salted). Score = containment of the reference's fingerprints in the solution's. `solution_overlap(solution, *, oracle, holdouts, threshold)`; any holdout containment ≥ threshold raises `HoldoutLeakError` (EVAL-4 alarm channel) carrying the result. Degenerate (un-fingerprintable) reference raises rather than scoring 0.0. Tests: `test_ac4_overlap_flags_verbatim`, `test_ac4_holdout_overlap_alarms`, plus near-verbatim-flags / independent-does-not / byte-identical-output.

**M4 — Probe + CLI.** `run_memory_probe(ledger_path, ctx, *, arms, tasks, provider=None, threshold, overlap_outcomes=None)` through `harness.judge.providers.base.get_provider` (the `harness.process` precedent): per arm model, canary-regurgitation and oracle-prefix-continuation probes at temperature 0; refuses to send a prompt containing the canary (`cant_probe: canary_in_prompt`); every `ProviderError` maps through `provider_failure_reason` to a `cant_probe` event. Ledgers exactly one `contamination_probe` event per run; merges the CLI's deterministic overlap outcomes so the event is the story's single measurement record. Entrypoint `contamination-probe` registered for the one-event property sweep (`EXPECTED_ENTRYPOINTS` + module import in `test_eval3_property.py`). CLI: `bench contamination probe <experiment-dir> --manifest … [--oracle-dir …]` — probes never run inside trial containers and share no context with judge calls (separate provider calls, separate module). Tests: `test_ac3_regurgitation_flags` (FakeProvider), `test_ac3_cant_probe_fail_closed`.

**M5 — Summary + fence.** `contamination_summary` joins dating (manifest × spec) with the latest complete probe event's flags per `(task, arm)` over the tasks that ran; `compute_findings` stores it; both renderers emit the section; `_assert_official_calibration` gains the asymmetry check raising `AsymmetricContaminationError` naming task ids and arms; `CantAnalyzeReason.ASYMMETRIC_CONTAMINATION` + mapping entry. Tests: `test_ac5_asymmetry_refuses_official`, `test_ac5_symmetric_discloses`, all-unknown-renders-official-with-caveat, exploratory-always-renders-with-summary.

**M6 — Contracts.** `.importlinter`: new `contamination-detectors-have-no-llm-clients` (sources: `dating`, `canary`, `overlap`, `summary`; forbidden: `harness.judge.providers`, `harness.judge.client`); `harness.contamination` added to the harbor-seam and ledger-writes source lists. Planted-import case in the contract test machinery. Test: `test_ac6_detectors_llm_free`.

**M7 — Graduation.** Spec + decisions move to `specs/` (same commit as the first AC tests, AC hook then enforces eval10); README gains the `bench contamination probe` line; `make verify` green.

## 6. Test plan summary

| AC | Tests |
|---|---|
| AC-1 | cutoff_tristate (all date combos), unknown_never_clean (absent cutoff ⇒ unknown; pure function) |
| AC-2 | canary_deterministic_embed, canary_never_published (scrub property; events hash-only) |
| AC-3 | regurgitation_flags (fake provider), cant_probe_fail_closed (reason enum; no partial outcomes) |
| AC-4 | overlap_flags_verbatim (+ near-verbatim, independent-negative, byte-identical), holdout_overlap_alarms (`HoldoutLeakError`) |
| AC-5 | asymmetry_refuses_official (names task + arms), symmetric_discloses (+ all-unknown caveat, exploratory summary) |
| AC-6 | detectors_llm_free (lint-imports contract; planted provider import breaks it) |

## 7. Constraints checklist at merge

- Canary values hash-only in events/reports/renders — never published ✓ (M2 property test; M4 event shape)
- Probes never inside trial containers, no shared context with judge calls ✓ (M4: separate module, separate calls; review)
- Overlap threshold pre-registered at plan lock, never post-hoc ✓ (spec field inside the locked bytes; M3)
- Unknown disclosed as unknown, never upgraded to clean or silently downgrading a finding ✓ (M1 test; M5 non-suppressing section)

## 8. Definition of done

The fixture experiment of the spec's expected post-state: a post-cutoff task counts `clean_by_date`, a canary-regurgitated task and an overlap-flagged trial count `flagged`, both renders carry the per-arm summary, the asymmetric case refuses official with a named refusal, `bench contamination probe` is in the one-event sweep and the README, `make verify` green.

## 9. Risks / watch items

- The canary's evidentiary value dies on any leak: every new render/packet surface added later must inherit the scrub property test — the shared mechanism exists precisely so this is one list to extend.
- Winnowing parameters (k=5, window=4) are code constants; changing them re-scores history, so treat them like the threshold: versioned, never tuned against observed trials.
- Out of scope, resist creep: logprob membership inference (D002 v2), contamination removal/task rewriting, probing non-arm models, dedup against public benchmark dumps.
