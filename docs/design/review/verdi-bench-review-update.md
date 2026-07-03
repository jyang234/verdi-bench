# verdi-bench audit update — EVAL-6/7/8/9 (now visible on main)

> **Superseded (2026-07-03)** by `verdi-bench-review-consolidated.md`, which re-verified every finding here against the code. Retained as a historical record.

**Date:** 2026-07-02 · **Scope:** the four stories that were previously on the wrong branch (commits `46c84d9`, `4df1b7a`, `ddf7901`, `d103435` + docs/test commits), ~5,000 new lines, plus reassessment of the original audit. **Method:** suite verification (210 passed, 3 contracts kept), four independent deep-review passes (one per story), all critical/high findings re-verified by hand.

## How this changes the original audit

**Resolved or reframed from the first report:**
- *"EVAL-8 slice A missing"* — the corpus tooling now exists (import, stratify, mining, curation, boundary checks). However the substantive consequence stands unchanged: **`CalibrationVariance` still has no loader** (`plan/power.py:48-59` is still the TODO holder, grep confirms zero production constructors), so every lock still runs on `AssumedVariance` defaults and is flagged `assumption_based_mde`. Slice A landed; the wiring the plan wanted it for did not.
- *"Missing §7.1 event types"* — `findings_rendered`, `reveal`, `curation_approval`, `process_score` are now registered constructors in `events.py`, all through the single `emit` funnel. Conforms.
- *"`review/scrub.blind_scrub` missing"* — now exists and is a genuine thin wrapper over `blind/core.py`. The single-blinding-codepath invariant holds across all three consumers (judge, review, process).
- *"§6 rows pointing at review"* — `test_ac5_boundary_enforced` and `test_ac6_finding_provenance` now exist **but both assert something weaker than the invariant** (see B1 and A6 below), so those §6 rows should not yet be flipped from `enforced_by: review`.

**Unchanged — every finding in the original report still stands.** The new commits touched only `cli.py` (verb registration), `events.py` (new constructors), `power.py` (a public alias), `confounds.py` (EVAL-6 flag detectors), `query.py`, and `builders.py`. Specifically re-verified: quarantine is still unwired in `bench run`; there is still no `bench judge` verb (judge_pair still has zero production callers); still zero docker-marked tests; the AC-naming hook still only reports; the `_vendor` prefix false-negative in `confounds.py:21-22` survived the rewrite of that file; all grading/redaction/harbor/lock/calibration findings are in untouched files.

**The systemic diagnosis strengthens.** The same two patterns from the first audit recur in all four new stories, now with a third:
1. **Fail-closed escape hatches** — every story has verified paths where an attempted operation ends with zero ledger events (analyze refusal, process provider/parse errors, review reveal refusal).
2. **Correct primitives, missing connective tissue** — the estimators, samplers, scrubbers, and fences are individually sound and tested, but the pipeline steps that connect them (packet assembly, arm-identity lookup, calibration routing, admission gating) are unwired or stubbed with placeholder data.
3. **Statistics that silently use the wrong population** — pooled judge verdicts across arm pairs, CANT_JUDGE imputed as ties, nullsim at fabricated N=50, nominal instead of realized IPW weights, disagreement-biased calibration kappa.

---

## A. EVAL-6 — analyze + pre-registration fence

### Critical
- **A1. `judge_preference` analysis pools all verdicts and imputes CANT_JUDGE as ties** (`analyze/report.py:151-160, 352-358`). `_judge_preference_values` reads every `judge_verdict` event with no `comparison_id`/arm-pair filter and maps any non-A/B winner to 0.0. Verified: a 3-arm experiment with true effects +1 and −1 reports mean_delta 0.0 with inflated n for *both* comparisons; even in 2-arm experiments, fail-closed non-measurements shrink the official effect toward zero (a direct never-impute violation). No task clustering either — repeated reps are independent bootstrap clusters, so CIs are anti-conservatively narrow. And the verdict's A↔arm mapping is assumed, never recorded, so a swapped packet silently flips the sign.
- **A2. The official calibration fence accepts any manifest** (`report.py:565-578` + `analyze/cli.py`). `_assert_official_calibration` checks only `calibration.status`; nothing cross-checks `manifest.corpus_id/semver` against the spec's declared corpus or the tasks actually run. Verified: an official render succeeded citing `totally-unrelated@9.9.9` against a `public-mini@1.0.0` spec — and the shipped tests themselves pass a mismatched manifest, baking the hole into the green suite.

### Major
- **A3. Refused official renders are unledgered** — `--official` without calibration exits with a raw traceback and zero events; no `CANT_ANALYZE` event type exists (§7.2 violation). Success-path event ordering is also findings-files-first, event-second.
- **A4. D004 coverage selection does not run at the experiment's N**: `_variance_params` reads `n_tasks/p/rho` from the lock's `AssumedVariance` block (n=50 default), with silent fallbacks. Verified: a 4-task experiment selected its CI method from null sims at N=50 (measured coverage difference 0.96 vs 0.78). For cost/wall-time primaries the null model is still Bernoulli.
- **A5. `render_html` does no escaping and doesn't use jinja2** — arm names/ledger strings land raw in HTML (verified `<script>` injection).
- **A6. The `[computed]/[judgment]` claim tags do not exist anywhere** — no field, no render, and `test_ac6_finding_provenance` (the §6 owning test) doesn't test them.

