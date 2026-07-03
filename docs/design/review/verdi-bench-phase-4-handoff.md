# verdi-bench — Phase 4 planning handoff

**For:** a fresh session that will *plan* Phase 4 (connective tissue — wire the
pipelines). **Written:** 2026-07-03, at the close of Phase 3. **You have no prior
context — this brief plus the in-repo documents it points to are self-contained.**

---

## 1. Orientation

verdi-bench is a benchmark-grade A/B evaluation instrument for agent stacks
(pre-registered experiments, paired hermetic trials, insulated arms,
deterministic-first grading, an identity-blind advisory LLM judge, a hash-chained
event ledger). Its credibility is its own correctness — a green fake path that can
be reward-hacked is worse than an absent real path. **Read `CLAUDE.md` (repo root)
first — its directives override convenience and this brief.**

**Authoritative in-repo documents (read these before planning):**
- `docs/design/review/verdi-bench-review-consolidated.md` — the ~100-finding
  audit. **§5 is the six-phase remediation plan; Phase 4 is your scope.** §3 is
  the findings register (IDs referenced below); §6 is the readiness gate.
- `docs/design/review/verdi-bench-phase-2-plan.md` and
  `verdi-bench-phase-3-plan.md` — the prior plans; **mirror their structure and
  rigor when you write the Phase 4 plan.** `verdi-bench-phase-3-handoff.md` is the
  brief that seeded Phase 3 — this document mirrors it.
- `docs/design/review/review.decisions.ndjson` — resolved review decisions
  (REVIEW-D-1..D-10). `docs/design/specs/eval{2,3,7,8,9}.spec.md` — the AC
  contracts for the stages Phase 4 wires (judge, plan, review, corpus, process).
  `docs/design/specs/eval{N}.decisions.ndjson` — per-story decisions (Phase 3 added
  `EVAL-{3,4,6,8}-D-P3-1`).

