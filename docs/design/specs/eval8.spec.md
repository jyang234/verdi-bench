--
# MACHINE CONTRACT — see template header for consumers and YAML style rules.
kind: "story"
ticket: "EVAL-8"    # synthetic key — source: consolidated design pass 2026-07-02
parent: "EVAL-1"
title: "Task corpus tooling: public import, calibration subset, monorepo mining, curation gate"
services: []
home: null          # inherited from EVAL-1; NOTE: internal corpora written to the
                    # Koalafi boundary path, never the instrument repo (parent invariant)
inherited_decisions:
  - "EVAL-1-D001"   # instrument residence + name (RESOLVED: verdi-bench)
  - "EVAL-1-D004"   # corpus strategy: both, public-first (RESOLVED)
touchpoints:        # PLANNED symbols [judgment]
  - "harness/corpus/public.py:import_terminal_bench"
  - "harness/corpus/stratify.py:calibration_subset"
  - "harness/corpus/mine.py:mine_mr"
  - "harness/corpus/registry.py:CorpusManifest"
​
graph_provenance: []
​
acceptance:
  - id: "AC-1"
    text: "Public import pulls terminal-bench@2.0 through the Harbor registry into a corpus manifest recording dataset version and per-task shas."
    vc: "The manifest lists every imported task with its sha; re-import against the same dataset version is idempotent."
    touchpoints:
      - "harness/corpus/public.py:import_terminal_bench"
    tests:
      - "test_ac1_public_import_manifest"
  - id: "AC-2"
    text: "The calibration subset is a seed-derived stratified ~30-task selection (strata from dataset metadata), recorded in the manifest; findings display corpus calibration status (subset-validated vs full-run-validated), and the first official internal finding requires full-run-validated."
    vc: "Selection is reproducible for a seed; manifest carries calibration status; the official-finding path refuses when status is subset-only."
    touchpoints:
      - "harness/corpus/stratify.py:calibration_subset"
    tests:
      - "test_ac2_stratified_selection"
      - "test_ac2_calibration_status"
      - "test_ac2_official_requires_full"
  - id: "AC-3"
    text: "mine_mr converts a merged MR into a task candidate: workspace reset to the parent sha, prompt from ticket text, holdouts from the MR's shipped test additions plus optional groundwork rules; candidates enter status pending-curation."
    vc: "A fixture MR yields a candidate with parent-sha workspace ref, extracted prompt, holdout set, and pending status."
    touchpoints:
      - "harness/corpus/mine.py:mine_mr"
    tests:
      - "test_ac3_mine_candidate"
  - id: "AC-4"
    text: "Corpus admission requires a recorded human curation approval; auto-admission is unrepresentable, and admission additionally requires a clean EVAL-5 flake baseline for the task version."
    vc: "A pending candidate cannot be scheduled; approval events gate admission; a candidate without a ledgered clean baseline is refused even when approved."
    touchpoints:
      - "harness/corpus/registry.py:CorpusManifest"
    tests:
      - "test_ac4_curation_required"
      - "test_ac4_baseline_prereq"
  - id: "AC-5"
    text: "Internal corpora and candidates are written only inside the declared boundary path; writes targeting the instrument repo are refused."
    vc: "Configuring the internal corpus path inside the instrument repo fails validation; boundary writes succeed."
    touchpoints:
      - "harness/corpus/registry.py:CorpusManifest"
    tests:
      - "test_ac5_boundary_enforced"
  - id: "AC-6"
    text: "Corpora are semver-versioned; findings cite corpus version and task shas; a task content change bumps the corpus version and re-triggers baseline."
    vc: "Findings for fixture experiments carry corpus version + shas; mutating a task without a version bump fails manifest validation."
    touchpoints:
      - "harness/corpus/registry.py:CorpusManifest"
    tests:
      - "test_ac6_semver_cited"
      - "test_ac6_mutation_requires_bump"
​
constraints:
  - text: "No task enters a corpus without human curation and a clean flake baseline."
    enforced_by: "test:test_ac4_curation_required"
  - text: "Internal tasks never leave the Koalafi boundary; the instrument repo is not a valid corpus target."
    enforced_by: "test:test_ac5_boundary_enforced"
  - text: "All tasks — public and internal — use the Harbor task format."
    enforced_by: "review"   # consequence of EVAL-1-D005; candidate manifest-validation rule
