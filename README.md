# verdi-bench

A benchmark-grade A/B evaluation instrument for agent stacks, models, and
configurations: pre-registered experiments, repeated paired trials in hermetic
containers, insulated arms, deterministic-first grading, and an **identity-blind**
advisory LLM judge. *Identity-blind* (not outcome-blind): the judge never sees
arm identities, but it does see per-response holdout outcomes by design, so
`judge_preference` is deliberately not independent of `holdout_pass_rate`
(disclosed in every render, EVAL-2 D002). Every operation is a hash-chained
ledger event; every local record is stamped `ADVISORY`.

## Why this instrument exists

Most evaluation harnesses answer *"what does this model score?"* — verdi-bench
answers a harder question: *"is stack A actually better than stack B, and can
you defend that finding?"* The difference is not features, it is posture:
every trust claim below is backed by an enforcing test or structural contract
in this repo, not by convention.

- **You cannot p-hack it.** The experiment spec (primary metric, decision
  rule, seed) is sha-locked *before* any trial runs; the official render
  refuses unregistered questions, and exploratory output is watermarked as
  such on every layer.
- **You cannot quietly edit history.** Every operation appends exactly one
  typed, provenance-stamped event to a hash-chained ledger; `verify-chain`
  (optionally against externally-held anchors) detects tampering, and a
  property test sweeps every registered verb for the one-event guarantee.
- **The graders cannot hallucinate.** The deterministic grading tier and the
  forensic detector tier import no LLM client — enforced structurally by
  import-linter, not by review vigilance.
- **The judge earns its weight.** The LLM judge is blinded to arm identity
  (canary-verified), order-debiased, advisory-only, and calibrated against
  blinded human review with an IPW-corrected kappa; the one designed
  dependence it has is disclosed in every render.
- **Gaming is looked for, not assumed away.** Every trial gets a trajectory
  profile and a gaming scan (holdout tampering, hardcoded expected outputs,
  test-skip insertion, suspicious single-step completion); each detector is
  owned by a planted-violation fixture that must flag and a clean fixture
  that must not. Flags are evidence, never verdicts — exclusion is a
  ledgered human decision.

What it is *not*: a benchmark library (bring or import your corpus), a
leaderboard, or a managed fleet. Trials run serially in local containers,
adapters cover two agent platforms today, and every local result is stamped
`ADVISORY` — the trusted tier arrives as a CI-tier config cutover. For the
full architecture, threat model, and the test that owns each guarantee, read
the [deep dive](docs/deep-dive.md).

## Status

Implemented stories (following the `00-EVAL-1` master-plan build order):

| Story | Scope | State |
|---|---|---|
| **M0** | Repo scaffolding, provenance helper, import-linter contracts, AC hook | ✅ |
| **EVAL-3** | Experiment schema, hash-chained ledger, plan lock, power/MDE, interleave | ✅ |
| **EVAL-4** | Run seam, adapters, hermetic Harbor engine, cost guard, insulation | ✅ |
| **EVAL-5** | Deterministic grading, flake baseline, grader plugins | ✅ |
| **EVAL-2** | Identity-blind configurable LLM judge, calibration | ✅ |
| **EVAL-8** | Corpus import (idempotent), stratified calibration, mining, curation gate, boundary | ✅ |
| **EVAL-6** | Analyze: paired bootstrap, effect sizes, confound flags, pre-registration fence | ✅ |
| **EVAL-7** | Human review packet (offline, blinded), capture-then-reveal, kappa estimator seam | ✅ |
| **EVAL-9** | Process rubric: isolated judge scoring, firewalls, weighted-kappa calibration | ✅ |
| **EVAL-12** | Trajectory capture (versioned per-trial record, sha-ledgered) + three-layer comparison dossier | ✅ |
| **EVAL-11** | Transcript forensics: trajectory metrics, gaming detectors, blinded advisory review, quarantine path | ✅ |
| **EVAL-10** | Contamination sentinel: cutoff dating, canaries, memory probes, overlap detector, asymmetry fence | ✅ |