**Where the program is:**
- **Phase 1 (results integrity)** — merged to `main` (PR #6). Chain verified at
  stage entries, lock hardened, task-content commitment, real grade path.
- **Phase 2 (real execution path)** — merged to `main` (PRs #7/#8). Real hermetic
  metered Harbor trials, honest cost guard/quarantine/baseline.
- **Phase 3 (the §7.2 fail-closed sweep)** — **complete on branch
  `claude/verdi-bench-phase-3-plan-5ilatc`, not yet merged to `main`.** Judge,
  process, review, analyze, and corpus fail closed; the one-event property sweep
  covers every ledgered operation (12 entrypoints); PL-14 folded. Plus a
  max-effort self-review pass that fixed 8 further defects. See §9 for the
  branch/merge decision you must make first.
- **Phase 4 (connective tissue)** — your scope. Wires the spec-promised verbs that
  are built-but-inert.

**Working method (non-negotiable, per CLAUDE.md):** reproduce-first (a failing
test before each fix), `make verify` green before every commit, atomic commits
whose messages explain *why*, single-responsibility, import-linter contracts stay
green. Ask the human on direction-setting decisions; give a recommendation with
trade-offs, don't open-endedly ask.

---

## 2. Phase 4 scope & exit (from consolidated review §5)

> **Phase 4 — connective tissue: wire the pipelines.** Every spec-promised
> capability reachable from `bench`, no test-only kwargs.
> **Exit:** a complete fake-engine experiment runs
> plan → run → grade → judge → analyze → review → process **end-to-end through
> `bench` verbs only**, with judge calibration and process reporting appearing in
> the rendered findings.

Phase 3 made every stage *fail closed*; Phase 4 makes every stage *reachable*. The
systemic diagnosis §2.2 stands: **"correct primitives, missing connective tissue"**
— `judge_pair`, `build_review_packet`, `select_for_review`, `reviewed_kappa_items`,
`kappa_report`, `process_kappa_by_dimension`, `score_telemetry_correlation`,
`is_schedulable`, `record_calibration_run`, `CalibrationVariance`, and
`EscalationConfig` all have **zero production callers** (re-confirmed against the
current tree — see §3). The verbs `judge`, `review build`, `process score`, and any
corpus admission verb do not exist.

---

## 3. Findings Phase 4 covers, by subsystem — with current status

⚠️ **The review's line numbers are from commit `01641cd` (pre-Phase-1) and are
stale; Phases 1–3 shifted the tree substantially. Re-verify every finding against
the current tree before planning — this is exactly what Phase 2 and Phase 3 did
(see each plan's "Re-verification" section). Do not trust a finding is still open,
or still at the cited line, without looking.** The re-verification below was run at
the close of Phase 3; all cited symbols still have **zero production callers**.

### Judge (EVAL-2) — `harness/judge/`
- **JD-9:** judge unwired — **no `bench judge` verb**; `judge_pair` has one caller,
  the Phase-3 property entrypoint (`client.py`), never a CLI. Spec-derived canary
  literals (arm names, model ids) never reach `validate_identity_free`;
  `EscalationConfig` is referenced nowhere; `kappa_by_class` re-hardcodes `0.6/20`
  so the D006 escalation seam is dead. **Wire `bench judge`:** derive canaries from
  the locked spec, thread `EscalationConfig` through calibration.
- **JD-11:** `orders:"single"` is accepted and never flagged, though the spec
  requires "single allowed only for smoke runs; **flagged**". Flag it.
- **JD-5:** `pairs_from_ledger` joins on `comparison_id=None` (verdicts without ids
  pair with each other); duplicate judge verdicts last-write-win; `CANT_JUDGE`
  enters kappa as an ordinary category. Dedupe; exclude/report `CANT_JUDGE`; join
  consistently. *(Ties to the review calibration slice.)*

### Review (EVAL-7) — `harness/review/`
- **RV-3:** the pipeline is unwired — the CLI docstring promises `build` but only
  `record`/`reveal` exist; `build_review_packet`, `select_for_review`,
  `reviewed_kappa_items`, `kappa_report` have **zero production callers**. Nothing
  records which arm was "Response 1/2", so the human's `--winner A` maps to the
  judge's A/B only by unrecorded convention. **Wire `bench review build`:**
  sampling → packet with per-comparison response-order randomization, **recording**
  the Response-1/2 ↔ arm mapping.
- **RV-2:** `bench review reveal` hardcodes `arm_identities={"1":"arm_a","2":"arm_b"}`
  — the ledgered unblinding is fiction. Reveal must read **real** arm identities
  from the trial records; no per-comparison response-order randomization exists in
  `review/` today (only the judge side randomizes).
- **RV-6:** `actual_arm` is never supplied by the CLI and no lookup exists, so
  guess accuracy is structurally 0.0 whenever a reviewer answers
  `--arm-recognized`. Supply `actual_arm` (and `task_class`) from the recorded
  mapping.
- **RV-4:** `kappa_by_class` computes raw pooled Cohen's kappa over the
  disagreement-heavy reviewed set, **bypassing the D003 IPW seam** (`review/kappa.py`,
  whose only consumer is EVAL-9). Route calibration through the IPW seam.
- **RV-5:** IPW weights use nominal `0.2`, not the realized `ceil(0.2n)/n`; up to
  ~1.67× floor over-weighting; `kappa_report` doesn't expose `floor_prob`.
- **RV-7:** the mandatory/floor boundary is recoverable from the packet's two
  independently id-sorted blocks — the id-order reset marks exactly which items are
  disagreements. Order the packet without a recoverable boundary.
- **RV-9 (remainder):** Phase 3 closed the fail-closed parts (refuse duplicate/
  post-reveal verdicts, existence-check the comparison id). **Left to Phase 4:**
  unifying the first-vs-last verdict join (`reveal_comparison` takes the *first*
  judge verdict while the kappa joins take the *last*), requiring integrity for
  calibration, and threading `task_class` through the CLI (every CLI-recorded
  verdict currently lands in `"default"`).

### Process (EVAL-9) — `harness/process/`
- **PR-5:** AC-5/AC-7 reporting is unreachable — `bench process` registers only
  `record`, though its docstring documents `score`; `process_kappa_by_dimension`
  and `score_telemetry_correlation` have **no production caller**; the analyze
  process section and render carry **no kappa, correlations, or `style_only`**,
  though plan M5 requires them. **Wire `bench process score`** (the isolated judge
  path already exists as `score_trial_process`) and surface kappa / correlations /
  `style_only` in analyze.

### Corpus (EVAL-8) — `harness/corpus/`
- **CO-8:** the mine→admit pipeline is disconnected end-to-end — `mine` writes a
  standalone candidate JSON; `admit_task` requires the candidate to *already* be a
  manifest `TaskEntry`, but no code inserts a mined candidate into any manifest,
  and **no `admit` CLI verb exists**. Phase 3 made `admit_task` *ledger*
  `task_admitted` when it runs; Phase 4 must add the **admission verb** and the
  **mine → manifest insertion** (with content sha) so admission is reachable.
- **CO-2:** `is_schedulable` has **zero production callers**; `bench run` reads
  `tasks.yaml` and never consults a manifest, so pending/quarantined tasks run,
  grade, and feed findings. Consult `is_schedulable` at `bench run`. **Prerequisite
  (D-6 Phase-4 boundary):** the Phase-1 D-6 resolution deferred *"full
  manifest+cache-as-source (holdout import into the cache, is_schedulable at run)
  to Phase 4 because the cache does not yet store holdouts."* So CO-2 depends on the
  corpus cache first storing holdouts — a real, non-trivial prerequisite to scope,
  not a one-line wiring.
- **CO-7:** `corpus review` prints holdout **paths** only (the human gate can't do
  the solution-leakage check it exists for); the approver is `getpass.getuser()`
  with **no attestation or self-approval bar**; nothing saves the manifest after an
  in-memory admission. Show holdout content/diff; add approver-≠-miner attestation.
- **Calibration-run producer (CO-4 Phase-4 half):** Phase 3 added the
  `calibration_run` event and the `ledger_calibration_run` function, but nothing
  invokes it from the run path. Wire the **run-path hook** so a calibration run
  actually ledgers (the fence-binding to the ledgered status is Phase 5, AN-2).

### Plan (EVAL-3) — `harness/plan/`
- **PL-1:** the power gate never consults the design — `n = variance_source.n_tasks`
  (default 50); `spec.repetitions` and corpus size are ignored; omitting
  `hypothesized_effect` skips the gate entirely with nothing ledgered. Compute
  power at the **real N** (`repetitions` × corpus size) and ledger gate-skips.
- **PL-5:** `CalibrationVariance` has **no loader** — it is a thin holder with a
  `TODO(EVAL-8)`; nothing in `harness/corpus/` reads manifest calibration runs into
  a variance source, so every production lock is `assumption_based_mde`. Build the
  loader from the manifest calibration runs (now ledgered as `calibration_run`,
  Phase 3) into `bench plan`.
- **PL-12:** `hypothesized_effect` is unbounded — negative values are always
  "underpowered", values > 1 always pass. Bound it.

### Carry-forward from the Phase 3 code review (fold into the owning slices)
- **RV-9 `comparison_id` reliability (correctness, review slice).** Phase 3's RV-9
  gate — `record_human_verdict` refuses a verdict whose `comparison_id` has no
  matching `judge_verdict` — assumes `comparison_id` is *populated*. But
  `comparison_id` is `Optional` on both `judge_pair` and the `Verdict` schema
  (defaults `None`). **`bench judge` (JD-9) must thread a deterministic
  `comparison_id` onto every verdict, and `bench review build` (RV-3) must record
  the Response↔arm mapping keyed by it**, or the RV-9 gate makes the whole review
  flow unusable against a real ledger. This is the natural place to make the gate
  reliable. Also refine RV-9's *CANT_JUDGE-counts-as-judged* corner (a human
  verdict on a `CANT_JUDGE` comparison passes the gate but still drops from kappa)
  as part of the JD-5/RV-4 "exclude CANT_JUDGE from kappa" work.
- **Shared provider-failure → reason mapper (cleanup, judge/process slices).**
  `CantJudgeReason` (`judge/schema.py`) and `CantScoreReason` (`process/score.py`)
  re-implement the same `ProviderTimeout→timeout / ProviderRefusal→refusal /
  ProviderError→provider_error / parse` mapping inline, with two parallel enums —
  the exact drift (`parse` vs `unparsed`) Phase 3 fixed. `analyze` already models
  the right altitude (`cant_analyze_reason(exc)`), and `run/interleave` has
  `_reason_for(exc)`. When Phase 4 touches judge calibration and process reporting,
  extract one shared `provider_failure_reason(exc)` mapper both stages call.
- **Ledger read consolidation (efficiency, review slice).** Phase 3's review guards
  made `record_human_verdict` re-read/parse the whole ledger 4× (`assert_chain` +
  three `find_events`) and `reveal_comparison` 4× — reintroducing the O(N²) batch
  cost the O(1) append was designed to avoid. Bounded today (the reviewed kappa set
  is a small sample), but Phase 4 reworks the review calibration/join path heavily;
  when it does, verify + `read_events` **once** and filter the parsed list in the
  predicate helpers.

---

## 4. Infrastructure to build on (don't reinvent)

Phase 3 built seams Phase 4 should reuse rather than rebuild:
- **The entrypoint registry + property sweep** (`harness/entrypoints.py`,
  `tests/test_eval3_property.py`): every ledgered operation registers an entrypoint
  (with an optional `prepare` hook for preconditioned ops) and the sweep asserts an
  **explicit expected set**. **Every new Phase-4 verb that appends an event must
  register an entrypoint and be added to `EXPECTED_ENTRYPOINTS`**, or the sweep
  fails closed.
- **New event types + producers (Phase 3):** `task_admitted` (emitted by
  `admit_task`), `calibration_run` (via `corpus/ledger_ops.ledger_calibration_run`),
  `subset_draw` (via `ledger_subset_draw`, wired into `corpus subset --ledger`),
  `cant_analyze`. Phase 4 wires their **production callers** (the admission verb, the
  run-path calibration hook) — the events and typed constructors already exist.
- **`corpus/ledger_ops.py`** — the home for ledgered corpus lifecycle operations
  (keeps the `CorpusManifest` model pure). Add admission/calibration wiring here or
  in `admit.py`, consistent with the existing split.
- **`run_analyze`** (`analyze/cli.py`) — reusable analyze orchestration (returns the
  render path or `None`-after-`cant_analyze`); the process-reporting slice (PR-5)
  extends the render it produces.
- **The fail-closed model:** `cant_grade`/`CANT_JUDGE`/`CANT_SCORE`/`cant_analyze`
  and the per-stage reason enums. New verbs inherit this discipline — an attempted
  operation is one ledgered event or a loud refusal.
- **The IPW kappa seam** (`review/kappa.py`) — arithmetic verified sound (§7),
  currently consumed only by EVAL-9; RV-4/RV-5 route review calibration through it.
- **`EscalationConfig`** (`schema/judge_config.py`) and the `kappa_by_class`
  threshold — the D006 seam JD-9 must feed.

---

## 5. Decisions

Phase 4 is the widest-surface phase; several direction-setting choices need
explicit human resolution **before** the owning slice (per CLAUDE.md), each
recorded as a `resolved` event in the owning `evalN.decisions.ndjson`. Candidates
to raise (give a recommendation + trade-offs, don't open-endedly ask):

- **The Response-1/2 ↔ arm mapping seam (RV-2/RV-3/RV-6/RV-9).** How is the
  blinded mapping recorded so reveal, guess-accuracy, and kappa joins are correct?
  A recorded field on the `review build` event keyed by `comparison_id` is the
  natural shape (it must be a versioned contract — the reveal and EVAL-9 process
  scoring both key off it). This is the load-bearing Phase-4 decision; it also
  resolves the RV-9 `comparison_id` carry-forward (§3).
- **Holdouts in the corpus cache (CO-2 / D-6 Phase-4 half).** The Phase-1 D-6
  resolution deferred storing holdouts in the cache to Phase 4. Decide the storage
  shape and whether `bench run`/`bench grade` switch to the manifest as task source
  now, or keep the lightweight `task_commitment` and only add `is_schedulable`
  gating. This is a genuine prerequisite with a migration story.
- **Approver-≠-miner attestation (CO-7).** What attestation binds a curation
  approval to a human who is not the miner? A recommended mechanism + a
  self-approval bar.
- **`bench judge` / `bench process score` / `bench review build` / corpus `admit`
  verb surfaces (JD-9, PR-5, RV-3, CO-8).** Confirm the verb names and their inputs
  (which read the *locked* spec, which take operational flags) before wiring.
- **Carry-forward decisions still pending:** REVIEW-D-5 (degenerate kappa) is
  Phase 5; there is no *new* Phase-4-blocking review-level decision, but the
  per-story seams above each need a recorded resolution.

---

## 6. Current baseline & how to verify

- Fast suite: `uv run pytest -m "not docker" -q` → **318 passed, 3 deselected**
  (the 3 docker-marked tests) at the close of Phase 3. `make verify` (full suite +
  import contracts) is the mandatory gate; **3 import-linter contracts kept**.
- Real-container suite: `uv run pytest -m docker` runs on the CI `docker` job; the
  local dev environment has no reachable daemon (docker-marked tests skip locally,
  CI-proven). Phase 4 is mostly non-Docker wiring; the end-to-end exit
  (plan→…→process through `bench`) can run on the fake engine without Docker.
- `uv run pytest --ac-report` recomputes AC coverage (a global union, not a
  per-story guarantee — XC-2, a Phase 6 item). Phase 4 wiring should move several
  currently-untested ACs (per-story reporting, escalation, admission) into reach.
- **Contract note:** completing the `.importlinter` source lists (XC-5) is Phase 6,
  but Phase 4 adds many cross-module wires (CLI → judge/review/process/corpus) — keep
  the three live contracts (`harbor-confined-to-seam`,
  `grade-has-no-llm-clients`, `ledger-writes-only-via-events`) green, and route all
  new ledger writes through `events.py` typed constructors.

---

## 7. Suggested Phase 4 shape (mirror the phase-2/phase-3 plans)

Plan it as ordered, mostly-independent, atomic slices. The stages are more coupled
than Phase 3 (the end-to-end exit needs judge → review → process to interlock), so
order to unblock the exit test last. A reasonable slicing:

1. **`bench judge` + calibration wiring** (JD-9, JD-11, JD-5): the verb; canaries
   from the locked spec; deterministic `comparison_id` on every verdict (unblocks
   the RV-9 carry-forward); `EscalationConfig` through calibration; flag
   `orders:"single"`; dedupe + exclude `CANT_JUDGE` from kappa.
2. **`bench review build` + reveal-from-reality** (RV-3, RV-2, RV-6, RV-9,
   RV-7): sampling → packet with per-comparison order randomization, recording the
   Response↔arm mapping (the §5 decision); reveal reads real identities; supply
   `actual_arm`/`task_class`; non-recoverable mandatory/floor ordering.
3. **Review calibration through IPW** (RV-4, RV-5): route `kappa_by_class` through
   the IPW seam with realized inclusion probabilities; consolidate the ledger reads
   (efficiency carry-forward).
4. **`bench process score` + analyze reporting** (PR-5): the verb; surface
   kappa / correlations / `style_only` in the analyze render (extends `run_analyze`).
5. **Corpus admission pipeline** (CO-8, CO-7, CO-4 run-hook): mine → manifest
   insertion; the `admit` verb (emits the Phase-3 `task_admitted` event); curation
   review shows content/diff; approver-≠-miner attestation; run-path calibration
   hook.
6. **Corpus-as-task-source + `is_schedulable` at run** (CO-2, D-6 Phase-4 half):
   holdouts in the cache; `bench run` consults `is_schedulable`. *(Scope the
   prerequisite decision first.)*
7. **Power gate at real N + variance loader** (PL-1, PL-5, PL-12):
   `CalibrationVariance` loader from ledgered calibration runs into `bench plan`;
   power at `repetitions` × corpus size; bound `hypothesized_effect`; ledger
   gate-skips.
8. **End-to-end exit test:** plan → run → grade → judge → analyze → review →
   process on a fake-engine fixture, **through `bench` verbs only**, asserting judge
   calibration and process reporting appear in the render.

Each slice: reproduce-first test proving the capability is unreachable today →
reachable after; register an entrypoint for each new ledgered verb; `make verify`
green before each commit. Fold the three Phase-3 carry-forwards (§3) into slices 2/3
(review), 1/4 (shared reason mapper), and 3 (ledger reads).

**Shared reason-mapper (cleanup):** when slices 1 and 4 touch judge/process reason
handling, extract one `provider_failure_reason(exc)` used by both, replacing the
two parallel enums' mapping bodies (keep the enum *values* as the closed set).

---

## 8. Phase 4 exit criteria (restate for your plan)

- A complete fake-engine experiment runs **plan → run → grade → judge → analyze →
  review → process end-to-end through `bench` verbs only** (no test-only kwargs), in
  a single ordered test.
- Judge calibration (kappa by class, escalation) and process reporting (kappa /
  correlations / `style_only`) **appear in the rendered findings**.
- Reveal discloses the **real** arm identities (from the recorded mapping), and
  guess accuracy is a measured number, not a structural 0.0.
- Admission is reachable via a `bench` verb, emitting the Phase-3 `task_admitted`
  event; `bench run` refuses a non-`admitted` task via `is_schedulable`.
- The power gate runs at the design's real N; a `CalibrationVariance` loader feeds
  `bench plan` from ledgered calibration runs.
- Every new ledgered verb is registered in the one-event property sweep
  (`EXPECTED_ENTRYPOINTS`); `make verify` green; no import-linter regressions; any
  new event type / event-field change carries a decisions-ledger entry + migration
  note.
- The RV-9 `comparison_id` gate is reliable end-to-end (judge threads it, review
  build records the mapping), and CANT_JUDGE is excluded from kappa rather than
  pooled.

---

## 9. First thing to settle: branch / merge

Phase 3 is complete but **unmerged** on `claude/verdi-bench-phase-3-plan-5ilatc`
(`origin/main` is at the Phase 2 merge). Before planning Phase 4, decide with the
human:
- **Merge Phase 3 to `main` first**, then branch Phase 4 from `main` (cleanest
  history; Phase 4 builds on a merged base — recommended, matching how Phase 3 was
  cut from a `main` that already contained Phase 2); **or**
- **Stack Phase 4 on the Phase 3 branch HEAD** (if Phase 3's PR is still open).

Your session will be given its own branch directive — reconcile it with the above.
Do **not** start Phase 4 from `main` alone without Phase 3, or you'll be missing
the fail-closed seams, the new event types/producers, and the entrypoint-registry
changes Phase 4 builds on.

---

*Prepared at the end of Phase 3. Treat the consolidated review as the map, this
brief as the orientation, and re-verify everything against the live tree before
committing to a plan.*
