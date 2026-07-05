# verdi-bench: the deep dive

This document explains the whole instrument: what each stage does, what it
writes, what it refuses, and — for every trust claim — the mechanism behind it
and the test that owns it. It is written for two readers at once: the skeptical
senior engineer who wants to know *why they should believe a number this tool
produces*, and the engineer new to the codebase who wants to understand every
moving part. Skeptics can read §1 and §4 and then spot-check the named tests;
learners should read straight through.

A convention used throughout: **"owned by"** names the test or structural
contract that fails if the claim stops being true. This repo's ethos (inherited
from its master plan and enforced in `CLAUDE.md`) is that an unverified claim
about the instrument is a defect in the instrument.

---

## 1. The problem and the threat model

You want to know whether agent stack A beats agent stack B — a different
model, a different scaffold, a different configuration. The naive approach
(run both over some tasks, compare pass rates) fails in well-documented ways,
and each failure is a design input here:

| Threat | The naive failure | The mechanism here |
|---|---|---|
| **Post-hoc metric shopping** | Run first, then pick whichever metric favors your hypothesis | The spec is sha-locked before any trial; the official render refuses unregistered questions (§4.1) |
| **Silent history editing** | Re-run the bad trial, delete the log, nobody notices | Append-only hash-chained ledger; chain verification, external anchors (§4.2) |
| **Cross-arm contamination** | One arm sees the other's output, or the grading rubric | Hermetic per-trial containers; holdouts and rubrics never enter the trial workspace, verified by canaries (§4.3) |
| **Grader subjectivity** | "The LLM judge preferred A" as a primary result | Deterministic grading is primary; the judge is advisory, blinded, and calibrated (§4.4–4.6) |
| **Benchmark gaming** | The agent deletes the tests, hardcodes expected values, and "passes" | Trajectory forensics: mechanical detectors owned by planted violations, plus a blinded advisory review (§4.9) |
| **Training-set contamination** | A model "solves" tasks it memorized, and only one arm benefits | Contamination sentinel: cutoff dating, hash-only canaries, membership probes, overlap scan; *asymmetric* flagged contamination refuses the official render (§2.9) |
| **Statistical overclaiming** | "A wins 6 of 10 tasks" with no uncertainty | Paired bootstrap CIs with a coverage-validated method, MDE always reported, pre-registered null phrasing (§4.7) |
| **Flake laundering** | Flaky tasks silently convert noise into signal | A ledgered flake baseline is an admission prerequisite for corpus tasks (§4.4, §4.10) |

None of this makes a finding *true* — it makes a finding *auditable*. The
instrument's honest posture is stamped on every artifact: local execution is
`ADVISORY` tier, exploratory renders are watermarked, and unmeasurable
telemetry is `null`, never estimated.

---

## 2. Anatomy of an experiment

The unit of work is an **experiment directory**: an `experiment.yaml` you
write, and a `ledger.ndjson` the instrument writes. Everything else (trial
workspaces, artifacts, renders) hangs off it. The full event vocabulary is
closed and registered in `harness/ledger/events.py`; the walkthrough below
names the events each stage appends.

### 2.1 `experiment.yaml` — the pre-registration

```yaml
arms:
  - {name: control,   platform: claude_code, model: anthropic/claude-3-5-sonnet-20241022, payload: {}}
  - {name: treatment, platform: codex,       model: openai/gpt-4o-2024-08-06,             payload: {}}
corpus: {id: public-mini, version: "1.0.0"}
repetitions: 3
primary_metric: holdout_pass_rate
decision_rule: "delta_holdout_pass_rate > 0"
judge: {model: google/gemini-1.5-pro-002, rubric: rubrics/code-task-v1.md, orders: both, temperature: 0}
seed: 1234
cost_ceiling: {amount: 25.0, currency: USD}
```

Schema validation (`harness/schema/`) is strict: `primary_metric` must come
from the closed EVAL-3 metric vocabulary — you cannot register a judge
preference, a process score, or a forensic metric as the primary metric, by
construction (owned by `test_ac5_primary_ineligible` for the forensic case).

