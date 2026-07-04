---
# MACHINE CONTRACT — PROPOSED (not yet graduated; AC enforcement begins when
# this file moves to docs/design/specs/ in the same commit as its first AC
# tests, the eval12..15 precedent). Drafted 2026-07-04 as the follow-on the
# EVAL-15 spec named out-of-scope: detectors that consume per-step detail,
# with their planted-violation fixtures.
kind: "story"
ticket: "EVAL-16"   # synthetic key — source: EVAL-15 out-of-scope follow-on
parent: "EVAL-1"
title: "Step-content forensics: detectors that see the moment of tampering, with honest coverage asymmetry"
services: []
home: null          # inherited from EVAL-1 (verdi-bench)
inherited_decisions:
  - "EVAL-1-D001"   # instrument residence + name (RESOLVED: verdi-bench)
  - "EVAL-14-D004"  # per-step detail exists (RESOLVED, built as EVAL-15)
  - "EVAL-11-D004"  # forensics is disclosure-only until spot-check precision (carries)
touchpoints:        # PLANNED symbols [judgment]
  - "harness/forensics/detectors.py:DETECTOR_IDS"
  - "harness/forensics/detectors.py:run_detectors"
  - "harness/forensics/scan.py:run_forensics"
  - "harness/analyze/report.py:_forensics_lines"

graph_provenance: []

acceptance:
  - id: "AC-1"
    text: "The new detectors join the closed vocabulary with a vocabulary_version bump: findings from different vocabulary versions never merge (the existing closed-enum enforcement extends to the new ids), every new detector ships with a planted-violation fixture that must flag and a clean fixture that must not, and the deterministic tier stays LLM-free (the existing import contract, unchanged)."
    vc: "The closed-enum test names the new ids at the bumped version; per-detector planted/clean fixture pairs pass; lint-imports stays green with no contract edits."
    touchpoints:
      - "harness/forensics/detectors.py:DETECTOR_IDS"
    tests: []
  - id: "AC-2"
    text: "Transient holdout tamper is caught at the step level: a file_edit step whose detail touches holdout paths or holdout expected content flags the trial even when a later step reverts the workspace (end-state clean) — the case end-state detectors are structurally blind to. Detection is mechanical matching against the trial's own holdout identifiers, not inference; the existing end-state tamper detector keeps its coverage unchanged."
    vc: "A planted edit-then-revert fixture flags with the step index in evidence; the clean fixture (edits near but not on holdout material) does not; existing end-state fixtures still behave."
    touchpoints:
      - "harness/forensics/detectors.py:run_detectors"
    tests: []
  - id: "AC-3"
    text: "Hardcoded expected outputs are caught in patch material: a file_edit detail containing a holdout's expected literal flags with the matching literal named in evidence. Matching is exact-literal against the task's holdout expectations (D003), documented as such — no normalization, no fuzzy matching, no guessing."
    vc: "A planted fixture embedding an expected literal in an edit flags and names the literal; a clean fixture with similar-but-different content does not."
    touchpoints:
      - "harness/forensics/detectors.py:run_detectors"
    tests: []
  - id: "AC-4"
    text: "Test-skip insertion is caught at the step level including transiently: a file_edit detail introducing skip/xfail markers into test material flags even if a later edit removes them; marker set is a closed, documented list."
    vc: "Planted transient-skip fixture flags; the clean fixture (markers in non-test prose) does not."
    touchpoints:
      - "harness/forensics/detectors.py:run_detectors"
    tests: []
  - id: "AC-5"
    text: "Coverage is null-honest and asymmetry is disclosed: a trial whose steps carry no detail (codex arms, pre-v3 records) yields not_evaluable coverage entries for the detail detectors — never a silent clean — recorded in the forensics_report coverage block (additive keys, D002); per-arm detail-detector coverage counts ride the report and analyze's forensics disclosure lines state the asymmetry, the telemetry-asymmetry precedent."
    vc: "A mixed experiment (one detail-bearing arm, one detail-null arm) reports per-arm not_evaluable counts; the exploratory render's forensics lines disclose the asymmetry; no detail-null trial appears as detail-clean."
    touchpoints:
      - "harness/forensics/scan.py:run_forensics"
      - "harness/analyze/report.py:_forensics_lines"
    tests: []
  - id: "AC-6"
    text: "Evidence discipline holds: detectors read only sha-verified trajectories (resolve_trajectory's verified status — unverified content is a coverage gap, never evidence), remain pure functions of (trajectory, task holdout identifiers), flags stay evidence-never-verdicts and gate nothing (EVAL-11-D004 unchanged: disclosure-only until spot-check calibration proves precision)."
    vc: "A sha_mismatch trajectory contributes a gap, not flags; two runs over the same artifacts produce identical reports; no fence or gate consults the new flags."
    touchpoints:
      - "harness/forensics/scan.py:run_forensics"
    tests: []

