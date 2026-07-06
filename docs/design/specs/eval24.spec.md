---
# MACHINE CONTRACT — graduated from specs/proposed/ 2026-07-05 in the same
# change as its first AC tests, all four local decisions resolved (see
# eval24.decisions.ndjson). Prototype-validated (scripts/shakedown discovery):
# reasoning capture's payoff is the advisory-review lane — it caught a
# *confessed* shortcut the deterministic detectors are structurally blind to;
# the shared blind scrub MANGLES identity rather than blocking it; and the
# advisory review's hardcoded default model was a retired id (now config-resolved).
kind: "story"
ticket: "EVAL-24"   # synthetic key — source: 2026-07-05 flight-recorder request (session)
parent: "EVAL-1"
title: "Flight recorder: per-trial reasoning capture, operator-tier and advisory-review-fed, judge-isolated by construction"
services: []
home: null          # inherited from EVAL-1 (verdi-bench)
inherited_decisions:
  - "EVAL-1-D001"   # instrument residence + name (RESOLVED: verdi-bench)
  - "EVAL-12-D001"  # trajectory is a versioned per-trial record bound by an additive _sha — the flight_recorder_sha precedent
  - "EVAL-11-D004"  # forensics is disclosure-only until spot-check precision (carries: reasoning-fed suspicions gate nothing)
  - "EVAL-4-D004"   # null = unmeasurable, never estimated — the reasoning-capture-asymmetry lineage
  - "EVAL-21-D001"  # the closed agent-role vocabulary — reused for optional reasoning attribution [AC-6]
touchpoints:        # PLANNED symbols [judgment]
  - "harness/adapters/base.py:Adapter.normalize_reasoning"
  - "harness/run/flight_recorder.py:persist_flight_recorder"
  - "harness/run/seam.py:run_trial"
  - "harness/ledger/events.py:record_trial"
  - "harness/judge/packet.py:build_packet"
  - "harness/forensics/review.py:forensic_review"
  - "harness/forensics/scan.py:run_forensics"
  - "harness/analyze/confounds.py"
  - "harness/serve/compare.py:paired_comparisons"

graph_provenance: []

