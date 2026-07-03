# verdi-bench — Phase 6 plan: enforcement infrastructure — make the closed holes unable to reopen silently

**Date:** 2026-07-03 · **Follows:** Phase 5 (merged to `main`, PR #11) ·
**Source of record:** `verdi-bench-review-consolidated.md` §5 Phase 6 + §3.9 (XC-*
cross-cutting register), §3.7/§3.4 (RN-18 dead symbols), §3.5 (AN-11 analyze
minors), §3.4 (JD-13 remainder), §6 (readiness gate). Orientation:
`verdi-bench-phase-6-handoff.md`.
**Branch:** `claude/verdi-bench-phase-6-plan-4btysw` (branched from `main`, which
already contains Phase 1 + Phase 2 + Phase 3 + Phase 4 + Phase 5 + the handoff).

## Context

Phase 1 made results *integrity* real; Phase 2 made the *execution path* real;
Phase 3 made every stage *fail closed*; Phase 4 made every stage *reachable*;
Phase 5 made every reported *number* honest. A complete fake-engine experiment
now runs plan → run → grade → judge → analyze → review → process end-to-end, and
the render prints a filtered, task-clustered judge-preference delta, an
identity-bound official fence, claim tags, HTML-escaped output, an ADVISORY tier
label, and a degenerate-kappa-safe agreement table.

**Phase 6 makes the instrument's checks *on itself* honest.** Today several of
them **report without enforcing** or **pass vacuously**, so a regression that
reopens a hole a prior phase closed would sail through green: the AC-coverage hook
*prints* but never *fails*; two tests assert tautologies; two import-linter source
lists are incomplete and the compensating AST seam test scans a relative path that
is empty from any other cwd; a handful of dead/misleading symbols still stand; and
"3.12-compatible" is an unverified README claim. This is the phase where the safety
net stops having holes — after it, `make verify` **fails** on a planted AC gap, a
planted forbidden import, or a regressed insulation invariant, instead of
under-reporting.

The §9 branch/merge question in the handoff is **already resolved**: Phase 5 was
merged (PR #11) and this branch's base `main` (`6a69df7`) *is* the merge. Phase 6
builds on the merged Phase-5 base — baseline **400 passed, 3 deselected**, not the
Phase-4-close 360. Nothing to stack or reconcile.

### Re-verification against the current tree (not `01641cd`)

The consolidated review's line numbers are pre-Phase-1 and stale; Phases 1–5
shifted the tree substantially. I re-located every Phase 6 finding against the
working tree at branch HEAD. **All of the enforcement gaps reproduce**; the
already-closed items (XC-1, most of XC-7) are confirmed closed. Concrete
current-tree evidence:

**Cross-cutting / test infrastructure:**
- **XC-2 (AC hook reports, never enforces) — CONFIRMED OPEN.** `conftest.py:36-46`:
  `pytest_collection_modifyitems` collects `test_ac(\d+)_` numbers into one global
  `_seen_acs` set; `pytest_terminal_summary` prints them **only** under
  `--ac-report`; **nothing fails** on a missing/misnamed AC test, and the regex
  conflates every story's *local* AC numbers into a single 9-element union.
  **Duplicate AC test name CONFIRMED:** `def test_ac4_mde_computed` exists in
  **both** `tests/test_eval3_power.py:108` and `tests/test_eval3_lock.py:123`
  (both EVAL-3 AC-4, different concerns), which defeats name-based coverage tooling.
  **New, favorable re-verification:** the *current* per-story AC coverage is
  **complete and exact** — every `eval<N>.spec.md` AC id has a matching
  `test_ac<N>_*` in the story's `test_eval<N>_*.py` files (eval2 1-8, eval3 1-7,
  eval4 1-9, eval5 1-5, eval6 1-7, eval7 1-6, eval8 1-6, eval9 1-7; eval1 declares
  **0** ACs, so no expectation). **So an enforcing hook passes on the clean tree
  today** — no pre-existing gap to backfill; enforcement can be switched on
  immediately, and the reproduce-first is a *planted* violation.
- **XC-1 (docker suite) — CONFIRMED CLOSED.** Three `@pytest.mark.docker` tests
  exist (`tests/test_e2e_pipeline.py`, `test_e2e_harbor.py`,
  `test_eval4_harbor_request.py`), the marker is declared (`pyproject.toml:38-40`),
  and CI runs a dedicated `docker` job on ubuntu plus the `-m "not docker"` fast
  job (`.github/workflows/ci.yml`). The original XC-1 ("zero docker tests, README
  lies") is resolved. **Left only as a confirm** — is 3 docker tests enough
  real-path coverage, or add more? (recommend: sufficient; see §Decisions).
- **XC-4 (vacuous tests) — CONFIRMED OPEN, two sites:**
  - `tests/test_eval4_insulation.py:33-42`
    (`test_ac9_holdout_canaries_absent`) — canary drawn from
    `st.text(alphabet="ABCDEFGHIJKLMNOP")` prefixed `CANARY_`, prompt from the
    **disjoint** `st.text(alphabet="abcdef ghij")`, and the canary is **never
    injected** into the prompt; so `assert canary_token not in task.prompt`
    (line 42) is a tautology. The artifact-fs assertion beside it (line 41) is real,
    and `test_ac9_leak_into_prompt_refused` (line 45) already covers
    inject→`HoldoutLeakError`.
  - `tests/test_eval3_power.py:108-112` (`test_ac4_mde_computed`) — asserts
    `res["mde"] is None or res["mde"] <= 0.5` while the swept deltas default to
    `[0.02..0.50]` (`power.py:186`), so `mde` is *by construction* `None` or
    `≤ 0.5` — a tautology.
- **XC-5 (fail-open contracts) — CONFIRMED OPEN, two holes:**
  - `.importlinter` source lists are **incomplete** (verified against the live
    module inventory): contract-1 (`harbor-confined-to-seam`) omits `harness.cli`,
    `harness.entrypoints`, `harness.version`, `harness.run.{cli,egress,redact,
    types}`, `harness.run.settings`, `harness.run.engines.fake`; contract-3
    (`ledger-writes-only-via-events`) omits `harness.blind`, `harness.cli`,
    `harness.entrypoints`, `harness.version`. (Re-verification **adds
    `harness.run.settings`** to the contract-1 gap — it exists in the tree but the
    handoff's list predates or overlooked it.) An unlisted module could import the
    forbidden target undetected.
  - The compensating AST seam test uses a **relative** `pathlib.Path("harness")`
    (`tests/test_eval4_seam.py:82`) — from any cwd other than the repo root it
    globs nothing and passes vacuously; `conftest.py:19` already anchors `sys.path`
    on `__file__`, so the pattern to mirror is in-repo.
- **XC-6 (Python floor, D-7) — CONFIRMED OPEN.** `pyproject.toml:9`
  `requires-python = ">=3.11"`; `.github/workflows/ci.yml` has **no** 3.12 job or
  syntax gate, so "3.12-compatible" is unverified. D-7 is
  `pending-confirmation-at-phase-6` in `review.decisions.ndjson`.
- **XC-7 (README overclaim) — CONFIRMED LARGELY CLOSED, one residual.** The verbs
  are wired and the README is honest about union coverage and the docker suite.
  **Left:** `README.md:25` says "**271** tests green in the fast suite" — stale; the
  live fast suite is **400 passed, 3 deselected** (re-run at planning).

**Run / adapters (RN-18 dead & misleading symbols) — CONFIRMED OPEN:**
- `Outcome.not_started_cost_ceiling` (`adapters/base.py:44`) — grep-confirmed
  **never constructed** anywhere; the real ceiling stop rides
  `RunOutcome.stopped_cost_ceiling` (`run/interleave.py:132`) + the
  `run_stopped_cost_ceiling` event.
- `CostGuard.stopped` (`run/budget.py:18`, `field(default=False, init=False)`) —
  grep-confirmed **never set or read** (the `.stopped` hits are all the unrelated
  `stopped_cost_ceiling` attribute on `RunOutcome`); the module docstring even
  claims "appends a `run_stopped_cost_ceiling` event" behavior that actually lives
  in `interleave.py`.
- The `sk-ant-` secret pattern (`blind/core.py:124`) is fully **shadowed** by the
  preceding `sk-[A-Za-z0-9_\-]{16,}` pattern (`:123`) — any `sk-ant-…` token is
  already redacted by the `sk-` rule, so `:124` never matches.
- The judge `FakeProvider` **silently replays its last scripted response when
  exhausted** (`judge/providers/fake.py:109`,
  `self._responses[min(self._i, len-1)]`) instead of raising — a fixture footgun.
  Used by `tests/test_eval2_plan.py`, `test_eval2_client.py`, `test_eval9_process.py`
  (audit for over-scripting when it starts raising).

**Analyze (AN-11 minors deferred from Phase 5) — CONFIRMED OPEN:**
- `ClusterRobustTCI` drops zero-SE resamples (`analyze/ci.py:101`, `good =
  boot_ses > 0`, then studentizes only `[good]`) — silently discards degenerate
  resamples.
- BCa `z0` biased low on discrete deltas (`analyze/ci.py:120`, `frac =
  float(np.mean(boot_means < m))`, strict `<`) — mid-p (`<` + ½·`==`) is the
  standard fix; the cleanest, best-defined of the four.
- `CIMethod` set only by coverage selection (`analyze/report.py`), no config/CLI
  knob.
- `fractional_score` recorded but never read — **re-verification correction:** it
  is written into the **ledgered grade event** (`ledger/events.py:216-230`,
  `record_grade`) and **only when the lock pre-registered `fractional_scoring`**
  (`grade/deterministic.py:156,164`; `grade/cli.py:131`). It is therefore
  *pre-registered, opt-in, hash-chained* data with no analyze consumer *yet* — not
  accidental dead data. Analyze reads only `binary_score` (grep-confirmed unread in
  `harness/analyze/`). **This flips the handoff's tentative "drop the recording"
  recommendation** — see §Decisions.

**Judge (JD-13 remainder) — CONFIRMED OPEN, a decision:**
- Response-label assignment is deterministic AB/BA (`judge/client.py:63-65`,
  `_pos_to_arm`: "AB"→{1:A,2:B}, "BA"→{1:B,2:A}); both orders always run
  (`config.orders == "both"`, `client.py:160`), so position bias cancels
  regardless. The spec says "assigned randomly per call" (`eval2.spec.md:184-185`).
  Phase 5 already extended `packet_sha256` to the rendered framing (the JD-13
  provenance half). This is a **decide-whether-the-spec-wording-still-needs-
  honoring** call, not a correctness bug.

**Baseline:** `uv run pytest -m "not docker" -q` → **400 passed, 3 deselected**
(re-run at planning); `make verify` green; 3 import-linter contracts kept
(`harbor-confined-to-seam`, `grade-has-no-llm-clients`,
`ledger-writes-only-via-events`). Phase 6 adds **no** runtime dependency, **no**
Docker requirement, and **no** hash-chained event-schema change.

## Decisions

Phase 6 is mostly mechanical hardening, but a few direction-setting choices need
explicit human resolution **before** the owning slice (per CLAUDE.md "the human
decides"), each recorded as a `resolved` event in the owning `evalN.decisions.ndjson`
(or `review.decisions.ndjson`) before its slice lands, mirroring Phase 5's
`D-P5-*` convention.

### Carried forward (confirmed at planning start)

- **REVIEW-D-7 (XC-6) — Python 3.11 floor. Confirmed `confirm-debt-plus-syntax-gate`
  (jyang, 2026-07-03),** recorded in `review.decisions.ndjson`. Keep 3.11 as the local
  floor (the 3.12 standalone build is unreachable behind the proxy,
  `pyproject.toml:6-8`) but add a cheap CI gate that verifies the claimed 3.12
  compatibility, so "3.12-compatible" stops being an unverified README claim.
  Trade-off: a full 3.12 CI matrix job is the strongest but heaviest (and may be
  unavailable on the runner image); an `ast`-parse/`compileall` gate under a 3.12
  interpreter — or `ruff`/`python -m py_compile` targeting `py312` — is cheap and
  catches the common regressions (f-string backslashes, `match`, new stdlib). Owned
  by 6G.

### New Phase-6 decisions (confirmed by jyang, 2026-07-03)

**Status:** D-P6-1, D-P6-2 (incl. the `fractional_score` retention reversal),
D-P6-3, D-P6-4 **confirmed by jyang (2026-07-03)** as recommended; each recorded as
a `resolved` event in the owning ledger before its slice lands. The XC-1
"3 docker tests suffice" call is also confirmed. Recommendations retained below with
their trade-offs.

- **D-P6-1 — the AC-enforcement mechanism (XC-2, 6A).** How does the enforcing hook
  know each story's expected AC set? **Recommend deriving it from the
  `eval<N>.spec.md` AC ids** (the pre-registered contract, already the single
  source of truth — `id: "AC-<n>"` in the spec front-matter), mapping story→tests
  by the `test_eval<N>_*.py` filename convention, and **failing at collection** when
  (a) a story's expected AC has no `test_ac<N>_*` (checked **per story**, not the
  global union), (b) two collected tests share an AC function name (duplicate), or
  (c) a `test_ac<N>_` names an AC number the story's spec does not declare
  (misnamed). Trade-off: deriving from the spec avoids a second list that could
  drift, at the cost of coupling the hook to the spec file's stable AC-id format
  (mitigated by the hook failing **loudly** if a spec is missing/unparseable). The
  alternative — a committed `tests/ac_manifest` data file — is decoupled from the
  markdown but duplicates the contract and can silently desync. This is the
  widest-surface Phase-6 decision: it *defines* what "AC coverage is enforced"
  means. **Note:** deselection (`-m "not docker"`) happens *after* collection, so
  docker-marked `test_ac*` tests still count toward per-story coverage — the hook
  enforces the full contract even in the fast job. *(Owned by 6A.)*
- **D-P6-2 — the four AN-11 minors, which land / which are dropped (6E).**
  Recommend, each an independent call:
  - **BCa `z0` mid-p correction — DO.** `frac = mean(boot_means < m) + ½·mean(==
    m)` (`ci.py:120`). Clean, well-defined, hand-checkable fixture, improves a real
    CI on discrete deltas.
  - **`ClusterRobustTCI` zero-SE — DISCLOSE/HANDLE.** Instead of silently dropping
    zero-SE resamples (`ci.py:101`), record the dropped count (and fall back
    transparently when too few remain). A silent drop is exactly the class of
    thing this phase exists to remove.
  - **`fractional_score` — LEAVE RECORDED AS-IS (reverses the handoff).** It is
    *pre-registered, opt-in, hash-chained* (emitted only when the lock set
    `fractional_scoring`), so "dropping the recording" is a **ledger-schema change**
    plus deletion of a pre-registered capability — both forbidden by Phase 6's
    no-schema-change scope and CLAUDE.md's "public seams are contracts." Consuming
    it (a fractional-scoring analysis path) is real EVAL-6 feature work, out of
    enforcement scope. Recommend: leave it recorded, note the future analyze path;
    do **not** drop. *(This is the one place my re-verification contradicts the
    handoff — flagged for explicit confirmation.)*
  - **`CIMethod` config-flippability — LEAVE COVERAGE-SELECTED.** No config/CLI
    knob; empirical coverage selection under the null-sim is the designed
    mechanism. Adding an override invites a hand-picked, non-coverage-justified CI.
- **D-P6-3 — JD-13 response-label determinism (6F).** Recommend **amend
  `eval2.spec.md`** to accept the deterministic both-orders scheme (position bias
  cancels because both orders always run, so per-call randomization adds nothing but
  nondeterminism, against CLAUDE.md "determinism by default"). Trade-off: the
  alternative (honor the literal "assigned randomly per call") adds a seeded
  per-call shuffle and a provenance field for **no** bias-reduction benefit and a
  new nondeterministic surface. Record whichever against EVAL-2. *(Owned by 6F.)*
- **D-P6-4 — the fake-provider exhaustion policy (RN-18, 6D).** Recommend **raise**
  on script exhaustion (`fake.py:109`) — fail loudly, per CLAUDE.md. The silent
  last-response replay can hide a miscounted test script. Trade-off: raising will
  surface any test that over-relies on the replay; those are latent bugs and should
  be tightened, not grandfathered (audit the three consumers). *(Owned by 6D.)*

### In-slice detail (recommendation stated, settled within the owning slice)

- **XC-1 docker-coverage sufficiency (6H/context):** *recommend* the existing 3
  docker tests are sufficient — they cover the grade container exit-code path
  (`test_e2e_pipeline`), a real Harbor trial (`test_e2e_harbor`), and the
  Harbor request/redaction delivery (`test_eval4_harbor_request`); the original
  XC-1 ("zero docker tests, README lies") is closed. Phase 6 adds no new docker
  test unless the human wants more real-path breadth.
- **Duplicate-name resolution (6A):** *recommend* renaming the lock-event copy to a
  distinct name that still carries the AC-4 prefix (e.g.
  `test_ac4_mde_in_lock_event`), keeping the `power.py` unit copy as
  `test_ac4_mde_computed` — both remain EVAL-3 AC-4 coverage, the name collision is
  gone.

### Contract additions (recorded before the owning slice lands)

Phase 6 is enforcement, tests, CI config, and dead-code removal: it adds **no
runtime dependency, no new ledger event type, and no hash-chained event-schema
change** — so CLAUDE.md's "public seams are contracts" migration discipline does
**not** apply to any code change here. The only contract-*adjacent* changes are
spec/decision text:

| Change | Kind | Owner | Slice | Note |
|---|---|---|---|---|
| Enforcing AC hook (per-story spec-derived manifest) | test-infra behavior; `make verify` now **fails** on an AC gap | M0/EVAL-* | 6A | no schema; decisions entry D-P6-1; passes on the clean tree today |
| `eval2.spec.md` response-label wording amended to deterministic both-orders | **spec** text change | EVAL-2 | 6F | decisions entry (D-P6-3 or its alternative); no ledger/packet impact |
| 3.12 compatibility CI gate | CI config addition | EVAL-1 | 6G | decisions entry REVIEW-D-7; no code change |
| `fractional_score` **retained** (not dropped) | *no change* — recorded here to document the reversal | EVAL-5/6 | 6E | pre-registered hash-chained field kept; future analyze consumer is out-of-scope |

Explicitly **not** contract changes: completing the `.importlinter` source lists
(they only *widen* enforcement over existing modules, catching more, never
changing a public seam), the AST seam-test anchor, the dead-symbol removals
(`not_started_cost_ceiling`, `CostGuard.stopped`, shadowed `sk-ant-` — none is a
public seam or a constructed value), the BCa/zero-SE numeric fixes (they correct a
statistic, adding a disclosed dropped-count is additive), and the vacuous-test
replacements.

## Phasing within Phase 6

Nine slices, ordered mostly-independent and atomic. The enforcement slices (6A,
6C) land first because they are the phase's spine and their reproduce-first is a
*planted violation → check fails → check made real*; the vacuous-test, dead-symbol,
statistical, spec, and CI slices are independent and interleave; **the exit check
lands last.** Each slice is one logical change (1–3 atomic commits), ships a
**reproduce-first** test (for enforcement: a planted violation the check catches;
for behavior: a failing-then-fixed assertion), records any decision/contract entry
before it lands, and `make verify` is green before every commit. Line numbers are
the current tree.

### 6A — AC-hook enforcement + duplicate-name fix · XC-2 · P1 (needs D-P6-1)
Turn the reporting hook into an enforcing gate, checked per story.
- **Per-story expected-AC manifest (D-P6-1):** in `conftest.py`, parse each
  `docs/design/specs/eval<N>.spec.md` for its declared `AC-<n>` ids (the
  pre-registered contract); group collected `test_ac<N>_*` items by story via the
  `test_eval<N>_*.py` filename; **fail at collection** (raise a collection error /
  `pytest.UsageError`, so `pytest -q` — hence `make verify` — exits non-zero) when a
  story's expected AC has no matching test, when two collected tests share an AC
  function name (duplicate), or when a `test_ac<N>_` names an AC the spec does not
  declare (misnamed). The enforcement runs **unconditionally**; `--ac-report` stays
  a reporting-only convenience layered on top.
- **De-duplicate `test_ac4_mde_computed`:** rename the lock-event copy
  (`tests/test_eval3_lock.py:123`) to `test_ac4_mde_in_lock_event`; keep the
  `power.py` unit copy's name (`tests/test_eval3_power.py:108`). Both remain EVAL-3
  AC-4 coverage.
- **Reproduce-first:** (1) a planted `test_acX_` for a nonexistent story AC → the
  hook fails collection; then a planted *removal* of a real `test_ac<N>_` → the hook
  fails; then restore → green (a small out-of-tree fixture harness or a
  subprocess-`pytest` test that asserts the exit code, so the planted violation
  never poisons the real suite). (2) the two duplicate `test_ac4_mde_computed`
  currently coexist → after the rename + the enforcing duplicate check, a
  re-introduced duplicate name fails. Extends/adds `tests/test_conftest_ac_hook.py`
  (new), touches `tests/test_eval3_lock.py`.

### 6B — Vacuous tests replaced with assertions that can fail · XC-4 · P2
Make the two tautologies break when the behavior regresses.
- **`test_ac9_holdout_canaries_absent` (`tests/test_eval4_insulation.py:42`):** drop
  the tautological `canary_token not in task.prompt` line (inject→refusal is already
  owned by `test_ac9_leak_into_prompt_refused:45`), leaving the **real** artifact-fs
  assertion (line 41); *or*, if a positive prompt-scrub assertion is wanted here,
  seed the canary into a *field the engine copies into the workspace* and assert it
  is scrubbed from the artifact blob. Recommend the drop — the injection→`HoldoutLeakError`
  behavior is already covered, and a tautology adds no signal.
- **`test_ac4_mde_computed` (`tests/test_eval3_power.py:112`):** replace with a
  **monotonicity** assertion that the clustered power model actually supports — a
  larger task-cluster count detects a strictly-smaller-or-equal MDE than a tiny one
  (`mde_check(..., n_tasks=large) ≤ mde_check(..., n_tasks=small)`, with the small-N
  case not `None`), which fails if the power sim stops responding to N.
- **Reproduce-first:** each replaced assertion is shown to pass **vacuously** today
  (mutate the code under test — e.g. make the redactor a no-op, or make `mde_check`
  ignore `n_tasks` — and the *old* assertion still passes) and to **fail** after the
  replacement under the same mutation. Extends `tests/test_eval4_insulation.py`,
  `tests/test_eval3_power.py`.

### 6C — Import-contract completeness + cwd-independent seam test · XC-5 · P2
Close the two fail-open holes and prove the completed contract catches a violation.
- **Complete the source lists:** add to contract-1 (`harbor-confined-to-seam`)
  `harness.cli`, `harness.entrypoints`, `harness.version`,
  `harness.run.{cli,egress,redact,types,settings}`, `harness.run.engines.fake`; add
  to contract-3 (`ledger-writes-only-via-events`) `harness.blind`, `harness.cli`,
  `harness.entrypoints`, `harness.version`. Keep all three contracts green.
- **Anchor the AST seam test on `__file__`:** `tests/test_eval4_seam.py:82` →
  `pathlib.Path(__file__).resolve().parents[1] / "harness"`, mirroring
  `conftest.py:19`, so it scans `harness/` regardless of cwd.
- **Reproduce-first — planted violation:** (1) a temporary module that imports a
  forbidden target (`harness.run.engines.harbor` from a newly-listed module;
  `harness.ledger.chain` directly from `harness.blind`) → `lint-imports` **fails**;
  remove it → green. (2) run `tests/test_eval4_seam.py::test_ac1_engine_isolated`
  from a non-root cwd → today it scans nothing and passes vacuously; after the
  anchor, it scans `harness/` and a planted `import ... harbor` in a non-seam module
  is caught from any cwd. Add a small `tests/test_importlinter_contracts.py` (new)
  that asserts the completed contract catches a planted forbidden import (via a
  temp module + `importlinter` API or a subprocess run), then cleans up.

### 6D — Dead/misleading symbols removed + fake-provider fails loud · RN-18 · P3 (needs D-P6-4)
Remove or implement each dead symbol; make the fake provider raise on exhaustion.
- **Remove `Outcome.not_started_cost_ceiling`** (`adapters/base.py:44`) — never
  constructed; the real ceiling stop is `RunOutcome.stopped_cost_ceiling` +
  `run_stopped_cost_ceiling`. Confirm no serialized `TrialRecord` fixture names it.
- **Remove `CostGuard.stopped`** (`run/budget.py:18`) and fix the module docstring
  to describe what `CostGuard` actually does (accumulate + `would_exceed`), moving
  the "appends `run_stopped_cost_ceiling`" description to where it lives
  (`interleave.py`).
- **Remove the shadowed `sk-ant-` pattern** (`blind/core.py:124`) — the preceding
  `sk-[A-Za-z0-9_\-]{16,}` already redacts every `sk-ant-…` token. Add a test that a
  literal `sk-ant-…` key is fully redacted (proving the `sk-` rule covers it), so
  the removal is behavior-preserving.
- **`FakeProvider` raises on exhaustion (D-P6-4):** `judge/providers/fake.py:109` →
  raise a clear error (e.g. `IndexError`/a dedicated `FakeProviderExhausted`) when
  `self._i >= len(self._responses)` instead of replaying the last item; audit
  `tests/test_eval2_plan.py`, `test_eval2_client.py`, `test_eval9_process.py` and
  tighten any script that relied on the replay (each is a latent bug per CLAUDE.md,
  fixed explicitly — not grandfathered).
- **Reproduce-first:** a `FakeProvider(["only one"])` driven twice today returns the
  same string twice (reproduce the silent replay) → after, the second call raises. A
  literal `sk-ant-ABCD…` redacts fully both before and after (behavior-preserving).
  The two dead symbols have no constructor/reader (grep-proven) so their removal
  keeps `make verify` green. Extends `tests/test_eval2_client.py`,
  `tests/test_eval4_redaction.py`.

### 6E — AN-11 statistical minors · analyze/ci.py · P3 (needs D-P6-2)
Fix the two CI-estimator edges; document the two hygiene calls.
- **BCa `z0` mid-p (`ci.py:120`):** `frac = float(np.mean(boot_means < m)) + 0.5 *
  float(np.mean(boot_means == m))` — the standard discreteness correction; the
  strict `<` biased `z0` low on discrete deltas.
- **`ClusterRobustTCI` zero-SE disclosure (`ci.py:101`):** count the dropped zero-SE
  resamples and surface the count (return it / log it via the findings' CI-selection
  block) rather than silently discarding; keep the transparent percentile fallback
  when too few remain.
- **`fractional_score` — retained (D-P6-2):** no code change; recorded in the
  decisions/contract table as a documented reversal of the handoff's tentative drop,
  because the field is pre-registered and hash-chained.
- **`CIMethod` — left coverage-selected (D-P6-2):** no config knob added.
- **Reproduce-first:** a hand-checkable discrete-delta fixture where the strict-`<`
  `z0` differs measurably from the mid-p `z0` (the mid-p interval matches the
  by-hand value; the strict one does not); a resample set with some zero-SE draws
  where the dropped-count is currently invisible → after, it is reported. Extends
  `tests/test_eval6_analyze.py` (or a focused `tests/test_analyze_ci.py`).

### 6F — JD-13 response-label decision · eval2.spec.md · P3 (needs D-P6-3)
Settle the spec wording against the deterministic both-orders scheme.
- **Amend `eval2.spec.md:184-185`** (recommended): state that response labels are
  assigned by a fixed both-orders scheme (AB then BA), which cancels position bias
  by construction, superseding "assigned randomly per call"; cite that per-call
  randomization adds nondeterminism without bias reduction. Record the decision
  against EVAL-2 (`eval2.decisions.ndjson`). *If the human instead wants the literal
  wording honored,* the alternative is a seeded per-call shuffle in
  `judge/client.py` + a provenance field — larger surface, flagged.
- **Reproduce-first:** an assertion (in `tests/test_eval2_client.py`) that the two
  orders always run and that the A/B→arm attribution is order-correct and
  deterministic — encoding the accepted scheme so a future silent switch to a single
  order or a mislabeled position is caught. (This slice is spec+test only if the
  amendment is chosen; no client behavior changes.)

### 6G — Python-floor compatibility gate · XC-6 / REVIEW-D-7 · P3 (needs D-7 confirm)
Make "3.12-compatible" a verified claim, not README prose.
- **Add the gate to `.github/workflows/ci.yml`** per the confirmed D-7: a cheap
  3.12 syntax/compat step (e.g. a `python3.12 -m compileall harness tests` /
  `py_compile` job, or a `ruff --target-version py312` check, or — if the runner can
  reach it — a full 3.12 matrix job running the fast suite). Keep the 3.11 local
  floor and the existing fast + docker jobs.
- **Reproduce-first:** a deliberately-3.12-incompatible construct (e.g. a syntax the
  3.11 floor accepts but the gate rejects, or vice-versa depending on the chosen
  gate) makes the new CI step fail; remove it → green. Because CI is not runnable
  locally, the reproduce is a scripted local invocation of the same gate command
  over a planted file, asserting non-zero exit. Record REVIEW-D-7 resolved.

### 6H — README + §6 gate truth-up · XC-7 · P3
Every remaining claim mechanically true; the owning checks now enforce.
- **Update the stale count:** `README.md:25` `271` → the live fast-suite count
  (**400**), regenerated from the actual run, and keep it phrased so it does not
  re-stale silently (e.g. reference the command, not just the number, where
  possible).
- **Flip the §6 invariant rows whose owning check now enforces:** the "Arms
  insulated; no rubric/holdout content to the agent" row (its property test is no
  longer vacuous after 6B) moves from "property test vacuous" toward enforced;
  confirm the consolidated review §6 table and the README reflect that the
  AC-coverage (6A), vacuous-test (6B), and import-contract completeness (6C) checks
  now *enforce* rather than *report*. Do **not** flip a row whose owning check does
  not yet enforce.
- **Reproduce-first:** a doc-consistency assertion where practical — e.g. the
  README's stated contract count matches the live `.importlinter` contract count,
  or the fast-suite count claim is generated/checked — so a future stale number is
  caught. (Where a claim is inherently prose, the 6A hook is the mechanical backstop
  for the AC portion.)

### 6I — Enforcement exit check · Phase 6 exit · (integration)
The single ordered proof that the closed holes cannot silently reopen.
- Asserts, without Docker, that each enforcement mechanism **fails on a planted
  violation**: a planted missing/duplicate/misnamed AC test fails collection (6A); a
  planted forbidden import fails `lint-imports` and the anchored seam test (6C); the
  two formerly-vacuous tests fail under a behavior mutation (6B); the fake provider
  raises on exhaustion (6D); and the two AN-11 CI edges compute the corrected value
  (6E). Gathers the per-slice reproduce-first proofs into one ordered exit test.
- New `tests/test_eval_phase6_enforcement.py` (or folded into the per-story files);
  the one-event property sweep is unaffected (Phase 6 adds no ledger entrypoint).

## Phase 6 exit criteria (all testable)

Restating the review's §5 Phase 6 exit against the slices:

1. **The AC hook enforces:** `make verify` **fails** on a missing, duplicate, or
   misnamed AC test, checked **per story** (not the global union); the two
   `test_ac4_mde_computed` duplicates are resolved (XC-2, 6A).
2. **The vacuous tests can fail:** `test_ac9_holdout_canaries_absent` and
   `test_ac4_mde_computed` assert properties that break if the behavior regresses
   (XC-4, 6B).
3. **The import contracts are complete and cwd-independent:** both `.importlinter`
   source lists list every module, a planted forbidden import is caught, and the AST
   seam test scans `harness/` regardless of cwd (XC-5, 6C).
4. **The dead/misleading symbols are gone:** `not_started_cost_ceiling`,
   `CostGuard.stopped`, the shadowed `sk-ant-` removed; the fake provider raises on
   script exhaustion, with over-scripted tests tightened (RN-18, 6D).
5. **The AN-11 minors are resolved** per the §Decisions: BCa `z0` mid-p and
   `ClusterRobustTCI` zero-SE disclosure land; `fractional_score` is **retained**
   (recorded reversal); `CIMethod` stays coverage-selected (6E). **The JD-13
   response-label wording is settled** in `eval2.spec.md`, recorded against EVAL-2
   (6F).
6. **The Python-floor claim is verified** by a CI gate (XC-6/D-7, 6G), and the
   **README and §6 gate are mechanically true** — the test count is current, and the
   flipped invariant rows have owning enforcers (XC-7, 6H).
7. **`make verify` green; CI runs both jobs; no import-linter regressions;** no
   runtime dependency added and no hash-chained event-schema change; the exit check
   (6I) proves each mechanism fails on a planted violation.

## Working method (per CLAUDE.md)

- **Reproduce before fixing:** every slice ships a test that fails first. For
  enforcement work the failing artifact is a **planted violation** the check
  catches (a missing/duplicate/misnamed AC test; a forbidden import; a non-root cwd);
  for behavior work it is a mutation under which the old assertion passes and the new
  one fails. The planted violations are run in isolation (subprocess/temp module) so
  they never poison the real suite. No fixes by inspection.
- **`make verify` green** before each commit; never weaken/skip a test to get green.
  The two vacuous tests corrected in 6B and any over-scripted fake-provider tests
  tightened in 6D are the cases of *changing an existing test* — done explicitly,
  with sign-off, because they encode a tautology or rely on a footgun (per CLAUDE.md
  "changing a genuinely wrong test requires saying so").
- **Single responsibility / boundaries:** each fix lands in the subsystem that owns
  the concern — the AC hook in `conftest.py`, the contracts in `.importlinter`, the
  seam test in `tests/`, the dead symbols in their defining modules, the CI edges in
  `analyze/ci.py`, the spec wording in `eval2.spec.md`, the CI gate in the workflow.
  The `harbor-confined-to-seam`, `grade-has-no-llm-clients`, and
  `ledger-writes-only-via-events` contracts stay green — 6C only **widens** their
  source lists over existing modules.
- **Determinism / fail loudly:** the enforcing hook and seam test are deterministic
  (spec-derived, `__file__`-anchored, no wall-clock/network); the fake provider and
  the completed contracts turn silent passes into loud failures; the AN-11 fixes
  remove a silent drop and correct a biased statistic.
- **Contract discipline:** Phase 6 adds **no** ledger event type and **no**
  hash-chained event-schema change; the only contract-adjacent changes are the
  `eval2.spec.md` wording amendment (6F) and the D-7 CI-gate decision (6G), each with
  a decisions entry. The `fractional_score` retention (6E) is recorded precisely
  *because* dropping it would have been a schema change — the reversal is the
  contract-preserving choice.
- **Judgment calls flagged for cheap veto:** the D-P6-1 spec-derived AC manifest; the
  D-P6-2 AN-11 split (with the `fractional_score` reversal); the D-P6-3 spec
  amendment; the D-P6-4 fake-provider raise; the XC-1 "3 docker tests suffice" call;
  the duplicate-name rename target. All stated with a recommendation; anything new
  that arises mid-slice gets a check-in.

## Verification

- `uv run pytest -m "not docker" -q` green throughout (post-Phase-5 baseline **400
  passed, 3 deselected**); Phase 6 adds reproduce-first tests per slice and turns the
  AC hook into a gate.
- `make verify` (full gate + the three import contracts) green before each commit;
  after 6A/6C it additionally **fails** on a planted AC gap or forbidden import.
- `uv run pytest --ac-report` still prints the coverage union (a reporting
  convenience); the *enforcement* runs unconditionally at collection, independent of
  the flag.
- CI runs the fast job, the docker job, and (after 6G) the 3.12 compat gate.
- Manual sanity: from a non-root cwd, `uv run pytest tests/test_eval4_seam.py -q`
  now scans `harness/`; a scratch `test_acZ_planted` for a nonexistent story AC
  makes collection fail; a scratch module importing `harness.run.engines.harbor`
  makes `uv run lint-imports` fail.

## Scope of this approval

Approving authorizes executing **Phase 6 (6A–6I)** as atomic commits with
`make verify` green, making the enforcement/hardening changes above and recording
each decision — the enforcing AC hook (D-P6-1), the AN-11 split incl. the
`fractional_score` retention reversal (D-P6-2), the JD-13 spec amendment (D-P6-3),
the fake-provider raise (D-P6-4), and the Python-floor CI gate (REVIEW-D-7) — with a
decisions-ledger entry before its owning slice. **No new runtime dependency, no new
ledger event type, no hash-chained event-schema change, no Docker requirement
added.** Slices land 6A → 6C (the enforcement spine) first, then 6B, 6D, 6E, 6F, 6G,
6H (independent, interleavable), then **6I last**.

**Decisions — status.** Confirmed by jyang (2026-07-03) and recorded in the owning
ledgers: REVIEW-D-7 (3.12 syntax/compat gate, 6G); D-P6-1 (spec-derived per-story AC
manifest, 6A); D-P6-2 (AN-11 split with `fractional_score` **retained** — reverses
the handoff, 6E); D-P6-3 (amend `eval2.spec.md` for the deterministic both-orders
label scheme, 6F); D-P6-4 (fake-provider raises on exhaustion, 6D); and the XC-1
"3 docker tests suffice" call. I'll report at natural breakpoints. No PR unless you
ask.
