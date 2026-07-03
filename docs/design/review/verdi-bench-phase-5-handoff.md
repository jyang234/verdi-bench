# verdi-bench — Phase 5 planning handoff

**For:** a fresh session that will *plan* Phase 5 (statistical correctness — make
the findings describe the experiment that actually ran). **Written:** 2026-07-03,
at the close of Phase 4. **You have no prior context — this brief plus the in-repo
documents it points to are self-contained.**

---

## 1. Orientation

verdi-bench is a benchmark-grade A/B evaluation instrument for agent stacks
(pre-registered experiments, paired hermetic trials, insulated arms,
deterministic-first grading, an identity-blind advisory LLM judge, a hash-chained
event ledger). Its credibility is its own correctness — a finding that reports a
statistic the data cannot support is worse than an absent finding. **Read
`CLAUDE.md` (repo root) first — its directives override convenience and this
brief.**

**Authoritative in-repo documents (read these before planning):**
- `docs/design/review/verdi-bench-review-consolidated.md` — the ~100-finding
  audit. **§5 is the six-phase remediation plan; Phase 5 is your scope.** §3 is
  the findings register (IDs referenced below); §6 is the readiness gate; §7 is
  "verified sound — protect with regression tests."
- `docs/design/review/verdi-bench-phase-{2,3,4}-plan.md` — the prior plans;
  **mirror their structure and rigor when you write the Phase 5 plan.**
  `verdi-bench-phase-{3,4}-handoff.md` are the briefs that seeded Phases 3/4 —
  this document mirrors them.
- `docs/design/review/review.decisions.ndjson` — resolved review decisions
  (REVIEW-D-1..D-10; **D-3, D-4 are resolved-for-Phase-5, D-5 is pending your
  confirmation** — see §5). `docs/design/specs/eval{2,3,6,9}.spec.md` — the AC
  contracts for the stages Phase 5 corrects (judge, plan, analyze, process).
  `docs/design/specs/eval{N}.decisions.ndjson` — per-story decisions (Phase 4
  added `EVAL-{2,3,7,8,9}-D-P4-*`).

