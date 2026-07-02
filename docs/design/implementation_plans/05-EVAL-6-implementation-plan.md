# 05 — EVAL-6 Implementation Plan: Analyze — paired statistics, effect sizes, confound flags, pre-registration fence

**Read with:** `00-EVAL-1-master-plan.md`, `Eval6.spec.md`, `Eval6.decisions.ndjson`. **Requires:** EVAL-3 (locked plan, ledger), EVAL-4 (trial records/telemetry), EVAL-5 (grades), EVAL-2 (verdicts + `judge_vendor_overlap` registration).

## 1. Gate status

- **RESOLVED:** D001 stats defaults — 95% CI, 10k paired bootstrap resamples on per-task deltas; effect size = mean paired delta + Cliff's delta; D002 auto confound-flag set — interleave imbalance, provider-error asymmetry, telemetry-null asymmetry, egress violations, version drift; D003 MDE reported with every finding, nulls phrased "no effect ≥ MDE detected", secondaries labeled exploratory.
- **OPEN (gate formally blocked):** **D004** — CI method selection: resample tasks as clusters; choose percentile vs BCa vs cluster-robust-t by **empirical coverage** under the null-simulation harness at the experiment's N. Working assumption = recommendation (`coverage-selected-method`), implemented behind a `CIMethod` seam (§M2/M5); percentile remains the trivially-available fallback if D004 flips to `fixed-percentile`.

## 2. Objective

Analyze is a **pure, reproducible function from (ledger, seed) to findings** that states exactly what the pre-registered design supports — effect sizes with uncertainty, nulls bounded by MDE, confounds surfaced automatically — and structurally refuses to state anything else officially. This stage is where homegrown evals usually lose soundness; here those failure modes become mechanical impossibilities.

## 3. Module layout & public symbols

```
harness/analyze/stats.py       paired_bootstrap
harness/analyze/effect.py      effect_sizes
harness/analyze/confounds.py   flag_confounds     (judge_vendor_overlap already here from EVAL-2)
harness/analyze/report.py      render_findings
```

Internal `[plan choice]`: `harness/analyze/nullsim.py` (null-simulation harness — D004's coverage selection **and** the substrate for EVAL-1-D008's A/A protocol; build once, serve both — master plan §7.7), `harness/analyze/ci.py` (`CIMethod` implementations: percentile, BCa, cluster-robust-t), `templates/` (jinja2 for findings renders).

## 4. Data contracts

**4.1 Purity.** `analyze(ledger_path, seed) -> findings` — no clock, no env, no network; all randomness from `numpy.random.Generator(PCG64(sub_seed))` with namespaced sub-seeds (master plan §7.5). **Byte-identical** outputs for same (ledger, seed): pin numpy, no parallel nondeterminism, deterministic serializer (sorted keys, fixed float formatting policy — store full-precision floats, render at fixed decimals) [AC-1].

**4.2 Findings document.** Per-comparison: primary-metric paired stats {mean paired delta, Cliff's delta, CI (method, level, resamples)}, MDE block (value + `assumption_based_mde` flag when EVAL-3-D007's fallback was used + `acknowledged_underpowered` if ledgered), confound flags, and **provenance**: instrument version + git sha, corpus version + task shas (from EVAL-8 manifest ref), **ledger head hash matching `verify_chain` output at render time**, judge provenance summary [AC-6]. Official vs EXPLORATORY status per §5 M4.

