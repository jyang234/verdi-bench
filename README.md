# verdi-bench

A benchmark-grade A/B evaluation instrument for agent stacks, models, and
configurations. You pre-register a question — *is stack A better than stack B
on these tasks?* — then run repeated paired trials in hermetic containers,
grade them deterministically against holdouts the agents never see, and end
with a finding you can hand to a skeptic: every operation on a hash-chained
ledger, every trust claim backed by an enforcing test or structural contract
in this repo, not by convention.

## Why this instrument exists

Most evaluation harnesses answer *"what does this model score?"* — verdi-bench
answers a harder question: *"is stack A actually better than stack B, and can
you defend that finding?"* The difference is not features, it is posture:

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
  blinded human review with an IPW-corrected kappa. *Identity-blind* is not
  *outcome-blind*: the judge sees per-response holdout outcomes by design —
  the one designed dependence it has, disclosed in every render.
- **Gaming is looked for, not assumed away.** Every trial gets a trajectory
  profile and a gaming scan (holdout tampering, hardcoded expected outputs,
  test-skip insertion, suspicious single-step completion); each detector is
  owned by a planted-violation fixture that must flag and a clean fixture
  that must not. Flags are evidence, never verdicts — exclusion is a
  ledgered human decision.

What it is *not*: a benchmark library (bring your corpus, or import a
standardized one), a leaderboard, or a managed fleet. Trials run serially in
local containers, and every local result is stamped `ADVISORY` — the trusted
tier arrives as a CI-tier config cutover. For the full architecture, threat
model, and the test that owns each guarantee, read the
[deep dive](docs/deep-dive.md).

## Key features

- **Pre-registered experiments** — arms, metric, decision rule, seed, and cost
  ceiling sha-locked before any trial; a power/MDE check at lock time; official
  findings pass a pre-registration fence or refuse with a named reason.
- **Hermetic execution** — a deterministic no-Docker `fake` engine for
  development, and a `harbor` engine for real trials: digest-pinned images,
  read-only task delivery outside the graded workspace, per-arm credential
  isolation, metered egress with per-trial attribution, confirmed
  kill-on-timeout.
- **Deterministic-first grading** — holdout assertions run in a fresh,
  network-less grading container and report over a nonce-authenticated stdout
  fence; the grading tier imports no LLM client, structurally.
- **An identity-blind advisory judge, calibrated** — order-debiased verdicts,
  a blinded capture-then-reveal human-review queue, and judge↔human agreement
  measured rather than assumed.
- **Integrity forensics** — per-trial trajectory metrics and gaming detectors,
  plus a contamination sentinel (training-cutoff dating, canaries, overlap
  scanning) whose *asymmetric* flags refuse the official render.
- **A tamper-evident record** — an append-only, hash-chained ledger with
  external anchors; findings and their self-contained HTML dossiers are
  byte-identical re-renders of it.
- **A Python SDK and a scaffold** — the fluent `Experiment` builder writes the
  same lockable files and drives the whole pipeline in-process; `bench init`
  scaffolds a keyless quickstart; `bench author` is the browser authoring
  surface.
- **Operator observability** — `bench status` and `bench serve` (live view,
  workspace home, static self-contained bundle), heartbeats, and full
  per-trial artifacts including trajectories and flight-recorder transcripts.
- **Corpus tooling** — idempotent imports, including standardized batteries
  (`bench corpus import --benchmark swebench`); a signed curation/admission
  gate with flake baselines; calibration subsets; canary insulation enforced
  before spend.
- **Comparable, citable results** — result cards via `bench card emit` and
  `bench card compare` set two runs side by side only when their task
  sets actually match, and refuse loudly otherwise.
- **Honest telemetry** — native adapters for two agent CLIs (claude-code,
  codex), a zero-code generic log format any stack can emit, multi-agent
  attribution, and opt-in OTLP span capture; what a platform cannot measure
  is `null`, never imputed.
- **Cost discipline** — pre-registered spend ceilings enforced mid-run,
  telemetry-measured cost, and exploratory-only control reuse for cheaper
  iteration.