### 2.2 `bench plan` — lock it

`harness/plan/lock.py` validates the spec, computes the sha of the exact
bytes, commits the judge rubric's content hash (so the rubric cannot be
swapped after registration), runs the power/MDE simulation
(`harness/plan/power.py`), derives the seeded paired interleave order
(`harness/plan/interleave.py`), and appends **`experiment_locked`**. From this
moment the spec file is immutable: any byte change makes every later stage
refuse with a lock mismatch (owned by `test_ac2_mutation_refused`).

The interleave deserves a sentence: trials are *paired* — each task ×
repetition runs on both arms, scheduled in a seeded interleaved order — so
time-varying confounds (provider load, rate limits) hit both arms
symmetrically, and the analysis can use paired statistics, which are far more
powerful at small N.

### 2.3 `bench run` — hermetic trials

`harness/run/seam.py:run_trial` is the single seam between the orchestrator
and any execution engine. Two engines exist: **fake** (fast, deterministic,
no Docker — the default, and the substrate for most of the test suite) and
**harbor** (real containers: digest-pinned images, `--pull=never`, the task
prompt delivered read-only at `/verdi/request.json` *outside* the graded
workspace, provider keys env-injected, egress confined to a metering proxy
with per-trial attribution, kill-on-timeout). The Harbor library is importable
only by the engine seam — an import-linter contract plus an AST sweep
(`tests/test_eval4_seam.py`) keep it that way.

Three of those container guarantees fail *closed* rather than degrading
silently. The metering proxy is an **external operational component** (a
reference squid config ships in `deploy/metering-proxy/`); a
configured-but-absent per-trial proxy log raises rather than being read as zero
egress and zero cost [PRA-H4], and a proxy that is unreachable at preflight
aborts the run instead of letting trials leak egress un-metered [PRA-M9].
Kill-on-timeout is *confirmed*, not assumed: after the kill the engine checks
`docker inspect .State.Running`, so a still-live container (whose unredacted
workspace could otherwise be graded) is reported `kill_failed` and fails the
trial closed [PRA-M7]. Trial containers also drop all capabilities, forbid
privilege escalation, and cap pids/swap [PRA-L9].

Each trial appends one **`trial`** event carrying normalized telemetry
(adapter-specific; what a platform cannot measure is `null` and listed in
`telemetry_nulls` — never estimated, owned by the `TrialRecord` model
validator). Failures append **`trial_infra_failed`** with a closed reason
vocabulary; hitting the pre-registered cost ceiling appends
**`run_stopped_cost_ceiling`** and refuses new trials. The realized schedule
is ledgered as **`executed_order`**.

Since EVAL-12, every trial also captures a **trajectory**: a versioned record
of the agent's ordered steps (`harness/run/trajectory.py`, schema v3:
`kind`, `relative_ts`, `tokens`, `cost`, `files_touched`, `exit_code`,
`command`, and the additive `detail` field for per-step forensic content),
normalized per adapter, scrubbed through the same redaction door
as every artifact, persisted as canonical JSON, and bound to the chain by an
additive `trajectory_sha` on the trial event. A corrupt trajectory fails the
trial closed; an engine that honestly cannot produce one records *absence*,
which is distinguishable from an empty record (owned by
`test_ac2_corrupt_fails_closed`, `test_ac2_absent_distinguishable_from_empty`).

### 2.4 `bench grade` — the deterministic tier

`harness/grade/` runs holdout assertions against each trial's workspace in a
network-less grading container (`--runner docker`) or locally for tests.
Holdouts are mounted read-only and never present during the trial itself —
arm insulation is verified by canary tests (`test_ac9_holdout_canaries_absent`,
`test_ac1_holdouts_readonly`). Each trial gets exactly one **`grade`** (with
per-assertion results and a binary score) or **`cant_grade`** with a named
reason — never a silent skip. Repeated-run flake measurement appends
**`flake_baseline`** events; a flake baseline is an admission prerequisite for
corpus tasks (§2.8), produced by `bench corpus baseline` against the task's
**reference-solution tree** (all-pass required; a fail-to-pass task's pre-fix
tree would always quarantine) and stamped `workspace_basis:
reference_solution` so a ran baseline is distinguishable from a fabricated
event [F-H2]. Disclosed operating characteristic: k zero-tolerance runs miss
a per-run flake of rate p with probability (1−p)^k — ≈90% at p=2%, k=5 —
so raise `--k` for stronger detection; the zero-tolerance rule itself is not
loosened.