### Notable minor
CIMethod seam not actually config-flippable (hardcoded call inside `compute_findings`); ADVISORY tier never surfaces in findings/renders; excluded-metric label misleading; `ClusterRobustTCI` drops zero-SE resamples and BCa's z0 is tie-heavy on discrete deltas; `findings_rendered.experiment_id` is the directory basename, not the locked id; `fractional_scoring` ignored by analysis.

### Verified sound
Paired bootstrap genuinely paired; percentile math clean; namespaced sub-seeds; byte-identical recomputation; Cliff's delta and BCa jackknife numerically correct; nullsim shares data/resamples across methods deterministically; the lock fence itself (sha + registered-primary-only, no `--metric` bypass) holds; asymmetric-null exclusion works and is disclosed; watermark on every exploratory section.

## B. EVAL-8 — corpus

### High
- **B1. AC-5 boundary enforcement is declaration-only.** `assert_boundary` (`corpus/registry.py:121-141`) validates the *declared* `boundary_path` string; nothing checks actual write destinations. Verified: an internal manifest saves inside the instrument repo; `bench corpus mine --out` writes Koalafi ticket text + holdout contents to any path with zero checks; nothing ever writes to the boundary path. `test_ac5_boundary_enforced` tests exactly and only the declared field — the §6 invariant is not yet structurally enforced.
- **B2. Admission status never gates execution.** `is_schedulable` has zero callers; `bench run` reads `tasks.yaml` and never consults a manifest (compounding the still-unwired quarantine from the first audit). A pending-curation or quarantined task listed in `tasks.yaml` runs, grades, and feeds findings.
- **B3. Public re-import bypasses the AC-6 mutation rule and destroys calibration state.** `import_terminal_bench` never loads the prior manifest nor calls `assert_valid_successor` (verified: mutated content at the same semver silently rewrites the cache), and rebuilds `Calibration()` from scratch — verified `full-run-validated` → `none` after a byte-identical re-import, wiping the AC-2 prerequisite.

### Medium
- **B4. Calibration recording has no production path and lives outside the ledger**: `record_calibration_run` has no CLI verb or run hook; status persists only in mutable manifest JSON, so hand-editing `"full-run-validated"` passes the official fence (compounds A2).
- **B5. The admission gate reads a ledger without verifying the chain** — a hand-forged 2-line ledger that fails `verify_chain` still admits a task (fail-open on exactly the evidence the chain exists to provide).
- **B6. Path traversal via registry-supplied `task_id`** (`public.py:95`): `task_id="../../escaped"` writes outside the cache; no dataset-level checksum pinning exists.
- **B7. `bench corpus review` shows paths only** — no holdout content or diff, so the human gate cannot do the solution-leakage check it exists for; approver is just `getpass.getuser()` with no attestation (miner can approve own candidate); admission itself is unledgered (`admit_task` mutates memory, nothing saves, no `task_admitted` event).

### Verified sound
Stratification deterministic with per-stratum namespaced sub-seeds, largest-remainder allocation correct; symlink/relative resolution on the declared path handled; sha-per-task computed consistently.

## C. EVAL-7 — human review

### High
- **C1. Verdicts are accepted after reveal and duplicates contaminate kappa.** `record_human_verdict` checks neither an existing reveal nor an existing verdict (verified: verdict → reveal → second verdict accepted; both enter `reviewed_kappa_items` and `pairs_from_ledger`). A now-unblinded reviewer can re-close a comparison; blinded-kappa validity breaks.
- **C2. Reveals ledger fabricated identities.** `bench review reveal` hardcodes `arm_identities={"1": "arm_a", "2": "arm_b"}` (`review/cli.py:77`) — no lookup from trial records. The unblinding record is fiction, and EVAL-9's reveal-keyed human scoring inherits it. No per-comparison response-order randomization exists anywhere in review/.
- **C3. The sampling→packet→kappa pipeline is unwired.** The promised `build` verb doesn't exist; `build_review_packet`, `select_for_review`, `reviewed_kappa_items`, `kappa_report` have zero production callers; nothing records which arm was "Response 1/2", so a human `--winner A` has no defined mapping to the judge's canonical A/B — kappa labels are commensurate only by unrecorded convention.
- **C4. Judge calibration bypasses the D003 IPW seam.** `judge/calibrate.kappa_by_class` computes raw pooled Cohen's kappa over the (disagreement-heavy, hence biased-low) reviewed set; the IPW estimator in `review/kappa.py` is a parallel path consumed only by EVAL-9 and tests. Escalation would fire spuriously — the exact bias D003 exists to remove.