**4.3 Statistics definitions** [D001]. Unit = task (cluster): per-task delta = mean over repetitions per arm, then A−B. Bootstrap resamples **task indices** with replacement (clusters — required by D004's framing), 10k resamples, seeded. Cliff's delta over the paired per-task deltas' underlying pairs, O(n log n) implementation, fixture-verified against hand-checked values [AC-2].

## 5. Implementation sequence

**M1 — Paired bootstrap core.** `paired_bootstrap(per_task_deltas, seed, ci_method)`; reproducibility golden test (two runs, byte-identical stats) and known-fixture CI recovery. Tests: `test_ac1_paired_bootstrap`, `test_ac1_reproducible_seeded`.

**M2 — Effect sizes + CI seam.** `effect_sizes` (mean paired delta + Cliff's delta, mandatory — a report fixture without them fails render validation); `ci.py` with the three `CIMethod`s, percentile wired as interim default pending M5. Tests: `test_ac2_effect_sizes`.

**M3 — Confounds.** `flag_confounds(ledger, telemetry)` emitting exactly [D002]: `interleave_imbalance` (executed order vs derived schedule / arm run-position balance), `provider_error_asymmetry` (infra/provider failures per arm), `telemetry_null_asymmetry` (a field null in one arm's adapter but not the other's), `egress_violations` (any flagged trial), `version_drift` (image digest / agent version varies within an arm across the run) — plus `judge_vendor_overlap` registered by EVAL-2. Flags **ride** findings; they never suppress them (disclosure over suppression). Constructed fixtures exhibit each condition ⇒ exactly that flag; clean fixture ⇒ none. Tests: `test_ac4_flags_emitted`, `test_ac4_clean_fixture_no_flags`.

**M4 — Renderer + the fence.** `render_findings(ledger, seed, mode)`:
- **Official** mode renders *only* the pre-registered primary metric + decision rule from the locked experiment.yaml (recomputed sha via EVAL-3's `assert_lock`); requesting official for anything unregistered ⇒ refusal [AC-5]. Additionally refuse official when corpus calibration status is not `full-run-validated` (EVAL-8 AC-2 hook — implement the check against the manifest field now).
- **Everything else** renders with an EXPLORATORY watermark **on every page** (HTML: fixed banner per page/section; md: per-section header) [AC-5, D003]; secondary metrics always labeled exploratory.
- MDE appears in every render; null results phrased structurally as "no effect ≥ MDE detected"; `acknowledged_underpowered` surfaced when present [AC-3, D003].
- Provenance block per §4.2; a render missing any provenance field fails validation; head hash cross-checked against `verify_chain` at render time [AC-6].
- Cross-stack guard: comparisons computed only over telemetry fields both arms measured; a metric with asymmetric nulls is **excluded from official comparison and flagged, never imputed** [AC-7]; raw token counts never compared across vendors — cross-vendor comparisons restricted to cost, latency, outcomes (implement as a schema-level rule in the renderer's metric table, upgrading the spec's `enforced_by: review` to a test).
Tests: `test_ac3_mde_in_report`, `test_ac3_null_phrasing`, `test_ac5_unregistered_refused`, `test_ac5_exploratory_watermark`, `test_ac6_finding_provenance`, `test_ac7_asymmetric_nulls_excluded`.

**M5 — Null-simulation harness + coverage-selected CI [D004].** `nullsim.py`: simulate null paired experiments at the experiment's N (tasks × reps × arms, variance from the same source `mde_check` uses), run each `CIMethod`, measure empirical coverage; `paired_bootstrap` selects the method whose coverage is closest to nominal at that N, and the findings record which method was selected and why `[assumption: D004 rec — if D004 resolves to fixed-percentile, the seam collapses to a constant]`. This module is also the substrate for `bench selfcheck` (A/A + coverage) if EVAL-1-D008 resolves as recommended — build the library now, the verb then.

**M6 — CLI.** `bench analyze <experiment-dir> [--official|--exploratory]`; asserts lock; pure-function discipline enforced (no writes except the findings output + its render event).

## 6. Test plan summary

| AC | Tests |
|---|---|
| AC-1 | paired_bootstrap, reproducible_seeded (byte-identical) |
| AC-2 | effect_sizes (hand-checked fixtures) |
| AC-3 | mde_in_report, null_phrasing |
| AC-4 | flags_emitted (×6 constructed fixtures), clean_fixture_no_flags |
| AC-5 | unregistered_refused, exploratory_watermark (every page) |
| AC-6 | finding_provenance (incl. head-hash == verify_chain) |
| AC-7 | asymmetric_nulls_excluded |

## 7. Constraints checklist at merge

- No official output outside the pre-registered primary metric + decision rule ✓ (M4)
- analyze = pure fn(ledger, seed), seed-reproducible ✓ (M1/M6)
- Raw tokens never cross vendors; cross-vendor = cost/latency/outcomes ✓ (M4, promoted from review to test)

## 8. Definition of done

`bench analyze` renders official findings for a fixture experiment and refuses an off-registration render; all statistics recover hand-checked fixture values; each confound fixture flags correctly; findings carry full provenance with verified head hash. Formal closure additionally requires D004 RESOLVED (the seam makes either resolution a small diff).

## 9. Risks / watch items

- Byte-identical reproducibility is fragile across numpy/BLAS versions — pin hard, and make the golden test part of CI so drift is caught at dependency bumps.
- BCa on clustered small-N data can be unstable; that instability is precisely what the coverage harness should reveal — report it, don't paper over it.
- Out of scope, resist creep: sequential/interim analysis, Bayesian alternatives, meta-analysis.