​
decisions:
  - "EVAL-8-D001"   # stratified ~30 then full-once before official (RESOLVED, jyang)
  - "EVAL-8-D002"   # human curation required for admission (RESOLVED, default)
  - "EVAL-8-D003"   # corpus semver + Harbor format + sha citation (RESOLVED, default)
open_decisions: []
​
policy_proposals: []
predicted_reach: null
expected_verify: "n/a for groundwork; mechanical gate analog: AC suite green including the boundary and admission-gate tests."
---
​
# EVAL-8 — Task corpus tooling
​
## Problem & context
​
The harness is a week; the corpus is the project. Findings are exactly as
sound as the tasks: unversioned tasks make experiments incomparable,
flaky holdouts grade noise, and public tasks carry training-data
contamination. This story builds the corpus machinery — public import
for calibration, monorepo mining for the contamination-free internal
benchmark, and the gates that keep task quality a precondition.
​
## Goal
​
Every experiment cites a semver'd corpus of curated, baseline-clean
tasks; the instrument's numbers are calibrated against published
terminal-bench results before any internal finding is called official;
and the internal benchmark grows from real merged work without
proprietary content ever leaving the boundary.
​
## Residence & runtime
​
Tooling lives in the instrument repo (`harness/corpus/`), inherited from
EVAL-1. Corpus *data* splits by lifecycle: public imports cache locally;
internal corpora write only to the declared Koalafi-boundary path — the
instrument repo is structurally refused as a target [AC-5].
​
## Design
​
**Public import + calibration** [EVAL-1-D004, EVAL-8-D001]. terminal-
bench@2.0 via the Harbor registry, manifest-pinned. The stratified ~30
subset is the fast plumbing-validation loop (our claude-code numbers
should land near published anchors within noise); one full run is the
credibility gate that the manifest records and EVAL-6's official path
checks — the instrument is calibrated before it testifies.
​
**Mining** [AC-3]. A merged MR is a task with free ground truth: reset
to parent sha, prompt from the ticket, holdouts from the tests that
actually shipped, optionally hardened with groundwork rules via the
EVAL-5 plugin. Candidates are only ever *pending*.
​
**Gates** [EVAL-8-D002 + EVAL-5 baseline]. Admission = human curation
approval AND a ledgered clean flake baseline, both mechanical
preconditions. Curation is where task ambiguity, prompt leakage of the
solution, and unrepresentative difficulty get caught — the reviewer of
the corpus is as load-bearing as the reviewer of specs.
​
**Versioning** [EVAL-8-D003]. Corpus semver; content changes bump and
re-baseline; findings cite version + task shas, so a finding's task set
is reconstructible byte-for-byte.
​
## Change surface
​
```mermaid
flowchart LR
  TB[terminal-bench@2.0<br/>Harbor registry] --> IM[import + manifest]
  IM --> CS[calibration_subset ~30<br/>seeded strata]
  MR[merged MR + ticket] --> MN[mine_mr candidate]
  MN --> CG[curation approval<br/>+ EVAL-5 baseline]
  CG --> RG[CorpusManifest semver<br/>boundary-enforced]
  CS --> RG
```
​
> Provenance: [judgment] hand-authored — greenfield.
​
## Acceptance criteria mapping
​
AC-1/AC-2 give the instrument its calibration story and make "calibrated"
a recorded, checkable status rather than a claim. AC-3 turns git history
into candidate tasks. AC-4 makes quality a gate, not a hope. AC-5
enforces the IP boundary structurally. AC-6 makes every finding's task
set reproducible.
​
## Expected post-state
​
`bench corpus import` and `bench corpus mine` functional against
fixtures; a pending candidate demonstrably unschedulable; manifest
validation live; calibration status flows into EVAL-6 renders.
​
## Out of scope
​
Non-code and multi-service task shapes; automated difficulty scoring;
corpus sharing/export tooling; capability-vs-regression tagging beyond a
manifest attribute (attribute ships, taxonomy work later).
​
## Open questions
​
None — local ledger clean, inherited EVAL-1-D001 resolved (verdi-bench).
Gate clear.