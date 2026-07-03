# verdi-bench — Phase 6 planning handoff

**For:** a fresh session that will *plan* Phase 6 (enforcement infrastructure —
make the fake/real and spec/test gaps unable to regrow silently). **Written:**
2026-07-03, at the close of Phase 5. **You have no prior context — this brief
plus the in-repo documents it points to are self-contained.**

---

## 1. Orientation

verdi-bench is a benchmark-grade A/B evaluation instrument for agent stacks
(pre-registered experiments, paired hermetic trials, insulated arms,
deterministic-first grading, an identity-blind advisory LLM judge, a hash-chained
event ledger). Its credibility is its own correctness — and Phase 6 is where the
instrument makes its *own guarantees mechanically self-enforcing*, so a future
change cannot silently reopen a hole a prior phase closed. **Read `CLAUDE.md`
(repo root) first — its directives override convenience and this brief.**

**Authoritative in-repo documents (read these before planning):**
- `docs/design/review/verdi-bench-review-consolidated.md` — the ~100-finding
  audit. **§5 is the six-phase remediation plan; Phase 6 ("enforcement
  infrastructure") is your scope.** §3.9 is the cross-cutting/test-infrastructure
  findings register (XC-* ids referenced below); §6 is the readiness gate (the
  invariant rows Phase 6 flips); §7 is "verified sound — protect with regression
  tests."
- `docs/design/review/verdi-bench-phase-{2,3,4,5}-plan.md` — the prior plans;
  **mirror their structure and rigor when you write the Phase 6 plan.**
  `verdi-bench-phase-{3,4,5}-handoff.md` are the briefs that seeded Phases 3/4/5 —
  this document mirrors them.
- `docs/design/review/review.decisions.ndjson` — resolved review decisions
  (REVIEW-D-1..D-10; **D-7 is pending your confirmation for Phase 6** — the Python
  3.11-floor call, see §5). `docs/design/specs/eval{1,3,4}.spec.md` — the AC
  contracts for the stories Phase 6's enforcement touches (M0 AC-hook, EVAL-3
  property sweep, EVAL-4 seam). `docs/design/specs/eval{N}.decisions.ndjson` —
  per-story decisions (Phase 5 added `EVAL-{2,3,6}-D-P5-*`, `REVIEW-D-5`).