The structural guarantee: `harness/grade/` cannot import an LLM client — an
import-linter contract, not a review convention. The grade you see was
computed by assertions, not vibes.

### 2.5 `bench judge` — the blinded advisory tier

`harness/judge/` assembles per-comparison packets (both arms' outputs for a
task × repetition), scrubs them through the blind core
(`harness/blind/core.py`: built-in identity patterns plus per-experiment
canaries derived from the arms — names, platforms, models), and asks the
configured provider for a preference, **in both presentation orders** when
`orders: both`. Verdicts land as **`judge_verdict`** events; provider failures
land as `CANT_JUDGE`-style refusals, never fabricated verdicts, over a closed
reason vocabulary (`CantJudgeReason`) that a `context_overflow` from an
oversized packet lands in cleanly rather than escaping the handler with no event
[PRA-H3]. Before any packet reaches the provider it is re-scanned for
provider-key-shaped secrets as defense-in-depth behind capture-side redaction —
a hit refuses as `secret_leak` rather than shipping the secret off-box [PRA-L4].
Only genuinely transient reasons (timeout, provider error) are re-attempted on a
re-run; deterministic refusals stay terminal. Order
consistency and judge↔deterministic agreement are computed as calibration
diagnostics (`harness/judge/calibrate.py`) and rendered with the findings.

The one designed dependence — the judge sees per-response holdout outcomes,
so its preference is not independent of the pass rate — is disclosed in every
render (EVAL-2 D002). A skeptic should treat `judge_preference` as a
secondary, correlated signal; the instrument phrases it that way itself.

### 2.6 `bench review` — the human authority

`harness/review/` builds an offline, blinded human-review packet
(**`review_packet_built`**): identity-scrubbed comparison pairs, sampled as
all judge↔deterministic *disagreements* (mandatory stratum) plus a seeded
random floor of agreements (floor stratum, inclusion probability 0.2).
Humans record **`human_verdict`** events capture-then-reveal: `bench review
reveal` refuses to show arm identities before a verdict exists (**`reveal`**
events make even *looking* auditable). Judge↔human agreement is an
IPW-corrected Cohen's kappa (`harness/review/kappa.py`) using the *realized*
floor inclusion probability — degenerate marginals yield `None`
("insufficient"), deliberately never "perfect agreement".

### 2.7 `bench process` — the openly-unblinded diagnostic tier

`harness/process/` scores *how* the work was done (planning quality, tool
efficiency, etc.) on a five-dimension rubric, per trial, via an isolated
judge call that structurally cannot receive outcome context — the packet
builder's signature is the allowlist, and a property test asserts no
verdict/winner parameter exists (`test_ac3_judge_call_isolated`). Every
attempt appends exactly one **`process_score`**, with per-dimension
`CANT_SCORE(reason)` on any failure. Human process scores calibrate the judge
per dimension with quadratic-weighted IPW kappa. Process scores are
exploratory by definition and rendered under an explicit unblinded
disclosure.

### 2.8 `bench corpus` — where tasks come from

`harness/corpus/` manages the third data lifecycle: idempotent public imports
(cached, manifest-pinned), seeded stratified calibration subsets
(**`subset_draw`**), internal task mining from merge requests, and a
curation gate — human approval (**`curation_approval`**, signed), a flake
baseline, and a calibration run (**`calibration_run`**) are prerequisites to
**`task_admitted`**. Internal corpora write only to the declared boundary
path (EVAL-8 AC-5), keeping proprietary task content structurally separated
from the public instrument.

### 2.9 `bench forensics` — did it pass *honestly*?