All EVAL-1 child stories plus the Phase-7 roadmap stories EVAL-12, EVAL-11,
and EVAL-10 are built. The fast suite
(`uv run pytest -m "not docker"`) is green — over 600 tests — plus a
`docker`-marked suite of real-container tests (a real grade container and a real
Harbor trial) run with `-m docker` in a dedicated CI job on Docker-capable
runners; 5 import-linter contracts kept. AC-mapped tests are **enforced per
story**: collection fails if any story's pre-registered acceptance criteria (from
its `eval<N>.spec.md`) lack a `test_ac<N>_*` test, or if an AC test is duplicated
or names an AC its story does not declare. `--ac-report` additionally prints the
exercised AC numbers.

## Provisional decisions

Per the master-plan decision-gate dashboard, these are implemented to the
recommended option behind a seam (a resolution is a config-sized diff):

- **D007** (power variance source) — `AssumedVariance` flags results
  `assumption_based_mde`; `CalibrationVariance` seam ready for EVAL-8 data.
- **D008** (lock hardening) — external `anchors` subsystem, on by default,
  cleanly severable.
- **D006** (kappa threshold / min sample) — `0.6 / 20` as config defaults in the
  judge `escalation` block.
- **EVAL-6 D004** (CI method by coverage) — `percentile`/`bca`/`cluster_robust_t`
  behind a `CIMethod` seam; selected by empirical coverage under `nullsim.py`.
- **EVAL-7 D003** (kappa estimator) — IPW default (floor reweighted `1/0.2`) with
  floor-only sensitivity, behind the `KappaEstimator` seam; EVAL-9 inherits it.
- **EVAL-9 D001–D004** — per-trial absolute scoring, judge+human scorers, the
  five v1 dimensions, and full-or-`CANT_SCORE` transcript policy, each
  parameterized so a decision flip is contained.
- **EVAL-11 D004** (fence coupling) — forensics is disclosure-only in v1; a
  detector must prove its precision through spot-check calibration before any
  flag can block an official finding.

## Quickstart

A complete experiment on the no-Docker fake engine, end to end:

```bash
uv sync
mkdir demo && cd demo
# write experiment.yaml (arms, corpus, repetitions, primary_metric,
# decision_rule, judge, seed, cost_ceiling — see docs/deep-dive.md §2)
uv run bench plan    experiment.yaml --ledger ledger.ndjson   # pre-register + lock
uv run bench run     .                                        # paired trials
uv run bench grade   .                                        # deterministic grades
uv run bench judge   .                                        # blinded advisory verdicts
uv run bench forensics scan .                                 # trajectory metrics + gaming scan
uv run bench selfcheck .                                      # A/A coverage gate
uv run bench analyze . --exploratory                          # findings + dossier
uv run bench verify-chain ledger.ndjson                       # audit the ledger
```

Everything the run produced — who did what, in what order, under which
instrument version — is on the ledger, and `findings.exploratory.dossier.html`
is a single self-contained file you can archive or hand to a reviewer.

## Usage

Every ledgering verb accepts `--actor <name>` (recorded on its events; refused
loudly rather than defaulted to `unknown` when the OS user is unresolvable).

