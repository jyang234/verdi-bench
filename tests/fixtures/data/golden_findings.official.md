# Official findings — golden-mini-ab
Pre-registered primary metric: **holdout_pass_rate**
Decision rule: `delta_holdout_pass_rate > 0`

## Minimum detectable effect
MDE = n/a
  (assumption_based_mde: variance not yet calibrated)

## Primary metric
**Comparison: control vs treatment**  (n_tasks=5) [computed]
- mean paired delta: 0.5000
- Cliff's delta: 0.8000
- 95% CI (bca, 500 resamples): [0.2000, 0.8000]
- Effect detected. Decision rule `delta_holdout_pass_rate > 0` ⇒ MET.

## Confounds (disclosed, non-suppressing)
- none

## Contamination (disclosed, non-suppressing)
- probe: not_run
- control: clean_by_date=5 unknown=0 flagged=0 flagged_task_ids=[]
- treatment: clean_by_date=5 unknown=0 flagged=0 flagged_task_ids=[]

## Blinding integrity
- blinding integrity: n/a (no human review recorded yet)

## Grade tier
- ⚠ ADVISORY: results include ADVISORY-tier grades (local / no trusted container) — advisory, not authoritative; tiers present: ['ADVISORY'] [AC-9]

## Judge coverage
- 1 of 10 judged comparison(s) terminally unjudgeable (refusal: 1) — excluded from judge_preference and calibration, never imputed. If exclusions correlate with outcomes (e.g. an arm salting canaries on losing trials), judge_preference is biased by this missing-data channel [F-M-J1].

## Provenance
- instrument: 0.0.0+golden @ ffffffffffff
- ledger head: a1257c240ee253c7…  chain_ok=True
- judge: {'judge_models': ['fake/deterministic-2026-01-01'], 'rubric_shas': ['086a6b27cca76b48855347d7d749bad55a1074e9918dab87fa3e5bf004aa6dea'], 'n_verdicts': 10}
- [computed] judge is identity-blind, not outcome-blind: the packet includes holdout results by design, so judge_preference is not independent of holdout_pass_rate [EVAL-2 D002]
- corpus: public-mini@1.0.0 (full-run-validated), 5 task sha(s)

CI method selected by coverage: bca