**Where the program is:**
- **Phase 1 (results integrity)** — merged to `main` (PR #6).
- **Phase 2 (real execution path)** — merged to `main` (PRs #7/#8).
- **Phase 3 (the §7.2 fail-closed sweep)** — merged to `main` (PR #9).
- **Phase 4 (connective tissue)** — **complete on branch
  `claude/verdi-bench-phase-4-plan-k0p5fx`, open as PR #10, not yet merged to
  `main`.** Every spec-promised verb is now wired (`bench judge`, `review build`,
  `process score`, `corpus admit`, `corpus calibrate`); reveal discloses the real
  arm; judge calibration + process reporting ride the render; `is_schedulable`
  gates `bench run`; the power gate runs at the design's real N; admission is
  cryptographically attested. A max-effort self-review pass fixed 9 further
  defects. See §9 for the branch/merge decision you must make first.
- **Phase 5 (statistical correctness)** — your scope. Makes the reported numbers
  honest: the judge-preference analysis, the calibration fence, the coverage
  simulation, the claim tags, the degenerate-kappa policy, and the alias/vendor
  guards.

**Working method (non-negotiable, per CLAUDE.md):** reproduce-first (a failing
test that exhibits the pathology before each fix), `make verify` green before
every commit, atomic commits whose messages explain *why*, single-responsibility,
import-linter contracts stay green. Ask the human on direction-setting decisions;
give a recommendation with trade-offs, don't open-endedly ask.

---

## 2. Phase 5 scope & exit (from consolidated review §5)

> **Phase 5 — statistical correctness.** Findings must describe the experiment
> that ran.
> **Exit:** the reproduced pathologies (3-arm pooling, wrong-corpus fence,
> fabricated-N coverage, alias false-passes, script injection) each have a
> failing-then-fixed test.

Phase 4 made every stage *reachable* and its outputs *appear in the render*.
Phase 5 makes those outputs *correct*: the pipeline now runs end-to-end and
prints a judge-preference delta, a coverage-selected CI, a kappa, an official
fence — **and every one of them is currently computed wrong or on the wrong
data.** This is the phase where the instrument stops lying with green numbers.

---

## 3. Findings Phase 5 covers, by subsystem — with current status

⚠️ **The review's line numbers are from commit `01641cd` (pre-Phase-1) and are
stale; Phases 1–4 shifted the tree substantially. Re-verify every finding against
the current tree before planning — this is exactly what Phases 2/3/4 did (see each
plan's "Re-verification" section). Do not trust a finding is still open, or still
at the cited line, without looking.** The re-verification below was run at the
close of Phase 4 against branch `claude/verdi-bench-phase-4-plan-k0p5fx`; **all
findings reproduce** except where noted.

### Analyze (EVAL-6) — `harness/analyze/`
- **AN-1 (P0):** `_judge_preference_values` (`report.py:173-183`) reads **every**
  `judge_verdict` with no comparison/arm-pair filter and imputes any non-A/B
  winner (incl. `CANT_JUDGE` **and** `TIE`) as `0.0`; the same pooled deltas feed
  **every** arm pair (`report.py:418-421`); **no task clustering** (each verdict
  is its own bootstrap cluster → anti-conservative CIs); the A↔arm mapping is
  **assumed** (A=arms[0] convention), never joined. **Phase 4 recorded the seams
  this needs but analyze does not yet consume them:** `judge_verdict.arm_map`
  (`schema.py`, the A/B→physical-arm frame) and the `review_packet_built`
  `response_map` keyed by `comparison_id`. **Wire AN-1:** filter by
  comparison/arm pair using the recorded mapping, never impute CANT_JUDGE/TIE,
  cluster the bootstrap by task, and attribute the delta to the arm via `arm_map`.
- **AN-7 (P1):** judge-preference effect sizes are **fabricated** —
  `a_vals=[max(d,0)]`, `b_vals=[max(-d,0)]` from the clipped deltas feed
  `cliffs_delta` (`report.py:420-421`, `effect.py`). Cliff's delta over clipped
  half-waves is statistically meaningless. **Drop it for a valid paired
  preference-effect measure** (a §5 decision).
- **AN-2 (P0, before the first official finding):** the official fence
  `_assert_official_calibration` (`report.py:632-645`) checks **only**
  `calibration.status == "full-run-validated"`; **no** cross-check of
  `corpus_id`/`semver` against `spec.corpus`, and **no** check that the recorded
  task shas match the shas actually run. Phase 4 landed the ledgered
  `calibration_run` events + `manifest.calibration.runs` this can bind to.
  **Bind the fence to corpus identity** — and fix the shipped tests that pass
  mismatched manifests.
- **AN-4 (P1):** CI-method selection runs at the lock's **assumed** params with
  silent defaults `p=0.5, rho=0.3, n_tasks=50` (`_variance_params`,
  `report.py:227-233`), **not** the experiment's realized N or per-arm rates; the
  null model is correlated-Bernoulli regardless of whether the primary metric is
  holdout, cost, or wall-time (`nullsim.py:60-64`). **Run nullsim at the realized
  N with a metric-appropriate null; no silent fallbacks.** *(Ties to the Phase-4
  power-N note — see §4.)*
- **AN-10 (P3):** coverage selection runs at `n_boot=500` (`report.py:390`,
  `nullsim.py:28`) but the deployed interval uses `n_boot=10_000`
  (`report.py:391,458`, `stats.py:25`) — the CI method is chosen at a resample
  count it is never applied at. **Match `n_boot`.**
- **AN-8 (P2):** `decides_positive` is recorded on the raw observed delta
  **regardless of significance** (`report.py:469-473`); only the markdown render
  gates on detection (`report.py:560,567-575`), so any consumer of `findings.json`
  reads `decides_positive: true` for a null result. **Gate it on detection in the
  artifact.**
- **AN-9 (P2):** orphan grades (no matching trial record) are silently dropped
  (`report.py:150-159`) — a ledger inconsistency shrinks n with no flag. **Flag
  loudly.**
- **AN-5 (P2):** `render_html` wraps each line as `f"<p>{line}</p>"` with **no
  escaping**, no jinja2 (`report.py:786-817`); a `<script>` in an arm name/reason
  lands verbatim. The **review packet escapes correctly** — the fix pattern
  exists in-repo (`review/packet.py`). **Escape in `render_html`.**
- **AN-6 (P1):** `[computed]`/`[judgment]` claim tags exist **nowhere** in
  `harness/` (grep-verified). The §6 "claims tagged" invariant row cannot flip
  until they exist. **Implement claim tags in the findings schema + renders, and
  make `test_ac6_finding_provenance` own them** (a §5 decision on the mechanism).
- **AN-11 (P3):** minors — `CIMethod` not config-flippable (only coverage
  selection sets it, `report.py:394-396`); the ADVISORY tier is stamped but never
  surfaced as a tier label in renders; `ClusterRobustTCI` drops zero-SE
  resamples; BCa `z0` biased low on discrete deltas; `fractional_score` recorded
  but never read. Confirm which ride Phase 5 vs Phase 6.
- **AN-12 (P2, resolved D-3 = keep-labeled):** `findings.json` includes the
  `process` section and hashes it into `findings_sha256` (`report.py:509`,
  `cli.py:80-92`); the official **markdown** omits it (`report.py:648-671`).
  **Per REVIEW-D-3 (resolved), keep it in `findings.json` and make the official
  markdown show it under an explicit advisory/process label** — do not strip it,
  do not break the hash contract.

### Judge (EVAL-2) — `harness/judge/`, `harness/schema/`
- **JD-4 (P2, decision D-5):** degenerate kappa — all-A/all-A ×20 returns
  `kappa=1.0, sufficient=True` (`calibrate.py:41-42,73-81`). Kappa is undefined
  at chance agreement 1. **Per REVIEW-D-5 (recommended `undefined-insufficient`,
  confirm at phase start), return undefined/insufficient on zero chance-corrected
  information** — in both `cohens_kappa` and the IPW/`weighted_kappa` path
  (`review/kappa.py`).
- **JD-12 (P2, resolved D-4 = code-to-enum):** `Verdict.confidence` is a bare
  `float = 0.0` (`schema.py:72`) and the client writes hardcoded 0.5/0.8
  (`client.py:193`), contradicting the spec's `"confidence": "low|medium|high"`
  event schema. **Migrate the verdict schema to the `low|medium|high` enum and
  stop discarding the parsed value.** This is a **hash-chained event-schema
  contract change of an existing field's type** — the load-bearing Phase-5
  contract decision; it needs a migration/compatibility story (heavier than
  Phase 4's *additive* fields — see §5).
- **JD-6 (P1):** the alias regex `\d+\.\d+` false-passes `google/gemini-1.5-pro`
  and `openai/gpt-4.1` (`judge_config.py:22-31`) — the mutable-alias rejection
  AC-5 exists to prevent. **Reject dotted-version aliases** (require a date /
  build stamp / `-NNN` suffix, not a bare dotted version).
- **JD-7 (P2):** `_vendor` returns the whole string for prefix-less models
  (`confounds.py:21-22`), so vendor-overlap is wrong for a bare `claude-3-5-…`.
  **Handle prefix-less models, or require prefixed ids at schema** (`Arm.model`
  is an unvalidated bare `str`).
- **JD-8 (P2):** prompt-injection surface — raw diff/holdout interpolation under
  a one-line system prompt, no fencing (`packet.py:49-68`). A content-keyed
  injection maps to the same arm in both orders, so D003's order-consistency
  check reads it as a legitimate win. **Fence/delimit the packet.**
- **JD-13 (P3) — PARTIALLY ADDRESSED:** connect-phase timeout classification is
  **fixed** (`_http.py` `_classify_urlerror` → `ProviderTimeout`). **Left to
  Phase 5:** `packet_sha256` still does **not** cover the rendered message body
  or system prompt (`packet.py:80-93`), so provenance can't detect a framing
  change; and response-label assignment is deterministic AB/BA (both orders run,
  so position bias cancels — decide whether the spec's "assigned randomly per
  call" wording still needs honoring).

### Plan / power (EVAL-3) — `harness/plan/`
- **Power-N clustering (carry-forward from the Phase-4 code review, folds into
  AN-1/AN-4):** Phase 4's power gate computes N as `repetitions × corpus_size`
  and the sim treats each `(task, repetition)` as an **independent** paired
  observation (`plan/lock.py`, `plan/power.py`). When `repetitions > 1` those reps
  are correlated within a task, so power is **optimistic** — a genuinely
  underpowered design can lock as adequate. This is the *same* clustering error
  AN-1 flags in the analysis. **Fix both together:** cluster by task in the power
  sim and in the analysis bootstrap, so the pre-registration power model and the
  realized-data analysis share one variance model. (A comment in `lock.py` marks
  this as the Phase-5 seam.)

### Process (EVAL-9) — `harness/process/`
- **Weighted-kappa degenerate case (rider on JD-4/D-5):** `weighted_kappa`
  (`review/kappa.py`) shares the `1-pe < 1e-9 → 1.0` guard; the D-5 resolution
  should apply to the process per-dimension quadratic-weighted kappa too, or the
  two kappa families diverge on the degenerate case.

---

## 4. Infrastructure to build on (don't reinvent)

Phase 4 built seams Phase 5 should reuse rather than rebuild:
- **`judge_verdict.arm_map`** — the recorded A/B→physical-arm frame per verdict
  (`schema.py`). AN-1 reads it to attribute the judge-preference delta to the
  right arm instead of assuming A=arms[0].
- **`review_packet_built` + its `response_map`** (`ledger/events.py`,
  `review/build.py`) — the comparison→arm mapping keyed by `comparison_id`. AN-1's
  comparison/arm-pair filter joins on it.
- **`calibration_run` events + `manifest.calibration.runs`**
  (`corpus/ledger_ops.py`, `corpus/cli.py corpus calibrate`) — the ledgered
  calibration the AN-2 fence binds to; each run carries `{p, rho, n_tasks}` (rho
  is currently a recorded *assumption* — **full within-task rho estimation is a
  Phase-5 statistical task**, feeding AN-4).
- **`CalibrationVariance` loader + real-N power gate** (`plan/power.py`,
  `harness/cli.py bench plan --corpus-manifest`) — AN-4 extends this so nullsim's
  coverage selection runs at the realized N/rates, not the assumed defaults.
- **The IPW kappa seam + `calibration_from_spec`** (`review/kappa.py`,
  `review/calibrate.py`) — the single per-class kappa seam both `bench judge` and
  analyze call; JD-4/D-5 changes `cohens_kappa`/`weighted_kappa` here once.
- **`run_analyze`** (`analyze/cli.py`) — the reusable analyze orchestration
  (returns the render path or `None`-after-`cant_analyze`); AN-2/AN-5/AN-6/AN-8
  extend the findings it computes and the markdown/HTML it renders.
- **The review packet's HTML escaping** (`review/packet.py`) — the fix pattern
  AN-5 copies into `render_html`.
- **The Phase-4 additive-contract pattern** (decisions-ledger entry + migration
  note per contract change; the guarded field additions on hash-chained events) —
  the template JD-12's confidence-enum migration follows, but that is a *type*
  change of an existing field, not additive (§5).

---

## 5. Decisions

Phase 5 is a correctness phase; several direction-setting choices need explicit
human resolution **before** the owning slice (per CLAUDE.md), each recorded as a
`resolved` event in the owning `evalN.decisions.ndjson`. Candidates to raise
(give a recommendation + trade-offs, don't open-endedly ask):

- **Carried forward, resolved — implement as specified:**
  - **REVIEW-D-3 (AN-12):** keep the process section in `findings.json` (hashed
    into `findings_sha256`) and make the official markdown show it under an
    explicit advisory/process label. Do not strip; do not break the hash.
  - **REVIEW-D-4 (JD-12):** migrate `Verdict.confidence` to `low|medium|high`.
    **This is the load-bearing Phase-5 contract decision** — it changes the *type*
    of a field on a hash-chained event (not additive), so it needs a migration
    story: how do existing float-confidence verdicts read, does the reader map
    old floats to buckets or refuse them, does `packet_sha256`/verdict validation
    change. Recommend a versioned reader that accepts both and a documented
    cut-over, since verdi-bench has **no production ledgers yet** (greenfield —
    the compatibility surface is a design note, not a live migration).
- **Confirm at phase start (resolved-pending):**
  - **REVIEW-D-5 (JD-4):** degenerate-kappa policy — recommended
    `undefined-insufficient` (return kappa undefined / `sufficient=False` at zero
    chance-corrected information, in both `cohens_kappa` and `weighted_kappa`).
    Confirm before the judge/process kappa slice.
- **New Phase-5 decisions to raise (recommendation + trade-offs):**
  - **The judge-preference effect measure (AN-1/AN-7).** What replaces the
    fabricated clipped-series Cliff's delta? Recommend a proper paired
    preference-effect (e.g. the per-task win-rate difference with a
    task-clustered bootstrap CI), computed only over comparisons with a real A/B
    verdict (CANT_JUDGE/TIE excluded, not imputed). This is the widest-surface
    Phase-5 decision — it defines what a "judge-preference primary metric" *is*.
  - **The AN-2 fence binding shape.** Bind the official fence to which identity
    fields — `corpus_id`+`semver` from the manifest vs `spec.corpus`, **and** the
    manifest/ledgered task shas vs the shas actually run? Recommend all three
    (a fence that ignores any of them is a hand-editable bypass, compounding the
    calibration-status check).
  - **The claim-tag mechanism (AN-6).** How do `[computed]`/`[judgment]` tags
    attach — a per-claim field on the findings schema, or inline markers in the
    render? Recommend a structured `claim_tag` on each finding/claim in
    `findings.json` with the render deriving the marker, so the tag is
    machine-checkable (and `test_ac6_finding_provenance` can own it) rather than
    a render-only string.
  - **The shared variance/clustering model (AN-1 + AN-4 + power-N).** Decide the
    one clustering model (cluster by task, reps within a task correlated) used by
    **both** the pre-registration power sim and the realized-data analysis, so the
    two cannot disagree. This is a genuine statistical-model decision with a
    migration note (recorded MDEs change).

---

## 6. Current baseline & how to verify

- Fast suite: `uv run pytest -m "not docker" -q` → **360 passed, 3 deselected**
  (the 3 docker-marked tests) at the close of Phase 4. `make verify` (full suite +
  import contracts) is the mandatory gate; **3 import-linter contracts kept**
  (`harbor-confined-to-seam`, `grade-has-no-llm-clients`,
  `ledger-writes-only-via-events`).
- **New dependency (Phase 4):** `cryptography` (pyca, Ed25519) for signed
  curation attestation. `uv.lock` is committed; Phase 5 adds no new runtime dep.
- Real-container suite: `uv run pytest -m docker` runs on the CI `docker` job; the
  local dev environment has no reachable daemon (docker-marked tests skip
  locally). Phase 5 is pure non-Docker statistical/rendering work — no new Docker.
- `uv run pytest --ac-report` recomputes AC coverage (a global union, not a
  per-story guarantee — XC-2, a Phase 6 item). Phase 5 should move the
  AN-6/AN-2/AN-1 ACs from "reachable" to "correct".
- **Contract note:** completing the `.importlinter` source lists (XC-5) is
  Phase 6; keep the three live contracts green and route any new ledger writes
  (e.g. a claim-tagged findings event, if you add one) through `events.py` typed
  constructors. **JD-12's confidence migration touches a hash-chained event
  schema** — treat it as a versioned contract per §5.

---

## 7. Suggested Phase 5 shape (mirror the phase-2/3/4 plans)

Plan it as ordered, mostly-independent, atomic slices, reproduce-first (a test
that exhibits the pathology, then passes). The analyze slices interlock (they
share `compute_findings`), so order the shared variance/clustering model first. A
reasonable slicing:

1. **Judge-preference correctness** (AN-1, AN-7): filter by comparison/arm pair
   via the recorded `response_map`/`arm_map`; exclude (never impute)
   CANT_JUDGE/TIE; cluster the bootstrap by task; replace the clipped-series
   Cliff's delta with the chosen preference-effect (§5).
2. **Shared variance/clustering + coverage at real N** (AN-4, AN-10, power-N):
   one task-clustered variance model feeding both the power sim and nullsim;
   coverage selection at the realized N with a metric-appropriate null and matched
   `n_boot`; no silent parameter fallbacks.
3. **Official fence bound to corpus identity** (AN-2): manifest
   `corpus_id`/`semver` vs `spec.corpus` and task shas vs the ledgered trials;
   fix the shipped tests that pass mismatched manifests.
4. **Artifact-honest decisions + orphan flagging** (AN-8, AN-9): gate
   `decides_positive` on detection in `findings.json`; flag orphan grades loudly.
5. **Claim tags + HTML escaping + ADVISORY surfacing** (AN-6, AN-5, AN-11): the
   `[computed]`/`[judgment]` mechanism (§5) owned by `test_ac6_finding_provenance`;
   escape `render_html`; surface the ADVISORY tier.
6. **AN-12 official process labeling** (D-3): the official markdown shows the
   process section labeled; `findings_sha256` unchanged.
7. **Judge guards** (JD-6, JD-7, JD-8): alias regex rejects dotted-version
   aliases; vendor-overlap handles prefix-less models (or the schema requires
   prefixed ids); fence/delimit the packet; extend `packet_sha256` to the rendered
   body (JD-13 remainder).
8. **Kappa correctness** (JD-4/D-5): `undefined-insufficient` in `cohens_kappa`
   and `weighted_kappa` (judge + process share the fix).
9. **Confidence enum migration** (JD-12/D-4): `Verdict.confidence` →
   `low|medium|high` with the migration story (§5); stop discarding the parsed
   value.
10. **Exit test:** the five reproduced pathologies (3-arm pooling, wrong-corpus
    fence, fabricated-N coverage, alias false-passes, script injection) each have
    a failing-then-fixed test, plus AN-1's CANT_JUDGE-not-imputed and the
    task-clustered CI.

Each slice: reproduce-first test exhibiting the pathology → correct after; any
new/changed hash-chained event carries a decisions-ledger entry + migration note;
`make verify` green before each commit.

---

## 8. Phase 5 exit criteria (restate for your plan)

- The reproduced pathologies each have a **failing-then-fixed** test: 3-arm
  judge-preference pooling (AN-1), wrong-corpus official fence (AN-2), fabricated-N
  coverage (AN-4), alias false-passes (JD-6), script/prompt injection (JD-8/AN-5).
- Judge-preference deltas are filtered by comparison/arm pair, exclude
  CANT_JUDGE/TIE (never impute), and are **task-clustered**; the reported effect
  size is a valid preference measure, not clipped-series Cliff's delta.
- The official fence refuses a mismatched corpus (`corpus_id`/`semver`/task-shas),
  and the shipped mismatched-manifest tests are corrected.
- Coverage selection runs at the realized N with a metric-appropriate null and
  matched `n_boot`; the power sim and the analysis share one task-clustered
  variance model.
- `[computed]`/`[judgment]` claim tags exist in the findings schema + renders and
  `test_ac6_finding_provenance` enforces them; `render_html` escapes; the §6
  "claims tagged" row can flip.
- Degenerate kappa returns undefined/insufficient (D-5); `Verdict.confidence` is
  the `low|medium|high` enum (D-4) with a recorded migration note; the process
  section is labeled in the official markdown (D-3).
- `make verify` green; no import-linter regressions; every hash-chained
  event-schema change (confidence enum; any claim-tag event) carries a
  decisions-ledger entry + migration note.

---

## 9. First thing to settle: branch / merge

Phase 4 is complete but **unmerged** on `claude/verdi-bench-phase-4-plan-k0p5fx`,
open as **PR #10** (`origin/main` is at the Phase 3 merge). Before planning
Phase 5, decide with the human:
- **Merge Phase 4 (PR #10) to `main` first**, then branch Phase 5 from `main`
  (cleanest history; Phase 5 builds on a merged base — recommended, matching how
  Phases 3/4 were cut from a `main` that already contained the prior phase); **or**
- **Stack Phase 5 on the Phase 4 branch HEAD** (if PR #10 is still open and you
  want to proceed without waiting on the merge).

Your session will be given its own branch directive — reconcile it with the above.
Do **not** start Phase 5 from `main` alone without Phase 4, or you'll be missing
the seams Phase 5 consumes: `arm_map`, the `review_packet_built` response_map, the
ledgered `calibration_run`/manifest runs, the `CalibrationVariance` loader, the
IPW `calibration_from_spec` seam, and the power-N clustering note.

---

*Prepared at the end of Phase 4. Treat the consolidated review as the map, this
brief as the orientation, and re-verify everything against the live tree before
committing to a plan.*