```bash
uv sync
uv run bench plan   experiment.yaml --ledger ledger.ndjson   # validate + lock (commits rubric hash)
uv run bench run    <experiment-dir>                          # execute trials
uv run bench grade  <experiment-dir>                          # deterministic grades
uv run bench grade  <experiment-dir> --retry-terminal <trial-id>   # ledgered terminal-cant_grade override
uv run bench judge  <experiment-dir>                          # identity-blind advisory verdicts (idempotent)
uv run bench selfcheck <experiment-dir>                      # D008 coverage selfcheck (required before official)
uv run bench analyze <experiment-dir> --exploratory                # watermarked findings
uv run bench analyze <experiment-dir> --official --corpus m.json   # fenced official render (requires a passed selfcheck)
#   every analyze invocation also writes the self-contained comparison dossier
#   (findings.<mode>.dossier.html) beside the markdown — same fence, same
#   single findings_rendered event, no network references or external assets
uv run bench verify-chain ledger.ndjson [--against-anchor anchors.ndjson]
uv run bench anchor ledger.ndjson --out anchors.ndjson       # refuses a tampered ledger

uv run bench corpus import <tasks-dir> --cache <dir>   # idempotent public import
uv run bench corpus subset <manifest> --seed 1234      # stratified calibration subset
uv run bench corpus mine <mr.json> --ticket t.txt --out cand.json
uv run bench corpus review <cand.json>                 # curation view
uv run bench corpus approve <experiment-dir> --candidate-id c --task-sha s --signing-key k --approver alice
uv run bench corpus calibrate <experiment-dir> --manifest m.json   # ledger a calibration_run from grades
uv run bench corpus admit <experiment-dir> --manifest m.json --candidate-id c --task-sha s --baseline-ref b --keyring keyring.json

uv run bench review build  <experiment-dir>            # blinded human-review packet (idempotent)
uv run bench review record <experiment-dir> --comparison-id c1 --winner 1|2|TIE|CANT_JUDGE ...
uv run bench review reveal <experiment-dir> --comparison-id c1   # refuses pre-verdict
uv run bench process score  <experiment-dir>          # isolated judge process scoring
uv run bench process record <experiment-dir> --trial-id t1 --comparison-id c1 --scores s.json
uv run bench forensics scan <experiment-dir> [--no-review]   # trajectory metrics + gaming detectors (+ blinded advisory review)
uv run bench forensics record <experiment-dir> --trial-id t1 --labels labels.json --stratum mandatory|floor
uv run bench forensics quarantine <experiment-dir> --trial-id t1 --reason "confirmed holdout tamper"   # ledgered operator exclusion, disclosed

uv run bench contamination probe <experiment-dir> --manifest m.json   # membership probes + overlap scan (one ledgered event)
```

`bench run` defaults to the hermetic **fake** engine (fast, no Docker).
`--engine harbor` runs the real container path: digest-pinned images
(`--pull=never`), the task prompt + arm delivered read-only at
`/verdi/request.json` (outside the graded workspace), provider keys env-injected
and redacted at capture, egress confined to the metering proxy on an internal
docker network with per-trial JSONL attribution, and containers killed on
timeout. Operational wiring (proxy, quotas, provider-key names) comes from an
optional `run.config.yaml` + the environment — never the sha-locked
`experiment.yaml` or the ledger. Its container behavior is covered by
`docker`-marked tests in CI (`uv run pytest -m docker`).

`bench grade` defaults to `--runner docker` (the real network-less grading
container), with `--runner local` for the no-daemon fake/test path.

## Learn more

- **[Deep dive](docs/deep-dive.md)** — the full architecture walkthrough:
  what each stage writes to the ledger, the trust mechanism behind every
  claim above (and the test that owns it), design principles, honest
  limitations, and how to extend the instrument.
- **Design record** — `docs/design/specs/` (machine-checked per-story
  acceptance criteria), `docs/design/implementation_plans/` (per-story build
  plans), and `docs/design/review/` (audit and phase reviews). Decisions are
  ledgered per story in `eval<N>.decisions.ndjson`.

## Development

```bash
uv run pytest -m "not docker" -q     # fast suite
uv run lint-imports                  # structural contracts
uv run pytest --ac-report            # recompute AC coverage
```

> **Python:** the spec binds 3.12+. This checkout's `requires-python` is relaxed
> to `>=3.11` because the 3.12 standalone build is unreachable in the current
> environment; 3.12 compatibility is verified by a `compileall` gate under a real
> 3.12 interpreter in the CI `py312-compat` job.
