# verdi-bench — Phase 3 planning handoff

**For:** a fresh session that will *plan* Phase 3 (the §7.2 fail-closed sweep).
**Written:** 2026-07-03, at the close of Phase 2. **You have no prior context —
this brief plus the in-repo documents it points to are self-contained.**

---

## 1. Orientation

verdi-bench is a benchmark-grade A/B evaluation instrument for agent stacks
(pre-registered experiments, paired hermetic trials, insulated arms,
deterministic-first grading, an identity-blind advisory LLM judge, a hash-chained
event ledger). Its credibility is its own correctness — a green fake path that
can be reward-hacked is worse than an absent real path. **Read `CLAUDE.md`
(repo root) first — its directives override convenience and this brief.**

**Authoritative in-repo documents (read these before planning):**
- `docs/design/review/verdi-bench-review-consolidated.md` — the ~100-finding
  audit. **§5 is the six-phase remediation plan; Phase 3 is your scope.** §3 is
  the findings register (IDs referenced below); §6 is the readiness gate.
- `docs/design/review/verdi-bench-phase-2-plan.md` — the Phase 2 plan; mirror
  its structure and rigor when you write the Phase 3 plan.
- `docs/design/review/review.decisions.ndjson` — resolved review decisions
  (REVIEW-D-1..D-10). `docs/design/specs/eval{2,6,7,8,9}.spec.md` — the AC
  contracts for the stages Phase 3 touches (judge, analyze, review, corpus,
  process). `docs/design/specs/eval{2,...}.decisions.ndjson` — per-story decisions.

