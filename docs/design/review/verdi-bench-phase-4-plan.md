# verdi-bench — Phase 4 plan: connective tissue — wire the pipelines

**Date:** 2026-07-03 · **Follows:** Phase 3 (merged to `main`, PR #9) ·
**Source of record:** `verdi-bench-review-consolidated.md` §5 Phase 4 + §3.4 (Judge),
§3.6 (Corpus), §3.7 (Review), §3.8 (Process), §3.3 (Plan), §6 (readiness gate).
Orientation: `verdi-bench-phase-4-handoff.md`.
**Branch:** `claude/verdi-bench-phase-4-plan-k0p5fx` (branched from `main`, which
already contains Phase 1 + Phase 2 + Phase 3 + the handoff).

## Context

Phase 1 made results *integrity* real (chain verified at every stage entry, lock
hardened, task-content commitment, real grade path). Phase 2 made the *execution
path* real (hermetic metered Harbor trials, honest cost guard/quarantine/
baseline). Phase 3 made every stage *fail closed* (judge/process/review/analyze/
corpus emit exactly one event per attempted operation; the one-event property
sweep covers all 12 ledgered entrypoints with an explicit expected set; PL-14
folded the ack path into one event). All three are on `main`.

Phase 3 made every stage *fail closed*; **Phase 4 makes every stage
*reachable*.** The systemic diagnosis §2.2 — **"correct primitives, missing
connective tissue"** — still holds verbatim in the current tree: `judge_pair`,
`build_review_packet`, `select_for_review`, `reviewed_kappa_items`,
`kappa_report`, `process_kappa_by_dimension`, `score_telemetry_correlation`,
`is_schedulable`, `record_calibration_run`, `CalibrationVariance`, and
`EscalationConfig` all have **zero production callers** (re-confirmed below); the
verbs `bench judge`, `review build`, `process score`, and any corpus admission
verb **do not exist**. Phase 4 wires the spec-promised verbs that are
built-but-inert, so a complete fake-engine experiment runs plan → run → grade →
judge → analyze → review → process **end-to-end through `bench` verbs only**.

The §9 branch/merge question in the handoff is **already resolved**: Phase 3 was
merged (PR #9), this branch is cut from `main`, so Phase 4 builds on the merged
base — the fail-closed seams, the four Phase-3 event types/producers, and the
entrypoint registry are all present. Nothing to stack or reconcile.

### Re-verification against the current tree (not `01641cd`)

The consolidated review's line numbers are pre-Phase-1 and stale; Phases 1–3
shifted the tree substantially. I re-located every Phase 4 finding against the
working tree at branch HEAD. **All of them reproduce.** Concrete current-tree
evidence:

**CLI verb surface (`harness/cli.py:125-137`)** registers only
`run/grade/corpus/analyze/review/process` — **no `judge`**. Sub-verbs:
`review record|reveal` (`review/cli.py:28,67`) — **no `build`**;
`process record` (`process/cli.py:28`) — **no `score`**;
`corpus import|subset|mine|review|approve` (`corpus/cli.py:30-127`) — **no
`admit`**.

**Zero-production-caller re-confirmation** (grep across `harness/` — the only
non-test callers are the Phase-3 property *entrypoints*, which are test
scaffolding, not CLI verbs):
- `judge_pair` (`judge/client.py:84`): sole non-test caller is
  `_judge_entrypoint` (`client.py:226`). No CLI. `canaries` is
  `Optional[list[str]] = None` (`client.py:92`) — never derived from the spec.
  `EscalationConfig` (`schema/judge_config.py:47`) is referenced only as a
  default (`:62`); `kappa_by_class` re-hardcodes `0.6/20` (JD-9).
- `build_review_packet` / `select_for_review` / `reviewed_kappa_items` /
  `kappa_report` (`review/packet.py:66`, `sample.py:114,142`, `kappa.py:143`):
  **only tests** (RV-3).
- `process_kappa_by_dimension` / `score_telemetry_correlation`
  (`process/calibrate.py:36,97`): **only tests** (PR-5). The isolated judge path
  `score_trial_process` (`score.py:171`) exists and is entrypoint-wired.
- `is_schedulable` (`corpus/registry.py:214`): **only tests** (CO-2);
  `bench run` never consults a manifest.
- `record_calibration_run` / `ledger_calibration_run`
  (`registry.py:220`, `ledger_ops.py:22`): the event type + emitter exist
  (Phase 3), invoked only from `_calibration_run_entrypoint` (`ledger_ops.py:83`)
  and tests — **no run-path producer** (CO-4 Phase-4 half).
- `CalibrationVariance` (`plan/power.py:49`): a thin holder with a
  `TODO(EVAL-8)` (`:53`); **no loader**; every lock uses `AssumedVariance()`
  (`lock.py:89`, default `n_tasks=50`) → `assumption_based_mde` (PL-5).

**Load-bearing specifics still true:**
- **RV-2:** `review reveal` hardcodes `arm_identities={"1":"arm_a","2":"arm_b"}`
  (`review/cli.py:81`) — the ledgered unblinding is fiction.
- **RV-9 carry-forward:** `comparison_id` is `Optional[str] = None` on both the
  `Verdict` schema (`judge/schema.py:80`) and `judge_pair` (`client.py:93`), so
  Phase 3's RV-9 gate (refuse a human verdict whose `comparison_id` has no
  matching `judge_verdict`) is only reliable once `bench judge` **threads a
  populated deterministic id** and `review build` **records the mapping keyed by
  it**.
- **PL-1:** `mde_check` uses `n = variance_source.n_tasks` (`power.py:145`);
  `spec.repetitions` (`experiment.py:106`, `Field(gt=0)`) and corpus size are
  ignored; the gate is entirely skipped when `spec.hypothesized_effect is None`
  (`lock.py:97`) with **no gate-skip flag ledgered**.
- **PL-12:** `spec.hypothesized_effect` (`experiment.py:112`) is
  `Optional[float] = None` with **no bounds** — negatives are always
  "underpowered", values > 1 always pass the gate (`lock.py:99`).

**Baseline:** `uv run pytest -m "not docker" -q` → **318 passed, 3 deselected**;
`make verify` green; 3 import-linter contracts kept.

## Decisions

Phase 4 is the widest-surface phase; four direction-setting choices need
explicit human resolution **before** the owning slice (per CLAUDE.md "the human
decides"). Each is stated below with a recommendation + trade-offs and recorded
as a `resolved` event in the owning `docs/design/specs/evalN.decisions.ndjson`
before its slice lands. They are marked `pending-confirmation-at-phase-4-start`,
mirroring how Phase 2's D-8/D-9/D-10 were confirmed at phase start.

### Carried forward (resolved, constrain Phase 4)

- **REVIEW-D-6 (task-content commitment, Phase-1 resolution).** Phase 1 pinned
  `{corpus_id, semver, sha256(per-task shas)}` into `experiment_locked` and
  **deferred** *"full manifest + cache-as-source (holdout import into the cache,
  `is_schedulable` at run) to Phase 4 because the cache does not yet store
  holdouts."* This is the real prerequisite behind CO-2 (see D-P4-2).
- **EVAL-2-D006 (escalation thresholds).** `kappa_threshold`/`min_human_verdicts`
  recommended `0.6`/`20` (`eval2.spec.md:196-197`). `bench judge` threads the
  **`EscalationConfig`** values, not the hardcoded `0.6/20` in
  `calibrate.py` (JD-9).
- **REVIEW-D-4 (verdict confidence enum) and REVIEW-D-5 (degenerate kappa) are
  Phase 5**, not Phase 4 — Phase 4 threads `comparison_id`/`task_class` and wires
  calibration through the IPW seam but does **not** migrate the confidence schema
  or change the degenerate-kappa policy.

### To confirm at the start of Phase 4 (recommendation + trade-offs stated)

- **D-P4-1 (RV-2/RV-3/RV-6/RV-9, JD-9 carry-forward) — the Response-1/2 ↔ arm
  mapping seam. Recommend a new additive `review_packet_built` event recording
  the per-comparison response-order → arm mapping, keyed by `comparison_id`.**
  Nothing today records which arm was "Response 1/2", so `--winner A` maps to the
  judge's A/B only by unrecorded convention, reveal is fiction (RV-2), and guess
  accuracy is structurally 0.0 (RV-6). This is the **load-bearing** Phase-4
  decision — reveal, guess-accuracy, and the EVAL-9 process kappa join all key off
  it. Recommend: `bench review build` samples comparisons, randomizes response
  order **per comparison**, and emits one `review_packet_built`
  `{comparison_id, task_id, task_class, response_map: {"1": arm, "2": arm},
  seed}` event; reveal, `record`, and process scoring read the mapping from that
  event. It is a **versioned, hash-chained contract addition** (additive event
  type: old ledgers lack it, no chain invalidated), recorded with a migration
  note. This also makes the RV-9 gate reliable: the recorded mapping is keyed by
  the same deterministic `comparison_id` the judge threads (D-P4 slice 4A).
  *Alternative:* a field on an existing event (e.g. onto `judge_verdict`) — rejected:
  the mapping is a review-build artifact produced after judging, on a different
  sampled subset, and belongs to its own operation with its own entrypoint.

- **D-P4-2 (CO-2 / REVIEW-D-6 Phase-4 half) — holdouts in the corpus cache +
  run task source. Recommend: store holdouts in the cache and add
  `is_schedulable` gating at `bench run`, but keep the lightweight
  `task_commitment` as the run/grade integrity fence (do *not* rip out the
  Phase-1 commitment).** The Phase-1 deferral is a genuine prerequisite: the
  cache does not yet store holdouts, so a manifest-as-task-source switch has a
  migration story. Recommend the **minimal** shape that unblocks the exit:
  `import_terminal_bench` writes each task's holdout blob into the cache under
  its content sha; the manifest `TaskEntry` gains a `holdout_ref`; `bench run`
  loads the manifest, and the scheduler consults `is_schedulable(task_id)` —
  a non-`admitted` task is refused via the Phase-2 per-trial-failure wrap
  (`trial_infra_failed(reason="not_schedulable")`, so `executed_order` still
  lands) rather than silently running. `tasks.yaml` + `task_commitment` stay the
  integrity fence; the manifest becomes the *schedulability* source. *Alternative:*
  a full cache-as-sole-source switch (run/grade read tasks only from the manifest,
  delete the `tasks.yaml` path) — rejected for Phase 4: larger blast radius,
  re-plumbs grade's task load, and is not needed for the exit criterion.

- **D-P4-3 (CO-7) — approver-≠-miner attestation. Recommend: `corpus approve`
  records the approver identity into the `curation_approval` provenance and
  **refuses when the approver equals the task's miner**, with a typed
  `SelfApprovalError`.** Today the approver is `getpass.getuser()` with no
  attestation and no self-approval bar, and `corpus review` prints holdout
  **paths** only — the human gate cannot do the solution-leakage check it exists
  for. Recommend: `corpus review` renders holdout **content/diff** (so the check
  is performable); `corpus approve` requires an explicit `--attested-by` and
  refuses `attested_by == miner` (the miner is recorded on the candidate/manifest
  entry). *Alternative:* a cryptographic attestation (signed approval) — noted as
  future hardening; the identity-inequality bar is the minimal spec-satisfying
  gate for Phase 4.

- **D-P4-4 (JD-9/PR-5/RV-3/CO-8) — verb surfaces + inputs. Recommend the verb
  names `bench judge`, `bench review build`, `bench process score`, `bench corpus
  admit`, each reading the *locked* spec for identity/config and taking
  operational flags for I/O.** Concretely: `bench judge` reads arm names + model
  ids from the locked `experiment.yaml` to derive canaries and reads
  `judge.escalation` for the `EscalationConfig`; `bench review build` takes a
  sampling seed + output dir (operational) and reads the locked spec's canary set;
  `bench process score` mirrors `score_trial_process`'s inputs; `bench corpus
  admit` takes a candidate id + manifest path. Confirm before wiring so the CLI
  surface is stable. *Alternative:* fold `judge` under `run` or `review build`
  under `review record` — rejected: the spec and the §6 CLI inventory name these
  as first-class verbs.

### Contract additions (recorded before the owning slice lands)

Per CLAUDE.md "public seams are contracts" and handoff §5:

| Change | Kind | Owner | Slice | Migration note |
|---|---|---|---|---|
| `review_packet_built` (response↔arm map) | additive event **type** | EVAL-7 | 4B | additive; old ledgers lack it, no chain invalidated; reveal/record/process read the map from it |
| deterministic `comparison_id` populated on every `judge_verdict` | field **population** (schema already has the optional field) | EVAL-2 | 4A | no schema change (field exists, was `None`); makes the RV-9 gate reliable |
| `power_gate_skipped` flag in the lock event's `mde.flags` | additive **flag value** | EVAL-3 | 4G | additive; a new possible string in an existing list; old locks lack it |
| `hypothesized_effect` bounds `(0, 1]` | schema **validation** (pre-lock) | EVAL-3 | 4G | rejects at plan before any event; no ledger contract touched |

The judge `comparison_id` population is **not** a schema change — the field is
already `Optional[str]` on both the schema and `judge_pair`; Phase 4 stops
leaving it `None`. The `mde.flags` addition mirrors the existing
`assumption_based_mde` flag (already inline on the lock event). No new event type
is needed for `bench judge`/`process score`/`corpus admit`/the run-path
calibration hook: those call already-ledgered operations
(`judge_verdict`/`process_score`/`task_admitted`/`calibration_run`) — Phase 4
adds their **production callers**, and the existing entrypoints
(`judge`, `process`, `corpus-admit`, `corpus-calibration-run`) already cover the
one-event property. Only `review build`'s new event adds a **new entrypoint**
(`review-build`) to `EXPECTED_ENTRYPOINTS`.

## Phasing within Phase 4

Eight slices. The stages are more coupled than Phase 3 (the exit needs judge →
review → process to interlock), so ordering matters more. **4A unblocks 4B**
(review build records the mapping keyed by the judge's `comparison_id`); **4B
unblocks 4C** (IPW calibration over the reviewed items) **and the honest reveal**;
**4D** (process reporting) consumes the reviewed sample from 4B; **4E → 4F**
(admission before schedulability-as-source); **4G** (variance loader) reads the
`calibration_run` events **4E** produces; **4H (the end-to-end exit) lands last.**
Each slice is one logical change (1–3 atomic commits), ships a **reproduce-first**
test proving the capability is unreachable today → reachable after, registers an
entrypoint for any genuinely new ledgered operation, and `make verify` is green
before every commit. Line numbers are the current tree.

### 4A — `bench judge` + calibration wiring · JD-9, JD-11, JD-5, RV-9(comparison_id) · P1 (needs D-P4-4)
Give the judge stage its verb, feed it the locked spec, and make its verdicts
join reliably.
- **The `bench judge` verb (JD-9):** register a `judge` subcommand
  (`_register_stage_commands`, `cli.py:125`) that reads the **locked**
  `experiment.yaml`, derives canaries from the arm names + model ids
  (`validate_identity_free(packet, canaries)`, `client.py:141`), builds the
  `JudgeConfig` including `judge.escalation`, and calls `judge_pair` over the
  trial comparisons — the first non-entrypoint production caller.
- **Deterministic `comparison_id` (JD-9 + RV-9 carry-forward):** thread a
  deterministic `comparison_id` (e.g. `sub_seed`-derived from
  `(task_id, repetition)`) onto every verdict so `comparison_id` is never `None`
  in production — this is what makes Phase 3's RV-9 gate reliable end-to-end and
  what `bench review build` keys its mapping on (4B).
- **`EscalationConfig` through calibration (JD-9):** feed the config's
  `kappa_threshold`/`min_human_verdicts` into `kappa_by_class` instead of the
  hardcoded `0.6/20` (`calibrate.py:58-59`), so the D006 seam is live.
- **Flag `orders:"single"` (JD-11):** a full experiment with `orders: single`
  emits a `single_order` flag on the verdict/provenance and surfaces it (the spec
  allows single "only for smoke runs; **flagged**", `eval2.spec.md:193`).
- **Dedupe + exclude `CANT_JUDGE` from kappa (JD-5):** `pairs_from_ledger`
  (`calibrate.py:97-114`) dedupes duplicate judge verdicts (consistent last-write
  join, unified with 4C) and **excludes** `CANT_JUDGE` from kappa rather than
  entering it as an ordinary category; join on the real `comparison_id`, never on
  `None`.
- **Shared reason mapper (carry-forward, with 4D):** extract one
  `provider_failure_reason(exc)` used by both `CantJudgeReason` (`judge/schema.py`)
  and `CantScoreReason` (`process/score.py`), replacing the two parallel inline
  mappings (keep the enum *values* as the closed set).
- **Reproduce-first:** `bench judge` on a fake-engine fixture is unrunnable today
  (no verb) → after, produces `judge_verdict` events with populated
  `comparison_id`; a spec whose arm name appears in a diff is refused as an
  identity leak (canaries derived from the spec, today `None`); two duplicate
  verdicts + a `CANT_JUDGE` yield a kappa computed over the deduped, CANT_JUDGE-
  excluded set (today pooled/last-write); `orders: single` is flagged. Extends
  `tests/test_eval2_client.py`, `tests/test_eval2_plan.py`, a new
  `tests/test_eval2_cli.py`.

### 4B — `bench review build` + reveal-from-reality · RV-3, RV-2, RV-6, RV-7, RV-9 · P1 (needs D-P4-1)
Wire the review pipeline and record the mapping that makes reveal and guess
accuracy real.
- **`bench review build` verb (RV-3):** register a `review build` subcommand that
  calls `select_for_review` → `build_review_packet` (both zero-caller today),
  emitting the **`review_packet_built`** event (D-P4-1) with the per-comparison
  response-order → arm mapping keyed by `comparison_id`. Register a
  **`review-build` entrypoint** and add it to `EXPECTED_ENTRYPOINTS`.
- **Per-comparison response-order randomization (RV-2):** randomize Response-1/2 ↔
  arm **per comparison** (seeded), recording the realized map — no review-side
  randomization exists today (only the judge side randomizes).
- **Reveal reads real identities (RV-2):** `reveal_comparison` reads the
  **recorded** `response_map` from `review_packet_built` instead of the hardcoded
  `{"1":"arm_a","2":"arm_b"}` (`review/cli.py:81`); the reveal references the
  verdict + the mapping event.
- **Supply `actual_arm` + `task_class` (RV-6, RV-9):** `review record`
  (`cli.py:28-61`) looks up `actual_arm` and `task_class` from the recorded map
  keyed by `comparison_id`, so guess accuracy is a measured number (today
  structurally 0.0) and CLI verdicts no longer all land in `"default"`.
- **Non-recoverable mandatory/floor ordering (RV-7):** order the packet so the
  mandatory/floor (disagreements-first) boundary is **not** recoverable from the
  two independently id-sorted blocks (`sample.py:138`) — e.g. one seeded shuffle
  over the combined set with the boundary recorded only in the (unblinded) event,
  not reconstructable from packet order.
- **Reproduce-first:** `bench review build` is unrunnable today (no verb) → after,
  emits `review_packet_built` with a recorded `response_map`; a reveal discloses
  the **real** arm (today always `arm_a`/`arm_b`); a `--arm-recognized` answer
  with the recorded `actual_arm` yields nonzero guess accuracy (today 0.0); the
  packet's item order does not reveal the disagreement boundary. Extends
  `tests/test_eval7_review.py`.

### 4C — Review calibration through the IPW seam · RV-4, RV-5, ledger-read consolidation · P1 (no new decision)
Route judge calibration through the correct estimator with realized weights.
- **IPW seam, not raw pooled kappa (RV-4):** `kappa_by_class` routes calibration
  through the D003 IPW seam (`review/kappa.py:98-159`, today consumed only by
  EVAL-9) instead of raw pooled Cohen's kappa over the disagreement-heavy reviewed
  set (`calibrate.py:55-82`).
- **Realized inclusion probabilities (RV-5):** use the realized
  `ceil(0.2n)/n` floor probability, not the nominal `0.2` (`sample.py:126` vs
  `kappa.py:23,113-115`); expose `floor_prob` in `kappa_report`.
- **Ledger-read consolidation (carry-forward):** Phase 3's guards made
  `record_human_verdict` re-read/parse the whole ledger 4× and `reveal_comparison`
  4×. As this slice reworks the join path, verify + `read_events` **once** and
  filter the parsed list in the predicate helpers (bounded today, but the review
  calibration path is heavily reworked here — the natural place to fix the O(N²)).
- **Reproduce-first:** a reviewed set with a known floor draw yields the IPW
  kappa with the **realized** weight `3` for `n=6` (today `5`, ~1.67× over-weight)
  and a `floor_prob` in the report (today absent); the escalation decision matches
  the IPW estimator, not the biased pooled one. Extends
  `tests/test_eval7_review.py`, `tests/test_eval2_plan.py`.

### 4D — `bench process score` + analyze reporting · PR-5 · P1 (needs D-P4-4)
Make AC-5/AC-7 reporting reachable and surface it in findings.
- **`bench process score` verb (PR-5):** register a `process score` subcommand
  driving the isolated judge path `score_trial_process` (`score.py:171`, already
  entrypoint-wired) over the trial transcripts — the docstring already documents
  `score` (`process/cli.py`) but only `record` is registered.
- **Wire the kappa/correlation reporting (PR-5):** call
  `process_kappa_by_dimension` and `score_telemetry_correlation`
  (`calibrate.py:36,97`, zero callers) over the reviewed sample from 4B, and
  surface **kappa / correlations / `style_only`** in the analyze process section
  and render (`report.py:269-313,646-660`), which carry none today though plan M5
  requires them. Extends `run_analyze` (`analyze/cli.py`).
- **Shared reason mapper (carry-forward, with 4A):** `CantScoreReason` consumes
  the same `provider_failure_reason(exc)` extracted in 4A.
- **Reproduce-first:** `bench process score` is unrunnable today (no verb) →
  after, emits `process_score` events; an analyze render over a fixture with
  process scores + a reviewed sample shows a per-dimension kappa table, a
  score-vs-telemetry correlation table, and a `style_only` flag (today the render
  has none). Extends `tests/test_eval9_process.py`, `tests/test_eval6_analyze.py`.

### 4E — Corpus admission pipeline + run-path calibration hook · CO-8, CO-7, CO-4(producer) · P1 (needs D-P4-3)
Connect mine → manifest → admit end-to-end and put calibration on the chain from
the run path.
- **Mine → manifest insertion (CO-8):** `mine` writes a standalone candidate JSON
  today; add the insertion that turns a mined candidate into a manifest
  `TaskEntry` with its content sha, so `admit_task` (which requires a manifest
  entry, `admit.py:63-66`) has something to admit.
- **`bench corpus admit` verb (CO-8):** register an `admit` subcommand that calls
  `admit_task` (emits the Phase-3 `task_admitted` event, `admit.py:86`) and saves
  the manifest — reuses the existing `corpus-admit` entrypoint (no new event type).
- **Curation review shows content + attestation (CO-7, D-P4-3):** `corpus review`
  renders holdout **content/diff** (today paths only, `cli.py:107-109`); `corpus
  approve` requires `--attested-by` and refuses `attested_by == miner`
  (`SelfApprovalError`); the manifest is saved after an in-memory admission.
- **Run-path calibration hook (CO-4 Phase-4 half):** invoke
  `ledger_calibration_run` (`ledger_ops.py:22`, today only entrypoint/tests) from
  the run/baseline path so a calibration run actually ledgers a `calibration_run`
  event — reuses the `corpus-calibration-run` entrypoint. (Binding the official
  fence to the ledgered status is Phase 5, AN-2.)
- **Reproduce-first:** a mined candidate cannot be admitted today (no manifest
  insertion, no `admit` verb) → after, `mine → admit` emits one `task_admitted`;
  `corpus review` shows holdout content; `corpus approve` refuses a self-approval
  (miner == approver); a run-path calibration run emits one `calibration_run`
  (today nothing invokes it). Extends `tests/test_eval8_corpus.py`,
  `tests/test_eval8_commit.py`.

### 4F — Corpus-as-schedulability-source + `is_schedulable` at run · CO-2, REVIEW-D-6(Phase-4 half) · P1/P2 (needs D-P4-2)
Make `bench run` consult the manifest so pending/quarantined tasks don't run.
- **Holdouts in the cache (D-P4-2):** `import_terminal_bench` stores each task's
  holdout blob in the cache under its content sha; `TaskEntry` gains a
  `holdout_ref` — the prerequisite the Phase-1 D-6 resolution deferred.
- **`is_schedulable` at `bench run` (CO-2):** `bench run` loads the manifest and
  the scheduler consults `is_schedulable(task_id)` (`registry.py:214`, zero
  callers); a non-`admitted` task is refused via the Phase-2 per-trial-failure
  wrap — `trial_infra_failed(reason="not_schedulable")` so `executed_order` still
  lands (RN-15 discipline), never a silent run. `tasks.yaml` + `task_commitment`
  stay the integrity fence.
- **Reproduce-first:** a manifest with a `pending` task runs, grades, and feeds
  findings today → after, `bench run` refuses it end-to-end with a
  `trial_infra_failed(not_schedulable)` and still lands `executed_order`; an
  `admitted` task runs. Extends `tests/test_eval4_lifecycle.py`,
  `tests/test_eval8_corpus.py`.

### 4G — Power gate at real N + `CalibrationVariance` loader · PL-1, PL-5, PL-12 · P1 (needs D-P4-4 verb confirm; contract-additive)
Make the power gate consult the design and read real calibration variance.
- **Power at real N (PL-1):** compute power at `spec.repetitions × corpus_size`
  paired observations, not `variance_source.n_tasks` (default 50); when
  `spec.hypothesized_effect is None`, ledger a `power_gate_skipped` flag in
  `mde.flags` (today the gate is silently skipped with nothing recorded).
- **`CalibrationVariance` loader (PL-5):** build the loader from ledgered
  `calibration_run` events (produced by 4E) into a `CalibrationVariance` and feed
  it to `bench plan`; fall back to `AssumedVariance` (still flagged
  `assumption_based_mde`) only when no calibration run exists for the spec's
  corpus — so a calibrated experiment stops being `assumption_based`.
- **Bound `hypothesized_effect` (PL-12):** validate `(0, 1]` at the schema
  (`experiment.py:112`), rejecting negatives (always "underpowered") and values
  > 1 (always pass) at plan, before any event.
- **Reproduce-first:** a spec with `repetitions=3` over a 10-task corpus computes
  power at N=30, not 50 (today ignores both); omitting `hypothesized_effect`
  ledgers a `power_gate_skipped` flag (today silent); a spec with a ledgered
  calibration run locks with a `CalibrationVariance` (not `assumption_based_mde`);
  `hypothesized_effect=-0.1` and `=1.5` are refused at plan. Extends
  `tests/test_eval3_power.py`, `tests/test_eval3_lock.py`.

### 4H — end-to-end exit test through `bench` verbs only · Phase 4 exit · (integration)
The single ordered test that proves the connective tissue holds.
- A complete **fake-engine** experiment runs **plan → run → grade → judge →
  analyze → review → process** end-to-end **through `bench` verbs only** (no
  test-only kwargs), on a fake-engine fixture (no Docker required).
- Asserts **judge calibration** (kappa by class, escalation table) and **process
  reporting** (kappa / correlations / `style_only`) **appear in the rendered
  findings**; the reveal discloses the **real** arm identities from the recorded
  `response_map`; guess accuracy is a measured number; admission is reachable via
  `bench corpus admit` (emitting `task_admitted`); `bench run` refuses a
  non-`admitted` task; the power gate ran at the real N.
- New `tests/test_eval_e2e_phase4.py`; the property sweep
  (`test_eval3_property.py`) covers the new `review-build` entrypoint.

## Phase 4 exit criteria (all testable)

Restating the review's §5 Phase 4 exit against the slices:

1. **A complete fake-engine experiment runs plan → run → grade → judge → analyze
   → review → process end-to-end through `bench` verbs only** (no test-only
   kwargs), in a single ordered test (4H, depends on 4A–4G).
2. **Judge calibration (kappa by class, escalation) and process reporting (kappa /
   correlations / `style_only`) appear in the rendered findings** (4A/4C/4D).
3. **Reveal discloses the real arm identities** from the recorded `response_map`,
   and **guess accuracy is a measured number, not a structural 0.0** (4B).
4. **Admission is reachable via `bench corpus admit`**, emitting the Phase-3
   `task_admitted` event; **`bench run` refuses a non-`admitted` task** via
   `is_schedulable` (4E/4F).
5. **The power gate runs at the design's real N**; a **`CalibrationVariance`
   loader feeds `bench plan`** from ledgered calibration runs; `hypothesized_effect`
   is bounded (4G).
6. **Every new ledgered verb is registered in the one-event property sweep**
   (`review-build` added to `EXPECTED_ENTRYPOINTS`; the other verbs reuse existing
   entrypoints); **`make verify` green**; no import-linter regressions; the four
   contract additions each carry a decisions-ledger entry + migration note.
7. **The RV-9 `comparison_id` gate is reliable end-to-end** (judge threads a
   populated id, review build records the mapping keyed by it), and **CANT_JUDGE
   is excluded from kappa** rather than pooled (4A/4B).

## Working method (per CLAUDE.md)

- **Reproduce before fixing:** every slice ships a test that fails first (capability
  unreachable / wrong join / structural 0.0) and passes after (verb reachable /
  correct estimator / measured value). No fixes by inspection.
- **`make verify` green** before each commit; never weaken/skip a test to get
  green. Phase 4 is almost entirely non-Docker wiring; the exit runs on the fake
  engine without Docker.
- **Single responsibility / boundaries:** each wire lands in the subsystem that
  owns the concern; the CLI verbs call stage seams by name — the
  `harbor-confined-to-seam`, `grade-has-no-llm-clients`, and
  `ledger-writes-only-via-events` contracts stay green. New ledger writes route
  only through `events.py` typed constructors (the `review_packet_built`
  constructor is added there). Completing the `.importlinter` source lists (XC-5)
  stays Phase 6 — but keep the three live contracts green as Phase 4 adds many
  cross-module wires (CLI → judge/review/process/corpus).
- **Contract discipline:** the one new event type (`review_packet_built`) and the
  two additive lock changes (`power_gate_skipped` flag, `hypothesized_effect`
  bounds) are additive/pre-lock — no existing chain invalidated; each gets a
  decisions entry + migration note before its slice. The judge `comparison_id`
  population is field-population, not a schema change.
- **Determinism / fail loudly:** the response-order randomization and
  `comparison_id` derivation are seeded (`sub_seed`); no wall-clock or new network
  seams; refusals (`SelfApprovalError`, `not_schedulable`) say what was wrong and
  where.
- **Judgment calls flagged for cheap veto:** D-P4-1's `review_packet_built` event
  shape; D-P4-2's minimal cache-storage-plus-`is_schedulable` scope (vs a full
  cache-as-sole-source switch); D-P4-3's identity-inequality attestation (vs
  cryptographic); the `power_gate_skipped` flag placement (Decisions). Direction-
  setting choices beyond D-P4-1..4 get a check-in.

## Verification

- `uv run pytest -m "not docker" -q` green throughout (current post-Phase-3
  baseline **318 passed, 3 deselected**); Phase 4 adds reproduce-first tests per
  slice.
- `make verify` (full gate + the three import contracts) green before each commit.
- `uv run pytest --ac-report` recomputes AC coverage — Phase 4 wiring moves
  several currently-untested ACs (per-story reporting, escalation, admission,
  guess accuracy) into reach.
- Manual end-to-end sanity: `bench plan → run --engine fake → grade → judge →
  analyze → review build → review record → review reveal → process score` on a
  fixture; confirm the render shows judge calibration + process reporting, reveal
  discloses the real arm, and a non-admitted task is refused.

## Scope of this approval

Approving authorizes executing **Phase 4 (4A–4H)** as atomic commits with
`make verify` green, adding the one new event type (`review_packet_built`) and the
two additive lock changes (`power_gate_skipped` flag, `hypothesized_effect`
bounds) with decisions-ledger entries + migration notes, and recording the four
confirmed decisions (D-P4-1..4) in the owning `evalN.decisions.ndjson`. 4A/4C are
decision-light and can start once D-P4-4 (verb surfaces) is confirmed; 4B needs
D-P4-1; 4E needs D-P4-3; 4F needs D-P4-2; 4D/4G need only the verb confirmation.
Slices land in the order 4A → 4B → 4C, 4D, 4E → 4F, 4G, then 4H last. I'll report
at natural breakpoints and check in before Phase 5 (statistical correctness). No
PR unless you ask.