### Medium
- **C5. IPW weights use nominal 0.2, not the realized inclusion probability**: sampling draws `ceil(0.2n)` (realized p > 0.2) while the estimator weights floor items 1/0.2 = 5 (verified: correct weight 3, used 5 for n=6) — floor over-weighted up to ~1.67× on small sets.
- **C6. Integrity guess-accuracy is structurally always 0**: `actual_arm` is never supplied by the CLI (and can't be — no lookup exists), so `arm_guess == actual_arm` is always False; blinding integrity is misreported as 0.0 rather than unknown.
- **C7. The mandatory/floor boundary leaks through packet ordering** (two independently-sorted blocks; the id-order reset marks exactly which items are disagreements — the partial unblind the docstring claims is avoided).

### Minor
Duplicate reveals allowed; refused reveal emits no event (§7.2); `verdict_event_id` holds the comparison id, not the verdict event ref; last-judge-verdict-wins joins; CANT_JUDGE enters kappa as a plain category; bare `append_human_verdict` closes a comparison per `comparison_closed` but never unlocks reveal — inconsistent closure semantics.

### Verified sound
Scrub is a true thin wrapper, packet re-scanned post-assembly, fail-closed on unscrubbed identity (no false-pass found); floor sampling pure in (seed, records) with namespaced sub-seed, empirically uniform; IPW arithmetic itself correct (hand-recomputed); reveal-before-verdict refused; judge verdicts never close comparisons.

## D. EVAL-9 — process rubric

### High
- **D1. Malformed judge JSON escapes with zero events** (`process/score.py:184-186`): `{"scores": [3,4,5]}` (list, not dict) raises `AttributeError` past the `except (ValueError, JSONDecodeError)` — verified escape, no `process_score` event. Same class as the EVAL-2 provider findings.
- **D2. A redaction-canary hit propagates `RedactionLeakError` with no event** — should be `CANT_SCORE(redaction_leak)`, matching EVAL-2's own `identity_leak` precedent. A leaky transcript leaves no audit trail that scoring was attempted and refused.

### Medium
- **D3. `get_provider` outside the try block** — unknown provider prefix escapes with no event (same latent structure as EVAL-2; verified).
- **D4. A judge-declared per-dimension `"CANT_SCORE"` (exactly what the packet instructs) is ledgered as reason `"unparsed"`**; timeout/refusal collapse to `provider_error`; reasons are free strings, not the §7.2 enum.
- **D5. The AC-5/AC-7 calibration/correlation reporting is dead code** — `process_kappa_by_dimension` and `score_telemetry_correlation` have no CLI verb and the analyze render includes neither correlations nor `style_only` flags; the judge scoring path itself has no CLI entry (`bench process` registers only `record`, despite the docstring documenting `score`).
- **D6. Official `findings.json` includes the process section** and hashes it into the ledgered `findings_sha256` — the firewall is render-only; the official run's on-disk artifact set is not process-free.

### Verified sound
The plan's central firewall holds: `PrimaryMetric` is one closed enum imported by both EVAL-3 validation and EVAL-9's negative test — a process dimension as primary is rejected; `unblinded: Literal[True]` makes blinded process scores unrepresentable; render refuses process content without the disclosure block; quadratic-weighted IPW kappa reuses EVAL-7's seam (hand-verified); Spearman/style_only math correct; redaction canary reuses the shared secrets list; EVAL-2's provider client genuinely reused; rubric loading is safe_load + extra=forbid, no injection surface.

---

## Updated priorities (supersedes the first report's list)

1. **Analysis correctness before anything official** — fix judge_preference pooling/imputation and task clustering (A1); bind the calibration fence to the spec's corpus identity (A2 + B4); run nullsim at the real N (A4).
2. **Close the integrity loops** — reject post-reveal and duplicate verdicts (C1); reveal real arm identities from trial records (C2); wire calibration through the IPW seam with realized inclusion probabilities (C4, C5).
3. **Finish the §7.2 sweep across all nine stories** — analyze refusals, process provider/parse/canary paths, reveal refusals, plus everything from the first report (EVAL-2 providers, grade skips, schedule exceptions).
4. **Make the corpus gates real** — enforce boundary on write destinations (B1), consult admission/quarantine in `bench run` (B2 + prior audit), enforce the successor rule and preserve calibration on re-import (B3), ledger calibration/admission events (B4).
5. **Wire the missing connective tissue** — `bench review build`, `bench process score`, a `bench judge` verb (still missing from the first audit), the CalibrationVariance loader, arm/model canary literals into judge and review scrubbing.
6. Everything in the first report's priority list for EVAL-3/4/5/2 still applies verbatim.

## Bottom line

The four new stories match the quality profile of the first four: the load-bearing primitives — paired bootstrap, IPW kappa, stratified sampling, scrub-and-rescan blinding, the closed metric enum, the schema-level unblinded-provenance firewall — are correct and genuinely tested (several hand-recomputed). What's not there is the instrument around them: identities are placeholders, gates have no callers, refusals vanish without ledger events, and the official-findings path has two verified bypasses (wrong-corpus manifest; pooled/imputed judge preference). With all nine stories now visible, the honest program-level status is: **the plan's architecture is implemented; the plan's guarantees are not yet enforced end-to-end.** I'd treat the §6 invariant table as still `enforced_by: review` for the boundary, claim-tags, and one-event rows, and gate any "first official finding" on the priority-1 and priority-2 items above.