**Where the program is:**
- **Phase 1 (results integrity)** — merged to `main` (PR #6). Chain verified at
  stage entries, lock hardened, task-content commitment, real grade path,
  docker-test enabler.
- **Phase 2 (real execution path)** — complete on branch
  `claude/verdi-bench-phase-2-plan-g4t7k9`, **not yet merged to main**. All
  `harness/run/` + grade-baseline work (RN-*, GR-8/9/10), plus a max-effort
  self-review pass that fixed 10 further defects. CI green (fast + a real
  `docker` job). See §9 for the branch/merge decision you must make first.
- **Phase 3 (the fail-closed sweep)** — your scope. **Untouched by Phase 2.**
  The Phase-3 subsystems sit at their Phase-1/`main` state.

**Working method (non-negotiable, per CLAUDE.md):** reproduce-first (a failing
test before each fix), `make verify` green before every commit, atomic commits
whose messages explain *why*, single-responsibility, import-linter contracts
stay green. Ask the human on direction-setting decisions; give a recommendation
with trade-offs, don't open-endedly ask.

---

## 2. Phase 3 scope & exit (from consolidated review §5)

> **Phase 3 — the §7.2 fail-closed sweep.** One attempted operation ⇒ exactly
> one event, in every stage.
> **Exit:** fault-injection tests per stage prove no zero-event escapes; the
> one-event property sweep covers all nine stages.

The invariant (master-plan §6): *"Fail closed; no operation without a ledger
event."* Today it is **false in every stage** (§2.3 of the review). Phase 1
established the model to copy: grade's `cant_grade(reason)` taxonomy with a
transient/terminal split. Phase 3 extends that discipline to judge, analyze,
process, review, and corpus, and makes the one-event property *mechanically
enforce* it across all nine stages.

---

## 3. Findings Phase 3 covers, by subsystem — with current status

⚠️ **The review's line numbers are from commit `01641cd` (pre-Phase-1) and are
stale. Phase 1 (in `main`) touched corpus and the review/process CLIs. Re-verify
every finding against the current tree before planning — this is exactly what
Phase 2 did (see the phase-2 plan's "Re-verification" section). Do not trust a
finding is still open, or still at the cited line, without looking.**

### Judge (EVAL-2) — `harness/judge/`
- **JD-2:** `get_provider` runs before the try envelope (`client.py`); an unknown
  provider prefix raises `ProviderError` with **no `CANT_JUDGE` event**. Move
  provider lookup + parsing inside the fail-closed envelope.
- **JD-3:** error-shaped / safety-blocked 200 responses raise uncaught
  `KeyError`/`IndexError` in openai/google providers (escape, no event); anthropic
  misclassifies as `CANT_JUDGE(parse)`. Catch them; correct reason classification.
- **JD-13:** connect-phase timeouts surface as `provider_error` not `timeout`;
  response-label assignment is deterministic AB/BA vs the spec's "assigned
  randomly per call"; `packet_sha256` doesn't cover the rendered message.
- *Note:* there is **no separate `CANT_JUDGE` event** — `judge_verdict` subsumes
  it via the `winner` field (`events.py` `append_verdict` docstring). So "one
  event" for judge = the verdict event always lands, even on failure.

### Analyze (EVAL-6) — `harness/analyze/`
- **AN-3:** refused official renders escape `analyze/cli.py` with **zero events**
  (`CalibrationIncompleteError`); there is **no `CANT_ANALYZE` event type** at
  all (confirm: not in `harness/ledger/events.py`). Add `CANT_ANALYZE(reason)`;
  ledger the refusal; write the event **before (or atomically with)** the
  findings files (success path currently writes files first, event second).

### Process (EVAL-9) — `harness/process/`
- **PR-1:** `{"scores":[3,4,5]}` (list not dict) raises `AttributeError` past the
  `except (ValueError, JSONDecodeError)` → escape, no `process_score` event.
- **PR-2:** `RedactionLeakError` from `build_process_packet` escapes with zero
  events → should be `CANT_SCORE(redaction_leak)` (mirror the judge's
  `identity_leak` precedent).
- **PR-3:** `get_provider` before the try → unknown prefix escapes, no event.
- **PR-4:** a judge-declared per-dimension `CANT_SCORE` is ledgered as
  reason `"unparsed"`; timeout/refusal collapse to `provider_error`; reasons are
  ad-hoc strings, not an enum.
- **PR-7:** `bench process record` silently maps a missing/typoed dimension to
  `CANT_SCORE("human_cant")` and ignores unknown keys — a misspelled dim
  degrades a real score with no error.
- **PR-8:** neither `record_human_process_score` nor `ProcessScore` validates
  `dimension_scores` against the rubric — unknown/subset/duplicate dims ledger
  cleanly.
- *Note:* `process_score` subsumes `CANT_SCORE` via per-dimension values (like
  judge). "One event" = the score event always lands.

### Review (EVAL-7) — `harness/review/`
- **RV-1:** `record_human_verdict` checks neither an existing reveal nor an
  existing verdict → verdict → reveal → second (unblinded) verdict accepted; dups
  poison kappa/integrity.
- **RV-8:** duplicate reveals allowed; a refused reveal is unledgered; last-judge-
  verdict-wins joins; `CANT_JUDGE` as a plain kappa category; a bare
  `append_human_verdict` closes a comparison but never unlocks reveal.
- **RV-9:** `reveal_comparison` takes the *first* judge verdict while kappa joins
  take the *last*; `review record` accepts any comparison_id with no existence
  check (mistyped ids silently drop from kappa); integrity-less verdicts still
  calibrate the judge; the CLI omits `task_class` → everything lands in `"default"`.
- *Partly touched by Phase 1:* commit `0a6ac9d` ("verify the chain at the reveal
  firewalls; clean CLI errors") added chain-verify at the reveal firewall.
  Re-verify what remains.

### Corpus (EVAL-8) — `harness/corpus/`
- **CO-1:** boundary enforcement is declaration-only; `save()`/`bench corpus
  mine --out` never check the destination. Enforce the boundary on **write
  destinations** (internal corpora must never enter the instrument repo — a §6
  readiness-gate row).
- **CO-4:** `record_calibration_run` has no CLI verb / run hook; status lives in
  mutable manifest JSON (hand-editable → passes the official fence). Ledger
  calibration runs. **(§6: a hand-editable JSON status does not satisfy EVAL-8
  AC-2 — this gates the first official finding.)**
- **CO-6:** path traversal via registry-supplied `task_id` (`public.py`);
  no dataset-level checksum pinning. Sanitize `task_id`. *(Phase 1 added "validate
  task ids" in commit `e1bbe40` — re-verify whether traversal is closed.)*
- **CO-9:** re-import leaves removed tasks' cache blobs (drift); `corpus subset`
  records the draw only in mutable manifest JSON (unledgered). Ledger subset draws.
- **New event types needed:** `task_admitted` (admission), a calibration-run
  event, a subset-draw event.
- *Done in Phase 1 (do not re-do):* **CO-3** (re-import preserves calibration +
  successor rule, commit `47ee047`); **CO-5** (admission gate verifies the chain,
  commit `6491cc0`).

### Cross-cutting
- **XC-3 (the structural exit):** the one-event property registry
  (`harness/entrypoints.py`, swept by `tests/test_eval3_property.py`) has **only
  three entrypoints registered** — `plan-lock`, `run-trial`, `grade-trial`. The
  six other stages (judge, analyze, review, process, corpus, + any sub-ops) have
  **none**, so "later stories join automatically" fails open. Register an
  entrypoint per stage and make the sweep *discover* registrations (the sweep
  already reads `all_entrypoints()`, but the stage modules must be imported to
  self-register, and the test asserts only non-emptiness — strengthen it).
- **PL-14:** the acknowledged-underpowered plan path emits *two* events per
  invocation, so the one-event property is false for that documented path and the
  property test never exercises it. Fold into the sweep.

---

## 4. Infrastructure to build on (don't reinvent)

- **The `cant_grade` model (Phase 1):** `harness/grade/` has the reference
  fail-closed taxonomy — a `cant_grade(reason)` event, a transient
  (`GraderUnavailableError`) vs terminal (`GradingContainerError`) split, and an
  enum of reasons. Copy this shape for `CANT_ANALYZE` and for tightening
  judge/process/review/corpus refusals.
- **The `identity_leak → CANT_JUDGE` precedent (`judge/client.py`):** the model
  for PR-2's `CANT_SCORE(redaction_leak)`.
- **The event funnel:** `harness/ledger/events.py` — every event goes through
  `emit()` (registered types only; reserved-key rejection). New event types
  register via `register_event(...)` + a typed constructor. `EventContext` carries
  an injectable `clock`/`actor` for deterministic tests.
- **The entrypoint registry:** `harness/entrypoints.py` +
  `tests/test_eval3_property.py` — the mechanism XC-3 must extend.
- **Fault-injection test style:** see Phase 2's `test_ac5_*` (per-cell fault →
  exactly one ledgered outcome) and the grade `cant_grade` tests for the pattern
  your per-stage exit tests should follow.

---

## 5. Decisions

- **No pending decision blocks Phase 3.** (D-5 degenerate-kappa is Phase 5;
  D-7 the 3.12 gate is Phase 6.)
- **New events are hash-chained contract additions.** `CANT_ANALYZE`,
  `task_admitted`, the calibration-run and subset-draw events each extend a
  versioned, hash-chained seam. Per CLAUDE.md "public seams are contracts," each
  needs a **decisions-ledger entry + a written additive/backward-compatible
  migration note** before it lands — same discipline Phase 1 used for the
  `task_commitment` field. Additive event *types* don't break existing chains
  (old ledgers simply lack them); adding *fields* to existing events is the case
  to guard.
- **Carry-forward from the Phase 2 review (candidates to fold into Phase 3's
  fail-closed theme):**
  - **#10 (needs sign-off):** `harness/run/settings.py` silently drops a provider
    key that is *named* in `run.config.yaml` but absent from the env → an arm runs
    unauthenticated → biased A/B. This IS a fail-closed defect; recommend raising
    (fail-loud). A test currently pins the silent drop, so changing it needs human
    agreement — a natural Phase 3 item.
  - **#9 (noted):** whole-workspace redaction (`harness/run/redact.py`) reads
    every non-binary file fully into memory → OOM/scale risk on large workspaces.
    Wants a streamed/bounded-scan redesign, not a patch.
  - **Durable infra cost (noted):** Phase 2's resume cost-seed recovers spend from
    the ceiling-stop event, but a crash *before* the ceiling still loses in-flight
    infra-attempt spend. A complete fix needs an additive `cost` field on the
    `trial_infra_failed` event (a contract addition to decide on).
  - **Phase-4 notes (not Phase 3):** the flake-quarantine *producer* is unwired in
    production (RN-5 wired only the consumer), and `task_content_sha` (run) will
    not match the corpus `TaskEntry.sha` (admission) — the sha-coherence to unify
    when the corpus manifest becomes the task source. These belong to Phase 4.

---

## 6. Current baseline & how to verify

- Fast suite: `uv run pytest -m "not docker" -q` → **283 passed, 3 deselected**
  (the 3 docker-marked tests). `make verify` (full suite + import contracts) is
  the mandatory gate; **3 import-linter contracts kept**.
- Real-container suite: `uv run pytest -m docker` runs on the CI `docker` job
  (GitHub `ubuntu-latest` has Docker). The local dev environment here has **no
  reachable daemon**, so docker-marked tests skip locally and are CI-proven — the
  same "on the web, CI proves it" arrangement holds for Phase 3 if it adds any
  container work (it mostly won't; Phase 3 is judge/analyze/process/review/corpus,
  all non-Docker).
- `uv run pytest --ac-report` recomputes AC coverage (a global union, not a
  per-story guarantee — XC-2, a Phase 6 item).

---

## 7. Suggested Phase 3 shape (mirror the Phase 2 plan)

Plan it as ordered, independent, atomic slices — the five subsystems are largely
independent, so they can land in any order; the XC-3 registry sweep lands **last**
(it depends on every stage having an entrypoint). A reasonable slicing:

1. **Judge fail-closed** (JD-2/3/13): provider lookup + parse inside the envelope;
   catch KeyError/IndexError; timeout vs provider_error vs parse.
2. **Process fail-closed** (PR-1/2/3/4/7/8): AttributeError-class parse escapes;
   `CANT_SCORE(redaction_leak)`; provider-in-envelope; reason enum; rubric
   validation; error on unknown/missing dims.
3. **Review fail-closed** (RV-1/8/9): ledger refused reveals; refuse duplicate
   reveals + post-reveal/duplicate verdicts; existence-check comparison ids.
4. **Analyze fail-closed** (AN-3): `CANT_ANALYZE(reason)`; ledger refused official
   renders; event-before-files. *(New event type — contract discipline.)*
5. **Corpus fail-closed + audit** (CO-1/4/6/9): boundary on write destinations;
   ledger admission / calibration runs / subset draws; sanitize task_id.
   *(New event types — contract discipline.)*
6. **XC-3 property sweep** (+ PL-14): register an entrypoint per stage; make the
   sweep discover + assert one-event-per-op across all nine stages; the
   fault-injection exit tests.

Each slice: reproduce-first fault-injection test proving "attempted op with zero
events" today → "exactly one ledgered refusal event" after. `make verify` green
before each commit.

**Confirm at the start of the owning slice** (recommendation stated): whether to
fold the Phase-2 carry-forward #10 (provider-key fail-loud) into the process/run
fail-closed work — it needs sign-off because a test pins the current behavior.

---

## 8. Phase 3 exit criteria (restate for your plan)

- A fault-injection test **per stage** (judge, analyze, process, review, corpus)
  proves an attempted operation that used to escape with zero events now emits
  exactly one refusal/outcome event.
- The one-event property sweep (`test_eval3_property.py`) covers **all nine
  stages** (entrypoints registered + discovered), and the acknowledged-
  underpowered path (PL-14) no longer emits two events.
- `make verify` green; no import-linter regressions; any new event type carries a
  decisions-ledger entry + migration note.
- The §6 readiness-gate row **"Fail closed; no operation without a ledger event"**
  flips from `enforced_by: review` to enforced by the all-stage property sweep.

---

## 9. First thing to settle: branch / merge

Phase 2 is complete but **unmerged** on `claude/verdi-bench-phase-2-plan-g4t7k9`.
Before planning Phase 3, decide with the human:
- **Merge Phase 2 to `main` first**, then branch Phase 3 from `main` (cleanest
  history; Phase 3 builds on a merged base); **or**
- **Stack Phase 3 on the Phase 2 branch HEAD** (if Phase 2's PR is still open).

Your session will be given its own branch directive — reconcile it with the
above. Do **not** start Phase 3 from `main` alone without Phase 2, or you'll be
missing the Phase 2 work.

---

*Prepared at the end of Phase 2. Treat the consolidated review as the map, this
brief as the orientation, and re-verify everything against the live tree before
committing to a plan.*
