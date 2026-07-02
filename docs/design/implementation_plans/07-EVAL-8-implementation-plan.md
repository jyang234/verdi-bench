# 07 — EVAL-8 Implementation Plan: Task corpus tooling — public import, calibration subset, monorepo mining, curation gate

**Read with:** `00-EVAL-1-master-plan.md`, `Eval8.spec.md`, `eval8.decisions.md`. **Two slices** (per EVAL-1's build-order note): **Slice A** (public import + calibration subset, §M1–M2) lands early, alongside EVAL-4, because instrument calibration needs real tasks; **Slice B** (mining + curation gates, §M3–M5) lands after the instrument is proven and requires EVAL-5's `flake_baseline`.

## 1. Gate status

**CLEAR.** D001 public calibration scope = stratified ~30-task terminal-bench subset for plumbing validation, then **one full run before the first official finding**; D002 mined tasks require **human curation approval** before admission (flake baseline alone insufficient; auto-admission unrepresentable); D003 corpus semver, findings cite corpus version + task shas, internal tasks authored in Harbor task format. Inherited EVAL-1-D001 and EVAL-1-D004 (both, public-first) RESOLVED. **Slice A can go to Opus immediately after EVAL-3.**

## 2. Objective

The harness is a week; the corpus is the project. Every experiment cites a semver'd corpus of curated, baseline-clean tasks; the instrument is calibrated against published terminal-bench numbers before any internal finding is called official; the internal benchmark grows from real merged work with proprietary content never leaving the Koalafi boundary.

## 3. Module layout & public symbols

```
harness/corpus/public.py     import_terminal_bench
harness/corpus/stratify.py   calibration_subset
harness/corpus/mine.py       mine_mr
harness/corpus/registry.py   CorpusManifest
```

Tooling lives in the instrument repo; corpus **data** splits by lifecycle: public imports cache locally; internal corpora write only to the declared Koalafi-boundary path — the instrument repo is **structurally refused** as a target [AC-5].

## 4. Data contracts

**4.1 `CorpusManifest`** [AC-1, AC-2, AC-6]: `{corpus_id, semver, kind: public|internal, dataset: {name: terminal-bench, version: 2.0}?, tasks: [{task_id, sha, format: harbor, status: admitted|pending-curation|quarantined, baseline_ref?, plugins?}], calibration: {subset: {seed, strata, task_ids}, status: none|subset-validated|full-run-validated, runs: [...]}, boundary_path}`. Validation rules: mutating task content without a semver bump fails validation; a bump re-triggers baseline [AC-6]; `boundary_path` resolved via realpath and refused if inside the instrument repo tree [AC-5]. All tasks — public and internal — in **Harbor task format** [D003; consequence of EVAL-1-D005; implement as the manifest-validation rule the spec anticipates, upgrading `enforced_by: review`].

**4.2 Events added.** `curation_approval` — `{candidate_id, task_sha, approver, notes}`; corpus admission mutations recorded via manifest versioning + ledger events so a finding's task set is reconstructible byte-for-byte.

**4.3 Candidate (from mining)** [AC-3]: `{workspace_ref: parent_sha, prompt: <ticket text>, holdouts: [<MR's shipped test additions>], groundwork_rules?: [...], status: pending-curation}`.

## 5. Implementation sequence

### Slice A — early (parallel with EVAL-4)

**M1 — Public import.** `import_terminal_bench`: pull terminal-bench@2.0 through the Harbor registry into a local cache + manifest recording dataset version and per-task shas; re-import against the same dataset version is **idempotent** (sha comparison, no duplicates, no churn) [AC-1]. Test: `test_ac1_public_import_manifest`.

**M2 — Calibration subset + status.** `calibration_subset(manifest, seed)`: seed-derived stratified ~30-task selection, strata from dataset metadata (category/difficulty fields as available), proportional allocation, reproducible for a seed; recorded in the manifest [AC-2, D001]. Calibration status lifecycle: `none → subset-validated → full-run-validated`; the **first official internal finding requires `full-run-validated`** — the enforcement point is EVAL-6's official-render path (its plan M4 already checks this manifest field; land the field + refusal test here, wire the render check there). Intent check for subset validation: our claude-code numbers should land near published anchors within noise — record the comparison in the calibration run entry `[plan choice: record, don't gate on it; the gate is the full run]`. Tests: `test_ac2_stratified_selection`, `test_ac2_calibration_status`, `test_ac2_official_requires_full`.

### Slice B — after EVAL-5 and instrument proven

**M3 — Mining.** `mine_mr(mr_ref, ticket_text)`: workspace reset to the **parent sha**; prompt extracted from ticket text; holdouts = the MR's shipped test additions (diff restricted to test paths), optionally hardened with groundwork rules via the EVAL-5 plugin; candidate enters `pending-curation` [AC-3, D002]. Candidates are only ever *pending* out of mining. Test: `test_ac3_mine_candidate` (fixture MR ⇒ parent-sha workspace ref, extracted prompt, holdout set, pending status).

**M4 — Admission gate.** Admission = recorded human `curation_approval` event **AND** a ledgered clean EVAL-5 flake baseline for that task version — both mechanical preconditions; auto-admission unrepresentable (no code path admits without the approval event); a pending candidate cannot be scheduled (wire the same quarantine/eligibility hook EVAL-5 M3 exposed to the EVAL-4 scheduler) [AC-4, D002]. Curation is where task ambiguity, prompt leakage of the solution, and unrepresentative difficulty get caught — the reviewer of the corpus is as load-bearing as the reviewer of specs; give the CLI a `bench corpus review <candidate>` view that surfaces prompt + holdouts + diff to make that review real. Tests: `test_ac4_curation_required`, `test_ac4_baseline_prereq` (approved but baseline-less ⇒ refused).

**M5 — Boundary + versioning enforcement.** Boundary write refusal per §4.1 [AC-5]; semver + sha citation flowing into findings (EVAL-6 provenance block consumes `corpus version + task shas` — verify end-to-end with a fixture finding) [AC-6]. Tests: `test_ac5_boundary_enforced`, `test_ac6_semver_cited`, `test_ac6_mutation_requires_bump`.

**M6 — CLI.** `bench corpus import`, `bench corpus subset`, `bench corpus mine`, `bench corpus review/approve` — functional against fixtures.

## 6. Test plan summary

| AC | Tests | Slice |
|---|---|---|
| AC-1 | public_import_manifest (idempotency) | A |
| AC-2 | stratified_selection, calibration_status, official_requires_full | A |
| AC-3 | mine_candidate | B |
| AC-4 | curation_required, baseline_prereq | B |
| AC-5 | boundary_enforced | B (rule lands with manifest in A) |
| AC-6 | semver_cited, mutation_requires_bump | B |

## 7. Constraints checklist at merge

- No task enters a corpus without human curation **and** a clean flake baseline ✓ (M4)
- Internal tasks never leave the Koalafi boundary; instrument repo not a valid corpus target ✓ (M5)
- All tasks in Harbor task format ✓ (manifest-validation rule, M-manifest — promoted from `review` to a schema test)

## 8. Definition of done

`bench corpus import` and `bench corpus mine` functional against fixtures; a pending candidate demonstrably unschedulable; manifest validation live (mutation-without-bump fails); calibration status flows into EVAL-6 renders; boundary refusal proven.

## 9. Risks / watch items

- terminal-bench metadata may not expose clean strata — degrade to whatever metadata exists, record the strata definition in the manifest so the selection stays auditable.
- Mining's "prompt from ticket text" is the leakage hot spot: curation review must specifically look for solution leakage in prompts — say so in the review-view checklist text.
- Out of scope, resist creep: non-code/multi-service task shapes, automated difficulty scoring, corpus export tooling, capability-vs-regression taxonomy (ship the manifest attribute only).
