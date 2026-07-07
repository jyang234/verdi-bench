# ============================================================================
# STORY SPEC — first-run UX friction: honest surfaces, decisive scaffold
#
# Implemented spec-first on branch improve-exp-ux (2026-07-07): the design was
# human-validated and written down before any code, then landed AC by AC. The
# AC-coverage hook (tests/ac_coverage.py) requires a story's spec and its
# test_ac<N>_* tests to enter the registry together, so this file and
# tests/test_eval25_ux_friction.py land in a single commit. Decisions D1–D8
# (D1–D4 design-session rulings, D5–D8 batch-review rulings) are resolved in
# eval25.decisions.ndjson.
#
# Provenance: live first-run walkthrough on the shipped scaffold
# (bench init → §4 pipeline, keyless, no Docker) on 2026-07-07; friction
# inventory F1–F9 validated by the human; decisions D1–D4 ruled by the human
# that day (D5–D8 followed at batch review; all resolved in the ndjson).
#
# YAML STYLE: string values double-quoted and single-line; block-style
# lists; no hanging-indent plain scalars; no >- / | folded blocks.
# ============================================================================
kind: "story"
ticket: "EVAL-25"   # promoted spec-first on branch improve-exp-ux (2026-07-07)
title: "First-run UX: honest stage summaries, correct provenance, a scaffold that reaches a decision keyless"

problem:
  - "F1: plan derives experiment_id from the UNRESOLVED spec path (harness/plan/api.py:34, experiment.parent.name), so the cd-in flow that bench init itself prints ('bench plan experiment.yaml --ledger ledger.ndjson') bakes experiment_id='' into every event of the permanent hash-chained ledger. Verified live: relative → '', absolute → correct."
  - "F2: the starter template declares control first with rule 'delta_holdout_pass_rate > 0'; the paired delta is arms[0] − arms[1] (analyze/findings/model.py:244, analyze/selfcheck.py:53), so the scaffold pre-registers 'control beats treatment' — opposite of the golden shakedown scenario and of the common hypothesis."
  - "F3: the scaffold ships one task; the paired bootstrap clusters on tasks, so n_tasks=1 can never yield a decision — disclosed only at analyze time ('n_tasks=1 < 2: no decision possible [F-H7]'), after the full pipeline has run."
  - "F4: the scaffold judge is google/gemini-1.5-pro-002; keyless first-timers get every comparison as ledgered CANT_JUDGE(provider_error). The keyless deterministic fake/ provider exists (judge/providers/base.py:121) but the product never points at it."
  - "F6: stage summaries are success-shaped. bench grade prints 'graded 6 trial(s)' when 0 scored and all 6 were cant_grade (grade/cli.py:61; GradeOutcome carries only graded:int). bench judge prints 'judged 3 comparison(s)' when all 3 were CANT_JUDGE (judge/cli.py:37). The ledger is honest; stdout is not."
  - "F7: --runner local with no holdout_results.json raises GradingContainerError('no holdout_results.json in workspace') (grade/runners.py:169) and ledgers terminal cant_grade(container_failure) (grade/deterministic.py:31) — a container failure on a path with no container."
  - "F5: a successful lock leaves a stray <ledger>.planlock flock file in the experiment dir (plan/lock.py:302)."
  - "F8: bench status header shows a blank experiment name for path '.' (display-side echo of F1's path-derived naming)."
  - "F9: judge.panel is schema-accepted (schema/judge_config.py:76) and read by nothing — setting it silently changes the spec hash and does nothing else, the exact silent no-op the extra='forbid' posture exists to prevent."

goal: "A keyless first-timer who runs the scaffold's own suggested commands ends with correct provenance, truthful stage summaries, and a decisive MET finding — with zero file edits and zero API keys — and every remaining sharp edge announces itself at the moment it is created, not at the end of the pipeline."

decisions:          # full raised/resolved content in eval25.decisions.ndjson
  - "EVAL-25-D001"  # starter template ships the keyless deterministic judge (RESOLVED: option-a-fake-judge)
  - "EVAL-25-D002"  # exit code stays 0 on all-cant_* outcomes (RESOLVED: fail-closed-not-command-failure)
  - "EVAL-25-D003"  # judge.panel refuses when set, stays a v2 breadcrumb (RESOLVED: refuse-when-set)
  - "EVAL-25-D004"  # plan-time warning, never a gate, on <2 tasks (RESOLVED: warn-never-gate)
  - "EVAL-25-D005"  # suite-wide dir-derived fixture ids (RESOLVED: eliminate-hardcoded-default; Batch A review)
  - "EVAL-25-D006"  # AC-1 broadened to every experiment_id derivation site (RESOLVED: every-derivation-site; Batch A review)
  - "EVAL-25-D007"  # AC-3 per-reason ×N counts replace 'dominant reason' (RESOLVED: per-reason-counts; Batch B review)
  - "EVAL-25-D008"  # template-ripple: pin historical inputs, goldens byte-untouched (RESOLVED: pin-inputs; Batch D review)