`harness/forensics/` (EVAL-11) reads what no outcome grade can: the
trajectory. Three parts, one **`forensics_report`** event per scan:

- **Metrics** (`metrics.py`): a closed, versioned vocabulary — step/tool
  distribution, edit→test cadence, thrash rate, time-to-first-test,
  error-recovery latency, destructive-command count — computed as a pure
  function of the trajectory record. Unmeasurable inputs yield `null`
  metrics, never estimates; byte-identical payloads for a fixed record
  (owned by `test_ac1_metrics_deterministic`).
- **Detectors** (`detectors.py`): holdout-tamper attempts, hardcoded
  expected outputs (token-boundary-aware for numeric literals), test-skip
  insertion, suspicious single-step completion. Every detector is owned by a
  planted-violation fixture that must flag and a clean corpus that must not
  (`test_ac2_planted_violations_flag`, `test_ac2_clean_corpus_silent`). Every
  flag carries an **attribution confidence** [PRA-M15]: a marker genuinely
  absent before the trial and present after is `pristine-diff` (high
  confidence); a marker in a file the agent *did* edit but for which no
  pristine baseline exists is stamped `edited-file-only` and rendered
  low-confidence — flagged, not silently dropped, so a pre-existing marker in a
  legitimately-edited file cannot masquerade as an insertion; and a change the
  evidence cannot attribute to the agent at all stays silent, because a missed
  flag is preferred to a fabricated accusation.
- **Advisory review** (`review.py`): a blinded, context-isolated LLM pass
  narrating whether a trajectory shows shortcut behavior a regex cannot
  name. It fails closed to `CANT_REVIEW(reason)` on any fault — including
  a missing transcript — tags every narrative `[judgment]`, and is
  calibrated per detector against ledgered human spot-checks
  (**`forensic_spotcheck`**) with the same IPW kappa machinery as every
  other judge in the instrument.

The deterministic tier imports no LLM client — enforced by its own
import-linter contract, mirroring the grading constraint.

Disposition is the part a skeptic should scrutinize: **flags are evidence,
never verdicts**. No flag auto-fails a trial or moves a metric. Excluding a
trial is `bench forensics quarantine` — a human act, ledgered as
**`forensic_quarantine`** with an actor and a reason, refused for unknown
trial ids, disclosed in every render, and treated as data-bearing by the
selfcheck staleness gate so the official fence cannot certify pre-quarantine
numbers. In v1 no forensic flag blocks an official render (EVAL-11 D004):
a detector must prove its precision through calibration before it can gate
findings. That is a deliberate epistemic choice, not a missing feature.

A sibling integrity tier is the **contamination sentinel**
(`harness/contamination/`, EVAL-10): deterministic cutoff dating of task
content against each arm's model (an honest tri-state — `predates_cutoff`,
`postdates_cutoff`, `unknown` — never a guess), canaries embedded at
admission and carried as `sha256(canary)` only outside task content,
membership probes ledgered as one **`contamination_probe`** event per run
(`bench contamination probe`), and solution/holdout fingerprint-overlap
scanning. Its fence coupling is deliberately asymmetric: flagged
contamination affecting *one arm but not the other* refuses the official
render (`cant_analyze: asymmetric_contamination`) because it biases the
comparison; symmetric contamination discloses instead of blocking.

### 2.10 `bench selfcheck`, `bench analyze` — the fence and the findings

`bench selfcheck` runs the A/A validation (D008): it simulates null
experiments at the realized N and verifies the chosen CI method actually
achieves its nominal coverage, appending **`selfcheck`**. An official render
requires a current, passing selfcheck.

`bench analyze` computes findings (`harness/analyze/report.py`): paired
per-task deltas, bootstrap CIs (method — `percentile`, `bca`, or
`cluster_robust_t` — selected by empirical coverage under
`harness/analyze/nullsim.py`, and each finding records the method that
*realized* its interval, so a silent fallback to a wider method is visible
rather than mislabeled [PRA-M14]), effect sizes, MDE, judge calibration,
confound flags (`harness/analyze/confounds.py`: telemetry null asymmetries,
cross-vendor token incomparability), process diagnostics, and the forensic
disclosure block.