**Where the program is:**
- **Phase 1 (results integrity)** — merged to `main` (PR #6).
- **Phase 2 (real execution path)** — merged to `main` (PRs #7/#8).
- **Phase 3 (the §7.2 fail-closed sweep)** — merged to `main` (PR #9).
- **Phase 4 (connective tissue)** — merged to `main` (PR #10).
- **Phase 5 (statistical correctness)** — **complete on branch
  `claude/verdi-bench-phase-5-plan-fohy0v` (15 commits ahead of `main`), not yet
  merged.** The judge-preference analysis is filtered by arm pair and
  task-clustered; the power/null sims share one cluster-by-task variance model;
  the official fence is bound to corpus identity; claim tags, HTML escaping, and
  the ADVISORY tier are surfaced; the judge alias/vendor/injection/provenance
  guards landed; degenerate kappa is undefined-insufficient; `Verdict.confidence`
  migrated to the `low|medium|high` enum. A max-effort code-review pass fixed 9
  further edge/quality defects. See §9 for the branch/merge decision you must
  make first.
- **Phase 6 (enforcement infrastructure)** — your scope. Makes the guarantees
  self-enforcing: the AC hook *fails* instead of merely reporting, the vacuous
  tests get assertions that can fail, the import-linter source lists and the AST
  seam test stop failing open, the dead/misleading symbols go, and the two
  statistical minors deferred from Phase 5 (the CI-estimator edges, the
  response-label determinism decision) are resolved.

**Working method (non-negotiable, per CLAUDE.md):** reproduce-first (a failing
test that exhibits the gap before each fix — for enforcement work, "the enforcing
check *fails* on a deliberately-planted violation, then passes once the check is
real"), `make verify` green before every commit, atomic commits whose messages
explain *why*, single-responsibility, import-linter contracts stay green. Ask the
human on direction-setting decisions; give a recommendation with trade-offs,
don't open-endedly ask.

---

## 2. Phase 6 scope & exit (from consolidated review §5)

> **Phase 6 — enforcement infrastructure.** Make the fake/real and spec/test gaps
> unable to regrow silently.
> **Exit:** `make verify` includes the enforcing AC hook; CI exercises (or
> explicitly gates) the docker suite; README claims are all mechanically true.

Phase 5 made the reported numbers honest. Phase 6 makes the *instrument's checks
on itself* honest: today several of them **report without enforcing** or **pass
vacuously**, so a regression that reopens a closed hole would sail through green.
This is the phase where the safety net stops having holes.

**⚠ The review's Phase 6 list is partly already done — re-scope before planning.**
Phases 2–5 landed a large slice of the review's original Phase 6 while building
their own exits: the docker-marked suite now **exists** (3 files) and CI **runs
it** (a dedicated `docker` job), the spec-promised verbs are **wired** (`bench
judge`/`review build`/`process score`/`corpus admit`), and the README is now
**honest** that AC coverage is "a program-wide union … not a verified per-story
guarantee." So XC-1 and most of XC-7 are effectively closed. Phase 6's *remaining*
scope (§3) is the enforcement gaps that are still open.

---

## 3. Findings Phase 6 covers, by subsystem — with current status

⚠ **The review's line numbers are from commit `01641cd` (pre-Phase-1) and are
stale; Phases 1–5 shifted the tree substantially. Re-verify every finding against
the current tree before planning — this is exactly what Phases 2/3/4/5 did (see
each plan's "Re-verification" section). Do not trust a finding is still open, or
still at the cited line, without looking.** The re-verification below was run at
the close of Phase 5 against branch `claude/verdi-bench-phase-5-plan-fohy0v`.

### Cross-cutting / test infrastructure (M0 / EVAL-3 / EVAL-4)

- **XC-1 (P0 enabler) — LARGELY CLOSED, confirm & finish.** The docker-marked
  suite now exists (`tests/test_e2e_pipeline.py`, `test_e2e_harbor.py`,
  `test_eval4_harbor_request.py`, one `@pytest.mark.docker` each), the marker is
  declared (`pyproject.toml:39`), and CI runs a dedicated `docker` job
  (`.github/workflows/ci.yml`) on ubuntu (which ships Docker) plus the
  `-m "not docker"` fast job. **Left to confirm:** is 3 docker tests *enough*
  real-path coverage (grade container exit-code gating, Harbor prompt/key
  delivery, redaction of the injected literal), or should Phase 6 add more? The
  original XC-1 ("zero docker tests, README lies about them") is resolved.
- **XC-2 (P1) — OPEN, the core Phase-6 item.** The AC hook (`conftest.py`) still
  only **reports**: `pytest_collection_modifyitems` collects `test_ac(\d+)_`
  numbers into one global set and `pytest_terminal_summary` prints them under
  `--ac-report`; **nothing fails** on a missing or misnamed AC test, and the
  regex conflates each story's local AC numbers into a single 9-element union (so
  even an enforcing hook on it couldn't detect a *story's* missing AC).
  **Duplicate AC test names persist:** `test_ac4_mde_computed` exists in **both**
  `tests/test_eval3_power.py:108` and `tests/test_eval3_lock.py:123`, which
  defeats name-based coverage tooling. **Phase 6:** add per-story expected-AC
  manifests, make the hook **fail** on a missing/duplicate/misnamed AC test, and
  de-duplicate the two `test_ac4_mde_computed`.
- **XC-4 (P2) — PARTIALLY OPEN.** Two vacuous tests remain:
  - `test_ac9_holdout_canaries_absent` (`tests/test_eval4_insulation.py`) still
    asserts `canary_token not in task.prompt` where the canary is drawn from an
    uppercase alphabet + `CANARY_` prefix and the prompt from a **disjoint**
    lowercase alphabet, and the test never injects the canary into the prompt —
    so that assertion is a tautology (the artifact-fs assertion beside it is
    real).
  - `test_ac4_mde_computed` (`tests/test_eval3_power.py:112`) asserts
    `res["mde"] is None or res["mde"] <= 0.5` while the swept deltas top out at
    `0.5` — a tautology. **Phase 6:** replace both with assertions that can fail.
- **XC-5 (P2) — OPEN.** Two fail-open holes:
  - `.importlinter` source lists are still **incomplete**: contract-1
    (`harbor-confined-to-seam`) omits `harness.cli`, `harness.entrypoints`,
    `harness.version`, `harness.run.{cli,egress,redact,types}`,
    `harness.run.engines.fake`; contract-3 (`ledger-writes-only-via-events`)
    omits `harness.blind`, `harness.cli`, `harness.entrypoints`,
    `harness.version`. An unlisted module could import the forbidden target
    undetected.
  - The compensating AST seam test uses a **relative** `pathlib.Path("harness")`
    (`tests/test_eval4_seam.py:82`) — from any other cwd it scans nothing and
    passes vacuously. **Phase 6:** complete the source lists and anchor the seam
    test on `__file__`.
- **XC-6 (P3, decision D-7) — OPEN.** The Python floor is still 3.11 while the
  plan targets 3.12+; nothing (CI matrix, a syntax gate) verifies the claimed
  3.12 compatibility. **Confirm D-7 at phase start** (recommended `confirm-debt-
  plus-syntax-gate`, see §5).
- **XC-7 (P3) — LARGELY CLOSED, minor residuals.** The verb-table overclaim is
  gone (the verbs are wired) and the README is honest about union coverage and
  the docker suite. **Left:** the fast-suite test count in `README.md` is **stale**
  (`271`; the current fast suite is **400**), and any §6 invariant-row wording
  should flip only once the owning check actually enforces (XC-2).

### Run / adapters (EVAL-4) — dead & misleading symbols

- **RN-18 (P3) — OPEN.** Dead/misleading symbols the review flagged still stand:
  - `Outcome.not_started_cost_ceiling` (`harness/adapters/base.py:44`) is never
    constructed;
  - `CostGuard.stopped` (`harness/run/budget.py:18`) is never set and its
    docstring claims behavior that actually lives in `interleave.py`;
  - the `sk-ant-` secret pattern (`harness/blind/core.py:124`) is fully shadowed
    by the preceding `sk-` pattern (`:123`);
  - the judge `FakeProvider` **silently replays its last scripted response when
    exhausted** (`harness/judge/providers/fake.py:109`,
    `self._responses[min(self._i, len-1)]`) instead of raising — a test-fixture
    footgun that can hide a miscounted script. **Phase 6:** remove or implement
    each dead symbol; make the fake provider **raise** on script exhaustion.
    (Fixing the fake provider is a fail-loud change that may surface latent
    over-scripted tests — expect to tighten a few.)

### Analyze (EVAL-6) — statistical minors deferred from Phase 5 (AN-11)

Phase 5 flagged these as a judgment call and **deferred them to Phase 6** (they
are P3 minors, not among the reproduced pathologies). Re-confirm each is wanted:
- **`ClusterRobustTCI` drops zero-SE resamples** (`harness/analyze/ci.py:101`,
  `good = boot_ses > 0`) — silently discards degenerate resamples; decide whether
  to disclose/handle rather than drop.
- **BCa `z0` biased low on discrete deltas** (`harness/analyze/ci.py:120`,
  `frac = float(np.mean(boot_means < m))` — strict `<`) — a mid-p correction
  (`< ` + ½·`==`) is the standard fix. This is the cleanest, best-defined of the
  four.
- **`CIMethod` not config-flippable** — only coverage selection sets it (no
  spec/CLI knob). Decide whether an override is even wanted (coverage selection is
  the designed mechanism).
- **`fractional_score` recorded but never read** — grade events carry it
  (`record_grade`), analyze reads only `binary_score` (grep-confirmed unread in
  `harness/analyze/`). Decide whether to consume it (a fractional-scoring analysis
  path) or drop the recording.

### Judge (EVAL-2) — JD-13 remainder

- **Response-label AB/BA determinism (JD-13 remainder) — OPEN, a decision.**
  Phase 5 extended `packet_sha256` to the rendered framing (the JD-13 provenance
  half), but left the response-label assignment **deterministic AB/BA**
  (`harness/judge/client.py`), where the spec says "assigned randomly per call"
  (`eval2.spec.md:184-185`). Because both orders are always run, position bias
  cancels regardless, so this is a **decide-whether-the-spec-wording-still-needs-
  honoring** call, not a correctness bug — resolve it explicitly (§5).

### Readiness gate (§6) — rows Phase 6 flips

The consolidated review §6 marks several invariant rows `enforced_by: review`
that flip only when the owning **enforcing** check exists. Phase 6 is where the
remaining ones flip: the AC-coverage row (XC-2), the vacuous-test rows (XC-4), and
the import-contract completeness (XC-5). The "claims tagged" and "Local = ADVISORY"
rows already flipped in Phase 5; confirm the §6 table and README reflect reality
once the Phase-6 checks enforce.

---

## 4. Infrastructure to build on (don't reinvent)

Phase 6 is enforcement, so most of it *hardens existing seams* rather than adding
features:
- **The `--ac-report` hook** (`conftest.py`) — extend this into the enforcing
  hook; it already collects AC numbers and has the `--ac-report` option and the
  `terminal_summary` seam. Add per-story expected-AC manifests (a small
  in-repo declaration per `evalN`) and a **collection-time failure** on
  missing/duplicate/misnamed AC tests.
- **The one-event property registry** (`harness/entrypoints.py`,
  `tests/test_eval3_property.py`) — the Phase-3/4 pattern for
  "discover-registrations-rather-than-hardwire"; the AC-manifest enforcement can
  mirror its discovery style.
- **The `.importlinter` contracts** — three live contracts already exist; Phase 6
  *completes their source lists* (add the unlisted modules) and keeps them green;
  it does not add new contract *types*.
- **The AST seam test** (`tests/test_eval4_seam.py`) — anchor its
  `Path("harness")` on `__file__` (`Path(__file__).resolve().parents[1] /
  "harness"`), mirroring the cwd-independence `conftest.py` already applies to
  `sys.path` (`_ROOT = Path(__file__).resolve().parent`).
- **The CI workflow** (`.github/workflows/ci.yml`) — the fast + docker jobs
  already exist; Phase 6 keeps them and adds a 3.12 syntax/compat gate if D-7
  resolves that way.
- **The BCa/ClusterRobustTCI estimators** (`harness/analyze/ci.py`) — the AN-11
  numeric edges are localized to these two classes; the mid-p `z0` fix is a
  one-line change with a hand-checkable fixture.

---

## 5. Decisions

Phase 6 is mostly mechanical hardening, but a few direction-setting choices need
explicit human resolution **before** the owning slice (per CLAUDE.md), each
recorded as a `resolved` event in the owning `evalN.decisions.ndjson`. Give a
recommendation + trade-offs, don't open-endedly ask.

- **Confirm at phase start (resolved-pending):**
  - **REVIEW-D-7 (XC-6): Python 3.11 floor.** Recommended
    `confirm-debt-plus-syntax-gate`: keep 3.11 as the local floor but add a CI
    gate (a 3.12 job, or a syntax/`ast`-parse check) that verifies the claimed
    3.12 compatibility, so "3.12-compatible" stops being an unverified README
    claim. Trade-off: a full 3.12 CI matrix job is the strongest but heaviest; a
    syntax/parse gate is cheap and catches the common regressions (f-string
    backslashes, `match`, new stdlib). Confirm before the XC-6 slice.
- **New Phase-6 decisions to raise (recommendation + trade-offs):**
  - **The AC-enforcement mechanism (XC-2).** How do per-story expected-AC
    manifests declare their ACs — a table in each `evalN.spec.md` (already the
    source of truth for AC ids), a small `evalN.acs` file, or a decorator/marker
    on the test? Recommend deriving the expected set from the **spec files' AC
    ids** (the pre-registered contract) and failing at collection when a story's
    expected AC has no `test_ac<N>_*` (per story, not the global union), plus a
    hard failure on duplicate AC test names. This is the widest-surface Phase-6
    decision — it defines what "AC coverage is enforced" *means*.
  - **The four AN-11 minors — which land, which are dropped.** Recommend: **do**
    the BCa `z0` mid-p correction (clean, well-defined, improves a real CI); **do**
    disclose-or-handle the `ClusterRobustTCI` zero-SE drop; **drop the recording**
    of `fractional_score` unless a fractional-scoring analysis is actually wanted
    (dead data on a hash-chained event is worse than absent); **leave `CIMethod`
    coverage-selected** (no config knob — the selection is the designed
    mechanism). Each is a small, independent call.
  - **JD-13 response-label determinism.** Recommend **amend the spec** to accept
    the deterministic both-orders scheme (position bias cancels because both
    orders always run, so per-call randomization adds nothing but nondeterminism)
    rather than introduce a seeded per-call shuffle. Trade-off: the alternative
    (honor the literal "assigned randomly per call") adds a seeded shuffle and a
    provenance field for no bias-reduction benefit. Record whichever against
    EVAL-2.
  - **The fake-provider exhaustion policy (RN-18).** Recommend **raise** on
    script exhaustion (fail loudly) — the silent last-response replay can hide a
    miscounted test script. Trade-off: raising will surface any test that
    over-relies on the replay; those are latent bugs and should be tightened, not
    grandfathered. Confirm because it touches many test fixtures.

---

## 6. Current baseline & how to verify

- Fast suite: `uv run pytest -m "not docker" -q` → **400 passed, 3 deselected**
  (the 3 docker-marked tests) at the close of Phase 5. `make verify` (full suite +
  import contracts) is the mandatory gate; **3 import-linter contracts kept**
  (`harbor-confined-to-seam`, `grade-has-no-llm-clients`,
  `ledger-writes-only-via-events`).
- Real-container suite: `uv run pytest -m docker` runs on the CI `docker` job (the
  local dev environment has no reachable daemon, so docker-marked tests skip
  locally). Phase 6 mostly hardens the *fast*/enforcement path; if it adds docker
  tests (XC-1 confirmation), those ride the CI docker job.
- `uv run pytest --ac-report` prints the AC-number union today; **Phase 6 turns
  this into an enforcing gate** — after the XC-2 slice, `make verify` must *fail*
  on a deliberately-removed/renamed AC test, not just under-report.
- **Contract note:** completing the `.importlinter` source lists (XC-5) is the
  Phase-6 job; keep the three live contracts green while you extend them, and
  verify the completed lists actually *catch* a planted violation (add a
  reproduce-first test module that would import a forbidden target, confirm the
  contract fails, then remove it). Phase 6 adds **no** runtime dependency and no
  hash-chained event-schema change — it is enforcement, tests, CI config, and
  dead-code removal, so the "public seams are contracts" migration discipline
  does **not** apply (except the JD-13 spec amendment, which is a spec/decision
  change, not a ledger change).

---

## 7. Suggested Phase 6 shape (mirror the phase-2/3/4/5 plans)

Plan it as ordered, mostly-independent, atomic slices, reproduce-first (for
enforcement work: plant a violation, prove the check *fails*, then make the check
real / fix the violation). A reasonable slicing:

1. **AC-hook enforcement + duplicate-name fix** (XC-2): per-story expected-AC
   manifests derived from the spec AC ids; the hook fails at collection on a
   missing/duplicate/misnamed AC test; de-duplicate the two `test_ac4_mde_computed`.
   Wire the enforcing hook into `make verify`.
2. **Vacuous tests replaced** (XC-4): `test_ac9_holdout_canaries_absent`'s prompt
   assertion actually injects the canary into the prompt and asserts refusal (or
   drops the tautological line for a real one); `test_ac4_mde_computed` asserts a
   non-tautological property (e.g. a larger N detects a strictly smaller MDE).
3. **Import-contract completeness + cwd-independent seam test** (XC-5): complete
   the two source lists; anchor the AST seam test on `__file__`; add a
   reproduce-first planted-violation check that the completed contract catches.
4. **Dead/misleading symbols** (RN-18): remove `not_started_cost_ceiling`,
   `CostGuard.stopped`, the shadowed `sk-ant-`; make `FakeProvider` raise on
   exhaustion (and tighten the tests that break).
5. **AN-11 statistical minors** (per §5 decision): BCa `z0` mid-p correction;
   `ClusterRobustTCI` zero-SE disclosure/handling; `fractional_score`
   drop-or-consume; `CIMethod` left as coverage-selected.
6. **JD-13 response-label decision** (per §5): amend the spec (recommended) or add
   the seeded per-call shuffle; record against EVAL-2.
7. **Python-floor gate** (XC-6/D-7): per the confirmed D-7, add the 3.12
   syntax/compat gate to CI (and/or the matrix job).
8. **README + §6 gate truth-up** (XC-7): update the stale test count; flip the §6
   invariant rows whose owning check now enforces; confirm every remaining README
   claim is mechanically true.
9. **Exit check:** `make verify` fails on a planted AC-coverage gap and a planted
   import-contract violation; the vacuous tests now fail when the behavior breaks;
   CI runs (or explicitly gates) the docker suite; the README is verified.

Each slice: reproduce-first (planted violation → check fails → check made real /
violation fixed); `make verify` green before each commit; no new runtime dep, no
ledger-contract change.

---

## 8. Phase 6 exit criteria (restate for your plan)

- **The AC hook enforces:** `make verify` **fails** on a missing, duplicate, or
  misnamed AC test, checked **per story** (not the global union); the two
  `test_ac4_mde_computed` duplicates are resolved (XC-2).
- **The vacuous tests can fail:** `test_ac9_holdout_canaries_absent` and
  `test_ac4_mde_computed` now assert properties that break if the behavior
  regresses (XC-4).
- **The import contracts are complete and cwd-independent:** the two
  `.importlinter` source lists list every module, a planted forbidden import is
  caught, and the AST seam test scans `harness/` regardless of cwd (XC-5).
- **The dead/misleading symbols are gone:** `not_started_cost_ceiling`,
  `CostGuard.stopped`, the shadowed `sk-ant-` removed or implemented; the fake
  provider raises on script exhaustion (RN-18).
- **The AN-11 minors are resolved** per the §5 decisions (BCa `z0` mid-p;
  `ClusterRobustTCI` zero-SE; `fractional_score`; `CIMethod`), and the JD-13
  response-label wording is settled (§5).
- **The Python-floor claim is verified** by a CI gate (D-7), and the **README and
  §6 gate are mechanically true** — every claim is backed by an enforcing check,
  the test count is current, and the flipped invariant rows have owning enforcers
  (XC-6, XC-7).
- **`make verify` green; CI runs both jobs; no import-linter regressions;** no
  runtime dependency added and no hash-chained event-schema change.

---

## 9. First thing to settle: branch / merge

Phase 5 is complete but **unmerged** on `claude/verdi-bench-phase-5-plan-fohy0v`
(15 commits ahead of `main`, which is at the Phase 4 merge `4cb6002`). Before
planning Phase 6, decide with the human:
- **Merge Phase 5 to `main` first**, then branch Phase 6 from `main` (cleanest
  history; Phase 6 builds on a merged base — recommended, matching how Phases
  3/4/5 were cut from a `main` that already contained the prior phase); **or**
- **Stack Phase 6 on the Phase 5 branch HEAD** (if you want to proceed without
  waiting on the merge).

Your session will be given its own branch directive — reconcile it with the above.
Do **not** start Phase 6 from `main` alone without Phase 5, or your baseline will
be **360** tests (the Phase-4 close) instead of **400**, the AC/vacuous/contract
findings will be at different lines, and the AN-11/JD-13 remainders this brief
scopes assume the Phase-5 tree (the coverage-per-comparison selection, the
`Confidence` enum, the fenced packet, the bound official fence).

---

*Prepared at the end of Phase 5. Treat the consolidated review as the map, this
brief as the orientation, and re-verify everything against the live tree before
committing to a plan — especially since Phases 2–5 already closed a large part of
the review's original Phase 6 (the docker suite, the wired verbs, the honest
README).*
