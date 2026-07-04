# verdi-bench — Phase 7 readiness assessment

**Date:** 2026-07-04 · **Assesses:** Phase 7 implementation (merged to `main`
via PR #15, `4d6d645`) against `verdi-bench-phase-7-implementation-plan.md`
(merged via PR #14) and the Phase 7 plan's exit criteria; plus an independent
code-quality and design assessment of the whole instrument.
**Method:** five parallel adversarial audits — one per slice group
(7A–7B, 7C–7D, 7E–7G, 7H–7I) verifying every planned commitment against code,
tests, docs, and git history, and one plan-blind quality sweep of
`harness/` + `tests/`. All owning tests were re-run; the full gate was run on
the merged tree.

## 1. Verdict

**Phase 7 was implemented substantially as specified — roughly 95% of the
plan's commitments landed exactly as written, with every deviation either
pre-authorized by the plan, formally recorded as a decision amendment, or
minor and test/doc-side.** No test tampering, weakened assertions, or gamed
exits were found anywhere in the 30-commit, 85-file diff. The two post-plan
commits (`7cc9b41`, `90e2a16`) are honest tightening, not scope creep: the
second closes a genuine gap the plan itself missed (selfcheck could certify a
CI method the render never deploys) and its D008 seed deviation is recorded
as an `amended` decision event per the public-seams directive.

**Four exit-criteria items are not fully met** (§3) — all documentation- or
test-coverage-side, none of them code-behavior defects. The disposition map
itself is fully bound: every row has an owning test or a recorded decision.

**Capability readiness: the instrument is ready to run experiments
end-to-end on both fake and real paths, and — new with Phase 7 — the
pre-Phase-7 blockers to issuing an official finding are closed** (selfcheck
exists and gates the official render; rubric content is lock-committed;
writers fail closed; judge/build are idempotent; curation approval is
identity-bound). The residual risk register is short, known, and recorded.

Verified live on the merged tree:

- `make verify`: **503 passed, 3 skipped** (the three docker-marked tests —
  no daemon in this container; CI runs them under `VERDI_REQUIRE_DOCKER=1`),
  import-linter **3 contracts kept, 0 broken**.
- `--ac-report`: AC-1…AC-9 covered; AC coverage is enforced at collection
  time and the enforcement is itself tested against planted violations.

## 2. Plan alignment by slice

| Slice | Verdict | Notes |
|---|---|---|
| Commit 0 (decision records) | ✅ as specified | All 7 D-P7 raised+resolved pairs + D008 resolved match Appendix A; plus a later D008 `amended` event (90e2a16) — additive, self-documenting. |
| 7A-1 truncated-append refusal | ✅ as specified | `chain.py:89-111` under the flock, named error, byte-identical file asserted. |
| 7A-2 anchor fail-closed | ✅ as specified | `verify_chain` before any write; CLI test asserts exit 1, no anchor line, no event. |
| 7A-3 plan chain-verify | ✅ (minor) | Owning test drives `lock_experiment`, not the CLI exit code (CLI mapping exists at `harness/cli.py:85-93`). |
| 7A-4 judge/build idempotency | ⚠ deviated | Per-verb zero-event tests landed; the planned **e2e "pipeline twice → byte-identical analysis inputs" test is missing** (§3.3). Ledgered `response_map` reuse per judgment call §5.5. |
| 7B-1 daemon probe | ✅ (minor) | Exit-1 daemon-down now transient; probe-level and batch-level tests, though the batch test monkeypatches `preflight` rather than wiring the two together. |
| 7B-2 `--retry-terminal` | ⚠ deviated | Code complete incl. `override_of` on every re-attempt path and disclosure in both renders; **the official-render disclosure line is asserted by no test** (§3.4). |
| 7B-3 ADVISORY on grader stamp + GR-13 | ✅ as specified | Judgment call §5.3 exact; GR-13 owning test present. |
| 7C-1 single validation source | ✅ as specified | `_prevalidate` gone; five named errors pinned on both loader paths; `ArmNameError`; `==` refused naming the operator. |
| 7C-2 `resolve_actor()` | ⚠ deviated | Semantics exact across all seven CLIs; the promised seven-verb parametrized refusal test narrowed to `bench plan` + unit tests; `actor="unknown"` defaults survive on the `run_analyze`/`run_selfcheck_cli` **library** seams (`analyze/cli.py:18,89`). |
| 7C-3 `--concurrency` removed | ✅ as specified | Zero code references; stale AC text remains in `eval4.spec.md:70`. |
| 7D-1 Google key in header | ✅ as specified | Only OpenAI classifies provider-side context overflow (7D-3 note below). |
| 7D-2 corrupt telemetry loud | ✅ as specified | One sensible in-code-documented refinement (completed-trial-only downgrade). |
| 7D-3 `spec` required + overflow | ✅ (partial edge) | Mechanism + OpenAI mapping done; Anthropic/Google overflow bodies still map to generic `provider_error`. |
| 7D-4 rubric lock-commitment | ✅ as specified | Hash byte-identical both sides; legacy warn/caveat posture and fence check exactly per D-P7-6's refinements; full swap-refusal test suite. |
| 7E-1 join unification + integrity filter | ✅ as specified | Hand-built duplicate-ledger test; reveal and kappa agree last-wins. |
| 7E-2 RV-7 ordering test + docs | ✅ as specified | Both planned mutations (delete shuffle, mandatory-first) are caught. |
| 7E-3 sensitivity kappa rendered | ✅ as specified | `kappa_report` has its production caller; seam-owned test; D003 resolved event appended. |
| 7F-1 identity-bound keyring | ✅ as specified | The verification's relabel probe lands as the reproduce-first test and is refused; legacy list format exits cleanly. |
| 7G-1 D-1 wording + D002 disclosure | ⚠ deviated | Note lives in the Provenance section (commit says so openly); **"outcome-blind" survives in `CLAUDE.md:10`** — the exit grep is not clean (§3.2). |
| 7G-2 README mechanically enforced | ✅ (minor) | Two-direction typer introspection; planted-verb exercise injects into the registered set rather than registering a dummy command — slightly weaker than planned. |
| 7G-3 record truth-up | ✅ as specified | §6 rows flipped with evidence, N-3 amendments, AN-11 record, dead imports gone. |
| 7H-1 import blind spot | ✅ via pre-authorized contingency | AST member-name test owns the channel; the import-linter source extension was verified ineffective (per commit `96734cd`) and dropped per judgment call §5-7; the `_CASES` extension was dropped with it (not explicitly called out). |
| 7H-2 CI all-skip hard-fail | ✅ as specified | `VERDI_REQUIRE_DOCKER=1` in CI; collection-time raise; guard unit-tested. |
| 7H-3 five owning tests | ✅ as specified | All five discriminating (mutation reasoning checked per test). |
| 7I-1 selfcheck | ✅ + recorded amendment | Seed = `spec.seed` (not `sub_seed`) per the D008 `amended` event — binds the validated method to the deployed one by construction; stale `sub_seed` wording remains in `selfcheck.py:11-12` docstring. |
| 7I-2 official fence | ✅ + tightening | Fifth check requires a **current** passed selfcheck (staleness rejection is a post-plan improvement); refusal names `bench selfcheck`; master plan §7.7 still reads "pending D008" (stale). |

## 3. Exit-criteria gaps (the honest-reporting list)

Ranked; none are code-behavior defects, all are cheap to close.

1. **The "Phase 7 disposition" appendix is missing.** Both the Phase 7 plan
   (exit criterion 1) and the implementation plan §4 require
   `verdi-bench-audit-verification.md` to gain a short appendix stating each
   row's disposition. The file was last touched at `138455f` (pre-Phase-7)
   and contains no Phase 7 content. The disposition map *is* satisfied in
   substance (every row traced to an owning test or decision event in this
   assessment), but the required record was never written.
2. **The 7G-1 exit grep is not clean.** "outcome-blind" survives in
   `CLAUDE.md:10` — a live directive file, not a historical audit doc — plus
   frozen spec/impl-plan files (defensible as pre-registered history, but no
   judgment call was recorded for leaving any of them).
3. **The 7A-4 e2e exit test is missing.** "The fake-engine e2e pipeline run
   twice end-to-end yields byte-identical analysis inputs" exists nowhere in
   `tests/`; only the two per-verb zero-event tests landed. This was the exit
   test that proves the idempotency guards compose.
4. **The 7B-2 official-render disclosure is unasserted.** D-P7-2 refinement
   (b) requires the override count in *both* renders; the code does both
   (`report.py:991-993`, `:1048-1050`) but only the exploratory render is
   tested. A regression deleting the official-render line passes the suite.

## 4. Deviations that are fine (recorded or pre-authorized)

- **7I seed binding** deviates from D008's letter (`sub_seed` → `spec.seed`)
  but strengthens its intent (validated method ≡ deployed method; a
  selfcheck cannot certify a stream analyze never uses) and is recorded as a
  D008 `amended` event — exactly the public-seams process working.
- **7H-1 contract fallback** was pre-authorized in the plan (judgment call
  §5-7) and the commit message records the verification that motivated it.
- **Post-plan commits** `7cc9b41` (four real defects, each with a
  reproduce-first test, additions-only test diff) and `90e2a16` (staleness
  rejection + method binding) are principled improvements, properly tested.
- **7A-2/7A-3 landed as one commit** (`1f35f44`) instead of two — an atomic
  pairing of two chain-verify-before-write fixes; harmless.

## 5. Code quality and design assessment (plan-blind sweep)

| Dimension | Rating | Basis |
|---|---|---|
| Architecture | **A−** | Real subsystem discipline (median module ~130 lines, one concern each); the three import-linter contracts are meaningful and kept (Harbor seam, no-LLM-in-grading, typed-constructor-only ledger writes). Docked for: `analyze/report.py` at ~1,200 lines spans extraction, fence, and two renderers — a god-module by the repo's own "split before merging" rule; no independence contract among the 12 subsystems (convention, not enforcement); a stale duplicate bootstrap in `plan/power.py:75-87` with a dead TODO. |
| Correctness discipline | **A−** | Zero bare `except:`; the only three `except Exception` sites are deliberate fail-closed ledgered outcomes; validation errors are named and located throughout (`TruncatedLedgerError` names the line, `ActorResolutionError` names the flag). Docked for: `actor="unknown"` programmatic defaults on `analyze/cli.py:18,89` contradicting the module's own policy; `cant_grade(plugin_error)` discards the exception detail. |
| Test rigor | **A** | 503 behavior-driven tests; byte-level ledger tampering, fault-injected atomic-append, 4-thread concurrency, planted-violation enforcement tests; mocks confined to Docker/HTTP boundaries; AC coverage enforced at collection and anti-gaming-tested against itself; no `xfail`, no skipped-to-green anywhere; Phase 7 diff shows no weakened or deleted assertions. |
| Integrity design | **B+** | Chain mechanics sound: canonical JSON pinned once for append and verify, prev-hash under flock, fsync-before-unlock, truncation refused pre-hash, anchors refuse non-verifying chains and catch truncate-to-prefix. The grade is capped by the honestly-in-code-documented threat model: same-user whole-file rewrite window, head-line rewrite undetectable without a later anchor, and a v1 anchor store on the same filesystem. Tamper-*evident* as advertised, not tamper-proof — and it says so. |
| Operational readiness | **B** | The `make verify` gate is real and fast (~35 s non-docker); every CLI fails closed with ledgered refusals; docker CI hard-fails on all-skip. Held back by: the metering proxy remains a declared contract with no reference implementation (contract/fixture-proven, not live-proven); Anthropic/Google provider error taxonomies are thinner than OpenAI's; `CLAUDE.md` still calls analyze/review/process/corpus "scaffolded" when all four are fully implemented and tested — the project's own front door understates it. |

Notable intrinsic strengths beyond the ratings: determinism is engineered,
not aspirational — wall-clock exists at exactly two injectable seams,
all statistical randomness flows through sha256-namespaced `sub_seed`, and
the no-LLM-in-grading rule is mechanically enforced. Module docstrings cite
the master-plan sections they implement and often record why a previous
design was wrong.

Notable intrinsic weaknesses (with owners suggested in §7): the
`difflib.HtmlDiff._default_prefix` reset in `review/packet.py:78-83` depends
on a private CPython attribute to achieve byte-identical re-renders;
`GradingContainer.preflight` uses a `getattr` fallback so a future runner
lacking the method silently opts out of probing (mildly fail-open); repeated
daemon-down invocations accumulate duplicate transient `cant_grade` events
per trial.

## 6. Capability readiness

Updating the verification doc's §5 table for the post-Phase-7 reality:

| Capability | Pre-Phase-7 caveats | Status now |
|---|---|---|
| Pre-registration / sha-lock | PL-10 duplicate arms; write-side verbs unverified | **Closed** (7C-1, 7A-2/3) — plus rubric content now lock-committed (7D-4). Ready. |
| Hash-chained ledger | anchor/plan fail-open; PL-13 | **Closed** (7A). Ready within the recorded threat model (§5). |
| Hermetic trials (Harbor) | metering proxy contract-proven only | **Unchanged, recorded.** Ready for real trials; AC-3 metering still not live-proven. |
| Insulated arms / redaction | arm-payload canary untested | **Closed** (7H-3c). Ready. |
| Deterministic grading | daemon-down misclassification; ADVISORY stamp unread | **Closed** (7B-1/3) + ledgered override recourse (7B-2). Ready. |
| Identity-blind judge | re-run duplication; "outcome-blind" docs; key in URL | **Closed** (7A-4, 7D-1, 7G-1) except the `CLAUDE.md:10` wording. Ready. |
| Analysis / official renders | no selfcheck; D008 open | **Closed** (7I): official fence now requires rubric-hash agreement and a current passed selfcheck. **The "not yet for official findings" verdict is lifted.** |
| Corpus lifecycle | CO-7 self-approval bypass | **Closed** (7F). Residuals stand as recorded decisions: keyring trust = local operator state (D-P7-3); `calibrate --kind full` self-attestation (recorded deferral); schedulability opt-in outside official renders. |
| Human review + process | RV-7 unowned; RV-9 joins | **Closed** (7E). Ready. |
| Test/CI self-enforcement | package-`__init__` blind spot; all-skip green | **Closed** (7H). Ready. |

**Bottom line: the instrument is credibly ready for official findings**, with
a residual register of exactly the items in §3 (process debt) plus three
known-and-recorded trust boundaries (metering proxy, keyring issuance,
calibration self-attestation). Nothing found in this assessment silently
weakens a grade, a verdict, a chain, or a render.

## 7. Recommended follow-ups (cheapest first)

1. Write the "Phase 7 disposition" appendix into
   `verdi-bench-audit-verification.md` (closes §3.1; this document's §2
   table is most of the content).
2. Fix `CLAUDE.md:10` "outcome-blind" → "identity-blind"; update its
   "scaffolded" subsystem claim while in the file (closes §3.2).
3. Add the two missing test assertions: e2e pipeline-twice byte-identity
   (§3.3) and the official-render override disclosure line (§3.4).
4. Remove the `actor="unknown"` defaults on the `analyze` library seams; fix
   the stale `selfcheck.py` docstring and master-plan §7.7 wording; delete
   the stale bootstrap TODO in `plan/power.py`.
5. Split `analyze/report.py` (extract / fence / render_md / render_html) —
   the one structural debt item that will keep compounding.
6. Consider: Anthropic/Google context-overflow classification; `SpecError`
   in the `bench plan` except list for clean exit-2 on schema refusals;
   a dedupe guard for repeated daemon-down `cant_grade` events.