open_decisions: []

constraints:
  - text: "Additive-only ledger vocabulary: new cant_grade reason strings and new lock-event flag strings extend existing string fields; no event schema, serialization recipe, or hash-chain change of any kind."
    enforced_by: "AC-4 / AC-9 forward-compat render tests + the existing chain goldens"
  - text: "The fake engine stays arm-blind; the operator injection step remains the designed fake-path mechanism. Nothing here adds arm-aware fake behavior."
    enforced_by: "existing arm-blindness property tests, unchanged"
  - text: "No new CLI verbs, no experiment-identity redesign (no explicit experiment_id spec field), no decision-rule DSL changes."
    enforced_by: "review"
  - text: "Reproduce-first: each AC lands with a failing test that reproduces today's behavior before the fix."
    enforced_by: "review + the per-AC tests below"

test_change_register:   # tests-are-contracts: pre-approved by the human with this spec, restated in the PR
  - test: "tests/test_starter_template_single_source.py:49"
    change: "the judge-model pin google/gemini-1.5-pro-002 → fake/deterministic-2026-01-01 (D1-A). Intent preserved: the pin's purpose is 'date-versioned, non-alias judge id in the canonical template' and the new id satisfies it (proven at plan time in the live run)."
  - test: "tests/test_schema_serialize.py:69,136"
    change: "the round-trip fixture drops panel:{size:3} (which AC-8 makes invalid) and keeps its non-default coverage via token_ceiling/escalation/orders/temperature. Intent preserved: 'non-default judge fields round-trip byte-stably' — panel was a convenient non-default, not the subject."
  - note: "The 15 other test files matching gemini-1.5-pro-002 hardcode their own inline specs and are expected to be unaffected; the author-page embed tests consume the template as data and follow it mechanically. Implementation must sweep and confirm, and list any additional edit in the PR under this register. [Corrected at Batch D triage: three tests leaked the template's default judge vendor through derived fixtures (test_eval2_plan, test_eval2_confounds, test_eval20_multimodel) — the original only-single-source claim undercounted.]"
  - test: "template-ripple fixtures (Batch D ruling): tests whose expectations froze under the old control-first/google-judge template defaults"
    change: "two sanctioned shapes, chosen per test for transparency and listed line-by-line in the landing report: (a) tests OF template-derived flows flip their arm-order/delta-sign/judge-id literals to track the new default; (b) tests whose purpose is indifferent to arm order (the frozen forensics_report seam golden, schedule-dependent trajectory/kappa/detector fixtures) PIN their historical inputs explicitly (explicit arms=/judge= overrides, or arm selection by platform instead of position) so frozen expectations — including the seam golden's EXPECTED_REPORT — stay byte-untouched. No golden or snapshot literal changes; no assertion deleted or weakened. Same explicit-inputs-over-invisible-defaults principle as D5."
  - test: "tests/test_e2e_pipeline.py:99 (test_retry_terminal_override_regrades_and_discloses)"
    change: "the pinned cant_grade reason literal on the local missing-results path updates container_failure → holdout_results_missing, mechanically tracking AC-4's spec-mandated reason change; the retry-terminal flow the test verifies (terminal → ledgered override_of re-grade) is unchanged. Added at Batch B review per this register's sweep-and-list rule."
  - test: "tests/fixtures/builders.py::fixed_ctx and its call sites (D5 ruling)"
    change: "the hardcoded 'exp-fixture' default is removed suite-wide: fixtures with a real experiment dir derive the id from the directory (via the AC-1 identity seam); dir-less unit tests pass an explicit literal at the call site. Asserted id LITERALS update mechanically ('exp-fixture' → the dir name or the explicit literal); no assertion is deleted or weakened, and every changed assertion line is listed in the landing PR."

