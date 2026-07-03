# verdi-bench — Phase 7 plan: close the register — remediate the verification residue

**Date:** 2026-07-03 · **Follows:** Phase 6 (merged to `main`, PR #12) and the
independent verification `verdi-bench-audit-verification.md` (this branch,
`138455f`).
**Source of record:** `verdi-bench-audit-verification.md` §3 (register tally),
§4 (not-properly-addressed + new defects), §5 (readiness caveats), backed by
`verdi-bench-review-consolidated.md` §3 for the original finding texts.
**Branch:** plan authored on `claude/verdi-bench-audit-verify-fajn6f`;
implementation should follow the established convention — merge this plan, then
cut the implementation branch from `main`.

## Context

Phases 1–6 executed the consolidated review's §5 plan and their exits hold
(verified adversarially; see the verification doc). What remains is precisely
enumerable and falls into four families:

1. **Register findings §5 never scheduled** — 7 not-fixed (GR-12, RN-17, PL-9,
   PL-10, PL-11, PL-13, PR-9) and the dropped sub-items of 14 partials. These
   fell out of the plan without recorded debt decisions, which the repo's
   "human decides" directive requires.
2. **Resolved decisions with unexecuted actions** — D-1's wording fix and
   analysis-side disclosure; EVAL-7 D003 still `raised`.
3. **New defects the verification found** — most consequentially `bench judge`
   non-idempotency (duplicate verdicts inflate calibration and preference
   statistics) and two fail-open writer verbs (`anchor`, `plan`).
4. **Missing owning tests** — fixes that are real in code but would survive
   regression (AN-1 swapped-frame attribution, AN-10 `n_boot` match, RV-7
   ordering, GR-13 evidence, arm-payload canary channel, two minor run
   branches), plus two demonstrated enforcement blind spots (package-`__init__`
   imports; CI docker job green on all-skip).

**Phase 7's job is a terminal disposition for every one of these**: either a
fix with an owning test that fails on regression, or a recorded human decision
accepting the behavior. Nothing on the list may simply fall out again — the
disposition map (§Completeness) is checked off item by item at exit.

The verification was run against `main` at `6dd285b` on the same day as this
plan; its evidence **is** the re-verification section for Phase 7. Line numbers
below cite that tree.

## Decisions

Per CLAUDE.md, direction-setting choices are resolved by the human **before**
the owning slice; each resolution is appended as a `resolved` event to
`review.decisions.ndjson` (or the owning `evalN.decisions.ndjson`).
Recommendations with trade-offs, not open questions:

### New Phase-7 decisions (confirm before the owning slice)

- **REVIEW-D-P7-1 (PL-10, slice 7C) — arm-list policy.** Recommend
  **`unique-names-required, no count cap`**: add a validator refusing duplicate
  arm names (a live bug — `run`'s `arm_map` silently collapses duplicates) and
  keep `min_length=2` with no upper cap, because Phase 5 made every analysis
  path correctly pairwise (AN-1's 3-arm reproduction is now a *supported*
  design). Alternative: hard cap at 2 ("paired A/B instrument" reading) —
  rejects designs the analysis layer now handles correctly.
- **REVIEW-D-P7-2 (GR-8/GR-11 residual, slice 7B) — grade transient taxonomy +
  override.** Recommend **`daemon-probe-plus-ledgered-retry-flag`**: (a) a
  pre-flight daemon probe in `DockerGradeRunner` so daemon-down (which exits
  **1**, not 125, on modern docker) classifies as transient
  `GraderUnavailableError` instead of terminal `container_failure` — today a
  single daemon outage quarantines a healthy task version and permanently
  blocks regrading; (b) a `bench grade --retry-terminal <trial-id>` escape
  hatch that re-attempts a terminal `cant_grade`, ledgering the override and
  actor. Trade-off: (b) deliberately weakens "terminal is terminal"; making it
  explicit, per-trial, and ledgered keeps it auditable. Alternative: probe only
  (no override) — leaves genuinely-misclassified historical trials stuck.
- **REVIEW-D-P7-3 (CO-7, slice 7F) — curation identity binding.** Recommend
  **`identity-bound-keyring`**: keyring becomes `{approver_id → pubkey}`; admit
  verifies the approval's signature against the *named approver's own key* and
  refuses `approver == miner`. Today the bar compares free-text labels, so any
  authorized-key holder self-approves by relabeling (probe-confirmed). This
  supersedes the key-only half of EVAL-8-D-P4-3; the keyring is local operator
  state (not hash-chained), so the format change needs only a loud error on
  the legacy list format. Alternative: record the limitation and keep
  labels — cheaper, but leaves CO-7's stated purpose (a real self-approval
  bar) unmet.
- **REVIEW-D-P7-4 (RV-3 residual, slice 7E) — EVAL-7 D003 disposition.**
  Recommend **`render-ipw-plus-floor-sensitivity`**: give `kappa_report` its
  production caller — analyze renders the floor-only sensitivity kappa beside
  the IPW estimate per class, resolving the still-`raised` D003 as designed.
  Alternative: delete `kappa_report` (dead-code directive) and record D003 as
  IPW-only — smaller, but discards the sensitivity check D003 exists for.
- **REVIEW-D-P7-5 (RN-18 residual, slice 7C) — the `--concurrency` knob.**
  Recommend **`remove`**: execution is strictly serial by design (determinism
  first); the knob only stamps a `contention_caveat` that describes nothing
  real. Removing the flag and the caveat field write is a CLI-surface change,
  hence a decision. Alternative: implement real concurrency — out of all
  proportion to the finding.
- **REVIEW-D-P7-6 (new defect #8, slice 7D) — rubric content commitment.**
  Recommend **`additive-rubric-sha-in-lock`**: add `rubric_sha256` to
  `experiment_locked` (additive field, exactly the `task_commitment` / D-6
  precedent, `ledger/events.py:115-140`); `bench judge` recomputes and refuses
  a post-lock rubric swap the way run/grade/judge already refuse a `tasks.yaml`
  swap. This is a **hash-chained contract addition** and needs explicit
  approval + a compatibility note (absent field ⇒ pre-Phase-7 lock, judge
  warns instead of refuses). Alternative: keep post-hoc-only detection via
  `rubric_sha256` in verdict provenance — leaves judge behavior swappable
  post-lock on the instrument's primary judgment input.
- **REVIEW-D-P7-7 (GR-12, slice 7C) — actor provenance policy.** Recommend
  **`env-fallback-then-refuse`**: one shared `resolve_actor()` (deduping the
  seven per-CLI copies), trying `getpass.getuser()` → `$USER`/`$LOGNAME` → a
  new `--actor` flag; if all absent, **refuse** with a message naming the flag,
  instead of silently ledgering `actor="unknown"` (a fail-loud directive
  violation feeding hash-chained provenance). Alternative: keep `"unknown"`
  with a stderr warning — cheaper, still masks the failure in the ledger.

### Carried forward (needs resolution; gates slice 7I only)

- **EVAL-1-D008 — A/A + coverage selfcheck.** Still `raised`
  (`eval1.decisions.ndjson`), recommended option `required-before-official`.
  The blocker the master plan named (nullsim must run at the realized N) was
  removed by Phase 5 (AN-4), so resolving it is now unblocked. If resolved as
  recommended, slice 7I builds `bench selfcheck` per master plan §7.7 and the
  official render additionally requires a ledgered selfcheck event. If
  resolved `advisory-only`, 7I is dropped and the resolution recorded. This
  decision is independent of 7A–7H and must not block them.

### In-slice recommendations (settled within the owning slice, veto cheap)

- **Judge/review-build idempotency (7A)** is a bug fix, not a decision: the
  judge verb's own docstring promises "one verdict each" and `process score`
  already implements the skip-scored pattern (`process/cli.py:77-83`).
- **PR-9 (7D):** make `spec` a required parameter of the process scoring path
  (production always passes it; only tests omit it) so `judge_vendor_overlap`
  stays an honest `bool` — no tri-state provenance change, no contract churn.
  Context gate: keep chars/4 as a *pre-flight* only, and map a provider-side
  context-overflow error to `CANT_SCORE(context_overflow)` with the provider's
  token counts when present, instead of generic `provider_error`.
- **RV-8(c) (7E):** keep the `verdict_event_id` field name; document (spec
  note) that post-RV-1 the comparison id *is* the unique verdict reference —
  renaming a field inside the hash-chained `reveal` event is contract churn
  with no information gain.
- **AN-11 residual (7G):** accept `findings_rendered.experiment_id` =
  directory basename as the repo-wide `EventContext` convention; record the
  acceptance rather than change it.

## Completeness — the disposition map

Every open item maps to exactly one slice; the phase cannot exit while any row
is undispositioned. (This table is the direct answer to how §5 lost findings.)

| Item | Source | Slice |
|---|---|---|
| `bench judge` / `review build` re-run duplication | verif. §4.4-1 | 7A |
| `bench anchor` fail-open; `bench plan` unverified append | verif. §4.4-2 | 7A |
| PL-13 append onto truncated final line | register | 7A |
| GR-8 / GR-11 daemon-down misclassification + no override | register (partial) | 7B |
| GR-13 owning test | register (partial) | 7B |
| `grader` stamp write-only (ADVISORY banner misses local grades) | verif. §4.4-7 | 7B |
| PL-9 validation duplication / named-error contract | register | 7C |
| PL-10 duplicate arm names / count policy | register | 7C |
| PL-11 `==` in decision-rule DSL | register | 7C |
| GR-12 `actor="unknown"` swallow (×7 CLIs) | register | 7C |
| RN-18 residual: inert `--concurrency` / `contention_caveat` | register (partial) | 7C |
| JD-10 residual: Google API key in URL | register (partial) | 7D |
| RN-17 corrupt telemetry → silent `{}` | register | 7D |
| PR-9 vendor-overlap unknown-as-False; context gate | register | 7D |
| Rubric content not lock-committed | verif. §4.4-8 | 7D |
| RV-9 residual: reveal first-wins vs kappa last-wins; integrity-less calibration | register (partial) | 7E |
| RV-7 ordering test + stale packet docstring | register (partial) | 7E |
| RV-8(c)/(f) reference + library asymmetry | register (partial) | 7E |
| RV-3 residual: `kappa_report` unrendered; EVAL-7 D003 `raised` | register (partial) | 7E |
| CO-7 self-approval label bypass | register (partial) + verif. §4.4-3 | 7F |
| JD-1 / D-1 actions: wording + disclosure + D002 clarification | register (partial) | 7G |
| XC-7 residual: README Usage block + consistency test | register (partial) | 7G |
| §6 gate rows stale (3 rows + entrypoint count) | verif. §4.4-9 | 7G |
| N-3 decision-record drift (EVAL-8-D-P4-1, EVAL-3-D-P4-1) | verif. §4.4-9 | 7G |
| Stale docstrings (settings.py, harbor-egress test, GradingContainerError, packet.py) | verif. §4.4-9 | 7G (or owning slice) |
| Unused `import shutil` ×3 docker tests | verif. §4.4-9 | 7G |
| AN-11 residual: `experiment_id` convention | register (partial) | 7G (record) |
| Package-`__init__` import blind spot (contract + AST test) | verif. §4.4-4 | 7H |
| CI docker job green on all-skip | verif. §4.4-5 | 7H |
| Owning tests: AN-1 swapped frame, AN-10 `n_boot`, arm-payload canary, RN-15 `unknown_arm`, RN-16 rewrite-failure | verif. §3 notes | 7H |
| EVAL-1-D008 → `bench selfcheck` + official gate | verif. §5 | 7I (gated) |

Explicitly **not** reopened (already terminally dispositioned by recorded
decisions): CO-2/CO-9 opt-in gating (EVAL-8-D-P4-1/D-P4-2; official renders are
fence-backstopped), metering-proxy unit-level coverage (phase-2 disclosure),
JD-13 deterministic labels (EVAL-2-D-P6-3), CIMethod/`fractional_score`
(EVAL-6-D-P6-2), quarantine keying (REVIEW-D-2), judge packet content
(REVIEW-D-1). If the human vetoes any D-P7 recommendation, the veto is itself
recorded and that row's disposition becomes the record.

## Phasing within Phase 7

Ordered so integrity-adjacent correctness lands first and doc/enforcement
truth-up lands last (it must describe the post-fix reality). Every slice:
reproduce-first (failing test exhibiting the defect, then the fix), `make
verify` green before each commit, atomic commits, no new runtime deps, no
contract change outside D-P7-6.

### 7A — Fail-closed writers + verb idempotency · P1 · no new decision

The two integrity verbs that *write* must stop trusting their inputs, and
re-runs must stop double-counting evidence.

- `bench judge`: build the `already`-judged set from existing `judge_verdict`
  events and skip those comparisons (mirror `process/cli.py:77-83`); re-run
  appends zero events. Same for `bench review build` (`review_packet_built`
  presence ⇒ no-op that reprints the packet path).
- `bench anchor`: `assert_chain` before reading the head; broken chain ⇒ exit
  1, **nothing appended** (today it anchors a tampered ledger with exit 0).
- `bench plan`: when the ledger file exists and is non-empty, `assert_chain`
  it before appending (refuses both tampered and truncated ledgers).
- `append_event`: refuse a ledger whose final line lacks a newline
  (PL-13's prescribed fix) with an error naming the line — never concatenate.

Reproduce-first: four failing tests — judge re-run doubles events; build
re-run duplicates packets; anchor on a byte-flipped ledger exits 0; append
onto a truncated ledger concatenates. Exit: all four now refuse/no-op, with
owning tests; the e2e pipeline re-run twice end-to-end yields byte-identical
analysis inputs.

### 7B — Grade robustness: transient taxonomy, override, tier consumption · P1 · needs D-P7-2

- Pre-flight daemon probe (`docker version` via the runner seam) at
  `DockerGradeRunner` batch start: probe failure ⇒ `GraderUnavailableError`
  for every trial in the batch (transient, regradeable, baseline-inconclusive).
  Correct the `GradingContainerError` docstring ("the grader ran") which is
  false in this mode.
- Per D-P7-2: `bench grade --retry-terminal <trial-id>` — re-attempts a
  terminal `cant_grade`, event records the override + actor.
- `_tier_summary` (analyze) additionally reads grade events' `grader` field:
  any `grader == "local"` ⇒ ADVISORY banner, regardless of trial tier — closes
  the write-only-stamp hole (an explicit `--runner local` grade over trusted
  trials currently renders unflagged).
- GR-13 owning test: assert baseline evidence carries per-run assertion
  vectors (a revert to `{run, passed}` must fail).

Reproduce-first: simulate daemon-down-exit-1 through the runner seam and
assert the *current* wrong quarantine/terminal behavior, then fix. Exit:
daemon outage never ledgers flake evidence or permanently blocks regrade;
local grades always banner; all with owning tests.

### 7C — Schema & CLI hygiene · P2 · needs D-P7-1, D-P7-5, D-P7-7

- PL-10 per D-P7-1: arm-name uniqueness validator (named `ArmNameError`),
  count policy per the decision; test that duplicate names are refused at
  `plan` time.
- PL-11: restrict rule DSL operators to `>`, `>=`, `<`, `<=` (reject `==` /
  any equality on a bootstrap float) with a named error; test.
- PL-9: collapse the duplicated validation — pydantic validators become the
  single source; the loader seam (`from_dict`/`from_yaml*`) unwraps the first
  error back to the named exception types; delete `_prevalidate`. Tests pin
  the named-error contract on both loader paths so the collapse is
  behavior-preserving.
- GR-12 per D-P7-7: one shared `resolve_actor()` used by all seven CLIs.
- RN-18 residual per D-P7-5: remove `--concurrency` + the `contention_caveat`
  stamp (or implement, per the decision); README/usage updated in 7G.

Exit: `make verify` green with the consolidated validators; planted duplicate
arm name, `==` rule, and actor-less environment each refused loudly.

### 7D — Judge / run / process residue · P2–P3 · needs D-P7-6

- JD-10: Google key moves to the `x-goog-api-key` header; test asserts the key
  never appears in the URL (proxy-log leak closed).
- RN-17: `_read_native_log` corrupt JSON ⇒ `trial_infra_failed(telemetry_corrupt)`
  (the finding's own prescription) instead of silent `{}`; test with a corrupt
  `agent_log.json` through the seam.
- PR-9 per the in-slice recommendation: `spec` required; provider
  context-overflow ⇒ `CANT_SCORE(context_overflow)` with token counts.
- Rubric commitment per D-P7-6: `rubric_sha256` additive on
  `experiment_locked`; `bench judge` recomputes and refuses a swapped rubric;
  compatibility note recorded (absent field ⇒ warn, don't refuse). Mirrors the
  task-commitment tests in `test_eval8_commit.py`.

Exit: post-lock rubric swap refused (new failing-then-fixed test); corrupt
telemetry is loud; no key material in any URL.

### 7E — Review residue · P2–P3 · needs D-P7-4

- RV-9: unify the reveal join to last-wins (matching both kappa joins); with
  7A's idempotency, duplicates can only be legacy — test uses a hand-built
  duplicate-verdict ledger.
- Integrity-required calibration: `reviewed_kappa_items` skips verdicts
  without an `integrity` block (also neutralizes RV-8(f)'s library asymmetry);
  test.
- RV-7: owning ordering test — the mandatory/floor boundary must not be
  recoverable from packet order (delete-the-shuffle mutation fails); fix the
  stale "disagreements-first" docstrings in `review/packet.py`.
- RV-8(c): spec note documenting comparison-id-as-verdict-reference.
- Per D-P7-4: analyze renders IPW + floor-only sensitivity kappa per class
  (`kappa_report` gains its production caller), and EVAL-7 D003 is recorded
  `resolved`.

Exit: kappa/reveal joins agree on a duplicated ledger; sensitivity kappa
visible in the render; ordering owned by a test.

### 7F — Curation identity binding · P2 · needs D-P7-3

- Keyring format `{approver_id: pubkey}`; `admit_task` resolves the approval's
  approver to their **own** key for signature verification and refuses
  `approver == miner`; legacy list-format keyring ⇒ loud migration error.
- Reproduce-first: the verification's probe (miner signs as `"alice"` with an
  authorized key) lands as a failing test, then is refused.

Exit: relabeled self-approval refused; the e2e admission test updated to the
new keyring format; decision recorded superseding the key-only half of
EVAL-8-D-P4-3.

### 7G — Docs, decisions, and disclosure truth-up · P3 (blocked by 7A–7F outcomes)

- D-1 actions, at last: "outcome-blind" → "identity-blind" (defined once) in
  master plan §1 and README lines 5/19; analysis-side disclosure — a
  `[computed]`-tagged note in the judge section of findings + both renders
  that `judge_preference` is not independent of `holdout_pass_rate` because
  the packet includes holdout results by design (D002); append the D002
  clarification event to `eval2.decisions.ndjson`.
- README Usage block: fix `--winner 1|2|TIE|CANT_JUDGE`; add `judge`,
  `review build`, `process score`, `corpus approve|calibrate|admit`; reflect
  any 7C flag changes. Strengthen `test_readme_consistency.py` to introspect
  the typer app and assert every verb named in the Usage block exists (and
  every registered verb is documented) — README drift becomes mechanically
  caught, closing the "only pins the contract count" gap.
- Consolidated review §6: flip the three stale rows (tamper-evidence,
  sha-lock, cost-ceiling) with evidence pointers; correct "12 entrypoints" →
  13 (+ any 7I addition).
- N-3: append amendment events to EVAL-8-D-P4-1 (`holdout_ref` was removed)
  and EVAL-3-D-P4-1 (loader reads manifest runs; the *official fence* reads
  ledgered events).
- Stale docstrings (`run/settings.py`, `test_eval4_harbor_egress.py` header)
  and the three unused `import shutil`; AN-11 `experiment_id`
  accept-as-convention record; accept-as-debt records for any vetoed D-P7
  recommendation.

Exit: strengthened README test green **and** demonstrably failing on a planted
undocumented verb; grep for "outcome-blind" returns only historical audit docs.

### 7H — Enforcement hardening + missing owning tests · P2–P3

- CI docker job: set `VERDI_REQUIRE_DOCKER=1` in the workflow; under it the
  daemon-probe fixture **fails** instead of skipping, so an all-skip run can
  never green the job (the exact silent-skip mode XC-1 existed to kill).
- Import blind spot: add the package modules (`harness`, `harness.run`,
  `harness.run.engines`, …) to both `.importlinter` source lists, and extend
  the AST seam test to inspect `ImportFrom` **member names** — reproduce-first
  with the verification's planted `from .engines import harbor` in
  `harness/run/__init__.py` (on a scratch copy), which today evades both.
- Owning tests for real-but-unowned fixes: AN-1 swapped-frame verdict fixture
  (`arm_map` with A ≠ `arms[0]` — regression to the assumption must fail);
  AN-10 `ci_selection["n_boot"] == stats["n_boot"]`; arm-payload canary
  refusal (the one insulation channel without a dedicated test); RN-15
  `unknown_arm` branch; RN-16 detected-but-unwritable rewrite branch.

Exit: each planted violation fails the suite/contracts/CI before its fix or
guard is merged (recorded in the test as the reproduce-first artifact).

### 7I — `bench selfcheck` + official-render gate · gated on EVAL-1-D008

Only if D008 resolves `required-before-official` (recommended): implement per
master plan §7.7 — an A/A null-experiment + CI-coverage selfcheck over the
existing nullsim machinery at the experiment's realized N, ledgered as a
`selfcheck` event; the official render's fence additionally requires it; the
verb registers in the one-event property sweep and the AC hook picks up any
new spec-declared ACs. If D008 resolves `advisory-only`: record it, drop this
slice, and the official-finding gate in the verification doc §5 is amended
accordingly.

## Phase 7 exit criteria (all testable)

- **The disposition map is empty**: every row either has an owning test that
  fails on regression, or a recorded decision event; the verification doc
  gains a short "Phase 7 disposition" appendix stating which.
- **Idempotency + fail-closed writers**: re-running any verb adds zero events;
  `anchor`/`plan` refuse tampered or truncated ledgers (owning tests).
- **The three headline probes re-run clean**: forged grade (0 grade events),
  tampered lock line (every verb refuses — now including the writers), judge
  re-run (0 new events, unchanged findings).
- **README mechanically true and mechanically enforced** (strengthened
  consistency test fails on a planted undocumented verb).
- `make verify` green throughout; 3 import-linter contracts kept with the
  completed source lists; no new runtime dependency; the only hash-chained
  contract change is D-P7-6's additive `rubric_sha256` (with its recorded
  compatibility note) — and none if D-P7-6 is vetoed.
- CI: fast + py312 + docker jobs green, docker job hard-fails on a daemon-less
  runner instead of green-skipping.

## Working method (per CLAUDE.md — unchanged from Phases 2–6)

Reproduce-first for every fix (plant the violation, watch it fail, fix, watch
it pass); `make verify` before every commit; atomic commits whose messages say
why; single responsibility per module (the shared `resolve_actor()` lives in
one place, not seven); decisions confirmed by the human before their owning
slice, recorded as ndjson events; judgment calls listed in the final summary
for cheap veto.

## Sizing

7A/7B/7C ≈ a Phase-6-slice each (small-medium); 7D/7E small-medium; 7F small;
7G small but wide (docs + one test); 7H small-medium (mostly tests); 7I medium
(new verb + fence change, gated). Total ≈ Phase 6. Suggested commit count:
15–20 atomic commits.