With more than two arms the spec still pre-registers exactly one
`decision_rule`, so the k−1 pairwise comparisons cannot all carry a
simultaneous official decision without inflating the family-wise error rate
[PRA-M4]. By default only the pre-registered primary pair carries a decision
(`official_decision`); the remaining pairs render their CI and effect size but
no decision, marked exploratory. Pre-registering `multi_arm_correction: holm`
in the locked spec instead keeps every pair official under a Holm-Bonferroni
adjustment at the pre-registered level — the policy is part of the sha-locked
decision rule, chosen before any trial runs, never at analyze time [F-H7].
Two render modes:

- `--exploratory`: watermarked on every layer.
- `--official`: passes the **pre-registration fence** — locked spec, corpus
  identity, rubric hash agreement, passing current selfcheck, no asymmetric
  flagged contamination — or refuses with a named **`cant_analyze`** reason.

Every invocation appends exactly one **`findings_rendered`** and writes the
markdown plus `findings.<mode>.dossier.html` — a single self-contained HTML
file (no network references, no external assets, byte-identical for a fixed
ledger and seed) with three layers: a *verdict* layer whose every sentence is
template-generated from computed findings fields (no free-form LLM prose — a
constraint, owned by a template-inventory test), an *analyst* layer (paired
deltas, timelines, calibration, flags), and an *auditor* layer (provenance,
ledger head, chain status).

### 2.11 `bench verify-chain`, `bench anchor` — audit it

`verify-chain` recomputes the hash chain and reports the first divergence;
`anchor` exports chain heads to an externally-held anchors file, so even a
full-ledger rewrite is detectable against state the attacker does not hold.

---

## 3. The trust architecture, mechanism by mechanism

This section is the skeptic's index: claim → mechanism → owner.

| Claim | Mechanism | Owned by |
|---|---|---|
| The question was fixed before the data existed | sha-locked spec + rubric hash in `experiment_locked`; fence re-checks at render | `test_ac2_mutation_refused`, fence tests in `test_eval6_analyze.py` |
| No operation happened off the record | every verb routes through typed constructors in `events.py`; direct chain writes are contract-forbidden | one-event property sweep `test_ac7_one_event_per_operation` over the closed entrypoint registry |
| The ledger you're shown is the ledger that was written | hash chain + optional external anchors | `test_eval3_chain.py`, `test_eval3_anchors.py` |
| Arms never saw graders' answers | holdouts/rubrics outside trial workspaces; canary strings planted and asserted absent | `test_ac9_holdout_canaries_absent`, `test_ac1_holdouts_readonly` |
| Grades are mechanical | no-LLM import contracts on `harness/grade/`, the `harness/forensics/` deterministic tier, and the `harness/contamination/` detectors | three of the seven import-linter contracts |
| The judge can't favor a brand | identity scrub with per-experiment canaries; property tests plant canaries and assert absence from payloads | `test_ac1_scrub_canaries` and packet property tests |
| Judge weight is earned, not assumed | order-consistency diagnostics; IPW kappa vs blinded humans; escalation gate at κ<0.6 | `test_eval2_calibrate.py`, `test_eval7_review.py` kappa suite |
| Secrets don't leak into artifacts | capture-side redaction plus defense-in-depth rescans before any provider call; property tests with generated secrets | `test_ac2_capture_post_redaction`, redaction suites in eval4 |
| The stats mean what they say | paired bootstrap; CI method chosen by simulated coverage at realized N; A/A selfcheck gates official renders; MDE always present | `test_eval6_analyze.py`, nullsim tests, selfcheck staleness tests |
| Gaming is detected, not narrated | planted-violation-owned detectors; advisory LLM pass fails closed and calibrates per detector | `test_ac2_planted_violations_flag`, `test_ac4_cant_review_fail_closed` |
| Nothing suppresses evidence | flags/confounds/quarantines render beside the comparison in *both* renders, non-suppressing | `test_ac5_flags_render_beside_comparison` |
| Docs match the binary | README verb coverage and both this doc's and the README's spelled-out contract counts are tested; AC coverage is recomputed at collection | `test_readme_consistency.py`, `tests/ac_coverage.py` hook |