## Quickstart

End to end on the deterministic fake engine and the keyless deterministic
judge — no Docker, no API keys, under a minute (the
[usage guide](docs/usage-guide.md) §1.5 walks the same flow with expected
output):

```bash
uv sync
uv run bench init scratch/quickstart               # scaffold experiment.yaml / tasks.yaml / rubric
uv run bench plan scratch/quickstart/experiment.yaml --ledger scratch/quickstart/ledger.ndjson
uv run bench run scratch/quickstart                # 12 paired trials, seeded interleave
# the fake engine is arm-blind — script the effect you want to observe:
uv run python -c 'from harness.sdk import ExperimentWorkspace as W; W("scratch/quickstart").inject_holdout_results(lambda arm, task: arm == "treatment")'
uv run bench grade scratch/quickstart --runner local   # deterministic grades
uv run bench judge scratch/quickstart              # blinded advisory verdicts
uv run bench forensics scan scratch/quickstart     # trajectory metrics + gaming scan
uv run bench selfcheck scratch/quickstart          # A/A coverage gate
uv run bench analyze scratch/quickstart --exploratory
uv run bench verify-chain scratch/quickstart/ledger.ndjson
```

Everything the run produced — who did what, in what order, under which
instrument version — is on the ledger, and
`findings.exploratory.dossier.html` is a single self-contained file you can
archive or hand to a reviewer.

Or drive the same experiment from **Python** with the SDK — the builder writes
those same files (they remain the source of truth for what gets locked) and runs
the pipeline in-process:

```python
from harness.sdk import Experiment, Task

ws = (Experiment("demo", seed=1234, cost_ceiling_usd=10.0)
      .arm("control",   model="anthropic/claude-haiku-4-5-20251001", platform="claude_code")
      .arm("treatment", model="openai/gpt-4o-2024-08-06",            platform="codex")
      .judge("fake/deterministic-2026-01-01")
      .task(Task("t1", prompt="Write solution.py defining add(a, b)..."))
      ).write("demo")                 # writes experiment.yaml, tasks.yaml, rubric.md
ws.plan(actor="me"); ws.run(engine="fake"); ws.grade(runner="local"); ws.judge()
ws.analyze(exploratory=True)          # findings + dossier — the same bytes the CLI writes
```

The usage guide §0.5 shows the complete runnable flow (including the fake-path
grade step), and `init <dir>` scaffolds the files if you prefer the CLI.

## Usage

Every ledgering verb accepts `--actor <name>` (recorded on its events; refused
loudly rather than defaulted to `unknown` when the OS user is unresolvable).

