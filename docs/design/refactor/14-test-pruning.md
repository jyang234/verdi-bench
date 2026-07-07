# 14 — Test-suite pruning pass

**Status:** accepted 2026-07-06 (operator, interactive). Tests-only: `harness/`,
`scripts/`, `images/`, fixtures under `tests/fixtures/data/` and
`tests/fixtures/otlp/`, `conftest.py`, and `tests/ac_coverage.py` are all
untouchable. The operator's approval of this spec is the CLAUDE.md "human
agreement" for changing tests — but every individual deletion/rewrite still
carries its own stated justification and proof (below). Review model: the
orchestrator reads the full diff.

## Goal

Prune or re-aim the migration-era scaffolding so that every surviving pin
points at a **contract**, not an **accident of migration** — with zero loss
of regression defense, proven per change. Expected yield ~40–70 test
functions pruned/rewritten; runtime is not a goal (the suite is fast); the
goal is eliminating future false alarms and dead oracle code.

## The evidence bar (per deletion or rewrite — no exceptions)

1. **Purpose-expired rationale** stated in the commit message: what the test
   guarded during migration, and why that purpose no longer exists.
2. **Mutation proof**: temporarily break the behavior the test guarded (the
   plant-proof pattern used throughout this program), demonstrate that a
   **surviving** test fails (name it in the commit message), revert the
   plant. For a REWRITE, additionally show the new assertion goes red under
   the same plant. A change that cannot produce a surviving-test failure is
   NOT prunable — it is load-bearing; keep it and record the adjudication.
3. Category-per-commit; `make verify` green at every commit.

## Untouchable inventory (the regression spine — adjudicate nothing here)

- All **AC-mapped** tests (`test_ac<N>_*`) — collection-enforced against
  specs; also `tests/ac_coverage.py` and `conftest.py`.
- All **golden/byte suites**: `test_golden_*`, `test_forensics_report_golden`,
  the OTLP mapping goldens + their drift counter-tests, `test_analyze_card`
  byte pins.
- All **meta-tests/guards**: detector fixture-coverage, import-contract
  completeness + planted-import cases, the refusal-enumeration AST scan, the
  blinding blob-scan coverage tests, the tunnel sweep, the holdout-filename
  equality test, readme/docs-consistency, the one-event property sweep.
- All **live e2es** (docker- and browser-marked), all **hypothesis** property
  tests, the **CI guards** (`test_*_ci_guard`, `tests/fixtures/{docker,browser,proxy}.py`).
- All **live parity properties** (permanent two-surfaces-must-agree
  invariants, NOT scaffolding): author-preview ≡ lock-preflight
  (`test_author_preview_parity`), render-fence ≡ observer-fence
  (`test_fence_parity`), the Python↔JS mirror parity tests
  (`test_serve_mirrors`), engine cross-contract suite rows.
- `scripts/shakedown/` (not pytest; charter-governed).

## Categories to adjudicate

**A — Expired migration oracles (`tests/test_ledger_view.py`).** The
hand-rolled oracle functions are verbatim ports of production code deleted in
P1; the equivalence purpose expired when consumers migrated (their suites +
the goldens pin behavior). Adjudication: assertions that pin REAL semantics
(latest-grade-wins ordering, sha-hoist reader rule, quarantine-set
membership, trial_story shape) are REWRITTEN as direct semantic assertions
against the golden ledger / rich scenario — the oracle code itself is
deleted. Assertions that only ever said "new == old" are deleted outright.
The `verify=True` chain-assert test and any projection test consumed by
other suites stay.

**B — Old-literal byte pins.** Tests asserting registry-derived text
reproduces pre-refactor hand-written strings byte-for-byte (the P4D importer
help/error pins in `test_corpus_benchmarks.py` and any siblings found by
grepping for byte-comparisons against inline legacy literals). Rewrite to
semantic assertions: derived-from-registry, names every registry key,
refusal names the offending value. Do NOT relax anything asserting a
LEDGERED or GOLDEN-PINNED string.

**C — Facade-parity halves.** Where a strangler-era test asserts
`facade(...) == direct(...)` and the facade is now the only production
caller (grep callers to prove it), the parity half is tautological — delete
it, keep the substantive half (determinism, structure). Named seed:
`test_findings_html.py`'s facade-parity component. The `report.py` facade
re-export test (names resolve) STAYS — out-of-tree/test importers still use it.

**D — Vacuity hunt (strengthens the suite).** Sweep for tests that
structurally cannot fail: assertions on values the type system or a
`ClassVar` declaration now guarantees, try/except-wrapped asserts,
`assert x or True`-shaped accidents, tests whose fixture already forces the
asserted outcome. Each finding: delete with the vacuity demonstrated in the
commit message (show the mutated code it fails to catch). If a vacuous test
reveals a REAL coverage gap, do not fix production — report it prominently
and leave the test in place with the gap documented in the report.

**E — Same-layer duplicate pairs.** Candidate pairs where two suites assert
the same bytes at the same seam (e.g. eval4 engine argv pins vs hermetic
builder argv pins). Default is KEEP BOTH — different failure modes (builder
regression vs wiring regression) justify both layers. Prune only where the
audit proves the pair is genuinely same-seam-same-bytes AND one side is
non-AC; document every kept pair's distinct-failure-mode rationale in the
report (not in code).

## Deliverables

1. Category-per-commit implementation meeting the evidence bar.
2. A final inventory table in the report: every adjudicated test →
   kept / rewritten / deleted, with reason and (for deletions) the named
   surviving guard.
3. Net counts: test functions and LOC before → after; suite runtime delta.
4. Gates: `make verify` green at every commit; final
   `env -u FORCE_COLOR make shakedown` green; docker/browser suites only if
   any marked test was touched (expected: none).
