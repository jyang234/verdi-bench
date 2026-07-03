# verdi-bench — Phase 5 plan: statistical correctness — make the findings describe the experiment that ran

**Date:** 2026-07-03 · **Follows:** Phase 4 (merged to `main`, PR #10) ·
**Source of record:** `verdi-bench-review-consolidated.md` §5 Phase 5 + §3.5 (Analyze),
§3.4 (Judge), §3.3 (Plan), §3.8 (Process), §6 (readiness gate).
Orientation: `verdi-bench-phase-5-handoff.md`.
**Branch:** `claude/verdi-bench-phase-5-plan-fohy0v` (branched from `main`, which
already contains Phase 1 + Phase 2 + Phase 3 + Phase 4 + the handoff).

## Context

Phase 1 made results *integrity* real; Phase 2 made the *execution path* real;
Phase 3 made every stage *fail closed*; Phase 4 made every stage *reachable* and
its outputs *appear in the render*. A complete fake-engine experiment now runs
plan → run → grade → judge → analyze → review → process end-to-end through `bench`
verbs only, and the render prints a judge-preference delta, a coverage-selected
CI, a per-class kappa, and an official fence.

**Phase 5 makes those numbers honest.** The pipeline now computes a
judge-preference primary metric, a CI-method-selection coverage, a calibration
fence, a degenerate kappa, and a set of provider guards — **and every one of them
is currently computed wrong, on the wrong population, or with a bypassable
gate.** This is the phase where the instrument stops lying with green numbers: a
finding that reports a statistic the data cannot support is worse than an absent
finding.

The §9 branch/merge question in the handoff is **already resolved**: Phase 4 was
merged (PR #10), this branch's HEAD *is* the merge commit (`4cb6002`), so Phase 5
builds on the merged base. Every seam Phase 5 consumes is present:
`judge_verdict.arm_map`, the `review_packet_built` `response_map`, the ledgered
`calibration_run` events + `manifest.calibration.runs`, the
`calibration_variance_from_runs` loader, the IPW `calibration_from_spec` seam, and
the power-N clustering note (a live comment in `plan/lock.py:90-97`). Nothing to
stack or reconcile.

### Re-verification against the current tree (not `01641cd`)

The consolidated review's line numbers are pre-Phase-1 and stale; Phases 1–4
shifted the tree substantially. I re-located every Phase 5 finding against the
working tree at branch HEAD. **All of them reproduce** except the sub-items Phase 4
already closed (noted inline). Concrete current-tree evidence:

**Analyze (`harness/analyze/`):**
- **AN-1** — `_judge_preference_values` (`report.py:173-183`) reads **every**
  `judge_verdict` with no comparison/arm-pair filter and maps any non-A/B winner
  (`TIE` **and** `CANT_JUDGE`) to `0.0` (line 182); `compute_findings` recomputes
  that same pooled list *inside* the `for other in spec.arms[1:]` loop
  (`report.py:418-421`), so **every** arm pair is fed the identical deltas. The
  bootstrap resamples verdict-level values (`report.py:458` → `stats.paired_bootstrap`),
  so **each verdict is its own cluster** (anti-conservative). The docstring even
  asserts "each comparison is its own cluster." The A↔arm attribution is assumed
  (`arm_a = spec.arms[0].name`, `report.py:415`), never read from the recorded
  `arm_map`. **Sharper than the review noted:** `judge/assemble.py:87` pairs *only*
  `arms[0]`↔`arms[1]`, so in a 3-arm design the `arms[0]`↔`arms[2]` finding is
  fabricated entirely from the `arms[0]`↔`arms[1]` verdicts. CONFIRMED.
- **AN-7** — `a_vals=[max(d,0)]`, `b_vals=[max(-d,0)]` (`report.py:420-421`) feed
  `effect_sizes` → `cliffs_delta` (`effect.py:20-36,51-64`). Cliff's delta over
  clipped half-waves is meaningless. CONFIRMED.
- **AN-2** — `_assert_official_calibration` (`report.py:632-645`) checks only
  `calibration.status == "full-run-validated"`; it receives `(findings,
  corpus_manifest)` — **not `spec`, not the ledger** — so it cannot cross-check
  `corpus_id`/`semver` or task shas. Reproduced by the shipped tests: `spec.corpus`
  is `public-mini@1.0.0` (`fixtures/builders.py:102`) while `_full_corpus()` is
  `terminal-bench@2.0.0` with one `TaskEntry(task0)` (`test_eval6_analyze.py:33-39`),
  yet `test_ac5_official_happy_path` (`:313-321`, tasks 0-4 run) renders official
  clean. CONFIRMED.
- **AN-4** — `_variance_params` (`report.py:227-233`) reads `p`/`rho`/`n_tasks`
  from the lock's `mde` with **silent defaults `0.5/0.3/50`**; nullsim's null is
  `simulate_correlated_pair_deltas(..., p, p, rho)` (`nullsim.py:60-64`) — a paired
  **Bernoulli** null regardless of whether the primary is holdout, cost, or
  wall-time. CONFIRMED (Phase 4 did put the real N into `mde["n_tasks"]`, but
  `p`/`rho` and the metric-of-the-null are still assumed).
- **AN-10** — coverage selection runs at `coverage_n_boot=500` (`report.py:389,395`)
  while the deployed interval uses `n_boot=10_000` (`report.py:390,458`). CONFIRMED.
- **AN-8** — `decides_positive` is written from the raw `observed` delta
  unconditionally (`report.py:469-472`); only the markdown gates on detection
  (`report.py:560,567-575`). `findings.json` says `decides_positive: true` for a
  null. CONFIRMED.
- **AN-9** — orphan grades (no matching trial) are silently `continue`-dropped
  (`report.py:156-157`). CONFIRMED.
- **AN-5** — `render_html` wraps each line `f"<p>{line}</p>"` with no escaping
  (`report.py:809`); a `<script>` in an arm name/reason lands verbatim. CONFIRMED.
- **AN-6** — `[computed]`/`[judgment]` claim tags exist **nowhere** in `harness/`
  (grep-empty). CONFIRMED — the §6 "claims tagged" row cannot flip.
- **AN-11** — minors confirmed: `CIMethod` set only by coverage selection
  (`report.py:396`), no config/CLI knob; ADVISORY is stamped
  (`adapters/base.py:78`) but never surfaced as a tier label in any render;
  `ClusterRobustTCI` drops zero-SE resamples (`ci.py:101-103`); BCa `z0` uses a
  strict `<` biased low on discrete deltas (`ci.py:120`); `fractional_score` is in
  the spec (`experiment.py:119`) but `_holdout_values` reads only `binary_score`
  (`report.py:158`).
- **AN-12** — `findings.json` includes `process` and hashes it into
  `findings_sha256` (`report.py:509` → `cli.py:80,90`); `_render_official_md`
  (`report.py:648-671`) omits it. CONFIRMED (D-3 = keep-labeled).

**Judge (`harness/judge/`, `harness/schema/`):**
- **JD-4** — the degenerate-kappa guard lives in **two** places:
  `cohens_kappa` (`judge/calibrate.py:41-42`, `1-pe < 1e-9 → 1.0`) and
  `weighted_kappa` (`review/kappa.py:78-79`, `den < 1e-12 → 1.0`). All-A/all-A ×20
  returns `kappa=1.0, sufficient=True`. CONFIRMED (D-5 pending confirm).
- **JD-12** — `Verdict.confidence` is `float = 0.0` (`judge/schema.py:73`); the
  client hardcodes `0.5/0.8` (`client.py:193`) and `0.0` for CANT_JUDGE
  (`client.py:123`), discarding the parsed `RawVerdict.confidence` (`client.py:45`).
  The spec event schema says `"confidence": "low|medium|high"` (`eval2.spec.md:226`).
  No downstream reader consumes `confidence` (grep: writes only in `harness/`).
  CONFIRMED (D-4 = code-to-enum).
- **JD-6** — `_VERSIONED` includes `\d+\.\d+` (`judge_config.py:28`), so
  `google/gemini-1.5-pro` and `openai/gpt-4.1` false-pass `is_alias_model_id`.
  CONFIRMED.
- **JD-7** — `_vendor(model_id) = model_id.split("/",1)[0]` (`analyze/confounds.py:21-22`)
  returns the whole string for a prefix-less id, so overlap is wrong for a bare
  `claude-3-5-…`; `Arm.model` is an unvalidated bare `str` (`experiment.py:34`).
  CONFIRMED.
- **JD-8** — `Packet.render` interpolates raw diff/holdout under a one-line system
  prompt with no fencing (`packet.py:49-68`). CONFIRMED.
- **JD-13** — connect-phase timeout classification is **fixed**
  (`providers/_http.py:18-28` `_classify_urlerror` → `ProviderTimeout`). **Left to
  Phase 5:** `packet_sha256` covers only the order-independent content, not the
  rendered body/system prompt (`packet.py:79-93`); response-label assignment is
  deterministic AB/BA (`client.py:152`).

**Plan / power (`harness/plan/`):**
- **Power-N clustering** — `real_n = spec.repetitions * len(task_dicts)`
  (`lock.py:98`) is fed to `mde_check(n=real_n)`, and the sim draws `n`
  **independent** paired Bernoulli obs (`power.py:98-104,134`). A live comment
  (`lock.py:90-97`) marks the cluster-by-task fix as the Phase-5 seam: with
  `repetitions > 1` the reps are correlated within a task, so power is optimistic.
  CONFIRMED. (PL-1 real-N and PL-12 `hypothesized_effect` bounds `(0,1]` are
  Phase-4-done — `experiment.py:118`.)

**Process (`harness/process/`):**
- **Weighted-kappa degenerate rider** — `process_kappa_by_dimension`
  (`process/calibrate.py:41-68`) calls `estimate_kappa`→`weighted_kappa`, so it
  shares the JD-4 guard; D-5 must apply here too or the two kappa families diverge.
  CONFIRMED.

**Baseline:** `uv run pytest -m "not docker" -q` → **360 passed, 3 deselected**;
`make verify` green; 3 import-linter contracts kept (`harbor-confined-to-seam`,
`grade-has-no-llm-clients`, `ledger-writes-only-via-events`). Phase 5 adds no
runtime dependency and no Docker.

## Decisions

Phase 5 is a correctness phase; several direction-setting choices need explicit
human resolution **before** the owning slice (per CLAUDE.md "the human decides"),
each recorded as a `resolved` event in the owning `evalN.decisions.ndjson` before
its slice lands, mirroring Phase 4's `D-P4-*` convention.

### Carried forward (resolved, constrain Phase 5)

- **REVIEW-D-3 (AN-12) — keep-labeled.** Retain the process section in official
  `findings.json` (hashed into `findings_sha256`, contract unbroken) and make the
  official markdown show it under an explicit advisory/process label per
  EVAL-9 AC-6. Do **not** strip; do **not** break the hash. *(Owned by 5F.)*
- **REVIEW-D-4 (JD-12) — code-to-enum.** Migrate `Verdict.confidence` to
  `low|medium|high`. This is the load-bearing Phase-5 contract decision — a *type*
  change of an existing field on a hash-chained event (not additive), so it carries
  a migration/compatibility story (§ Contract additions). *(Owned by 5I.)*

### Confirmed at planning start (resolved by jyang, 2026-07-03)

- **REVIEW-D-5 (JD-4) — `undefined-insufficient` (confirmed).** Return kappa
  undefined / `sufficient=False` at zero chance-corrected information, in **both**
  `cohens_kappa` and `weighted_kappa` (judge **and** process share the fix).
  Trade-off: `1.0/sufficient` reads as "perfect agreement" when there is in fact
  *no* chance-corrected signal (all raters picked one category) — the instrument
  would over-credit the judge exactly when the data cannot support any kappa;
  `undefined-insufficient` refuses to certify agreement it cannot measure, at the
  cost of a slightly more conservative escalation table. **Confirmed
  `undefined-insufficient` (jyang, 2026-07-03).** Recorded in `review.decisions.ndjson`.

### New Phase-5 decisions (recommendation + trade-offs)

**Status:** D-P5-1, D-P5-2, D-P5-4 **confirmed by jyang (2026-07-03)** and recorded
in the owning `evalN.decisions.ndjson`; D-P5-3 is taken as recommended, vetoable at
slice 5E.

- **D-P5-1 — the judge-preference effect measure (AN-1/AN-7).** What replaces the
  fabricated clipped-series Cliff's delta? **Recommend** a proper paired
  preference-effect: the **per-task win-rate difference** (mean, over tasks, of the
  per-task signed preference in `{+1 A, −1 B}`) with a **task-clustered bootstrap
  CI**, computed **only** over comparisons carrying a real A/B verdict
  (CANT_JUDGE/TIE excluded, never imputed). Trade-off: this is the widest-surface
  Phase-5 choice — it *defines* what a "judge-preference primary metric" is; an
  alternative (a rank-biserial / Cliff's-delta over the *un-clipped* signed series)
  is defensible but harder to interpret against the decision rule
  `delta_judge_preference <op> <thr>`, which is naturally a win-rate difference.
  **Confirmed: per-task win-rate difference (jyang, 2026-07-03).** *(Owned by 5B.)*
- **D-P5-2 — the AN-2 fence binding shape.** Bind the official fence to which
  identity? **Recommend all three:** (a) manifest `corpus_id`/`semver` vs
  `spec.corpus.id`/`version`; (b) manifest `task_shas()` vs the shas actually run
  (the lock event's `task_commitment`, which pins `sha256(per-task shas)`); and (c)
  the **ledgered** `calibration_run` status for that corpus, not the hand-editable
  `manifest.calibration.status` (CO-4 compounds AN-2 — a JSON edit currently passes
  the fence). Trade-off: a fence that ignores any one of the three is a
  hand-editable bypass; binding all three is stricter but each check is cheap and
  the failure message says exactly which identity mismatched. **Confirmed: all
  three checks (jyang, 2026-07-03).** *(Owned by 5C.)*
- **D-P5-3 — the claim-tag mechanism (AN-6).** How do `[computed]`/`[judgment]`
  tags attach? **Recommend** a structured `claim_tag` field on each finding/claim
  in `findings.json`, with the renders deriving the visible marker — so the tag is
  machine-checkable and `test_ac6_finding_provenance` can enforce it, rather than a
  render-only string a test can only regex. Trade-off: a schema field is the
  durable, auditable form (it survives into `findings_sha256`); inline render
  markers are lighter but unverifiable and easy to desync between markdown and HTML.
  *(Owned by 5E.)*
- **D-P5-4 — the shared variance/clustering model (AN-1 + AN-4 + power-N).** One
  clustering model for **both** the pre-registration power sim and the realized-data
  analysis, so the two cannot disagree. **Recommend:** cluster by **task**, with
  `repetitions` reps correlated *within* a task (the design's real structure), used
  by the power sim (`plan/power.py`), the null-sim coverage selection
  (`analyze/nullsim.py`), and the analysis bootstrap (`analyze/stats.py` /
  judge-preference). Trade-off: this is a genuine statistical-model decision — the
  **recorded MDEs change** (honestly larger when `repetitions > 1`, because
  correlated reps carry less information than independent obs), so a design that
  *locked* as adequate under the old independent-obs model may re-lock as
  underpowered. That is the correct behavior (it was optimistic before), but it is a
  contract-visible change carrying a migration note. **Confirmed: cluster by task
  (jyang, 2026-07-03).** *(Owned by 5A; consumed by 5B.)*

Two smaller **in-slice** forks (recommendation stated, settled within the owning
slice, not gating the whole phase):
- **JD-7 vendor handling (5G):** *recommend* requiring a `<provider>/<id>` prefix
  on `Arm.model` at the schema (a bare id has no vendor to compare), with `_vendor`
  raising loudly on a prefix-less id rather than silently returning the whole
  string — over the leaner "make `_vendor` best-effort parse a bare id," which
  re-introduces guessing. Fixtures already use prefixed ids.
- **The AN-4 metric-appropriate null (5A):** *recommend* a paired-**binary** null
  for `holdout_pass_rate` and `judge_preference` (bounded 0/1 outcomes) and a
  paired-**continuous** null (resampled from the realized per-arm telemetry, mean
  effect 0) for `cost_per_task`/`wall_time` — with the selected null model recorded
  in the `ci_selection` block so it is never silently a binary null under a
  continuous metric.

### Contract additions (recorded before the owning slice lands)

Per CLAUDE.md "public seams are contracts" and handoff §6:

| Change | Kind | Owner | Slice | Migration note |
|---|---|---|---|---|
| Task-clustered variance model (recomputed MDEs) | statistical-model change; lock `mde` **values** differ | EVAL-3 | 5A | no schema change; recorded MDEs honestly larger when `repetitions>1`; greenfield (no locked specs), so a design note; decisions entry D-P5-4 |
| `null_model` disclosed in the `ci_selection` block | additive **field** on `findings.json` | EVAL-6 | 5A | additive; states which null (binary/continuous) coverage selection used |
| `claim_tag` (`computed`/`judgment`) per finding/claim | additive **field** on `findings.json` | EVAL-6 | 5E | additive; covered by `findings_sha256` for new renders; old findings lack it |
| orphan-grade flag/count in `integrity` (or a new `ledger_consistency` block) | additive **field** on `findings.json` | EVAL-6 | 5D | additive; a clean ledger reports zero |
| `decides_positive` gated on detection in `findings.json` | **value** change (null results now `false`/`null`) | EVAL-6 | 5D | consumers reading `true` for a null now read the gated value; matches the render |
| official markdown shows the labeled process section | **render** change only | EVAL-9 | 5F | `findings.json` unchanged; `findings_sha256` unchanged (D-3) |
| `_VERSIONED` rejects bare dotted versions (`1.5`, `4.1`) | schema **validation** (pre-lock) | EVAL-2/3 | 5G | rejects at plan; audit fixtures for bare-dotted judge ids |
| `Arm.model` requires `<provider>/<id>` | schema **validation** (pre-lock) | EVAL-3 | 5G | rejects at spec load; fixtures already prefixed |
| `packet_sha256` covers the rendered body + system prompt | **provenance-semantics** change on a hash-chained field | EVAL-2 | 5G | the value now covers framing; old verdicts covered less; not a new field, so no schema break; decisions entry |
| degenerate kappa → undefined/insufficient | **behavior** change in `cohens_kappa`/`weighted_kappa` | EVAL-2/7/9 | 5H | leaf kappa returns `None` on zero chance-corrected info; `ClassCalibration`/`DimensionCalibration.kappa` already `Optional`; findings kappa may be `null` with `sufficient=false` |
| `Verdict.confidence` `float` → `low\|medium\|high` **enum** | **type** change on a hash-chained event field (not additive) | EVAL-2 | 5I | versioned reader accepts old float and new enum; documented cut-over; greenfield → design note; `confidence` is not part of `packet_sha256`, so no packet-hash impact |

The two schema-validation additions and the two `findings.json` field additions are
the **additive/guarded** case (per CLAUDE.md); each carries a migration note and a
gate/genesis test. The `packet_sha256` semantics change and the **confidence enum
type change** are the load-bearing ones: the latter is the only *type* change of an
existing hash-chained field, so it follows the Phase-4 pattern (decisions entry +
migration note + a versioned reader) exactly. No new ledger **event type** is added
in Phase 5 — every change is to a finding artifact, a render, a schema validator, or
an existing verdict field.

## Phasing within Phase 5

Ten slices. The analyze slices interlock (they share `compute_findings` and the
renders), so the **shared clustering model lands first** and the judge-preference
correctness builds on it; the fence, artifact-honesty, tags, and process labeling
then extend the same `compute_findings`/render surface; the judge guards, kappa,
and confidence migration are independent judge-subsystem changes that interleave;
**the exit test lands last.** Each slice is one logical change (1–3 atomic
commits), ships a **reproduce-first** test proving the pathology today → corrected
after, records any decision/contract entry before it lands, and `make verify` is
green before every commit. Line numbers are the current tree.

### 5A — Shared task-clustered variance model + coverage at real N · AN-4, AN-10, power-N · P0/P1 (needs D-P5-4)
One clustering model, used by the power sim, the null-sim, and the analysis.
- **Cluster by task in the power sim (power-N):** model the design as
  `corpus_size` task clusters each carrying `repetitions` correlated reps — a
  two-level draw (task-difficulty latent shared across a task's reps, plus a
  within-rep component) — instead of `n = repetitions × corpus_size` independent
  paired obs (`power.py:98-104,134`). The public `simulate_correlated_pair_deltas`
  gains the cluster structure so nullsim inherits it unchanged
  (`nullsim.py:23,60-64`) — one definition, no desync (the module docstring already
  promises this).
- **Coverage at the realized N/rates (AN-4):** drive `_variance_params`
  (`report.py:227-233`) from the **realized** experiment (per-arm rates + real N /
  cluster count) or the ledgered `calibration_run` params, **never** the silent
  `0.5/0.3/50` defaults — a missing parameter fails loudly, it does not default.
- **Metric-appropriate null (AN-4):** select the null by primary metric — paired
  binary for `holdout_pass_rate`/`judge_preference`, paired continuous (resampled
  realized telemetry, zero mean effect) for `cost_per_task`/`wall_time` — and
  record the chosen `null_model` in the `ci_selection` block (no silent binary null
  under a continuous metric).
- **Matched `n_boot` (AN-10):** coverage selection uses the *same* `n_boot` as the
  deployed interval (`10_000`), so the method is chosen at the resample count it is
  applied at (drop the `coverage_n_boot=500` split, or raise it to match).
- **Reproduce-first:** a `repetitions=3` × 10-task design currently computes power
  over 30 independent obs → a smaller (optimistic) MDE; after, 10 task clusters ×
  3 correlated reps → a larger (honest) MDE, and a design that locked as adequate
  can now correctly refuse as underpowered. A `cost_per_task` primary currently
  selects its CI method under a paired-binary null (reproduce the AN-4 coverage
  gap); after, under a continuous null, with `null_model` in `ci_selection`.
  Coverage selection currently runs at `n_boot=500` while deploying `10_000`;
  after, matched. Extends `tests/test_eval3_power.py`, `tests/test_eval6_analyze.py`.

### 5B — Judge-preference correctness · AN-1, AN-7 · P0 (needs D-P5-1, builds on 5A)
Filter, attribute, exclude, and cluster the judge-preference primary metric.
- **Filter by comparison/arm pair via `arm_map` (AN-1):** for each
  `(arm_a, arm_b)` comparison in `compute_findings` (`report.py:414-421`), select
  only verdicts whose recorded `arm_map` maps `{A,B}` onto exactly `{arm_a, arm_b}`,
  and **attribute** the winner to the physical arm via `arm_map` (not the assumed
  `A = arms[0]`). In the current tree `judge/assemble.py:87` only pairs
  `arms[0]`↔`arms[1]`, so a 3-arm design's `arms[0]`↔`arms[2]` finding correctly
  becomes *no judge-preference data for this pair* instead of the pooled
  `arms[0]`↔`arms[1]` verdicts.
- **Exclude, never impute (AN-1):** drop `TIE` and `CANT_JUDGE` from the preference
  series (they are non-answers, not zeros); `n` reflects real A/B verdicts only.
- **Task-cluster the bootstrap (AN-1, shared model from 5A):** group the per-verdict
  preferences by `task_id` and resample **tasks** (clusters), reducing reps within a
  task first — the same cluster-by-task unit the holdout/telemetry path already uses
  and the power sim now uses (5A).
- **Replace the fabricated effect size (AN-7):** drop the clipped-series Cliff's
  delta; report the D-P5-1 preference-effect (per-task win-rate difference) with the
  task-clustered CI.
- **Reproduce-first:** seed `judge_verdict`s across two arm-maps (`{A:arm0,B:arm1}`
  and `{A:arm0,B:arm2}`) with opposite true signs; today `compute_findings` reports
  the *same* pooled `mean_delta`/`n` for both `arms[1]` and `arms[2]` comparisons
  (reproduce the review's "0.0, n=11 for both"); after, each comparison reports only
  its arm-pair's verdicts, correctly signed via `arm_map`. A CANT_JUDGE/TIE-heavy
  fixture today imputes zeros (delta pulled toward 0, n inflated); after, they are
  excluded (n drops, delta honest). A single task with many reps today yields an
  anti-conservative CI (each rep its own cluster); after, one task cluster.
  Extends `tests/test_eval6_analyze.py`.

### 5C — Official fence bound to corpus identity · AN-2 · P0 (needs D-P5-2)
The official fence must refuse a mismatched corpus and a hand-edited status.
- **Bind to identity (AN-2, D-P5-2):** thread `spec` (and the lock event's
  `task_commitment`) into `_assert_official_calibration` (`report.py:632-645`); refuse
  official render unless (a) `manifest.corpus_id`/`semver` == `spec.corpus.id`/`version`,
  (b) `manifest.task_shas()` reconcile with the ledgered `task_commitment`, and (c)
  the corpus is `full-run-validated` **per the ledgered `calibration_run` events**,
  not the mutable `manifest.calibration.status`. Each mismatch raises a distinct,
  enumerated `cant_analyze` reason (extend `CantAnalyzeReason`) so the refusal says
  which identity failed and where.
- **Fix the shipped mismatched-manifest tests:** rewrite `_full_corpus()` and the
  official-render tests (`test_eval6_analyze.py:33-39,313-321,347,383-390`) so the
  manifest matches the spec's `public-mini@1.0.0` and the run's task shas — with an
  explicit sign-off note that these tests baked in the bug (per CLAUDE.md "changing a
  genuinely wrong test requires saying so").
- **Reproduce-first:** the official render currently accepts `terminal-bench@2.0.0`
  against a `public-mini@1.0.0` spec (reproduce), and accepts a hand-edited
  `full-run-validated` manifest with no ledgered calibration run; after, both are
  refused with the specific reason, and a matching manifest renders. Extends
  `tests/test_eval6_analyze.py`.

### 5D — Artifact-honest decisions + orphan flagging · AN-8, AN-9 · P2
Make `findings.json` say what the render says, and never shrink `n` in silence.
- **Gate `decides_positive` on detection in the artifact (AN-8):** in
  `compute_findings` (`report.py:469-472`) record `decides_positive` only when the CI
  excludes zero (the same `BootstrapResult.excludes_zero()` / detection the render
  uses), so a consumer of `findings.json` reads `false`/`null` for a null result.
- **Flag orphan grades loudly (AN-9):** count grade events with no matching trial
  record (`report.py:156-157`) and surface the count/ids as an additive
  `ledger_consistency` flag on the findings (a nonzero count rides the render), rather
  than a silent `continue`.
- **Reproduce-first:** a null-result comparison currently has `decides_positive: true`
  in `findings.json` while the markdown says "no effect detected"; after, the artifact
  matches. A ledger with an orphan grade currently shrinks `n` with no signal; after,
  the findings carry an orphan flag/count. Extends `tests/test_eval6_analyze.py`.

### 5E — Claim tags + HTML escaping + ADVISORY surfacing · AN-6, AN-5, AN-11 · P1/P2 (needs D-P5-3)
Make claims machine-taggable, renders injection-safe, and the tier visible.
- **Claim tags (AN-6, D-P5-3):** add a structured `claim_tag` (`computed` |
  `judgment`) to each finding/claim in the `FindingsDocument` schema; the markdown
  and HTML renders derive the visible `[computed]`/`[judgment]` marker from it; move
  `test_ac6_finding_provenance` to **own** the tags (assert every claim carries one).
  This flips the §6 "claims tagged" invariant row.
- **HTML escaping (AN-5):** escape in `render_html` (`report.py:786-817`) using the
  in-repo review-packet escaping pattern (`review/packet.py`); a `<script>` in an arm
  name/reason renders inert.
- **ADVISORY surfacing (AN-11):** surface the ADVISORY grade tier as a label in the
  renders (local grades are ADVISORY, `adapters/base.py:78`) so the §6 "Local =
  ADVISORY" row is honestly reflected.
- **AN-11 remainder — judgment call:** the `ClusterRobustTCI` zero-SE drop
  (`ci.py:101-103`) and the BCa `z0` strict-`<` discreteness bias (`ci.py:120`) are
  statistical-correctness minors that ride here; `CIMethod` config-flippability and
  the unread `fractional_score` are hygiene → **defer to Phase 6** (flagged for veto).
- **Reproduce-first:** findings today carry no claim tags (grep-empty) and
  `test_ac6_finding_provenance` tests only provenance fields; after, every claim is
  tagged and the test enforces it. `render_html` today emits `<script>` verbatim
  (reproduce); after, escaped. The ADVISORY tier is absent from renders today; after,
  surfaced. Extends `tests/test_eval6_analyze.py`.

### 5F — Official process labeling · AN-12 / REVIEW-D-3 · P2
Show the process section in the official markdown, labeled; keep the hash.
- **Labeled official process section (D-3):** `_render_official_md`
  (`report.py:648-671`) renders the process section under an explicit
  EXPLORATORY/advisory label carrying the unblinded disclosure (EVAL-9 AC-6:
  "official renders including process scores show both the exploratory label and the
  disclosure"); `findings.json` and `findings_sha256` are unchanged (the section was
  always in the JSON).
- **Reproduce-first:** an official render over a fixture with process scores today
  omits the process section from the markdown while `findings.json` hashes it; after,
  the official markdown shows it labeled + disclosed, and `findings_sha256` is
  byte-identical to before. Extends `tests/test_eval6_analyze.py`,
  `tests/test_eval9_process.py`.

### 5G — Judge guards · JD-6, JD-7, JD-8, JD-13(remainder) · P1/P2 (JD-7 in-slice fork)
Close the alias, vendor, injection, and provenance-framing holes.
- **Reject dotted-version aliases (JD-6):** drop the `\d+\.\d+` alternative from
  `_VERSIONED` (`judge_config.py:22-31`) so a versioned id requires a date / build
  stamp / `-NNN` suffix; `google/gemini-1.5-pro` and `openai/gpt-4.1` are rejected,
  while `google/gemini-1.5-pro-002` (the fixture default) still passes via `-002`.
- **Vendor for prefix-less ids (JD-7, recommend schema-require-prefix):** require a
  `<provider>/<id>` prefix on `Arm.model` at the schema (`experiment.py:30-35`) and
  make `_vendor` (`analyze/confounds.py:21-22`) raise on a prefix-less id, so
  vendor-overlap is well-defined (fixtures already prefix their ids).
- **Fence the judge packet (JD-8):** delimit the untrusted diff/holdout content in
  `Packet.render` (`packet.py:49-68`) — explicit fences + a strengthened system
  instruction that fenced content is data, never instructions — so a content-keyed
  injection can no longer pose as a legitimate both-orders win.
- **Extend `packet_sha256` (JD-13 remainder):** cover the rendered message body +
  system prompt in `packet_sha256` (`packet.py:79-93`) so a framing change is
  provenance-detectable (a versioned provenance-semantics change).
- **Reproduce-first:** `google/gemini-1.5-pro` passes plan today (reproduce the
  false-pass); after, rejected as an alias. A prefix-less `claude-3-5-sonnet` arm
  yields `overlap=False` today; after, rejected/handled. An injected diff maps to a
  both-orders win at confidence today; after, fenced. A framing change leaves
  `packet_sha256` unchanged today; after, it changes. Extends
  `tests/test_eval2_client.py`, `tests/test_eval2_plan.py`,
  `tests/test_eval6_analyze.py`.

### 5H — Kappa correctness · JD-4 / REVIEW-D-5 + process rider · P2 (needs D-5 confirm)
Degenerate kappa is undefined/insufficient, in both kappa families.
- **`undefined-insufficient` (D-5):** at zero chance-corrected information
  (`1-pe`/`den` ≈ 0), `cohens_kappa` (`judge/calibrate.py:41-42`) and
  `weighted_kappa` (`review/kappa.py:78-79`) return **undefined** (propagate `None`),
  and their callers set `sufficient=False` even when `n ≥ min_human_verdicts`. The
  `ClassCalibration`/`DimensionCalibration.kappa` fields are already `Optional`, so
  the findings shape absorbs it (kappa `null`, `sufficient=false`).
- **Process rider:** the same fix flows through `estimate_kappa` → `weighted_kappa`
  into `process_kappa_by_dimension` (`process/calibrate.py:41-68`), so the outcome and
  process kappa families agree on the degenerate case.
- **Reproduce-first:** all-A/all-A ×20 returns `kappa=1.0, sufficient=True` today
  (reproduce, both `cohens_kappa` and the IPW/`weighted_kappa` path); after,
  undefined/insufficient. A degenerate per-dimension quadratic-weighted case behaves
  the same. Extends `tests/test_eval2_calibrate.py` (or equivalent),
  `tests/test_eval7_review.py`, `tests/test_eval9_process.py`.

### 5I — Confidence enum migration · JD-12 / REVIEW-D-4 · P2 (resolved; contract-type change)
Migrate the verdict confidence to the pre-registered enum; stop discarding the parse.
- **`low|medium|high` (D-4):** change `Verdict.confidence` from `float`
  (`judge/schema.py:73`) to a `low|medium|high` enum; the client derives it from the
  judge's parsed confidence combined with order-consistency (recommend:
  order-inconsistent → `low`; otherwise bucket the model's parsed
  `RawVerdict.confidence`), replacing the hardcoded `0.5/0.8` (`client.py:193`) and
  the discarded parse.
- **Migration story:** a versioned reader accepts both an old float and a new enum
  (documented cut-over); since verdi-bench has **no production ledgers**, the
  compatibility surface is a design note, not a live migration. `confidence` is not
  part of `packet_sha256` (content-only), so no packet-hash impact; the
  verdict-schema genesis/validation tests are checked (guarded field, but a *type*
  change — the load-bearing contract decision, recorded as `EVAL-2-D-P5-*` before the
  slice).
- **Reproduce-first:** a judge verdict today records `confidence: 0.8` (a discarded
  hardcode); after, records the enum bucket derived from the real signal; a legacy
  float verdict reads under the versioned reader. Extends `tests/test_eval2_client.py`,
  `tests/test_eval2_schema.py` (or equivalent).

### 5J — Statistical-correctness exit test · Phase 5 exit · (integration)
The single ordered proof that the reproduced pathologies stay fixed.
- Asserts, on fake-engine fixtures (no Docker), that each reproduced pathology has a
  **failing-then-fixed** test: **3-arm judge-preference pooling** (AN-1),
  **wrong-corpus official fence** (AN-2), **fabricated-N coverage** (AN-4),
  **alias false-passes** (JD-6), **script/prompt injection** (JD-8/AN-5) — plus
  **AN-1's CANT_JUDGE/TIE-not-imputed** and the **task-clustered CI** agreeing between
  the power sim and the analysis.
- New `tests/test_eval_phase5_correctness.py` (or folded into the per-story files);
  the property sweep is unaffected (Phase 5 adds no ledger entrypoint).

## Phase 5 exit criteria (all testable)

Restating the review's §5 Phase 5 exit against the slices:

1. **The reproduced pathologies each have a failing-then-fixed test:** 3-arm
   judge-preference pooling (AN-1, 5B), wrong-corpus official fence (AN-2, 5C),
   fabricated-N coverage (AN-4, 5A), alias false-passes (JD-6, 5G),
   script/prompt injection (JD-8/AN-5, 5G/5E) (5J gathers them).
2. **Judge-preference deltas are filtered by comparison/arm pair, exclude
   CANT_JUDGE/TIE (never impute), and are task-clustered; the effect size is a valid
   preference measure**, not clipped-series Cliff's delta (5B).
3. **The official fence refuses a mismatched corpus** (`corpus_id`/`semver`/task-shas)
   **and a hand-edited status**, and the shipped mismatched-manifest tests are
   corrected (5C).
4. **Coverage selection runs at the realized N with a metric-appropriate null and
   matched `n_boot`; the power sim and the analysis share one task-clustered variance
   model** (5A).
5. **`[computed]`/`[judgment]` claim tags exist in the findings schema + renders and
   `test_ac6_finding_provenance` enforces them; `render_html` escapes; the ADVISORY
   tier is surfaced** — the §6 "claims tagged" and "Local = ADVISORY" rows can flip
   (5E).
6. **`decides_positive` is gated on detection in `findings.json`; orphan grades are
   flagged loudly** (5D).
7. **Degenerate kappa returns undefined/insufficient** in both kappa families (5H);
   **`Verdict.confidence` is the `low|medium|high` enum** with a recorded migration
   note (5I); **the process section is labeled in the official markdown**,
   `findings_sha256` unchanged (5F).
8. **`make verify` green; no import-linter regressions;** every contract change
   (recomputed MDEs, the `findings.json` field additions, the two schema validators,
   the `packet_sha256` semantics change, the confidence enum type change) carries a
   decisions-ledger entry + migration note (§ Contract additions).

## Working method (per CLAUDE.md)

- **Reproduce before fixing:** every slice ships a test that fails first (wrong
  population / bypassable fence / fabricated statistic / degenerate kappa / alias
  false-pass) and passes after. No fixes by inspection.
- **`make verify` green** before each commit; never weaken/skip a test to get green.
  Phase 5 is pure non-Docker statistical/rendering/schema work. The shipped
  mismatched-manifest tests corrected in 5C are the one case of changing an existing
  test — done explicitly, with sign-off, because they encode the AN-2 bug (per
  CLAUDE.md "changing a genuinely wrong test requires saying so").
- **Single responsibility / boundaries:** each fix lands in the subsystem that owns
  the concern — the shared variance model in `plan/power.py` + `analyze/nullsim.py`,
  the judge-preference statistic in `analyze/report.py`, the kappa policy in the
  `review/kappa.py` + `judge/calibrate.py` seams, the confidence enum in
  `judge/schema.py`. The `harbor-confined-to-seam`, `grade-has-no-llm-clients`, and
  `ledger-writes-only-via-events` contracts stay green (deterministic grading imports
  no LLM client — the kappa/effect changes touch no client). Completing the
  `.importlinter` source lists (XC-5) stays Phase 6.
- **Determinism / fail loudly:** the clustered sims and bootstraps stay seeded via
  `sub_seed` (no wall-clock, no unseeded randomness, no new network seam); `AN-4`
  removes the silent `0.5/0.3/50` fallbacks so a missing variance parameter is a loud
  refusal, not a default; the fence refusals name the mismatched identity.
- **Contract discipline:** the confidence enum (a *type* change on a hash-chained
  field) and the `packet_sha256` semantics change each get a decisions entry +
  migration note before their slice; the `findings.json` field additions and schema
  validators are the guarded/additive case with a gate test. The recomputed MDEs
  (D-P5-4) are a model change with a migration note, not a schema change.
- **Judgment calls flagged for cheap veto:** the D-P5-1 preference-effect measure;
  the D-P5-2 three-way fence binding; the D-P5-3 structured claim-tag field; the
  D-P5-4 cluster-by-task model (and its honestly-larger MDEs); the JD-7
  schema-require-prefix fork; the AN-4 continuous-null shape; the AN-11 split
  (zero-SE/BCa-z0 ride Phase 5, `CIMethod`-config/`fractional_score` defer to
  Phase 6); the confidence-derivation mapping in 5I. All are stated with a
  recommendation; anything new that arises mid-slice gets a check-in.

## Verification

- `uv run pytest -m "not docker" -q` green throughout (post-Phase-4 baseline
  **360 passed, 3 deselected**); Phase 5 adds reproduce-first tests per slice.
- `make verify` (full gate + the three import contracts) green before each commit.
- `uv run pytest --ac-report` recomputes AC coverage — Phase 5 moves the
  AN-6/AN-2/AN-1 ACs from "reachable" to "correct" and flips the §6 "claims tagged"
  and "Local = ADVISORY" rows.
- Manual sanity: `bench plan → run --engine fake → grade → judge → analyze
  --official` on a fixture whose corpus matches the manifest; confirm the official
  render carries claim tags, refuses a mismatched corpus, shows the labeled process
  section, and reports a task-clustered judge-preference CI over real A/B verdicts
  only.

## Scope of this approval

Approving authorizes executing **Phase 5 (5A–5J)** as atomic commits with
`make verify` green, making the statistical/rendering/schema corrections above and
recording each contract change — the recomputed MDEs (D-P5-4), the `findings.json`
field additions (`null_model`, `claim_tag`, orphan flag), the two schema validators
(alias regex, `Arm.model` prefix), the `packet_sha256` semantics change, and the
**confidence enum type change** (D-4) — with a decisions-ledger entry + migration
note before its owning slice. No new runtime dependency, no new ledger event type,
no Docker. Slices land in the order 5A → 5B → 5C → 5D → 5E → 5F, then 5G, 5H, 5I
(independent judge/kappa work, interleavable), then **5J last**.

**Decisions — status.** Confirmed by jyang (2026-07-03) and recorded in the owning
ledgers: REVIEW-D-5 (degenerate kappa → `undefined-insufficient`, 5H), D-P5-1
(judge-preference per-task win-rate difference, 5B), D-P5-2 (three-way fence binding,
5C), D-P5-4 (shared cluster-by-task model, 5A). Already resolved: REVIEW-D-3 (AN-12
keep-labeled), REVIEW-D-4 (confidence enum). Recommended, vetoable at its slice:
D-P5-3 (structured claim-tag field, 5E). I'll report at natural breakpoints and check
in before Phase 6 (enforcement infrastructure). No PR unless you ask.