acceptance:
  - id: "AC-1"
    text: "Every experiment_id derivation resolves its path first, through ONE shared seam: plan (from the spec path's resolved parent) and every stage that stamps EventContext from the experiment directory — run, grade, and the shared cli_common.event_context the remaining ledgering verbs use — derive the id from the RESOLVED directory name. Invoking any ledgering verb via '.', a bare relative path, or an absolute path produces the identical non-empty experiment_id; a resolved name that is empty refuses with a typed error (naming the offending path) rather than ever ledgering ''. [Broadened at Batch A review 2026-07-07: the original plan-only scope under-delivered F1's 'every event' — bench run . / bench grade . still ledgered '' on trial/grade events.]"
    vc: "plan's three invocation forms yield byte-identical provenance.experiment_id on the lock event; bench run . and bench grade . stamp trial/grade events with the directory's real name (today ''); a grep pins no remaining experiment_id=<path>.name construction outside the shared seam; the empty-name refusal names the path and appends nothing."
    touchpoints:
      - "harness/ledger/ (new identity seam beside actor resolution)"
      - "harness/plan/api.py:34"
      - "harness/run/api.py:250"
      - "harness/grade/api.py:194"
      - "harness/cli_common.py:60"
    tests:
      - "test_ac1_experiment_id_path_independent"
      - "test_ac1_empty_resolved_name_refused"
      - "test_ac1_derive_seam_path_independent"
      - "test_ac1_derive_seam_empty_name_refused"
      - "test_ac1_event_context_id_resolved"
      - "test_ac1_run_trial_events_resolved_id"
      - "test_ac1_grade_events_resolved_id"
  - id: "AC-2"
    text: "GradeOutcome reports the split — scored count, cant_grade count, and per-reason counts — and the bench grade summary line discloses it whenever cant_grade > 0 (e.g. 'graded 6 trial(s): 0 scored, 6 cant_grade (holdout_results_missing ×6) — see bench status'). Exit code stays 0 (D2). The all-scored line stays terse."
    vc: "The live-run reproduction (scaffold, no injection, --runner local) fails on today's 'graded 6 trial(s)' and passes with the split; an all-scored run prints no cant_grade clause."
    touchpoints:
      - "harness/grade/api.py:125"
      - "harness/grade/cli.py:61"
    tests:
      - "test_ac2_grade_outcome_reports_split"
      - "test_ac2_grade_cli_discloses_split_exits_zero"
      - "test_ac2_grade_summary_terse_all_scored"
      - "test_ac2_grade_summary_lists_reasons_sorted"
  - id: "AC-3"
    text: "JudgeOutcome reports verdicts vs cant_judge with per-reason counts, and the bench judge summary discloses the split whenever cant_judge > 0 (e.g. 'judged 3 comparison(s): 0 verdicts, 3 cant_judge (provider_error ×3)'), shape-consistent with grade's line. Exit code stays 0 (D2). [Amended at Batch B review: per-reason ×N counts replace the draft's 'dominant reason' — strictly more informative, and the full reason map rides the outcome.] [Amended at residual review 2026-07-07: the reused-control line discloses the same split; reuse retries transient cant_judge across passes, so the line reports the current pass.]"
    vc: "The keyless real-provider reproduction fails on today's 'judged 3 comparison(s)' and passes with the split; the same keyless judge over a reused control fails on today's bare 'judged N reused-control comparison(s) [exploratory]' and passes with the split."
    touchpoints:
      - "harness/judge/api.py:27"
      - "harness/judge/cli.py:37"
    tests:
      - "test_ac3_judge_cli_discloses_cant_judge_exits_zero"
      - "test_ac3_judge_summary_discloses_split"
      - "test_ac3_judge_summary_terse_all_substantive"
      - "test_ac3_reused_line_discloses_cant_judge"
  - id: "AC-4"
    text: "The local runner's missing-results outcome ledgers terminal reason 'holdout_results_missing' (new constant beside REASON_CONTAINER in grade/deterministic.py); container_failure is no longer emitted for file absence on --runner local. Docker-runner semantics are untouched. Readers (status, serve, analyze, control-reuse preflight) render an unrecognized reason string verbatim rather than crashing — pinned by test so the vocabulary stays forward-extensible."
    vc: "The no-injection local grade ledgers cant_grade(holdout_results_missing); a synthetic future reason string flows through status/serve/analyze renders unmodified."
    touchpoints:
      - "harness/grade/runners.py:169"
      - "harness/grade/deterministic.py:31"
    tests:
      - "test_ac4_local_missing_results_reason"
      - "test_ac4_docker_fence_still_container_failure"
      - "test_ac4_unknown_reason_renders_forward_compat"
  - id: "AC-5"
    text: "bench status titles the experiment from the locked ledger's experiment_id, falling back to the directory name only when no lock exists; bench status . and the absolute-path invocation render the same header."
    vc: "Post-lock, the '.' form shows the real name (blank today); pre-lock, the directory-name fallback holds."
    touchpoints:
      - "harness/status/"
    tests:
      - "test_ac5_status_header_from_ledger"
  - id: "AC-6"
    text: "A successful lock removes its <ledger>.planlock flock file; a failed lock attempt leaves cleanup unchanged. Safe because post-success every future planner is refused by check_single_lock regardless of the flock — a waiter that acquires the unlinked inode proceeds into that refusal."
    vc: "After a green plan the experiment dir contains only user files, the ledger, and (later) run artifacts; the existing concurrent-plan property test stays green."
    touchpoints:
      - "harness/plan/lock.py:302"
    tests:
      - "test_ac6_planlock_removed_on_success"
      - "test_ac6_refused_lock_no_planlock_resurrect"
  - id: "AC-7"
    text: "The starter template (single source: harness/sdk/templates/) declares the contender arm FIRST (aligning the scaffolded 'delta > 0' rule with 'treatment beats control' and with the golden scenario), ships the fake/deterministic-2026-01-01 judge with an adjacent comment showing the real-provider swap (D1-A), and starter-tasks.yaml ships TWO placeholder tasks. The template still passes the real validators. North-star outcome: bench init → plan → run → inject → grade --runner local → judge → analyze --exploratory reaches 'decision … ⇒ MET' with zero file edits, zero keys, zero Docker."
    vc: "The zero-edit scaffold pipeline e2e recovers a MET decision and a verifying chain; the single-source suite passes with the new pins; usage-guide §1.5 drops its judge-edit step and §2.1's 'scaffold pins a real Gemini judge' prose updates (the §2.1 example itself keeps the Gemini judge per D1-A)."
    touchpoints:
      - "harness/sdk/templates/starter-experiment.yaml"
      - "harness/sdk/templates/starter-tasks.yaml"
      - "tests/test_starter_template_single_source.py"
      - "docs/usage-guide.md"
    tests:
      - "test_ac7_template_contender_first_fake_judge"
      - "test_ac7_scaffold_zero_edit_pipeline_met"
  - id: "AC-8"
    text: "JudgeConfig refuses a set panel with a typed SpecError stating it is a v2 placeholder, not implemented, and must be removed (message names the field and the fix). Default None is unchanged; no green path today sets it (test-change register covers the one serializer fixture). The field itself stays in the schema as the v2 breadcrumb (D3)."
    vc: "A spec with panel set fails validation with the named error at load — before lock, before spend; every existing fixture spec validates unchanged; usage-guide §2.1's panel row updates from 'inert' to 'refused when set'."
    touchpoints:
      - "harness/schema/judge_config.py:76"
      - "harness/schema/errors.py"
      - "docs/usage-guide.md"
    tests:
      - "test_ac8_panel_set_refused_named_error"
      - "test_ac8_panel_absent_unchanged"
  - id: "AC-9"
    text: "bench plan warns — never gates (D4) — when the task suite has fewer than two tasks: a stdout line naming the consequence ('a decision needs ≥2 task clusters [F-H7]; this design will render findings but no decision') and an additive 'insufficient_tasks_for_decision' entry in the lock event's existing flags vector, beside power_gate_skipped. Two or more tasks: no warning, no flag. The lock succeeds either way."
    vc: "A one-task plan locks green with the warning line and the flag on the lock event; a two-task plan carries neither; analyze behavior is unchanged in both cases."
    touchpoints:
      - "harness/plan/api.py:45"
      - "harness/plan/power.py"
    tests:
      - "test_ac9_single_task_warns_and_flags"
      - "test_ac9_two_tasks_no_warning_no_flag"
  - id: "AC-10"
    text: "bench init's closing message teaches the two things first-timers otherwise learn the hard way: the fake-path next steps (plan → run → inject → grade --runner local) and 'bench status <dir>' as the standing read-only triage view."
    vc: "The init output names the injection step and bench status; the message stays under ~6 lines."
    touchpoints:
      - "harness/cli.py:111"
    tests:
      - "test_ac10_init_next_steps_message"

phasing:
  - "P0 (no design coupling, each independently landable, reproduce-first): AC-1, AC-2, AC-3, AC-4, AC-5, AC-6."
  - "P1 (template/product shape; carries the test_change_register edits): AC-7, AC-8, AC-9, AC-10."

non_goals:
  - "No change to fake-engine arm-blindness or any insulation property; injection remains an explicit operator step."
  - "No new CLI verbs (a dedicated inject verb was considered and rejected — the SDK one-liner is the documented public seam)."
  - "No experiment-identity redesign: experiment_id stays directory-derived, only derived correctly."
  - "No plan-time gate on task count (D4: warning only) and no coupling of plan to analyze's decision internals beyond the count."
  - "No hash-chain, event-schema, or canonical-serialization change; the two vocabulary additions (AC-4 reason, AC-9 flag) are additive strings in existing fields."