acceptance:
  - id: "AC-1"
    text: "The recorder is a per-trial artifact bound to the chain by an additive `flight_recorder_sha` on the trial event — the trajectory_sha precedent [EVAL-12-D001]: absent means pre-change and no reader may require it, and it is an additive optional field, NOT a new event kind (REGISTERED_EVENTS unchanged). The adapter emits reasoning through a `normalize_reasoning` seam that returns None honestly when the platform exposes none (the normalize_trajectory precedent). The record passes the same EVAL-4 secret-redaction door as the trajectory before persist and is re-validated after; a scrub that breaks it, or an unwritable/corrupt recorder, fails the trial closed — never silently-wrong bytes."
    vc: "A reasoning-bearing trial writes flight_recorder.json + stamps flight_recorder_sha; a legacy trial event with no such field reads back clean and no reader requires it; a planted provider-key in reasoning is redacted at persist; a corrupt-after-scrub record raises TrajectoryCorruptError rather than persisting."
    touchpoints:
      - "harness/run/flight_recorder.py:persist_flight_recorder"
      - "harness/ledger/events.py:record_trial"
    tests:
      - "test_ac1_recorder_additive_sha_redacted"
      - "test_ac1_sha_hoisted_additive_absent_when_none"
  - id: "AC-2"
    text: "The recorder is invisible to the graded and judged path BY CONSTRUCTION: build_packet, the deterministic grade, and the official/pre-registration fence take no recorder/reasoning parameter (the signature-is-the-allowlist convention shared with the judge and process packets). A property test asserts no reasoning/flight_recorder parameter exists on build_packet or the grade path, mirroring test_ac3_judge_call_isolated. Reasoning can never move a primary metric, a judge preference, or an official decision."
    vc: "inspect(build_packet).parameters and the grade path carry no recorder/reasoning key; an experiment WITH recorders yields byte-identical grades and official findings to one without."
    touchpoints:
      - "harness/judge/packet.py:build_packet"
    tests:
      - "test_ac2_recorder_judge_grade_isolated"
  - id: "AC-3"
    text: "The forensics advisory review may read the recorder, identity-scrubbed through the shared blind core and re-scanned fail-closed (`CANT_REVIEW(identity_leak)` on a scrub-surviving canary, exactly as the transcript today). A recorder over the review's context budget yields `CANT_REVIEW(context_overflow)` — a named coverage gap, never a truncated review or a silent skip. The review's provider model is resolved from configuration (not a hardcoded id): the current retired default (`anthropic/claude-3-5-sonnet-20241022`) is fixed so the advisory tier is not silently dark [D002]."
    vc: "Identity content in reasoning is scrubbed before the provider (blinded — the review completes on arm-blinded content, the mangles-not-blocks finding), and the pathology signal reaches a `[judgment]`-tagged review that flags the confessed shortcut; an over-budget trace → CANT_REVIEW(context_overflow); an unconfigured model → CANT_REVIEW(provider_error) with no retired hardcoded default. (The identity_leak survivor path is EVAL-11's existing defense, unchanged.)"
    touchpoints:
      - "harness/forensics/review.py:forensic_review"
      - "harness/forensics/scan.py:run_forensics"
    tests:
      - "test_ac3_recorder_review_blinded_bounded"
  - id: "AC-4"
    text: "Capture is null-honest and cross-arm asymmetry is disclosed: a platform exposing no reasoning records ABSENCE (distinguishable from an empty recorder), never a fabricated trace [EVAL-4-D004]; per-arm reasoning coverage rides the record and analyze discloses cross-arm reasoning-capture asymmetry as a confound (the telemetry-null-asymmetry precedent) so a lopsided-capture A/B never silently biases the comparison. Reasoning is exploratory by definition — schema-ineligible as a primary_metric, never an official input."
    vc: "A mixed experiment (one reasoning-bearing arm, one reasoning-null arm) reports per-arm reasoning coverage and an analyze confound line; no reasoning-null arm reads as reasoning-clean; a reasoning field cannot validate as a primary_metric."
    touchpoints:
      - "harness/analyze/confounds.py"
    tests:
      - "test_ac4_reasoning_null_honest_asymmetry_disclosed"
  - id: "AC-5"
    text: "The recorder is an unblinded OPERATOR-TIER diagnostic: it renders in the read-only operator/compare surface so test and control reasoning read side-by-side (how each arm arrived), under the existing serve disqualification banner (anyone who reads it is disqualified as that experiment's blinded EVAL-7 reviewer). The observability tier stays LLM-free — the recorder is inert data; status/serve name no LLM client (the existing import contract, unchanged). The recorder never appears in a blinded review/judge packet."
    vc: "The compare view surfaces per-arm reasoning for a paired trial; the observability import contract stays green with no edits; a blind-scrub over any blinded packet still finds no recorder content."
    touchpoints:
      - "harness/serve/compare.py:paired_comparisons"
    tests:
      - "test_ac5_recorder_operator_tier_exploratory"
  - id: "AC-6"
    text: "Reasoning is attributable to a sub-agent of a multi-agent workflow: ReasoningEntry carries an optional `agent` field over the SAME closed EVAL-21 role vocabulary as the trajectory (planner/executor/critic/worker-N, via the shared validate_agent_label), null = unattributed (single-agent reasoning, v1 records — no reader may require it); an out-of-vocabulary label is refused at the schema (identity leakage unrepresentable, not scrubbed); slice_reasoning_by_agent groups a workflow's reasoning by role (the slice_by_agent precedent, the UNATTRIBUTED bucket explicit). Additive: the recorder schema bumps to v2 the trajectory-additive-field way; v1 recorders read back null agent throughout [EVAL-24-D006]."
    vc: "A ReasoningEntry with agent='planner'/'worker-2' validates and agent=None is unattributed; 'llama-planner'/free text is refused (GenericLogError via the generic parse); a mixed workflow recorder slices into per-role reasoning with unattributed entries in the UNATTRIBUTED bucket; the compare payload carries the per-entry agent."
    touchpoints:
      - "harness/run/flight_recorder.py:ReasoningEntry"
      - "harness/run/flight_recorder.py:slice_reasoning_by_agent"
    tests:
      - "test_ac6_reasoning_attributed_to_subagent"