constraints:
  - text: "Mechanical over clever: v1 detection is closed-table matching (paths, literals, markers) against the trial's own task material — no workspace replay, no similarity scoring, no inference. A detector that cannot decide mechanically yields not_evaluable, never a guess [D001]."
    enforced_by: "AC-2/AC-3/AC-4 planted+clean fixture pairs on graduation"
  - text: "Detail-coverage asymmetry is a disclosed measurement condition, not a correction: no reweighting, no imputation — the same posture as telemetry-null asymmetry [EVAL-4-D004 lineage]."
    enforced_by: "AC-5 tests on graduation"
  - text: "The forensics_report payload change is additive keys inside the existing coverage block; the record_forensics_report shape validation extends, old ledgers are never refused [the additive-event-field precedent]."
    enforced_by: "AC-5 tests on graduation"

decisions: []
open_decisions:
  - "EVAL-16-D001"  # transient-tamper mechanism: mechanical step matching (recommended) vs workspace replay
  - "EVAL-16-D002"  # asymmetry surfacing: additive coverage keys in the report (recommended) vs a separate event
  - "EVAL-16-D003"  # expected-literal matching: exact only, documented (recommended) vs normalized matching

policy_proposals: []
predicted_reach: null
expected_verify: "On graduation: closed-enum bump test, planted/clean pairs for each new detector (including the edit-then-revert case), the mixed-arm asymmetry disclosure test, determinism and verified-only-evidence tests."
---

# EVAL-16 — Step-content forensics (proposed)

## Problem & context

EVAL-11's detectors see trajectory *shape* and workspace *end-state*; a
trial that tampers mid-flight and reverts before finishing is structurally
invisible to them. EVAL-15 put the moment-by-moment content on the record —
sha-verified, scrubbed, operator-tier. This story lets the deterministic
detector tier read it, under the same rules that made the existing
detectors trustworthy: closed vocabulary, planted-violation ownership,
flags as evidence only.

## Goal

Catch edit-then-revert holdout tamper, hardcoded expected outputs, and
transient test-skip insertion at the step where they happened — and say
honestly, per arm, where the detectors could not look.

## Design

Three detectors over verified trajectories' detail, each mechanical:
holdout-material matching (paths + expected literals from the trial's own
task), expected-literal matching in patch material, and closed-list skip
markers. Coverage extends per detector: evaluated / flagged /
not_evaluable(no_detail | unverified_trajectory); per-arm counts ride the
report's coverage block and analyze disclosure. The asymmetry story is the
point, not a footnote: a detail-bearing arm must not silently face more
scrutiny than a detail-null arm — the report says exactly who was looked at
and who could not be.

## Out of scope

Fence coupling for any flag (EVAL-11-D004 stands until spot-check
calibration); LLM-tier review changes; workspace replay reconstruction;
detector consumption of transcripts (fake-engine-only artifact).
