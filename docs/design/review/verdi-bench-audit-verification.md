# verdi-bench consolidated-audit remediation — independent verification

**Date:** 2026-07-03 · **Verifies:** `verdi-bench-review-consolidated.md` (the
~100-finding register, §5 six-phase plan, §6 readiness gate) against `main` at
`6dd285b` (the Phase 6 merge, PR #12).
**Method:** every register finding independently re-verified against the current
tree by nine adversarial passes (one per subsystem plus cross-cutting), with the
key fixes exercised live rather than read: full pipeline driven end-to-end
through subprocess `bench` verbs; a hand-tampered lock line replayed against
every downstream verb; forged `holdout_results.json` replayed against the
default grade path; ~30 live adversarial probes (injection, traversal,
self-approval, degenerate kappa, alias false-passes, IPW weights, swapped
response frames); and 11+ mutation tests (each fixed pathology re-introduced on
a scratch copy to confirm the shipped suite actually fails). CI history for the
docker job inspected on GitHub (main @ `6dd285b`: `3 passed`, real containers).
Suite state at verification: **427 passed, 3 skipped (the docker-marked tests;
no local daemon), 3 import-linter contracts kept** — `make verify` green.

---

## 1. Verdict

**The six phases genuinely executed their scoped plans, and the instrument's
core integrity claims now hold under adversarial testing.** Phase exits 1–5
pass empirically; Phase 6 passes two of its three exit criteria. The remediation
was honest work: no gamed tests, no hardcoded expectations, no vacuous
enforcement was found anywhere — several fixes survived deliberate mutation
attempts, and the enforcement infrastructure (AC hook, import contracts, docker
CI) demonstrably fails on planted violations.

**But "every finding in the register is properly addressed" is not true.** The
§5 remediation plan itself never scheduled ~10 register findings into any
phase; those remain open today, without the recorded debt decisions the repo's
"human decides" directive requires. One Phase-6 exit criterion ("README claims
are all mechanically true") demonstrably fails. And this verification found a
small number of new defects introduced or exposed by the remediation, one of
which (`bench judge` non-idempotency) has real statistical consequences on
operational re-runs.

Register tally (per-finding statuses in §3): **81 fixed** (including
recorded-decision resolutions) · **14 partial** · **7 not fixed** (GR-12,
RN-17, PL-9, PL-10, PL-11, PL-13, PR-9 — all but PL-9/PL-10 are P3).

---

## 2. What was verified live (not just read)

- **Phase 1 exit** — all four criteria PASS: a forged all-pass
  `holdout_results.json` produced **zero** grade events on the default
  (docker-runner) path; one flipped hex digit in the lock line's `spec_sha256`
  was refused by `run`, `grade`, `judge`, `analyze`, `review`, `process`, and
  corpus admission (`ChainIntegrityError` naming the broken link) and by
  `verify-chain` (exit 1); re-lock exits 2; `bench anchor` ledgers a
  `chain_anchor` event.
- **Phase 4 exit** — the complete fake-engine experiment ran end-to-end through
  subprocess `bench` verbs only (plan → run → grade → judge → review
  build/record/reveal → process score/record → analyze → verify-chain OK), with
  judge calibration, process diagnostics, `[computed]`/`[judgment]` claim tags,
  and the ADVISORY-tier warning all present in the rendered findings, and the
  reveal disclosing the *recorded* response map (a genuinely randomized
  `{"1": "treatment", "2": "control"}` on this run).
- **Official fence** — `bench analyze --official` without ledgered calibration
  refused (exit 2) and ledgered `cant_analyze(calibration_incomplete)`; the
  fence reads calibration from **ledgered** `calibration_run` events, so a
  hand-edited manifest status no longer passes (AN-2/CO-4).
- **CI** — all three jobs green on main @ `6dd285b`; the docker job's log shows
  `3 passed, 427 deselected` — the real-container tests ran, not skipped. The
  three docker tests are adversarial (the grade one *pre-plants a forged
  all-pass file and asserts the container's FAIL wins*; the harbor one asserts
  redaction of an env-injected literal no built-in pattern matches).
- **Decisions** — REVIEW-D-1..D-10 all recorded `resolved` with rationale and
  attribution in `review.decisions.ndjson`; implementations match D-2
  (version-keyed quarantine), D-3 (keep-labeled), D-4 (confidence enum +
  legacy-float reader), D-5 (degenerate kappa undefined-insufficient), D-6
  (task commitment pinned into the lock; post-lock `tasks.yaml` swap refused by
  run/grade/judge), D-7 (CI `py312-compat` gate), D-8/9/10 (request-file
  delivery, run-config, per-trial proxy attribution). Exception: **D-1's two
  prescribed actions were never executed** (§4.2).

## 3. Register outcome by subsystem

| Subsystem | Fixed | Partial | Not fixed | Notes on the non-fixed |
|---|---|---|---|---|
| Grade GR-1..13 | 9 | 3 (GR-8, GR-11, GR-13) | 1 (GR-12) | GR-8/11: the transient set is {OSError, timeout, exit 125}, but a daemon-down-with-CLI-present outage exits **1** → terminal: a single daemon outage still quarantines a healthy task version and permanently blocks regrade with no override — the audited scenarios resurface through the exit-code mapping. GR-13 fixed in code, no owning test. GR-12 (`actor="unknown"` swallow) untouched. |
| Run RN-1..18 | 16 | 1 (RN-18) | 1 (RN-17) | RN-17 (corrupt telemetry JSON → silent `{}`) fell out of every phase plan. RN-18's `contention_caveat`-from-inert-knob sub-item dropped from Phase-6 scope without a record; the other five sub-items fixed. |
| Plan/lock/ledger PL-1..14 | 10 | 0 | 4 (PL-9, 10, 11, 13) | None of the four was scheduled by §5. PL-10 is live: 3 arms and **duplicate arm names** are accepted, and `run`'s `arm_map` would silently collapse duplicates. PL-13 is live: `append_event` still concatenates onto a truncated final line (reachable via `bench plan`). |
| Judge JD-1..13 | 11 | 2 (JD-1, JD-10) | 0 | JD-1: the packet is correctly unchanged per D-1, but the decision's two actions — the "outcome-blind"→identity-blind wording fix and the analysis-side judge↔holdout correlation disclosure — were never implemented. JD-10: confidence half fixed; the Google API key still rides the URL query string. |
| Analyze AN-1..12 | 11 | 1 (AN-11 sub-item) | 0 | All five Phase-5 reproduced pathologies have mutation-verified regression tests. Two clauses lack owning tests: AN-1's swapped-frame attribution (regressing to the `arms[0]` assumption passes the whole suite) and AN-10's coverage/deployed `n_boot` match. |
| Corpus CO-1..9 | 8 | 1 (CO-7) | 0 | CO-7: the approver≠miner bar compares **free-text labels** — a miner holding any authorized curator key can sign as a different approver name and self-approve (probe-confirmed). CO-2/CO-9 gates are opt-in flags per recorded minimal-scope decisions; official findings are backstopped by the analyze fence, exploratory runs are not. |
| Review RV-1..9 | 5 | 4 (RV-3, 7, 8, 9) | 0 | RV-7's shuffle fix is correct but unowned by any test. RV-9's first-vs-last verdict-join unification and integrity-required calibration were deferred *to* Phase 4 by the handoff, then dropped without a record. RV-3 residual: `kappa_report` (D003's floor-only sensitivity) still has zero production callers; EVAL-7 D003 is still `raised`, never resolved. |
| Process PR-1..9 | 7 | 0 | 1 (PR-9) | PR-1..4/7/8 verified live (list-shaped scores → exactly one `CANT_SCORE` event, etc.). PR-9 (vendor-overlap `False`-when-unknown; chars/4 context gate) was never scheduled. |
| Cross-cutting XC-1..7 | 6 | 1 (XC-7) | 0 | Enforcement is real: the AC hook aborts collection per-story on planted violations; completed import contracts catch planted forbidden imports; the two vacuous tests now demonstrably discriminate. XC-7 residual is §4.1. |

## 4. What is NOT properly addressed

### 4.1 Phase 6 exit criterion 3 fails — the README Usage block

The criterion is "README claims are all mechanically true." Load-bearing claims
(test counts, docker suite + CI, AC enforcement, harbor behavior ×8, grade
defaults, provisional-decision wiring) all verify. But:

- `bench review record … --winner A` — **mechanically false**; the CLI accepts
  `1|2|TIE|CANT_JUDGE` and exits 2 on `A` (reproduced).
- `corpus approve` is still absent from Usage — the audit named this omission
  verbatim in XC-7.
- The Phase-4 verbs (`judge`, `review build`, `process score`,
  `corpus calibrate|admit`) are undocumented, so the documented review flow
  cannot execute as written (`record` refuses without a prior `build`).
- Lines 5 and 19 still say "outcome-blind" (see 4.2).
- `test_readme_consistency.py` pins only the import-contract count; every other
  README claim can drift silently.

### 4.2 Resolved decisions whose actions were never executed

- **D-1 (JD-1):** recorded as `docs-only-D002-stands` with two actions —
  master-plan/README wording ("outcome-blind" → identity-blind) and an
  analysis-side disclosure that `judge_preference` correlates with
  `holdout_pass_rate` by design. Neither exists in the tree (grep-verified); no
  phase ever scheduled them; no D002 clarification was recorded against EVAL-2.
- **EVAL-7 D003** (kappa sensitivity) remains `raised`; the coded floor-only
  sensitivity estimator (`kappa_report`) reaches no render.

### 4.3 Register findings dropped from the plan without a debt decision

GR-12, GR-13(test), RN-17, RN-18(one sub-item), PL-9, PL-10, PL-11, PL-13,
JD-10(key-in-URL), PR-9, AN-11(`experiment_id` sub-item), RV-9(join
unification). The pattern: §5 scheduled ~90% of the register; the remainder
simply fell out. Under the repo's own directives these need either fixes or
recorded accept-as-debt decisions. Highest-value among them: **PL-10**
(duplicate arm names, P2) and **RN-17 / GR-8-residual** (silent or
misclassified infra failure).

### 4.4 New defects found by this verification

1. **`bench judge` is not idempotent** (independently confirmed by two passes):
   a re-run doubles `judge_verdict` events, violating the verb's own docstring.
   Downstream, live-confirmed: the same comparison is selected and rendered
   twice in the review packet; `realized_floor_prob` computes over
   duplicate-inflated n (wrong IPW weights, e.g. 0.25 vs the correct 0.333);
   per-task judge-preference n inflates; and the RV-9 reveal(first)/kappa(last)
   divergence becomes production-reachable. `process score` has the
   skip-already-scored guard; `judge` (and `review build`, which appends
   duplicate `review_packet_built` events) needs the same.
2. **`bench anchor` fails open on a broken chain** — it anchored a tampered
   ledger (exit 0) and appended a `chain_anchor` event on top of the broken
   chain. `bench plan` likewise appends to a pre-existing ledger without
   verifying it (and onto a truncated one, compounding PL-13).
3. **CO-7 self-approval bypass** — commit e99876e says "refuse signer==miner"
   but the code refuses *approver-string == miner-string*; any authorized-key
   holder can relabel themselves and self-approve their own mined task
   (probe-confirmed). D-P4-3 knowingly chose key-not-identity binding, but the
   limitation is under-disclosed relative to what CO-7 existed to prevent.
4. **Import-contract blind spot (demonstrated):** `from .engines import harbor`
   planted in the unlisted, conventionally-empty `harness/run/__init__.py`
   evades both contract 1 and the AST seam test (which ignores `ImportFrom`
   member names).
5. **CI docker job would green on all-skip** — `pytest -m docker` exits 0 if
   the daemon probe fails; nothing asserts the 3 tests actually ran. This is
   the exact silent-skip mode XC-1 existed to kill, one config line from
   regressing.
6. **Grade-runner exit-code misclassification** (the GR-8/GR-11 residual in
   §3): daemon-down-with-CLI-present exits 1 → terminal `container_failure` →
   healthy-version quarantine + permanent regrade block with no override.
7. **The `grader` stamp is write-only** — grade events record
   `grader: "local"`, but analyze's ADVISORY banner keys only on the *trial's*
   provenance tier, so an explicit `--runner local` grade over trusted-engine
   trials feeds findings with no ADVISORY flag (auditable only in the raw
   ledger).
8. **Judge rubric file content is not lock-committed** — a post-lock rubric
   swap changes judge behavior and is detectable only post hoc via
   `rubric_sha256` in verdict provenance, unlike the task swap, which is
   refused.
9. Doc rot (minor, but this is an instrument whose docs are part of the
   contract): the consolidated review's §6 rows for tamper-evidence, sha-lock,
   and cost-ceiling still describe the pre-Phase-1 holes as open (they are
   fixed); "12 entrypoints" is stale (13); `review/packet.py` still describes
   the removed disagreements-first ordering; `run/settings.py`'s docstring
   contradicts its own fail-loud behavior; `test_eval4_harbor_egress.py`'s
   docstring claims docker-marked proxy/kill tests that don't exist; two
   decision records (EVAL-8-D-P4-1's `holdout_ref`, EVAL-3-D-P4-1's
   "ledgered events" loader wording) describe implementations that differ from
   what landed.

## 5. Production-readiness by advertised capability

| Advertised capability | Verdict |
|---|---|
| Pre-registered experiments; sha-locked spec; power at real N | **Ready.** Lock is genesis, TOCTOU-free, re-lock refused, task content committed (D-6), gate-skips ledgered. Caveats: PL-10 (duplicate arm names accepted), write-side verbs don't chain-verify first. |
| Hash-chained ledger; tamper-evident; verified downstream | **Ready.** Every gating verb chain-verifies before trusting content (verified by live tamper). Caveats: `anchor`/`plan` write-side (§4.4-2), PL-13 truncated-append. |
| Hermetic container trials (Harbor) | **Ready for real trials** — request delivery, digest pinning + `--pull=never`, kill-on-timeout, key injection with capture-time redaction, all CI-tested in real containers. Honest boundary: the metering proxy is a declared JSONL contract with per-trial auth attribution — **no reference proxy implementation ships in the repo**, so AC-3 metering is contract/fixture-proven, not live-proven. |
| Insulated arms; secret/identity redaction | **Ready.** Whole-workspace scan-everything redaction, full PEM bodies, injected-literal keys, fail-loud on unreadable files. One test gap: the arm-payload canary channel has no dedicated refusal test (prompt and fake_behavior do). |
| Deterministic-first grading | **Ready.** Fresh-copy docker grading defeats forged results (proven in CI with a planted forgery); fail-closed reasons enumerated; version-keyed quarantine per D-2. Caveats: daemon-down misclassification (§4.4-6); local runner is ADVISORY-stamped but the stamp is unread by analyze (§4.4-7). |
| Identity-blind advisory LLM judge | **Ready, mislabeled.** Fail-closed envelope covers every probed failure shape; packet fencing is content-keyed and unforgeable; calibration through the IPW seam with realized weights; D-4/D-5 honored. Caveats: re-run duplication (§4.4-1); docs still say "outcome-blind" (D-1 unexecuted); Google key in URL (JD-10). |
| Analysis: paired bootstrap, effect sizes, fenced official renders | **Ready.** Per-comparison, recorded-frame, task-clustered judge preference; corpus-identity-bound official fence reading ledgered calibration; `cant_analyze` fail-closed; claim tags enforced by an owning test; ADVISORY surfaced. |
| Corpus lifecycle (import/mine/curate/admit/calibrate) | **Ready with eyes open.** Idempotent import preserving calibration, successor rule, traversal-safe, boundary checked on real write destinations, mine→approve→admit ledgered end-to-end. Caveats: self-approval label bypass (§4.4-3); schedulability gate is opt-in outside official renders. |
| Human review + process rubric | **Ready.** Blinded packet with recorded per-comparison response maps, pre-reveal verdict capture strictly enforced, measured guess accuracy, process diagnostics rendered. Caveats: RV-7 ordering unowned by tests; RV-9 join residuals. |
| Self-enforcing test/CI infrastructure | **Ready.** AC coverage enforced per story at collection (aborts on planted violations), complete import contracts (catch planted imports), genuine docker CI, honest 3.12 gate. Caveats: §4.4-4/5. |

**Before the first official finding** (unchanged from the audit's §6, and still
binding): EVAL-1-D008 (A/A null experiments + coverage selfcheck) is still
`raised`, deliberately unresolved — `bench selfcheck` does not exist and the
master plan forbids building the hard requirement until D008 resolves. The
nullsim machinery it needs now runs at the realized N (AN-4 fixed), so
resolving D008 is unblocked. Additionally, `corpus calibrate --kind full` is
operator self-attestation — nothing binds a calibration run's task coverage to
the corpus size (recorded deferral).

## 6. Bottom line

- **"Fully addressed in all six phases": ~90% true.** The phases did what they
  scoped, the exits hold (5½ of 6), the integrity story survives adversarial
  probing, and the enforcement infrastructure is real. The remaining 10% is
  precisely enumerable: 7 not-fixed findings, 14 partials, 2 resolved decisions
  with unexecuted actions, and the §4.4 list this verification adds.
- **"Production-ready for all capabilities advertised": yes for running
  experiments end-to-end on both the fake and real paths** — with the §5
  caveats — **but not yet for issuing an official finding** (EVAL-1-D008 open
  by design), **and the README itself is the weakest advertised artifact**
  (one false example, missing verbs, stale "outcome-blind").
- Recommended cheapest-first: fix the README Usage block + wording (an hour,
  closes Phase 6 exit 3 and D-1's doc half); add the `bench judge` /
  `review build` idempotency guards; add the daemon-down exit-1 → transient
  reclassification; record explicit accept-or-fix decisions for the §4.3 list;
  then resolve EVAL-1-D008 before any official run.

## Appendix — Phase 7 disposition

*Appended 2026-07-04, after Phase 7 merged (PR #15, `4d6d645`), per the Phase 7
exit criterion that this document — the register of record — state each open
item's terminal disposition. The item-by-item audit backing this table is
`verdi-bench-phase-7-readiness-assessment.md` §2; commits cite the merged
history.*

Every row of the Phase 7 disposition map is terminally dispositioned: a fix
with an owning test that fails on regression, an owning test alone (for
real-but-unowned fixes), or a recorded decision event.

| Item | Disposition | Owner |
|---|---|---|
| PL-13 truncated-final-line append | fix + test (`31489b9`) | `test_eval3_chain.py` (byte-identical refusal) |
| `bench anchor` fail-open (§4.4-2) | fix + test (`1f35f44`) | `test_eval3_cli.py` (exit 1, nothing written) |
| `bench plan` unverified append (§4.4-2) | fix + test (`1f35f44`) | `test_eval3_lock.py` |
| judge / review build re-run duplication (§4.4-1) | fix + tests (`5e77e84`) | `test_eval2_cli.py`, `test_eval7_build.py`; e2e composition owned by `test_e2e_pipeline.py::test_fake_pipeline_rerun_yields_byte_identical_analysis_inputs` (added post-merge, gap §3.3 of the readiness assessment) |
| GR-8/GR-11 daemon-down misclassification | fix + tests (`8039108`) | `test_eval5_container.py`, `test_eval5_grade.py` |
| terminal-override recourse (D-P7-2) | fix + tests (`b3a7634`) | `test_eval5_grade.py`, `test_e2e_pipeline.py`; official-render disclosure assertion added post-merge (`test_eval6_analyze.py::test_dp7_2_override_disclosure_in_official_render`, gap §3.4) |
| `grader` stamp write-only / ADVISORY hole (§4.4-7) | fix + test (`bc4e998`) | `test_eval6_analyze.py` |
| GR-13 owning test | test (`bc4e998`) | `test_eval5_baseline.py` |
| PL-9 validation duplication | fix + tests (`4cea334`) | `test_eval3_schema.py` (both loader paths) |
| PL-10 duplicate arm names (D-P7-1) | fix + test (`4cea334`) | `test_eval3_schema.py` (`ArmNameError`) |
| PL-11 `==` in rule DSL | fix + test (`4cea334`) | `test_eval3_schema.py` |
| GR-12 `actor="unknown"` ×7 (D-P7-7) | fix + tests (`c3c7f28`) | `test_eval_phase7_actor.py` |
| RN-18 inert `--concurrency` (D-P7-5) | removal (`b9d445a`) | grep-clean; README enforced by `test_readme_consistency.py` |
| JD-10 key in URL | fix + test (`8e9eba1`) | `test_eval2_providers.py` |
| RN-17 corrupt telemetry | fix + test (`7df1126`) | `test_eval4_harbor_request.py` |
| PR-9 vendor-overlap + context gate | fix + tests (`d9594aa`) | `test_eval9_process.py`, `test_eval2_providers.py` |
| rubric not lock-committed (§4.4-8, D-P7-6) | fix + tests (`9ea8dc7`) | `test_eval_phase7_rubric.py`, `test_eval6_analyze.py` (fence + legacy caveat) |
| RV-9 join asymmetry; integrity-less calibration | fix + tests (`98c9954`) | `test_eval7_review.py` |
| RV-7 ordering + stale docstrings | test + docs (`4a5e47f`) | `test_eval7_review.py` (delete-the-shuffle fails) |
| RV-8(c) reference note | spec note (`4a5e47f`) | `eval7.spec.md` |
| RV-3 `kappa_report` unrendered / EVAL-7 D003 | fix + test (`73acce1`) | `test_eval7_calibrate.py`; D003 `resolved` in `eval7.decisions.ndjson` |
| CO-7 self-approval label bypass (§4.4-3, D-P7-3) | fix + tests (`fd892a8`) | `test_eval8_corpus.py` (relabel probe refused) |
| D-1 actions + D002 clarification | docs + decision event (`607dca2`) | `test_eval6_analyze.py` (both renders); `eval2.decisions.ndjson` |
| XC-7 README Usage + consistency test | fix + strengthened test (`b0f8d0d`) | `test_readme_consistency.py` (two-direction) |
| §6 stale rows, N-3 drift, stale docstrings, `shutil`×3, AN-11 | docs + decision events (`5b5cb7e`) | consolidated review §6; `eval8`/`eval3`/`eval6` decisions files |
| package-`__init__` import blind spot (§4.4-4) | fix + planted-violation test (`96734cd`) | `test_eval4_seam.py` (AST member names own the channel; the import-linter source extension was verified unable to catch it — plan judgment call §5-7) |
| CI docker all-skip green (§4.4-5) | fix + guard test (`d6107f1`) | `ci.yml` `VERDI_REQUIRE_DOCKER=1`; `test_eval_phase7_ci_guard.py` |
| owning tests: AN-1 swapped frame, AN-10 `n_boot`, arm-payload canary, RN-15, RN-16 | tests (`4fefefb`) | `test_eval6_analyze.py`, `test_eval4_insulation.py`, `test_eval4_interleave.py` |
| EVAL-1-D008 selfcheck + official gate | feature + fence (`72e4155`, tightened `90e2a16`) | `test_eval_phase7_selfcheck.py`; D008 `resolved` + `amended` in `eval1.decisions.ndjson` |

Not reopened (recorded decisions stand): CO-2/CO-9 opt-in gating, metering-
proxy unit coverage, JD-13 deterministic labels, CIMethod/`fractional_score`,
quarantine keying, judge packet content.

Residue judgments recorded here for the register:

- **"outcome-blind" wording**: fixed in the live docs (master plan §1, README,
  and — post-merge — `CLAUDE.md`). The term is deliberately retained in the
  frozen pre-registered specs (`eval1/2/9.spec.md`) and the historical
  implementation plans: retro-editing pre-registered documents would be a
  worse defect than the stale term. Grep for "outcome-blind" is expected to
  hit only those frozen documents and the audit-history docs.
- The 2026-07-04 readiness assessment (`verdi-bench-phase-7-readiness-
  assessment.md`) found four exit-criteria gaps; all four are closed by the
  same change series that adds this appendix (this appendix itself closes
  §3.1).