constraints:
  - text: "Reasoning is operator-tier and advisory-review-only: it never enters the graded trajectory vocabulary, the deterministic grade, the judge packet, or the official/pre-registration fence — isolation BY CONSTRUCTION (the packet/grade signatures take no recorder param), not by scrub [AC-2; prototype-validated: build_packet params are {response_a, response_b, rubric, task_prompt}]."
    enforced_by: "AC-2 property tests on graduation"
  - text: "The blind scrub MANGLES identity, it does not block: identity spans are removed (readability degrades, the pathology signal survives) and CANT_REVIEW(identity_leak) fires only on a scrub-resistant survivor [prototype-validated: 'I am Claude, made by Anthropic. The treatment arm's gpt-4.1-mini…' → 'I am [REDACTED], made by [REDACTED]. The [REDACTED] arm's [REDACTED]…', 0 survivors, review completed and named the confessed shortcut]."
    enforced_by: "AC-3 tests on graduation"
  - text: "A per-trial reasoning SIZE BUDGET bounds the recorder; over-budget degrades to CANT_REVIEW(context_overflow), never a truncated or silently-skipped review [prototype-validated: a ~400k-char trace overflowed the review's 100k-token ceiling → context_overflow]."
    enforced_by: "AC-3 tests on graduation"
  - text: "flight_recorder_sha is an additive optional trial-event field; absent = pre-change, no reader may require it, no new event kind — the trajectory_sha / additive-contract precedent [EVAL-12-D001]. As a hash-chained-adjacent contract change it requires the same human approval + migration note the trajectory_sha addition took (ContractChange gate)."
    enforced_by: "AC-1 tests on graduation"
  - text: "The recorder is byte-deterministic for a fixed capture — relative timing, no wall-clock, canonical serialization (the trajectory precedent) [determinism-by-default]."
    enforced_by: "AC-1 tests on graduation"
  - text: "Reasoning-capture asymmetry across arms is a disclosed measurement condition, not a correction — no reweighting, no imputation [EVAL-4-D004 lineage]."
    enforced_by: "AC-4 tests on graduation"
  - text: "The forensic advisory review reads agent-authored reasoning, so build_forensic_packet fences the transcript in a content-derived delimiter (sha256(transcript)[:16], unforgeable) and marks it UNTRUSTED — an injection in reasoning stays in the data channel [EVAL-24-D005, the JD-8 judge-packet pattern]. A framing change to the advisory tier that may nudge its spot-check calibration (advisory/non-gating, recalibrated per EVAL-11-D004)."
    enforced_by: "test_forensic_packet_fences_untrusted_transcript (EVAL-11 review suite)"

decisions:
  - "EVAL-24-D001"  # recorder tier (RESOLVED: separate-artifact-additive-sha)
  - "EVAL-24-D002"  # forensic_review model (RESOLVED: resolve-from-config-no-default)
  - "EVAL-24-D003"  # reasoning size budget (RESOLVED: fixed-per-trial-byte-cap)
  - "EVAL-24-D004"  # cross-arm asymmetry surfacing (RESOLVED: additive-coverage-keys-plus-analyze-line)
  - "EVAL-24-D005"  # forensic-packet injection fence (RESOLVED: content-derived-fence) — code-review follow-on
  - "EVAL-24-D006"  # multi-agent reasoning attribution (RESOLVED: optional-agent-role-additive-v2)
open_decisions: []

policy_proposals: []
predicted_reach: null
expected_verify: "On graduation: the additive-sha + redaction-door persist test (incl. corrupt-after-scrub fail-closed), the judge/grade/fence isolation property test, the advisory-review trio (identity_leak survivor, context_overflow over-budget, clean shortcut-confessing [judgment] review against a configured live model), the mixed-arm null-honest asymmetry disclosure test, and the operator-tier LLM-free render test."
---

# EVAL-24 — Flight recorder: per-trial reasoning capture (proposed)