Four structural contracts complete the set: Harbor is importable only through
the engine seam, ledger appends flow only through the typed constructors, the
blinded reviewer surface never imports the unblinded operator tier, and
read-only observability imports no LLM client.

---

## 4. Design principles you'll meet everywhere

Reading any module goes faster once you know the house rules:

1. **Closed vocabularies, versioned.** Event types, metric ids, detector
   ids, CANT reasons, trajectory step kinds — all closed enums or registered
   sets. Adding a member is a deliberate, versioned act (the forensics
   vocabulary version is stamped into every report), never string drift.
2. **Null means unmeasurable.** Telemetry, trajectory fields, and forensic
   metrics are `Optional` end to end; a platform that cannot measure
   something yields `null`, renderers phrase it "not measured", and nothing
   ever imputes a zero. The `TrialRecord` validator makes a silently-imputed
   null unrepresentable.
3. **Fail loud, fail closed.** No bare `except`, no sentinel that masks
   failure. Where an operation touches evidence, the failure mode is a named
   `CANT_*` event or a refusal with the field that was wrong — a crash beats
   a silently wrong grade.
4. **One event per operation.** Attempted-but-unledgered work is
   unrepresentable; the property sweep runs every registered verb against a
   fixture and counts.
5. **Additive contract changes only.** Anything hash-chained or
   pre-registered changes by adding optional fields (`trajectory_sha`,
   `rubric_sha256` follow the same precedent), with "absent = pre-change,
   no reader may require it" migration semantics, and requires explicit
   human approval recorded in a decisions ledger.
6. **Open decisions live behind seams.** Where a design question is
   unresolved, the recommended option is implemented behind a named seam
   (`CIMethod`, `KappaEstimator`, …) so resolving it differently is a
   config-sized diff. The per-story `eval<N>.decisions.ndjson` files record
   who decided what, when, and why.
7. **Determinism by default.** No wall clock, no unseeded randomness, no
   dict-ordering dependence in anything rendered or ledgered; two renders of
   the same ledger are byte-identical (the dossier test asserts exactly
   that).

---

## 5. How the test suite keeps the instrument honest

The suite (700+ fast tests, plus Docker-marked container tests) is not just
regression cover — parts of it are the instrument's *own* integrity
mechanism:

- **AC binding.** Each story's spec (`docs/design/specs/eval<N>.spec.md`)
  pre-registers acceptance criteria with named `test_ac<N>_*` tests. A
  collection hook fails the run if an AC lacks its test, a test names an AC
  its story doesn't declare, or an AC test appears while its spec is still
  `proposed/`. Coverage is recomputed mechanically (`--ac-report`), so "the
  spec is implemented" is a checked claim.
- **Planted violations.** Detectors, redaction, blinding, and the README
  checker are all tested *positively and negatively*: a planted secret must
  be scrubbed, a planted violation must flag, a clean corpus must not, and
  the checker tests plant a phantom to prove the checker itself works.
- **Property tests.** Hypothesis generates the secrets, canaries, and
  orderings; the one-event sweep and packet-isolation tests are properties
  over the closed registries, so a new verb or parameter is automatically in
  scope.
- **Mocks only at boundaries.** Provider fakes (`FakeProvider`,
  `DeterministicFakeProvider`) and the fake engine stand in for the network
  and Docker; the logic under test is never mocked.

---

## 6. Honest limitations

Read these before trusting a result — the instrument states them about
itself, and so should its documentation:

- **Everything local is `ADVISORY`.** The trusted tier is a planned CI-tier
  cutover; today's stamps say exactly what they mean.
- **Two adapters** (claude-code, codex). Their telemetry nulls are
  deliberately asymmetric — cross-vendor token counts are flagged
  incomparable rather than compared.
- **Serial local execution.** Rigor costs wall-clock; there is no fleet
  scheduler. Repetitions × tasks × 2 arms run one at a time.
