# Findings (EXPLORATORY) — golden-mini-ab
⚠ EXPLORATORY — not an official, pre-registered finding

## ⚠ EXPLORATORY — not an official, pre-registered finding
### Pre-registered context
- primary metric: holdout_pass_rate
- decision rule: `delta_holdout_pass_rate > 0`

## ⚠ EXPLORATORY — not an official, pre-registered finding
### Minimum detectable effect
MDE = n/a
  (assumption_based_mde: variance not yet calibrated)

## ⚠ EXPLORATORY — not an official, pre-registered finding
### Primary metric — control vs treatment
**Comparison: control vs treatment**  (n_tasks=5) [computed]
- mean paired delta: 0.5000
- Cliff's delta: 0.8000
- 95% CI (bca, 500 resamples): [0.2000, 0.8000]
- Effect detected. Decision rule `delta_holdout_pass_rate > 0` ⇒ MET.

## ⚠ EXPLORATORY — not an official, pre-registered finding
### Secondary metrics (exploratory)
- per-arm means: {'control': {'cost': 0.5, 'tokens_cache': 50.0, 'tokens_in': 500.0, 'tokens_out': 200.0, 'tool_calls': 3.0, 'wall_time_s': 12.0}, 'treatment': {'cost': 0.5, 'tokens_cache': 50.0, 'tokens_in': 500.0, 'tokens_out': 200.0, 'tool_calls': 3.0, 'wall_time_s': 12.0}}
- cross-vendor: raw token fields ['tokens_in', 'tokens_out', 'tokens_cache'] are vendor-incomparable and NOT compared across arms; cross-vendor comparisons restricted to ['cost', 'wall_time_s', 'tool_calls']

## ⚠ EXPLORATORY — not an official, pre-registered finding
### Judge calibration (per class)
- thresholds: kappa ≥ 0.6 at ≥ 20 EFFECTIVE human verdicts (Kish, IPW-weighted); escalation fires when the interval's UPPER bound is below threshold — a straddling interval is INCONCLUSIVE, not silently fine [AC-7, F-M-S4]
- no human-reviewed comparisons yet — kappa pending

## ⚠ EXPLORATORY — not an official, pre-registered finding
### Confounds (disclosed, non-suppressing)
- none

## ⚠ EXPLORATORY — not an official, pre-registered finding
### Contamination (disclosed, non-suppressing)
- probe: not_run
- control: clean_by_date=5 unknown=0 flagged=0 flagged_task_ids=[]
- treatment: clean_by_date=5 unknown=0 flagged=0 flagged_task_ids=[]

## ⚠ EXPLORATORY — not an official, pre-registered finding
### Blinding integrity
- blinding integrity: n/a (no human review recorded yet)

## ⚠ EXPLORATORY — not an official, pre-registered finding
### Grade tier
- ⚠ ADVISORY: results include ADVISORY-tier grades (local / no trusted container) — advisory, not authoritative; tiers present: ['ADVISORY'] [AC-9]

## ⚠ EXPLORATORY — not an official, pre-registered finding
### Judge coverage
- 1 of 10 judged comparison(s) terminally unjudgeable (refusal: 1) — excluded from judge_preference and calibration, never imputed. If exclusions correlate with outcomes (e.g. an arm salting canaries on losing trials), judge_preference is biased by this missing-data channel [F-M-J1].

## ⚠ EXPLORATORY — not an official, pre-registered finding
### CI method selection (coverage)
- {'selected_method': 'bca', 'nominal': 0.95, 'coverage': {'bca': 0.95, 'cluster_robust_t': 0.95, 'percentile': 0.95}, 'n_sim': 40, 'n_boot': 500, 'n_tasks': 5, 'null_model': 'paired_binary'}

## ⚠ EXPLORATORY — not an official, pre-registered finding
### Provenance
- instrument: 0.0.0+golden @ ffffffffffff
- ledger head: bd1019604c2e1d59…  chain_ok=True
- judge: {'judge_models': ['fake/deterministic-2026-01-01'], 'rubric_shas': ['086a6b27cca76b48855347d7d749bad55a1074e9918dab87fa3e5bf004aa6dea'], 'n_verdicts': 10}
- [computed] judge is identity-blind, not outcome-blind: the packet includes holdout results by design, so judge_preference is not independent of holdout_pass_rate [EVAL-2 D002]
- corpus: public-mini@1.0.0 (full-run-validated), 5 task sha(s)