## Problem & context

verdi's trajectory records a trial's *actions* (EVAL-12/15/16). What it does not
record is the trial's *reasoning* — the chain of thought by which each arm
arrived at its answer. An operator debugging "how did test and control reach
their respective destinations, and where did each go wrong?" has no reasoning
surface; the forensic advisory review (EVAL-11), which exists precisely to
narrate "shortcut behavior a regex cannot name", is starved of the one signal
that most directly reveals shortcut *intent*.

A 2026-07-05 prototype (`scripts/shakedown`, in-process against the real
adapter / persist / blind / forensics / judge-packet code) confirmed the payoff
and surfaced the constraints this contract is written around:

- **The advisory review is the killer app.** Given a reasoning trace where the
  agent confessed "…I'll just hardcode the expected answer and skip running the
  real holdout test", the real `forensic_review` — blinded — returned
  `[judgment] … explicit admissions of gaming behavior` and flagged
  hardcoded-output / test-skip / holdout-tamper. The **deterministic** detectors
  found nothing on the same trajectory: intent lives in reasoning, not in the
  workspace end-state.
- **Blinding mangles, does not block.** The shared blind scrub turned
  "I am Claude, made by Anthropic" into "I am [REDACTED], made by [REDACTED]"
  (0 survivors) and the review proceeded on arm-blinded content —
  `identity_leak` fires only on a scrub-resistant survivor.
- **Two existing bugs the recorder depends on:** `forensic_review`'s default
  model is the retired `claude-3-5-sonnet-20241022` (→ `CANT_REVIEW(provider_error)`
  out of the box — the advisory tier is silently dark), and a ~400k-char trace
  overflows the review's context ceiling (→ `CANT_REVIEW(context_overflow)`).

## Goal

Capture per-trial reasoning as a chain-anchored, secret-redacted, operator-tier
artifact; feed it to the advisory review (blinded, fail-closed, size-bounded) so
pathology tracing can read intent; render it side-by-side in the operator
compare view — and keep it, by construction, out of every graded, judged, and
officially-fenced path.

## Design

- **Tier (D001, recommended `separate-artifact`).** A per-trial
  `flight_recorder.json` bound by an additive `flight_recorder_sha` on the
  trial event, mirroring `trajectory_sha` [EVAL-12-D001]. Keeping it *separate*
  from the graded trajectory means verbose, identity-leaky reasoning never
  perturbs the closed trajectory vocabulary the deterministic detectors and the
  official path consume — no v-N kind bump, no forensics-metric churn.
- **Adapter seam.** `Adapter.normalize_reasoning(native_log) -> Optional[list]`,
  the `normalize_trajectory` precedent: `None` is the honest state for a platform
  that exposes no reasoning. Reasoning entries are closed-shape
  (ordered content + optional tokens/cost + an OPTIONAL `agent` role [AC-6]
  reusing the trajectory's closed EVAL-21 vocabulary for multi-agent workflows;
  null = unattributed — the prototype-rejected free-form `agent:"primary"`).
- **Isolation by construction.** The recorder is not a parameter of `build_packet`,
  the grade, the review-packet, or the fence — so it *cannot* reach them, exactly
  as the process rubric cannot reach a verdict (EVAL-9). The one blinded consumer
  it *does* reach is the advisory review, which scrubs + re-scans fail-closed and
  is size-bounded (D003).
- **Disclosure.** Reasoning is exploratory (never a metric); cross-arm capture
  asymmetry is a disclosed confound (EVAL-4-D004 lineage), surfaced per D004.
- **Prerequisite fix (D002).** Resolve the advisory review's provider model from
  configuration, retiring the hardcoded stale default, so the recorder has a live
  consumer.

## Out of scope

Fence coupling for any reasoning-fed suspicion (EVAL-11-D004 stands until
spot-check calibration proves precision); reasoning-based *deterministic*
detectors (this story feeds the LLM tier, which is where un-nameable intent
lives); and summarize-before-review compression (a follow-on to the size
budget). Multi-agent reasoning attribution — deferred at graduation — is now
in scope as AC-6 [EVAL-24-D006].