- **The LLM tiers are advisory, and the forensic detectors are young.** The
  gaming detectors ship proven against planted violations, but their
  real-world precision is only now accumulating through spot-check
  calibration; that is exactly why no flag gates the official fence in v1.
- **The corpus is yours to bring — but you can plug into a standardized one.**
  There is no *bundled* benchmark library (deliberate), and verdi-bench is an
  instrument that runs a corpus, not a benchmark that ships one. What it does
  provide is an importer path for recognized public batteries: you export a
  dataset once and `bench corpus import --benchmark swebench` maps it into
  citable, admitted, contamination-dated corpus tasks, which
  `bench corpus materialize` turns into a runnable experiment (agent-visible
  `tasks.yaml` + insulated holdouts). Agentic, test-graded batteries (SWE-bench
  family) fit naturally; string-metric Q&A batteries fit awkwardly. Executing a
  battery's tests still needs that battery's own grading image — the one
  environment-bound piece verdi consumes rather than synthesizes. Adding a
  battery is a `TaskSource` shim (`harness/corpus/benchmarks.py`), not authoring
  tasks.
- **A/A selfcheck validates coverage, not ground truth.** The fence keeps a
  finding statistically honest relative to its pre-registration; it cannot
  make a badly designed experiment meaningful.
- **Egress confinement is a shared responsibility with the deployment.** The
  engine attaches trials to a `--internal` docker network (no external route)
  and meters through a proxy, but the metering proxy itself is an external
  component you supply and validate; and `--internal` blocks the outside world,
  not the host gateway or a sibling container on the same network. Confining a
  trial to *only* the allowlisted model APIs — and making per-trial attribution
  unspoofable — depends on the proxy's allowlist and auth and, for the strongest
  posture, deployment-level firewalling (`DOCKER-USER`/host-gateway rules) the
  harness does not install. Treat `deploy/metering-proxy/` as a reference to
  validate in your environment, not a turnkey guarantee.

---

## 7. Extending the instrument

- **A new agent platform** = often zero code: have the trial emit
  `artifacts/agent_log.json` in the verdi normalized log format and declare
  `platform: generic` (full spec and multi-agent guidance in
  `docs/adapters.md`). A platform with its own native log format = one
  adapter in `harness/adapters/` implementing `normalize` (telemetry) and
  `normalize_trajectory` (steps; return `None` honestly when the platform
  exposes no trajectory) — the `Adapter` base already speaks the generic
  format, so override only what your platform measures differently. Every
  field you cannot measure stays `None` — the tests that own null-honesty
  will hold you to it.
- **A new grader** = a plugin in `harness/grade/plugins/` (see
  `groundwork.py`). It runs inside the no-LLM import contract and, on the real
  (docker) grade path, inside the same fresh-copy, **network-less** grading
  container as holdout assertions [PRA-M6] — so a plugin that shells out over the
  agent-controlled workspace has no network and no host access. Only the
  no-daemon `LocalGradeRunner` runs plugins in-process (an explicit ADVISORY
  fallback, stamped `grader_name="local"`).
- **A new gaming detector** = a detector in `harness/forensics/detectors.py`
  plus its planted-violation and clean fixtures, plus a vocabulary version
  bump — the closed-enum test forces the bump; findings across versions are
  never merged silently.
- **A new ledger event** = `register_event` + a typed `record_*` constructor
  in `harness/ledger/events.py`, README usage documentation (a test checks),
  and an entrypoint registration so the one-event property sweep covers your
  verb (`tests/test_eval3_property.py` fails closed if you forget).
- **Resolving an open decision** = a `resolved` line in the story's
  `decisions.ndjson` and the config-sized diff behind the relevant seam.

The workflow discipline is in `CLAUDE.md` and is short enough to quote in
spirit: reproduce bugs as failing tests before fixing them; run `make verify`
after every change; never weaken a test to get green; contract changes need a
human and a migration story. The instrument is only as credible as the
process that builds it — which is why the process is also enforced by
machines.
