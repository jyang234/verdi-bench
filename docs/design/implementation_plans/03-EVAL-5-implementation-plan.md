# 03 — EVAL-5 Implementation Plan: Deterministic grading — isolated holdouts, flake baseline, grader plugins

**Read with:** `00-EVAL-1-master-plan.md`, `eval5.spec.md`, `Eval5.decisions.ndjson`. **Requires:** EVAL-3 (ledger/events, lock assertion), EVAL-4 (trial artifacts + workspace layout, container plumbing to reuse).

## 1. Gate status

**CLEAR.** D001 flake baseline = k=5, zero tolerance, quarantine on any failure; D002 scoring = per-assertion results always recorded, primary deterministic score binary task-level, fractional only if pre-registered. Inherited EVAL-1-D001 RESOLVED. **Can go to Opus immediately after EVAL-4.**

## 2. Objective

Layer-0 verdicts beyond argument: every trial receives exactly one deterministic grade event containing the full assertion vector, produced in an isolated fresh environment, against holdouts proven non-flaky before any agent ever ran. This layer contains **no LLM calls** — determinism is its entire authority.

## 3. Module layout & public symbols

```
harness/grade/deterministic.py        grade_trial
harness/grade/baseline.py             flake_baseline
harness/grade/plugins.py              GraderPlugin
harness/grade/plugins/groundwork.py   GroundworkGrader
```

Internal `[plan choice]`: `harness/grade/container.py` (grading-container launcher — a thin, network-less specialization of EVAL-4's container plumbing; do **not** reuse trial containers), plugin registry keyed by id declared in the task manifest.

## 4. Data contracts

**4.1 Assertion vector.** `Assertion = {id, source ∈ {holdout_test, plugin:<id>}, result ∈ {pass, fail, abstain}, detail}`. Groundwork rule verdicts map with **rule ids preserved**; a `NO-STRUCTURAL-SIGNAL` abstention maps to `result=abstain`, **never** `pass` [spec: consistent with verdi-go epistemics] [AC-4].

**4.2 Ledger events added.** `grade` — `{trial_id, task_sha, assertions: [...], binary_score, fractional_score?}`; `cant_grade` — `{trial_id, reason ∈ {container_failure, malformed_holdout_output, workspace_missing, plugin_error, ...}}` [AC-5]; `flake_baseline` — `{task_id, task_sha, k, results: [...], verdict ∈ {clean, quarantined}}` [AC-2].

**4.3 Scoring policy** [D002, AC-3]. `binary_score = all(holdout assertions pass)` (abstain does not count as pass; plugin assertions contribute to the vector and to fractional scoring but a plugin abstain must not fail the binary either — `[plan choice]`: binary is computed over holdout-test assertions, matching "binary task-level (matching published calibration numbers)"; plugin assertions are recorded data). `fractional_score` computed **only** when the locked `experiment.yaml` pre-registered `fractional_scoring: true` (checked against the EVAL-3 lock, not runtime config).

## 5. Implementation sequence

**M1 — Grading container.** Fresh container per trial: copy of the trial's final workspace tar'd in, holdouts bind-mounted **read-only**, `network_mode=none`. Trial containers never reused. Tests: `test_ac1_grading_isolated` (container inspect: no network namespace access), `test_ac1_holdouts_readonly` (write attempt inside container fails).

**M2 — grade_trial.** Run holdouts in the container, parse per-assertion results, invoke declared plugins (M4), assemble the vector, compute scores per §4.3, append exactly one `grade` event. Any failure anywhere ⇒ exactly one `cant_grade(reason)` event — an attempted grade without an event is unrepresentable [AC-5]. Tests: `test_ac3_per_assertion_recorded`, `test_ac3_binary_default`, `test_ac3_fractional_requires_prereg` (experiment without pre-registration cannot render a fractional primary — the render half lands with EVAL-6; pin the data half here: fractional field absent), `test_ac5_fail_closed` (fault-inject container failure and malformed holdout output ⇒ one CANT_GRADE each, machine-readable reason).

**M3 — flake_baseline.** Run each task's holdouts k=5 against the **unmodified** workspace [D001]; any failure ⇒ task *version* quarantined and excluded from run scheduling; baseline event ledgered with task sha. This is invoked at **corpus-admission time by EVAL-8** (AC-4 there requires a ledgered clean baseline before admission) — expose `flake_baseline(task, ledger)` as the callable EVAL-8 wires in, and expose the quarantine list to EVAL-4's scheduler (a scheduled quarantined task version must be refused). Tests: `test_ac2_baseline_quarantine` (fixture flaky holdout ⇒ quarantined + unschedulable), `test_ac2_baseline_ledgered` (k results + task sha present).

**M4 — Plugin seam.** `GraderPlugin`: declared per task; contract `(workspace, task) -> [Assertion]`. Registry maps plugin id → class; a task manifest lists plugin ids. `GroundworkGrader`: runs `verify`/fitness rules against the workspace for internal Go tasks, maps each rule verdict to an assertion with the rule id preserved; abstentions per §4.1. Tests: `test_ac4_plugin_contract` (fixture plugin contributes assertions to the grade event), `test_ac4_groundwork_plugin` (fixture Go task: rule ids preserved; NO-STRUCTURAL-SIGNAL ⇒ abstain).

**M5 — CLI + structural lint.** `bench grade <experiment-dir>` (asserts lock first; iterates ungraded trials). Import-linter contract: `harness/grade/` may not import any LLM-client module [spec constraint, `enforced_by: review` → make it the candidate import-lint the spec anticipates].

## 6. Test plan summary

| AC | Tests |
|---|---|
| AC-1 | test_ac1_grading_isolated, test_ac1_holdouts_readonly |
| AC-2 | test_ac2_baseline_quarantine, test_ac2_baseline_ledgered |
| AC-3 | test_ac3_per_assertion_recorded, test_ac3_binary_default, test_ac3_fractional_requires_prereg |
| AC-4 | test_ac4_plugin_contract, test_ac4_groundwork_plugin |
| AC-5 | test_ac5_fail_closed |

## 7. Constraints checklist at merge

- Holdouts never present in trial containers (EVAL-4 AC-9 owns the trial side; assert the mount lists here too) and never writable in grading containers ✓ (M1)
- A task version cannot be scheduled without a clean ledgered baseline ✓ (M3 + scheduler hook)
- No LLM calls in this layer ✓ (M5 import-lint)

## 8. Definition of done

`bench grade` produces chained grade events for a fixture experiment; quarantine list functional and honored by scheduling; groundwork plugin demonstrated against a fixture Go task; full AC suite green.

## 9. Risks / watch items

- Holdout output parsing must be format-explicit (pytest/go test/JUnit XML as applicable per task format — Harbor task format per EVAL-8 constraint); malformed output is a `cant_grade(malformed_holdout_output)`, never a guessed pass/fail.
- Zero-tolerance flake policy is deliberate and cheap (no agent involved) — do not add a threshold "for pragmatism"; the spec explicitly defers flake-rate-threshold policies until data argues otherwise.
- Keep grading-container caching out of scope even though it will be tempting.