```bash
uv sync
uv run bench init   <experiment-dir>                         # scaffold experiment.yaml/tasks.yaml/rubric from templates (not ledgered)
uv run bench plan   experiment.yaml --ledger ledger.ndjson   # validate + lock (commits rubric hash)
uv run bench run    <experiment-dir>                          # execute trials
uv run bench run    <experiment-dir> --reuse-control <bundle> # reuse an unchanged control (exploratory; preflight refuses on drift)
uv run bench control-cache export <experiment-dir> --arm control --out control.bundle.json   # export a control arm for reuse
uv run bench proxy up   --allow api.anthropic.com --allow api.openai.com   # stand up the managed metering proxy for harbor egress (ledgers nothing)
uv run bench proxy down                                       # tear the managed metering proxy + its networks down
uv run bench otlp  up                                         # stand up the managed OTLP trace collector for in-trial span capture (ledgers nothing)
uv run bench otlp  down                                       # tear the collector + its network down (deletes the raw envelope log unless --keep-raw)
uv run bench images list                                      # official trial images: name -> tag (ledgers nothing)
uv run bench images build generic-llm --pin                  # build (FROM verdi-base first) and pin a trial image to a sha256 digest
uv run bench images verify <image-ref>                       # offline compliance check: hardened + network-none, asserts the harbor contract
uv run bench grade  <experiment-dir>                          # deterministic grades
uv run bench grade  <experiment-dir> --retry-terminal <trial-id>   # ledgered terminal-cant_grade override
uv run bench judge  <experiment-dir>                          # identity-blind advisory verdicts (idempotent)
uv run bench selfcheck <experiment-dir>                      # D008 coverage selfcheck (required before official)
uv run bench analyze <experiment-dir> --exploratory                # watermarked findings
uv run bench analyze <experiment-dir> --official --corpus m.json   # fenced official render (requires a passed selfcheck)
# >2-arm decision policy: pre-register `multi_arm_correction: holm` in experiment.yaml before locking (default: none)
#   every analyze invocation also writes the self-contained comparison dossier
#   (findings.<mode>.dossier.html) beside the markdown — same fence, same
#   single findings_rendered event, no network references or external assets
uv run bench card emit <experiment-dir> [--corpus m.json] [--format json|md|html] [--out card.json]   # citable, comparable result card (read-only)
uv run bench card compare <a.json> <b.json>                  # side-by-side; refuses across different task sets/metrics
uv run bench verify-chain ledger.ndjson [--against-anchor anchors.ndjson]
uv run bench anchor ledger.ndjson --out anchors.ndjson       # refuses a tampered ledger

uv run bench status <experiment-dir> [--json]          # lifecycle snapshot (read-only, ledgers nothing)
uv run bench serve  <experiment-dir> [--port 8383]     # live operator view (read-only, loopback, unblinded — see banner)
uv run bench serve  --root <workspace-dir>             # workspace home: every experiment under the root, one dashboard
uv run bench serve  <experiment-dir> --bundle out.html # static self-contained snapshot of that view (no server, no event)
uv run bench author <workspace-dir> [--actor <name>]   # draft/validate/preview experiments; the lock is its one ledgered op

uv run bench corpus import <tasks-dir> --cache <dir>   # idempotent public import (harbor json dir)
uv run bench corpus import <swe-bench.jsonl> --cache <dir> --benchmark swebench [--image-template T]   # a standardized battery
uv run bench corpus materialize <manifest> --cache <dir> --out <experiment-dir> [--all]   # → runnable tasks.yaml + holdouts
uv run bench corpus validate-tasks <experiment-dir>   # strict-lint tasks.yaml (unknown keys / drift traps); ledgers nothing
uv run bench corpus subset <manifest> --seed 1234      # stratified calibration subset
uv run bench corpus mine <mr.json> --ticket t.txt --out cand.json
uv run bench corpus review <cand.json>                 # curation view
uv run bench corpus approve <experiment-dir> --candidate-id c --task-sha s --signing-key k --approver alice
uv run bench corpus calibrate <experiment-dir> --manifest m.json   # ledger a calibration_run from grades
uv run bench corpus baseline <experiment-dir> --task-id c --task-sha s --workspace ref-solution/ --holdouts-dir holdouts/c   # run the admission-prerequisite flake baseline (k=5, reference solution, all-pass)
uv run bench corpus admit <experiment-dir> --manifest m.json --candidate-id c --task-sha s --baseline-ref b --keyring keyring.json

uv run bench review build  <experiment-dir>            # blinded human-review packet (idempotent)
uv run bench review serve  <experiment-dir> --reviewer alice   # blinded capture-then-reveal queue (never the operator view)
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
and redacted at capture, egress confined to a metering proxy on an internal
docker network with per-trial JSONL attribution, and containers killed on
timeout. **The metering proxy can be harness-managed** (`proxy.managed: true`
in `run.config.yaml`, or `bench proxy up` out of band) **or an external
component you operate** (a reference config ships in `deploy/metering-proxy/`);
egress confinement, per-trial attribution, and cost enforcement for
non-self-reporting arms depend on it — a configured-but-missing proxy log fails
loud rather than silently allowing spend, and with no proxy configured trials
get no egress route at all. Operational wiring (proxy, quotas, provider-key
names) comes from an optional `run.config.yaml` + the environment — never the
sha-locked `experiment.yaml` or the ledger. Provider keys may be declared flat
(`provider_key_names`, injected into every arm) or per-arm
(`provider_key_names_by_arm`), so a multi-model experiment can hand each arm
only its own credentials and never leak one arm's key into another's container.
The digest-pin, request-mount, and key redaction paths are covered by
`docker`-marked real-container tests in CI (`uv run pytest -m docker`); the
proxy-egress end-to-end path has a real-proxy docker test under
`deploy/metering-proxy/`.

`bench grade` defaults to `--runner docker` (the real network-less grading
container), with `--runner local` for the no-daemon fake/test path.

`bench run` also maintains `run.heartbeat.json` beside the ledger — operational
liveness (state, in-flight cell, progress, spend) for `bench status` /
`bench serve`, written atomically and never ledgered. Watching the live view
shows arm identities: it is the openly-unblinded operator tier, and anyone who
watches is disqualified from serving as that experiment's blinded reviewer —
the page banner says exactly this.

## Learn more

- **[Usage guide](docs/usage-guide.md)** — follow-along from an empty directory
  to a defensible finding: authoring `experiment.yaml`/`tasks.yaml`/holdouts, the
  full `plan → run → grade → judge → forensics → selfcheck → analyze` pipeline,
  the harbor real-container path, extending the base adapter for a custom stack,
  and how multi-agent workflows plug in (with their limits).
- **[Deep dive](docs/deep-dive.md)** — the full architecture walkthrough:
  what each stage writes to the ledger, the trust mechanism behind every
  claim above (and the test that owns it), design principles, honest
  limitations, and how to extend the instrument.
- **[Adapters](docs/adapters.md)** — the normalized telemetry/trajectory log
  contract (v1 and v2) any test subject integrates through, and the OTLP
  span projection.
- **[Engines](docs/engines.md)** — the normative engine contract: what `fake`
  and `harbor` (and any engine you add) must guarantee.
- **[Trial images](docs/images.md)** — the trial-image compatibility contract,
  the maintained image tree, and the offline `bench images verify` check.
- **Design record** — `docs/design/specs/` (machine-checked per-story
  acceptance criteria), `docs/design/implementation_plans/` (per-story build
  plans), `docs/design/review/` (audit and phase reviews), and per-story
  decision ledgers (`eval<N>.decisions.ndjson`) recording every resolved and
  still-open design question.

## How it was built

The instrument was built the way it expects its users to run experiments:
story by story from a pre-registered master plan, with each story's
acceptance criteria machine-checked at test collection
(`docs/design/specs/`; `uv run pytest --ac-report` recomputes the coverage)
and every design decision recorded in a per-story decisions ledger. An
instrument-to-product refactor then composed the Python SDK, the maintained
trial images, the managed hermetic layer (metering proxy, OTLP trace
capture), and the authoring scaffold around the unchanged measurement core —
proven by byte-identical contract goldens. Open design questions are
implemented behind named seams (CI method, kappa estimator, …) so resolving
one is a config-sized diff, not a rewrite. The full record — specs, build
plans, phase reviews, decisions — lives under `docs/design/`.

## Development

```bash
make verify                          # full gate: all tests + import contracts
uv run pytest -m "not docker" -q     # fast suite (1,600+ tests)
uv run lint-imports                  # structural contracts only
uv run pytest --ac-report            # recompute AC coverage
```

`make verify` runs the fast suite plus the 10 import-linter contracts. Two
further CI tiers cover what the fast suite cannot: `docker`-marked
real-container tests (the grade container, a Harbor trial, redaction,
digest-pinning, kill-on-timeout, metering-proxy egress attribution — run with
`-m docker` under `VERDI_REQUIRE_DOCKER=1`) and a `browser` job for the
operator/reviewer/author UI acceptance tests (under
`VERDI_REQUIRE_BROWSER=1`) — both fail-closed switches, so a job cannot
green-pass by skipping.

> **Python:** the spec binds 3.12+. This checkout's `requires-python` is relaxed
> to `>=3.11` because the 3.12 standalone build is unreachable in the current
> environment; 3.12 compatibility is verified by a `compileall` gate under a real
> 3.12 interpreter in the CI `py312-compat` job.
