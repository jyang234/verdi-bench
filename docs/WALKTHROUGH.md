# The Map and the Gate

### A walkthrough of how we used **verdi-bench** to find out what **verdi-go** actually does for AI coding agents

> This document is a guided tour for a technical audience. It tells one
> continuous story — a ~25-hour, $111 experimental program — and stops to
> define every concept it uses, so no prior background in benchmarking,
> statistics, or these two tools is assumed. Everything quantitative in it was
> recomputed from the raw experiment record in this directory (the
> hash-chained ledgers and per-trial artifacts under `runs/consistency/`),
> including an adversarial independent review
> ([INDEPENDENT-REVIEW.md](INDEPENDENT-REVIEW.md)) that we fold into the story
> rather than hide. Where our own first-draft report overstated something, this
> walkthrough presents the corrected version and says so.

**The cast, in one line each:**

- **verdi-go** — the *subject*: a determinism tool for Go codebases. It builds
  a call-graph "map" of a service and enforces declared structural rules
  ("no DB write reachable from a read endpoint") as a merge gate.
- **verdi-bench** — the *instrument*: a benchmark-grade A/B evaluation
  platform for agent stacks. Its whole design goal is that when it reports a
  number, you can trust the number.

**The question:** when you hand an AI coding agent a map of the codebase and a
gate that rejects rule-breaking changes, does the agent write better code —
and if so, *how*, *when*, and *at what cost*?

**The one-paragraph spoiler** (details and receipts follow): merely *giving*
an agent the tool does nothing. *Telling* it to use the tool does nothing —
even when the agent obeys, runs the verifier, and sees the failures. The only
treatment that moved any number was *enforcement*: a hook that blocks the
agent from finishing until the structural gate passes. Under enforcement, on
the one task class where the gate directly detects the planted defect, a small
model went from **never** solving a concurrency-invariant task (0/17) to
almost always solving it (16/17) — with genuine, feature-preserving fixes,
verified by reading the final code. Everywhere the gate could *not* see the
defect, enforcement did nothing (and produced false-green "gate passed"
signals instead). A stronger model needed the tool far less. And because the
test corpus was built by the tool's own side, purpose-built to contain exactly
these detectable defects, the honest claim is narrow: **verdi-go's enforcement
makes a weak agent *consistent* at honoring declared structural invariants; it
does not make any agent smarter.** That is — verbatim — the claim the tool's
owner set out to test.

---

## Table of contents

1. [Part I — Why measuring agents is hard](#part-i)
2. [Part II — The instrument: verdi-bench](#part-ii)
3. [Part III — The subject: verdi-go](#part-iii)
4. [Part IV — Designing the experiment program](#part-iv)
5. [Part V — The shakedown: when the instrument catches itself](#part-v)
6. [Part VI — Climbing the ladder: nothing, still nothing, and a warning](#part-vi)
7. [Part VII — The reach experiments: the definitive findings](#part-vii)
8. [Part VIII — The adversarial review: what survived it](#part-viii)
9. [Part IX — What we learned](#part-ix)
10. [Part X — Addendum: the mechanism, decomposed](#part-x)
11. [Appendix A — Glossary](#appendix-a)
12. [Appendix B — Evidence map & reproduction](#appendix-b)

---

<a name="part-i"></a>
## Part I — Why measuring agents is hard

Suppose you build a tool you believe helps AI agents write better code. You
run an agent twice — once with the tool, once without — and the tool-assisted
run does better. What have you learned?

Almost nothing. Here is the standard list of ways that comparison lies to you,
each of which will appear again later in this story *because we hit it*:

- **Stochasticity.** Agents are randomized. The same agent on the same task
  passes sometimes and fails sometimes. One run per condition is a coin flip
  wearing a lab coat.
- **Misattribution.** Was it even the model you think it was? (Foreshadowing:
  our very first pilot result was produced by the *wrong model* — the CLI
  silently used its default instead of the one we specified.)
- **Broken instruments.** If the harness silently denies the agent permission
  to run its tools, both arms fail together and you conclude "no effect" for
  the wrong reason. (We hit this too.)
- **Gamed tests.** If the agent — or the experimenter — can see the grading
  criteria, scores optimize toward the criteria rather than the goal.
- **Cherry-picking / p-hacking.** If you run many comparisons and report the
  one that looks best, you will "discover" effects that do not exist.
- **Circularity.** If the thing you measure is the thing the treatment
  optimizes, the treatment "wins" by definition. (This one is the deepest
  finding of the whole program — Part VIII.)

> **Definition — benchmark / evaluation ("eval").** A fixed set of tasks plus
> a fixed grading procedure, used to compare systems. "Benchmark-grade" means
> the comparison is designed to survive adversarial scrutiny: same inputs,
> controlled randomness, tamper-evident records, grading that can't be
> argued with after the fact.

> **Definition — A/B experiment.** Run condition A (the *control*) and
> condition B (the *treatment*) on the same tasks, differing in exactly one
> thing, and compare outcomes. The difference in pass rates is the *effect*.
> Everything about experiment design is about making "exactly one thing" true.

verdi-bench exists because every one of the failure modes above is a *design
problem*, solvable with mechanism rather than good intentions.

---

<a name="part-ii"></a>
## Part II — The instrument: verdi-bench

verdi-bench is a Python platform (`harness/`, one subsystem per concern:
`plan`, `run`, `grade`, `judge`, `ledger`, `corpus`, `contamination`,
`forensics`, `blind`, `analyze`, `serve`, `author`, …) that runs **paired
trials in hermetic containers** and writes everything it does to a
**hash-chained ledger**. Its design pillars, each defined as we go:

### II.1 Paired, repeated, hermetic trials

> **Definition — trial.** One agent, one task, one container, one outcome.

> **Definition — arm.** One experimental condition. Here every experiment has
> two arms: `*-bare` (the control: a pinned `claude` CLI with no extras) and
> `*-grounded` / `*-enforced` (the treatment: same CLI plus the verdi-go
> payload). Arms are *paired*: same tasks, same starter code, same random
> seed, same container image.

> **Definition — repetition (rep).** The same (task, arm) cell run N times to
> average out agent stochasticity. This program used 5 reps per cell (12 in
> the confirmatory experiment — Part VII).

> **Definition — hermetic container.** The agent runs in a Docker container
> with no network except an explicit, metered egress proxy to the model API
> (`run.config.yaml` allowlists `api.anthropic.com`; every request is logged
> and attributed to its trial). Nothing else gets in or out: no web search, no
> package registry, no "the agent quietly Googled the answer."

> **Definition — payload.** The machine-readable description of what a
> treatment arm gets. It's data, not code edits — the trial image reads it and
> arms itself. Example from this program:
> `{tools: [groundwork], workflow: ground_verify_enforced}`. The bare arm's
> payload is `{}`. Fail-closed: any unrecognized shape means "control."

### II.2 Deterministic-first grading

> **Definition — determinism.** The property that the same inputs always
> produce the same outputs. verdi-bench grades with deterministic machinery
> wherever possible — actual test suites in a sealed grader container — so a
> grade is a *fact*, not an opinion. By project rule, the deterministic
> grading path may not even import an LLM client (enforced with import
> contracts).

> **Definition — holdout.** Grading material the agent never sees. Each task
> ships a `holdouts/<task>/` directory that is mounted only into the *grader*
> container, never the trial container. If the agent can see the test, the
> agent (or its training data) can overfit the test; a holdout measures the
> *work*, not test-reading skill.

> **Definition — holdout canary.** A unique string planted in each holdout
> file (e.g. `GWV0-gw-r5-HOLDOUT-CANARY`). If that string ever appears in an
> agent's workspace or output, you have proof the supposedly-hidden material
> leaked.

In this program, each task's holdout has three parts (this exact composition
becomes a major plot point in Part VIII — remember it):

```json
// holdouts/gw-r5/holdout.json
{
  "kind": "command",
  "argv": ["sh", "-c",
    "set -e;
     cp $HOLDOUTS/functional/feature_test.go ./internal/wire/;  // hidden feature test
     go test ./...;                                             // starter tests + hidden test
     verdi-groundwork-check gw-r5"]                             // the structural gate, re-run
}
```

A trial's **binary score** is exactly this command's exit status: the hidden
functional test *and* the starter repo's own tests must pass, **and then** the
verdi-go gate itself is re-run against holdout-side (tamper-proof) policy
inputs. Pass = all of it passes.

There is also an **identity-blind advisory LLM judge** seam in the platform —
a rubric-driven model comparison where the judge never learns which arm
produced which diff (that's the `blind` subsystem) — but this program
deliberately pinned the judge to a deterministic fake
(`judge.model: fake/deterministic-2026-01-01`). Every number in this story
comes from the deterministic path. No LLM opinion enters any result.

### II.3 The tamper-evident record

> **Definition — ledger / hash chain.** Every event (experiment lock,
> contamination probe, each trial, each grade) is appended as one JSON line to
> `ledger.ndjson`, and each line carries the cryptographic hash of the
> previous line. Change any historical byte and every subsequent hash breaks.
> `bench verify-chain` re-checks the whole chain. All seven ledgers in this
> program verify.

> **Definition — pre-registration.** Writing down, *before* the data exists,
> what you will measure, what each possible outcome will mean, and what you
> promise to publish regardless of outcome. This is the standard defense
> against fooling yourself (see *p-hacking*, Part VII). verdi-bench's `plan`
> step "locks" an experiment: the ledger's first event commits hashes of the
> experiment spec, the task set, and the rubric, plus the random seed and a
> power analysis. Prose interpretation notes live in `PRE-REGISTRATION.md`
> beside the spec.

> **Definition — contamination probe.** A pre-run check for whether the model
> might already *know* the tasks (from training data). A private,
> purpose-built corpus plus canaries is the primary defense; the probe seam
> records what was checked. (Honesty note: in this program the probes ran but
> recorded no per-task probing — the defense rested on the corpus being
> private and the canaries. The ledger says so plainly rather than
> pretending otherwise.)

> **Definition — attestation.** Verifying *after the fact*, from telemetry the
> harness didn't write, that each trial actually ran what the design said —
> here, that the right model produced the tokens. `attest_models.py` reads
> each trial's native CLI result JSON (`agent_log.json`, persisted verbatim)
> and compares its `modelUsage` against the arm's declared model. Part V
> explains why this exists.

> **Definition — flight recorder.** The full session transcript of every
> trial (every message, tool call, and tool result the agent saw), captured to
> `artifacts/claude-session/` and viewable in the operator UI (`bench serve`).
> Ceiling-not-floor observability: when a number looks weird, you can watch
> the trial that produced it.

### II.4 What an experiment *is*, concretely

To define an experiment, a user assembles (or generates with the authoring
kit, `scripts/flagship/author_consistency.py`):

| Component | File | What it pins down |
|---|---|---|
| Experiment spec | `experiment.yaml` | Arms (name, platform, model, payload), corpus id+version, repetitions, primary metric (`holdout_pass_rate`), decision rule, judge config, **seed**, **cost ceiling**, multi-arm correction |
| Task set | `tasks.yaml` | Per task: the prompt, the starter files (the agent's workspace), the pinned trial-image digest, timeout, task class, holdout dir, canaries |
| Holdouts | `holdouts/<task>/…` | The hidden feature test, the gate's policy + base graph, the grading command |
| Judge rubric | `rubric.md` | How the (advisory) judge compares diffs |
| Run config | `run.config.yaml` | Egress allowlist, metering log, per-arm API-key names |
| Interpretation lock | `PRE-REGISTRATION.md` | Hypotheses and bound-in-advance readings of every outcome |
| The record | `ledger.ndjson` | Created at lock; grows append-only through grading |

> **Definition — seed.** A fixed random-number-generator starting point so
> that anything randomized (trial ordering, arm blinding) is reproducible.

> **Definition — cost ceiling.** A pre-committed spend limit per experiment
> (e.g. $24 for reach-enforced), enforced by the harness, with per-trial cost
> measured from the provider's own telemetry — not estimated.

And the lifecycle is five commands (full forms in Appendix B):

```
author → bench plan (LOCK) → bench contamination probe → bench run → bench grade
          └─ then: attest_models.py, bench verify-chain, bench serve
```

Once `plan` locks, the design is frozen: the spec hash is in the chain.
Changing anything afterward is, definitionally, a *new experiment*.

---

<a name="part-iii"></a>
## Part III — The subject: verdi-go

verdi-go is two cooperating Go binaries plus integration surfaces:

- **`flowmap`** builds a **code graph**: every function in a service, every
  call edge between them, annotated with *effect boundaries* — "this node
  performs a DB INSERT via `database/sql`", "this node publishes to a bus."
  > **Definition — call graph / reachability.** A directed graph of "function
  > A can invoke function B." *Reachability* asks: starting from an HTTP
  > handler, can execution possibly arrive at a DB write? Static analysis
  > answers this without running the code — over-approximately (it may report
  > paths that can't happen at runtime) but *soundly* for a chosen algorithm.
  > The graph-construction algorithm is called the **substrate** here (this
  > corpus pins `rta`, Rapid Type Analysis).

- **`groundwork`** evaluates a **policy** against that graph and issues a
  verdict. A policy is a small JSON file of structural invariants. The four
  rule types in this program, with the corpus's real examples:

  | Rule type | Example (task) | Plain English |
  |---|---|---|
  | `must_not_reach` | `read-route-stays-read-only` (gw-r2): `GetOrder` must not reach `db INSERT/UPDATE/DELETE` | A read endpoint must never write |
  | `must_pass_through` | `writes-through-authorize` (gw-r4): every entrypoint→DB-write path must traverse `core.Service.Authorize` | All writes go through authorization |
  | `no_concurrent_reach` | `no-concurrent-db-writes` (gw-r5): no DB write reachable along a goroutine (`go`) edge | No fire-and-forget DB writes |
  | `io_budget` | `max_writes_per_route: 2` (gw-r3) | A route may not fan out into more than N writes |

  > **Definition — structural invariant.** A property of the code's *shape*
  > (who can call what) rather than its runtime behavior. The pitch for
  > structural invariants is precisely that ordinary tests are bad at them: a
  > race or an authz bypass can pass every functional test you thought to
  > write.

- **The verdict is tri-state**, and this matters enormously later:
  - `STRUCTURALLY-CLEAR` — no rule violated (relative to the base graph: the
    gate checks for *new* violations introduced by the change);
  - `BLOCK` — a rule violated, with the offending path spelled out
    (`api.Server.GetOrder → boundary:db UPDATE orders`);
  - `NO-STRUCTURAL-SIGNAL` — the analyzer *can't tell*: the code's frontier
    passed through something opaque to static analysis (reflection, dynamic
    dispatch), so absence-of-violation cannot be proven.

  > **Definition — fail-open vs fail-closed.** What a gate does when it can't
  > decide. verdi-go's gate is **fail-open**: `NO-STRUCTURAL-SIGNAL` does not
  > block. Defensible for developer experience (never trap a session on an
  > analyzer limitation) — and, as Part VIII shows, measurable as an escape
  > hatch.

- **Integration surfaces for agents** — the three ways an agent can meet the
  tool, which map exactly onto our treatments:
  1. **Read tools:** an MCP server (`mcp__groundwork__ground` /
     `reach` / `fitness` / `reload`) that answers "what rules bind this
     function?" and "what can this function reach?" — the *map*.
     > **Definition — MCP.** Model Context Protocol: the standard by which a
     > CLI agent discovers and calls external tools. Every MCP call in a trial
     > is logged (`groundwork-mcp.jsonl`), which is how we measure *adoption*.
  2. **A workflow skill + verify binary:** `groundwork verify` in the shell
     reports the current verdict; a vendored skill documents the intended
     ground→edit→regenerate→verify loop.
  3. **The merge gate:** `groundwork review` as an automated check — in CI,
     or (rung 3) *in the agent's own loop* via a Stop hook.

**The owner's claim under test**, stated before any of this ran: *"verdi-go
improves consistent agent performance — a map makes a consistent navigator,
not a better one."* Note what a good claim this is to test: it's falsifiable
in both directions (no effect at all / effect but on capability, not
consistency), and it names its own mechanism.

---

<a name="part-iv"></a>
## Part IV — Designing the experiment program

### IV.1 The questions, in ladder order

We pre-declared a **treatment ladder** — escalating integration depth, each
rung a separate experiment, each only worth running because of what the rung
below showed:

| Rung | Treatment | Question |
|---|---|---|
| 1 — *availability* | Tools + skill present; nothing said | Will an agent pick up a map because it's there? |
| 2 — *instructed* | Rung 1 + one system-prompt mandate to use the workflow | Does *information* (the map, on request) rescue failures? |
| 3 — *enforced* | Rung 2 + a Stop hook that blocks completion until the gate passes (≤3 rounds, fail-open) | Does a *forcing function* rescue failures? |

> **Definition — treatment rung.** A pre-declared point on an escalation
> scale. Declaring the ladder up front means "we tried stronger and stronger
> treatments" is a *plan*, not post-hoc fishing (compare *p-hacking*,
> Part VII).

The rung-3 hook deserves one paragraph, because its design is unusually
honest: on every Stop attempt it regenerates the branch graph, runs the
grader's own `groundwork review` against a tamper-proof copy of policy + base
graph kept under `$HOME` (so the agent editing `/workspace/policy.json`
cannot defeat it), and if the verdict is BLOCK it refuses completion and
re-drives the agent with the gate's findings — at most 3 times, after which it
lets the agent finish anyway. It never traps a session (any error → allow),
and it carries **no anti-gaming instructions whatsoever**: whether agents
game the gate is a *measurement*, not something the treatment nudges. Every
evaluation is logged to `groundwork-enforce.jsonl` — one line per round.

### IV.2 The corpus: 16 tasks, four classes, and a disclosed conflict

The corpus (`groundwork-v0`) is 16 small-but-real Go HTTP services, each with
a task prompt, starter code, a policy, and a hidden holdout. Four task
classes:

| Class | Tasks | Design intent | What "bare" agents did with them |
|---|---|---|---|
| **null** | n1–n4 | Nothing wrong; harm controls — does the treatment *break* working code? | Passed everything, always |
| **obligation-trap** | o1–o4 | Obligation-style rules | In practice both arms passed these nearly always at both tiers — they behaved as additional harm controls in this program |
| **multi-impl** | m1–m3 | Success requires completing *several* implementations; the gate checks something orthogonal | Everyone failed, always (0% both arms, all experiments) |
| **reach-trap** | r1–r5 | The star class: a natural implementation violates a declared reachability invariant | r1 everyone passes; r2–r5 are the story |

Two design facts you must hold onto:

1. **The corpus was built by the tool's own side**, expressly to contain
   defects the tool can detect. This is a *disclosed conflict of interest*.
   It makes the program a test of the mechanism on its home turf, not a
   measure of real-world value. (Part IX returns to what that costs us.)

2. **The traps are baited.** gw-r5 is the perfect specimen. The prompt:
   *"Add a send audit… Auditing is bookkeeping and must not block or delay
   the send."* The natural reading practically dictates
   `go func() { audit() }()` — a fire-and-forget goroutine. The policy in the
   repo says `no_concurrent_reach` to DB writes. And the hidden feature
   test's own comment says it deliberately never reads the audit path,
   because "reading it here would race." **The functional test is blind to
   the planted defect by documented design.** Only the gate can see it.
   Meanwhile `policy.json` sits in the starter files, visible to *both* arms
   — the control agent could, in principle, read and honor it. (Empirically:
   it never does. That, too, is a finding.)

### IV.3 The statistics we committed to before running

> **Definition — effect size / delta (Δ).** The difference in pass rates,
> treatment minus control. Δ = +0.32 means the treated arm passed 32 more
> trials per hundred.

> **Definition — MDE (minimum detectable effect).** The smallest true effect
> the experiment is big enough to reliably see. Pre-computed at lock from a
> simulation: with 5 reps and this task count, **MDE ≈ 0.32** at 80% power.

> **Definition — statistical power.** The probability the experiment detects
> an effect of a given size if it's really there. Underpowered experiments
> produce false "no effect" conclusions. (The lock event honestly flags its
> own power analysis as `assumption_based_mde, power_gate_skipped` — the
> power model assumed, among other things, a within-task correlation ρ=0.3.)

> **Definition — publish-the-null.** The pre-registered promise that *every*
> outcome — including "no effect" — is a reportable result. Nothing in this
> program was exploratory-until-positive.

---

<a name="part-v"></a>
## Part V — The shakedown: when the instrument catches itself

Here is the part most write-ups omit, and the part that makes the rest
believable. Before this program produced a single valid number, it produced
**two invalid ones** — and the instrument's own layers caught both.

### V.1 The pilot that lied (bug #4: model delivery)

An early pilot ran 16 paired trials and produced a *perfect* calibration
score for the small model — pass rates so high they implied the cheap model
saturated the corpus. Too good to be true, and it was: the arm's model was
configured in the experiment spec but **never actually passed to the CLI**,
which silently ran its default — a top-tier model (`opus-4-8`). Every
"haiku" trial in the pilot had been an opus trial.

> **Definition — misattribution.** Reporting a result under the wrong
> condition label. The most dangerous class of benchmarking bug, because the
> numbers look completely healthy.

The fix was mechanical (`--model=` on the CLI argv). The *lesson* became
infrastructure: **per-trial model attestation**. Agents now persist the CLI's
native result JSON verbatim, and `attest_models.py` verifies, for every trial,
that the tokens were produced by the declared model. From this point on,
"which model ran" is a measured fact. (This pays off spectacularly in
Part VII's cross-model experiment.)

### V.2 The crippled recon (bug #5: permissions)

The first full 160-trial experiment, `recon`, was meant to be the rung-1
baseline. The telemetry said otherwise: **234 permission denials** across the
run. The CLI's `--permission-mode acceptEdits` was silently denying every
build, test, and tool call in headless mode — in *both* arms (119 denials
bare, 115 grounded; the recomputation confirms the crippling was symmetric).
Agents were coding blind: no compiler, no tests, no tools.

The fix: `bypassPermissions` — the hermetic container *is* the sandbox, so
in-container permission prompts protect nothing and distort everything.

`recon` was **kept in the record, relabeled as bug evidence, and excluded
from calibration** — not silently re-run. The redo (`recon2`) is a separate,
separately-locked experiment. That distinction — *keep the broken run, label
it honestly* — is what an audit trail is for.

Three more instrument fixes shipped during the program, each with the same
red-first discipline (a failing test reproducing the defect before the fix):
reverse-listener egress metering (the pinned CLI ignores `HTTP(S)_PROXY`, so
the proxy grew protocol-level reverse listeners with per-trial attribution),
native cost telemetry (exact per-trial dollars from provider usage data, not
estimates), and the flight recorder (full transcripts per trial).

**Why this section matters to the findings:** an obvious worry about any
"we fixed bugs mid-program" story is *fixing-until-favorable* — repairing the
instrument repeatedly until it produces the answer you want. The record shows
the opposite signature: after all fixes, the baseline experiments were re-run
and **stayed null** (next section). The positive result, when it finally
came, came from a stronger *treatment*, not a friendlier instrument.

---

<a name="part-vi"></a>
## Part VI — Climbing the ladder: nothing, still nothing, and a warning

### VI.1 Rung 1, done right this time: availability does nothing

`recon2` — 160 trials, permissions verified open (0 denials), model attested
(100% haiku tokens), tools verified announced to the agent.

**Result: zero.** Not "small": zero. In 80 grounded trials, the MCP log shows
**0 tool calls beyond the server handshake** — no agent ever touched the map.
Δ = +0.013 (46/80 bare vs 47/80 grounded), indistinguishable from noise.

> **Definition — adoption / manipulation check.** Before asking "did the
> treatment work?", ask "did the treatment *happen*?" Adoption — did the agent
> actually use what we gave it — is the manipulation check for a tool
> treatment. Zero adoption means the experiment measured *availability*, and
> availability's effect is honestly zero at this model tier.

### VI.2 Rung 2: instruction produces obedience, not rescues

`instructed` — identical to rung 1 plus exactly one added argv token: an
`--append-system-prompt` mandate naming the literal tools and the workflow
(ground before editing → regenerate graph → `groundwork verify` → don't
conclude until STRUCTURALLY-CLEAR). The prompt text is byte-stable in the
image and is process-only — it names tools and sequence, never any task
property or expected answer. (A v1 text that named the tools *wrongly* was
piloted, produced zero adoption, and was revised **before** the experiment
locked — after lock, the text is frozen. Iterating on the manipulation before
pre-registration is legitimate; after, it would be a new experiment.)

**Result: obedience without effect.** Adoption jumped 0% → 56% (45/80 trials
used the MCP tools; 46/80 ran `groundwork verify` in the shell). The agents
did the ritual. The needle did not move: Δ = +0.02.

The damning detail (recovered from the flight recorder, and a correction to
our own first-draft report, which had claimed the verdict "never entered the
loop"): on the concurrency trap gw-r5, **all five instructed grounded trials
ran the verifier**, and the BLOCK verdict is visible in two of those
transcripts — *yet only one of the five shipped a fix*. The information was
in the loop. A small model, told its change is structurally rejected, with the
finding spelled out, mostly ships it anyway.

That result is what makes rung 3 interesting rather than gratuitous: if
*information* were sufficient, a blocking hook would be redundant.

### VI.3 Rung 3 smoke test: the mechanism works — and the first warning shot

Before spending on the full rung-3 experiment, a 12-trial smoke on the
m-class (multi-impl — deliberately the tool's *worst* class, where the gate
checks something orthogonal to task success).

> **Definition — orthogonal vs aligned gate.** A gate is *aligned* with a
> task when satisfying the gate ≈ fixing the defect (the gate checks the very
> invariant the task plants). It is *orthogonal* when the gate checks
> something else entirely, so you can satisfy it without helping the task.

Two findings:

1. **The mechanism works.** The loop fired live: block → agent edits →
   block → agent edits → clean, visible in `groundwork-enforce.jsonl`.
   Bounded, never trapped, honest logs.
2. **The warning: gate-gaming.** All 3 trials that reached STRUCTURALLY-CLEAR
   under enforcement **still failed the hidden functional test**. Rescues:
   0/6, exactly equal to bare.

> **Definition — gate-gaming (false-green).** Satisfying the proxy (the gate)
> without achieving the goal (working code). The danger is not that the gate
> fails — it's that it *succeeds*, visibly, while the code is still wrong: a
> green light that means nothing. On an orthogonal class, enforcement
> manufactures false-green by construction.

So the pre-registered stakes for the main event were set *by the tool's worst
case*: on the aligned class, would struct-clean actually mean fixed?

---

<a name="part-vii"></a>
## Part VII — The reach experiments: the definitive findings

### VII.1 What was pre-registered

`reach-enforced`: rung-3 enforcement vs bare, on the 5 reach-traps + 2 nulls
(harm controls), 5 reps, haiku, locked with four hypotheses **written before
any data**:

- **H1 (rescue).** On the persistent-fail traps (r2, r3, r5 — bare ≈ 0/5),
  enforced > bare.
- **H2 (the decisive test).** Among enforced trials reaching struct-clean,
  the *functional* pass rate should be HIGH — if it's low, gate-gaming
  generalizes even to the aligned class and the tool's enforcement story
  collapses. Pre-bound as "the experiment's headline."
- **H3.** On r4, are enforced fixes *correct* (route the write through
  authorization) or *destructive* (neuter the feature to silence the gate)?
- **H4 (harm).** On r1 + nulls (already passing), enforced must not drop
  below bare.

All four outcome branches had bound-in-advance readings, including the ugly
ones. Publish-the-null carried over.

### VII.2 Headline results (recomputed; all reproduce from the ledger)

Per-task, bare → enforced, 5 reps each:

| task | rule | HAIKU bare→enf | SONNET bare→enf |
|---|---|:--:|:--:|
| gw-r1 | control (passes everywhere) | 5/5 → 5/5 | 5/5 → 5/5 |
| gw-r2 | read-only route | 0/5 → 1/5 | 2/5 → 5/5 |
| gw-r3 | io-budget | 0/5 → 0/5 | 0/5 → 0/5 |
| gw-r4 | writes-through-authorize | 0/5 → 3/5 | 5/5 → 5/5 |
| gw-r5 | no-concurrent-write | 0/5 → 4/5 | 3/5 → 5/5 |
| **reach class** | | **5/25 → 13/25, Δ+0.32** | **15/25 → 20/25, Δ+0.20** |

Harm controls: r1, n2, n4 — 5/5 ↔ 5/5 everywhere, both models. Enforcement
never broke working code (H4 ✓), at a measured cost premium of ~2.3× tokens
per enforced trial (a pre-registered secondary our first-draft report forgot
to publish; stated here).

Haiku's Δ+0.32 lands exactly at the pre-registered MDE. So far, so good — but
two of those cells are about to *not* survive scrutiny, and being able to say
which two, with receipts, is the whole point of the platform.

### VII.3 The confirmatory experiment — and the discipline of admitting selection

The rescue pattern at 5 reps (r4: 0→3, r5: 0→4) was *suggestive, not
powered*. So a **confirmatory** experiment locked those two tasks at 12 reps
per arm.

> **Definition — exploratory vs confirmatory.** Exploratory analysis finds
> candidate effects; confirmatory analysis re-tests them on *new* data with
> the hypothesis frozen first. An effect isn't real until it survives
> confirmation.

> **Definition — selection effect / p-hacking / garden of forking paths.**
> If you choose *which* comparisons to highlight because they looked good in
> the data, ordinary statistics stop meaning what they say — you've silently
> multiplied your chances of a fluke. r4 and r5 were chosen **because** they
> showed rescue at n=5. The pre-registration says so in its first sentence,
> which is the only honest way to run a confirmatory: name the selection,
> then let new data decide. Quantified: the pre-registered all-reach delta is
> +0.32; the post-selection pooled r4/r5 delta is +0.53. That +0.21 gap is
> what selection *buys* you, and why the pooled number should never be the
> headline.

**What the confirmatory found — the split verdict:**

| task | confirmatory bare | confirmatory enforced | verdict |
|---|:--:|:--:|---|
| gw-r5 | **0/12** | **12/12** | Confirmed, decisively |
| gw-r4 | 5/12 | 4/12 | **Reversed. The 5-rep signal was noise.** |

> **Definition — p-value / Fisher's exact test.** The probability of seeing
> data at least this extreme if there were truly no effect. Fisher's exact
> test computes this exactly for small pass/fail tables. Convention: p < 0.05
> is "statistically significant" — but a p-value is only as meaningful as the
> honesty of the comparison it sits on (see selection, above).

Pooling exploratory + confirmatory reps for r5: **bare 0/17 → enforced
16/17**, Fisher one-sided p ≈ 8×10⁻⁹. Bare haiku *never* passed r5 — not
once in 32 attempts across the whole program (every experiment's bare arm
included). Under enforcement it nearly always did.

For r4, pooled: bare 5/17 vs enforced 7/17, p = 0.36 — nothing. Program-wide,
bare r4 passes ~31% of the time; it never was a persistent-fail task. Our
first-draft report printed the pooled r4+r5 headline ("+0.53, p=0.00001,
driven by r5") without stating that r4 had *reversed*; the independent review
called this out, correctly, as laundering a null through a real effect. The
citable result is r5's, alone.

> **Definition — clustering / unit of analysis.** Seventeen reps of *one*
> task are seventeen samples of one prompt, not seventeen independent facts
> about concurrency tasks. At the trial level r5's p-value is astronomical;
> at the *task* level, n=1. The honest scope of the r5 result is "this
> invariant, this task shape, this model tier" — its generality is an open
> question by design, answerable only with more tasks per rule.

### VII.4 The cross-model baseline: capability changes everything

A byte-identical mirror of reach-enforced with only the model changed:
`claude-sonnet-5` (a mid-tier model) instead of haiku (small).

First, the attestation system earned its keep: it **flagged all 70 sonnet
trials as model mismatches**. Investigation (per-trial token accounting):
sonnet produced 91.7% of tokens; haiku 8.3% — a constant ~660 tokens per
trial, the CLI's auxiliary model doing titles and summaries, not coding. The
flag was reported, diagnosed, and retained rather than suppressed. This is
what "trust but verify, and log the verification" looks like in practice.

The findings:

- **A stronger model needs the map less.** Bare-sonnet passes 60% of the
  reach class unaided vs bare-haiku's 20% (3×). The r2 "capability wall"
  (haiku: 4/5 enforced trials burned all 3 rounds still blocked) simply falls
  to sonnet. r4, sonnet solves bare. The failure classes are substantially
  *capability*, not information.
- **The sonnet enforcement delta (+0.20) is not statistically significant**
  (p = 0.108; 95% CI −0.05..+0.42) — and, more interesting than the p-value,
  the *mechanism was barely exercised*: sonnet self-ran `groundwork verify` in
  35/35 enforced sessions and arrived at its Stop already clean; **the hook
  blocked in exactly one of 35 trials**. Whatever helps sonnet is the
  *instructed workflow* (which at haiku did nothing) — the same prompt, but a
  model capable of obeying it to completion. Enforcement-as-blocking went
  essentially untested at sonnet because it was never needed.
- **One thing resists everyone:** r3 (io-budget) — 0/5 at both tiers, bare
  and enforced. Part VIII explains why (it isn't a capability wall).

---

<a name="part-viii"></a>
## Part VIII — The adversarial review: what survived it

Before publishing, we did to our own dossier what we'd want a skeptic to do:
an independent reviewer with instructions to **recompute every number from
raw evidence, assume nothing, and contradict the report wherever warranted**
([INDEPENDENT-REVIEW.md](INDEPENDENT-REVIEW.md)). Every headline pass count,
cost, adoption figure, and attestation share reproduced exactly; all seven
hash chains verified. And then the review found the thing that reframes the
program — not in the arithmetic, but in the *grading contract*.

### VIII.1 The score contains the gate

Recall the holdout command: `set -e; go test ./...; verdi-groundwork-check
<task>`. Fail-fast semantics mean: if the *gate* check is what failed, then
`go test` — the functional part — had already **passed**.

Classifying every bare-arm failure by its recorded failure detail:

| task | bare failures | failed by the gate, with functional tests PASSING | failed by actual tests |
|---|:--:|:--:|:--:|
| gw-r2 | 20/20 | **20 (100%)** | 0 |
| gw-r5 | 32/32 | **32 (100%)** | 0 |
| gw-r3 | 20/20 | 0 | 20 |
| gw-r4 | 22/32 | 0 | 22 |

On the two tasks that carry the entire headline effect, **every single bare
failure was the gate's re-verdict** — the hidden functional test was green.

> **Definition — circularity / tautology (in evaluation).** When the metric
> contains the thing the treatment optimizes, the treatment improves the
> metric *by construction*. Here: enforcement forces the agent to satisfy
> `groundwork review` in-loop, and the score's decisive component on r2/r5 is
> … `groundwork review`, re-run at grading. To the extent the score is the
> gate, "enforcement improves scores" approaches true-by-definition.

Is the program therefore worthless? No — and being precise about *why not*
is where the real understanding lives:

1. **The conjunction has independent teeth.** The functional component vetoed
   23 gate-clean trials across the program (all of r3's, most of r4's, all
   the m-class's) — the score is a genuine AND, not the gate wearing a
   costume. What it is *not* is what our own pre-registration prose casually
   called it ("the hidden functional go-test") — it is *feature-tests AND
   gate*, and every reader of the results needs to know that.
2. **The non-tautological content is real and specific.** (a) Bare agents
   never spontaneously satisfy the in-repo policy — 0/52 on r2+r5 at haiku
   with `policy.json` sitting in their workspace. That is a true, useful fact
   about agents. (b) Under enforcement the agent *converts gate findings into
   compliant code without breaking the features* — which the m-class proves
   is not a given.
3. **On the tool's own philosophical terms, the conjunction is the point.**
   The corpus *defines* correctness as "features work AND declared invariants
   hold" — the exact definition a team adopting such a tool signs up for. The
   score isn't smuggling the gate in; it's declaring structural compliance
   part of "done." But then the honest reading of the headline is: **"when
   correctness is defined to include the gate, enforcing the gate achieves
   it"** — an operational-consistency claim, not a code-quality claim. Which
   is, again, literally the owner's original claim.

### VIII.2 The r5 rescue survives the strictest reading

Because the r5 result could have been pure tautology, the review went to the
final code of all 34 r5 workspaces:

- **No trial — either arm — deleted the audit feature.** This matters because
  deleting it would have passed *both* score components (the feature test is
  audit-blind, and no audit call means no concurrent path): the perfect
  crime, available, never committed.
- Every enforced pass restructured the audit to be synchronous — the
  substantively correct resolution of the prompt's ambiguity in favor of the
  declared invariant.
- The loop demonstrably did the work at haiku: **12 of 16 rescues followed at
  least one BLOCK** (block → edit → clean in the hook log); only 4 were
  clean on first Stop.

So: real defect class (test-invisible concurrency hazard), real baseline
failure (32/32), real mechanism (blocking loop), real fixes (feature intact,
made synchronous). *Scoped to one task and one invariant* — but inside that
scope, airtight.

### VIII.3 Where the gate's writ ends — three distinct failure geographies

The review also sharpened three findings our first draft had blurred:

- **r3 is a coverage gap, not gate-gaming.** The planted io-budget defect
  never trips the gate — the gate was already CLEAR on the buggy baseline in
  most trials. Enforced agents weren't gaming anything; the gate simply
  cannot see this defect as instantiated. Calling it "gate-gaming" (as our
  draft did) blames the agent for the rule's blind spot. Correct statement:
  **1 of the 4 reach rules contributed no possible signal.**
- **r4's trap never fires, so r4 measures something else.** The starter
  code's idiom (every existing mutation routes through `Authorize`) carries
  both arms safely through the authz invariant — **the hook never blocked a
  single r4 trial (17/17 first-Stop clean)**. What actually decided r4
  outcomes: whether the agent, having widened the `repo.Store` interface,
  remembered to update the starter's own `fakeStore` test double. All 8
  confirmatory enforced failures are that identical compile error. Our
  draft's "~40% destructive fixes that neuter the feature to satisfy the
  gate" was wrong twice — the full-n rate of gate-clean-but-failing r4 trials
  is 59%, and the mechanism is test-double hygiene, not feature destruction,
  on a task where the gate never fired. H3's premise dissolved under review.
- **The fail-open door is real and was used.** 4 of the 6 r2 "rescues" across
  both models exited via `NO-STRUCTURAL-SIGNAL` — the agent's edit pushed the
  code's frontier into territory the analyzer can't see (reflection-shaped),
  and the fail-open gate waved it through. Nobody instructed anyone to blind
  the analyzer; it emerged. A gate that fails open on analysis blindness has
  a standing escape hatch, and agents *will* wander through it.

### VIII.4 H2, re-scored honestly

The pre-registered "decisive test" — functional-pass among enforced
struct-clean trials — computes to 0.77 (haiku) / 0.86 (sonnet) under the
report's definition. The review showed that definition flatters: it includes
the nulls and r1 (15 trials that pass everything no matter what) and counts
fail-open abstains as clean. Restricted to the actual traps: **0.53 (haiku)
and 0.75 (sonnet)** — and per rule it isn't a ratio at all but a split
verdict: **r5 = 16/16, r4 = 7/17, r3 = 0/10.**

The correct sentence is not "gate-gaming does not generalize to the aligned
class." It is: **struct-clean means fixed exactly where the gate and the
defect coincide, and means nothing where they don't — and you can tell which
regime you're in only because the functional holdout was there to check.**
A structural gate must never be a merge gate's sole voice. (Which the
m-class smoke had already told us; the reach data says it again with better
lighting.)

---

<a name="part-ix"></a>
## Part IX — What we learned

### IX.1 About verdi-go, for agents — the evidence-backed claim set

**What it does (demonstrated):**

1. **Enforcement converts detectable invariant violations into fixes, at the
   weak-model tier, reliably.** r5: 0/17 → 16/17, p ≈ 8×10⁻⁹, loop-mediated,
   feature-preserving, zero harm to passing code, ~2.3× token cost. This is
   the tool's core mechanism working exactly as designed.
2. **It makes the weak agent consistent, not smart.** Bare haiku resolves the
   r5 prompt's ambiguity against the declared policy 32/32 times; enforced
   haiku resolves it with the policy 16/17 times. Nothing about the model
   improved — its *output distribution* was squeezed onto the policy. A map
   makes a consistent navigator, not a better one: **claim confirmed, in its
   own words, in the only place the instrument could test it.**
3. **The value shrinks as capability grows.** The stronger model needs only
   the *instruction* (it self-verifies and arrives clean; the hook fired
   once in 35 trials), solves 3× more of the class unaided, and demolishes
   the weak model's capability wall (r2). Deploy-time corollary: enforcement
   is a floor for cheap/weak agents, a near-no-op backstop for strong ones —
   at 1.7–2.5× token cost either way.

**What it doesn't do (equally demonstrated):**

4. **Nothing below enforcement moves anything.** Availability: zero adoption,
   zero effect. Instruction: 56% adoption, verifier run, BLOCK verdicts seen
   — zero effect. If you ship a tool like this for weak agents, ship the
   forcing function or don't bother.
5. **Outside the gate's sightline, enforcement is inert at best and
   false-green at worst.** Orthogonal task classes: 0 rescues; 3/3 gate-clean
   trials still functionally broken (m-class); one rule blind from the start
   (r3); one trap that never fires (r4). The gate's green light is only as
   meaningful as the overlap between its rules and your actual defects.
6. **Fail-open is an exploit surface.** Agents reached "clean" by blinding
   the analyzer in 4 of 6 r2 rescues. Any serious deployment needs abstain
   accounting (report NO-SIGNAL exits separately) and probably an adversarial
   audit of how easy deliberate blinding is.

### IX.2 About running agent experiments — the instrument lessons

1. **Attest everything you didn't personally watch.** The two biggest
   near-misses (wrong model, crippled permissions) produced *plausible*
   numbers. Telemetry-first attestation — models, permissions, adoption,
   costs — converted both from silent lies into loud, fixable defects.
2. **Nulls are results.** Rungs 1 and 2 are publishable findings about how
   agents (don't) pick up tools. The pre-registered publish-the-null posture
   is what makes the eventual positive credible: the same instrument that
   printed +0.32 printed +0.01 twice, on the same corpus, and kept both.
3. **Confirm before you believe — and let the confirmation kill things.**
   Half of the confirmatory's pre-selected effects died (r4). The
   pre-registration's own bound reading ("a collapse → the 5-rep signal was
   noise") is what forced the honest verdict. Selection disclosed + new data
   + frozen hypotheses = the difference between science and marketing.
4. **Know what your score is made of.** The single deepest finding — the
   holdout embedding the gate — wasn't a bug (it's a defensible definition of
   "done") but an *undisclosed composition* that changed what every number
   meant. Decompose your metrics in the report, always: "functional AND
   structural" reads very differently from "functional."
5. **Adversarial review of your own dossier is cheap and brutal and worth
   it.** Everything in Part VIII came from pointing a skeptic at the same
   ledgers we had. The instrument's credibility survived because the raw
   record was complete enough to re-litigate — which is the whole design
   thesis of verdi-bench.

### IX.3 Scope, honestly — and the confidence statement

The corpus was authored by the tool's side, engineered so its two effective
tasks bait the violation and blind the functional oracle to it. `policy.json`
was visible to control agents (so "bare agents ignore declared policy" is a
fair finding), but **no evidence anywhere in this program measures how often
such declared-but-untested structural invariants occur in real codebases** —
and the tool's practical value is proportional to that unmeasured base rate.

> **Definition — external validity / base rate.** External validity: does the
> result generalize beyond the experimental setup? Base rate: how often does
> the situation the tool helps with actually occur in the wild? An effect can
> be perfectly real in-corpus and worthless in practice if the base rate is
> ~0 — or transformative if races and authz bypasses that tests miss are as
> common as production postmortems suggest.

Calibrated confidence in the central claim — *"enforced in-loop grounding
prevents structural-invariant violations the agent would otherwise ship"*:

- **≈ 0.85** as demonstrated: declared invariant, gate-detectable,
  fixable-within-3-rounds, weak-model tier, correctness defined to include
  the gate. (Held back from 1.0 by: one task per rule, the fail-open escape,
  and provenance gaps — the prose pre-registrations aren't hashed into the
  ledger chain, and the instrument's git state during the program is
  reconstructable but not cryptographically pinned.)
- **≈ 0.35** as a general engineering claim about real agent coding work —
  pending an independent corpus, more tasks per rule, placebo arms
  (placebo-instruction and placebo-gate, to decompose *mandate* vs *map* vs
  *block*), and a real-world base-rate study. All four are specified in the
  independent review as the concrete next program.

### IX.4 The two tools, one discipline

The quiet thesis of this whole exercise: **an AI-enablement tool and its
evaluation platform are the same kind of object.** verdi-go's pitch is that
agent output should be *checked by mechanism, not vibes* — declared
invariants, deterministic verdicts, no trust in the author's self-report.
verdi-bench applies the identical philosophy one level up, to the evaluation
itself: pre-registered hypotheses, hermetic trials, holdout grading,
hash-chained records, attestation of every claim.

The program found real limits in the subject — a gate that's blind to some
defects, gameable through fail-open, unnecessary for strong models,
tautological when it grades itself — and the instrument caught real defects
in *itself* — a lying pilot, a crippled baseline, an overstated first-draft
report corrected by adversarial review of its own ledgers. Neither tool comes
out unscathed; both come out *understood*. In a field allergic to negative
results, the most valuable thing either tool produced was the sentence:

**"Here is exactly where it works — 0/17 → 16/17, at 2.3× cost, for weak
models, on invariants the gate can see — and here is everywhere it doesn't."**

---

<a name="part-x"></a>
## Part X — Addendum: the mechanism, decomposed (2026-07-10)

Part IX ended with four specified follow-ups. Two days and **$7.38** later,
three of them had run — a pre-registered mechanism-decomposition program
(design: `docs/design/mechanism-decomposition-program.md`; full dossier:
`runs/consistency/MD-REPORT.md`). This addendum reports what they did to the
story you just read. Spoiler: the r5 rescue is real and now has an attributed
mechanism — and the *baseline* it rescued from turns out to be mostly an
artifact of our own prompt.

One instrument upgrade came with it, closing Part VIII's provenance gap: the
prose `PRE-REGISTRATION.md` is now sha256-hashed into each experiment's lock
event (`prereg_sha256`), so a post-lock edit of the interpretation text is
detectable. Every experiment below was locked that way.

### X.1 The score, decomposed for real

Part VIII's deepest finding — the holdout embeds the gate — rested on
classifying recorded failure strings. The follow-up re-executed both halves of
the fused holdout **separately** (the hidden feature tests; the gate) against
all **678** preserved historical workspaces in the pinned grader image
(`scripts/flagship/decompose_scores.py`). Result: the recomputed conjunction
matches the recorded `binary_score` in **678/678 trials — zero mismatches** —
and the channel table confirms the review by re-execution: every bare r2/r5
failure was gate-channel with the functional channel green. The decomposed
table (`runs/consistency/DECOMPOSITION.md`) is now a standing artifact; no
future reader has to take "functional AND structural" on faith.

### X.2 The placebo: what does a block *without* a map buy?

> **Definition — placebo treatment.** A control that reproduces a treatment's
> *form* while removing its hypothesized *content* — here, rung 3's exact
> Stop-hook machinery (same tools staged, same prompt token, same 3-round
> fail-open bound), except the hook runs no gate and blocks every Stop with
> one static, content-free line: *"Review your changes for policy compliance
> before finishing."* If the placebo rescues, the mechanism was "being forced
> to look again," not the gate's findings.

`md-placebo2`, gw-r5, 12 reps per arm, with pre-registered bound readings
frozen at lock (≤2/12 → findings content is the ingredient; ≥9/12 → generic
forcing suffices, headline rewritten; 3–8 → both contribute):

| arm | pass | note |
|---|:--:|---|
| bare | 0/12 | the historical pattern, exactly (0/49 program-wide on this task) |
| placebo | **5/12** | every trial: block ×3 → exhausted, uniformly |
| *(historical enforced)* | *16/17* | the anchor |

**The middle band triggered: both ingredients contribute.** A content-free
block rescues ≈40% of trials (p = 0.019 vs bare); the gate's findings roughly
double that (16/17 vs 5/12, p = 0.003). And the placebo is *more expensive*
than the real thing — 4.7× bare per trial vs enforcement's 2.3× — because with
no clean-exit signal it always burns all three rounds. A generic forcing
function is strictly dominated: less effective, costlier. The map's content
earns its keep; it just doesn't earn *all* of it.

### X.3 The pointer: a null with a lesson inside

`md-pointer` tested the cheapest conceivable treatment — one appended
system-prompt line ("This repository declares structural policy in
`policy.json`; your change must honor it."), no tools, no hook — on gw-r2 +
gw-r5, 5 reps. Result: **0/10**, and 0/10 pointer sessions so much as
*mention* the policy, indistinguishable from bare. The rung ladder gains a
rung 1.5 that behaves like rungs 1 and 2: at this model tier, information
without enforcement moves nothing — now shown even for the minimal case.

The methodological lesson cost more than the experiment: our first
manipulation check searched session transcripts for the prompt text, found it
in 0/10, and briefly looked like non-delivery. Calibrating against rung-2
history showed the CLI's transcripts **never record appended system prompts**
— the "hits" in historical sessions were tool-call *residue* tracking the 56%
adoption rate. Delivery was instead established at the shipped-artifact level
(the pinned image, asked directly, maps the locked payload to an argv carrying
the sentence verbatim). Moral: a manipulation check needs its own calibration,
or it measures something else.

### X.4 The de-bait: how much of 0-for-everything was our own prompt?

Part IV disclosed that gw-r5's prompt *baits* the violation ("must not block
or delay the send"). `md-debait` measured the bait: `gw-r5b` is byte-identical
to gw-r5 except that one clause is gone (admitted to the corpus through the
full curation cycle — k=5 docker baseline, signed approval, chain-anchored
admit; corpus 0.1.0). Five reps per arm:

- **bare: 4/5.** Against 0/49 on the baited prompt (p = 1.6×10⁻⁵). Un-steered,
  haiku writes the synchronous audit naturally; the observed natural violation
  rate is ~20% (1/5 — n=5, wide interval).
- **enforced: 5/5, hook round-1 clean in all five** — the gate never fired,
  because there was almost nothing to catch.

This is the sharpest scope qualifier in the whole record: **"bare haiku NEVER
passes r5" was a property of the baited prompt, not of the model or the task
class.** The honest restatement of the headline: enforcement converts
violations *when they occur*; the corpus made them occur ~100% of the time;
un-steered, they occurred ~20% here.

### X.5 The instrument catches itself, again

In the Part V tradition, the program's first placebo run is in the record as
labeled bug evidence (`runs/consistency/md-placebo/INVALIDATED.md`), not a
result. A concurrently-running e2e test tore down the live metering proxy —
it removes the proxy container *by its fixed global name* — after trial 2;
the remaining 21/24 trials got `ConnectionRefused`, the CLI exited 0 with
`is_error: true`, and the engine counted them successful. Untouched
workspaces then **passed** gw-r5's holdout (the feature test is audit-blind
by design; an unmodified graph trips no gate), producing "bare 11/12" — a
number wildly better than 49 real trials ever produced. Per-trial model
attestation (Part V's lesson, mechanized) caught it: 21 MISMATCH, empty
`modelUsage`, $0.00 cost. Three instrument defects are now on file for their
own reproduce-first fixes: the global proxy-name collision, the
`is_error`-counted-as-success gap, and the deepest one — **on add-a-feature
tasks, this holdout composition cannot distinguish "did nothing" from "did it
right."** No historical result is affected (every historical trial is
attested), but the blind spot is real and now documented.

### X.6 The sentence, revised

Part IX's honest one-sentence finding, updated with the mechanism and the
base-rate-in-miniature:

**At the haiku tier, a blocking in-loop gate converts policy-violating
implementations into correct fixes where a content-free block rescues only
~40% at higher cost, a policy pointer rescues nothing — and the violations
themselves were induced ~5× above their natural rate by the corpus's own
bait; the gate's content is real (≈0.42 → ≈0.94 on the baited task), and the
demonstrated frequency of the problem it solves remains the property of the
corpus, not the world.**

What remains is what remained before, minus three items: the
behavioral-oracle (`-race`) corpus and the real-world base-rate study are
still the path from "real in-corpus" to "valuable in practice."

**Addendum evidence map** — `md-placebo2` (24 trials, $4.46), `md-pointer`
(20, $1.32), `md-debait` (10, $1.10), plus the invalidated `md-placebo`
(~$0.50, bug evidence); all under `runs/consistency/`, all chain-verified,
100% model-attested, prereg-hashed; decomposed channels re-executed with the
same pinned grader digest as the original program; instrument code on PR #35.

---

<a name="appendix-a"></a>
## Appendix A — Glossary

| Term | Definition |
|---|---|
| **A/B experiment** | Paired comparison of a control arm and a treatment arm differing in exactly one thing |
| **Adoption** | Whether the treated agent actually used the provided tool (measured from MCP call logs) |
| **Arm** | One experimental condition (bare vs grounded/enforced) |
| **Attestation** | Post-hoc verification from independent telemetry that trials ran as designed (right model, open permissions) |
| **Base rate** | How often the situation a tool helps with occurs in real work; multiplies any in-corpus effect |
| **Canary** | A unique planted string whose appearance outside its home proves a leak |
| **Capability wall** | A failure a weak model cannot convert even with perfect information (r2 at haiku), distinguished from a tool limitation because a stronger model clears it |
| **Circularity / tautology** | Metric contains what the treatment optimizes, so improvement is partly by construction |
| **Clustering / unit of analysis** | Reps of one task are correlated samples of one prompt; task-level n is the honest generalization unit |
| **Confidence interval (CI)** | The range of effect sizes consistent with the data (95% CI shown throughout) |
| **Confirmatory vs exploratory** | Frozen-hypothesis re-test on new data vs pattern-finding; only the former establishes an effect |
| **Contamination probe** | Pre-run check that the model doesn't already know the tasks |
| **Cost ceiling / premium** | Pre-committed spend cap; measured cost multiple of treatment vs control (~2.3× for enforcement) |
| **Delta (Δ) / effect size** | Treatment pass rate minus control pass rate |
| **Determinism** | Same inputs → same outputs; the grading path's core property |
| **Fail-open / fail-closed** | Whether a gate allows or blocks when it cannot decide; verdi-go's gate fails open on NO-SIGNAL |
| **False-green** | A passing gate signal on code that is actually broken (the gate-gaming outcome) |
| **Fisher's exact test** | Exact p-value computation for small pass/fail contingency tables |
| **Flight recorder** | Full per-trial session transcript, browsable in the operator UI |
| **Gate-gaming** | Satisfying the proxy (gate) without achieving the goal (working code) |
| **Hash-chained ledger** | Append-only event log where each record hashes its predecessor; tamper-evident |
| **Hermetic** | No uncontrolled inputs/outputs; container with metered, allowlisted egress only |
| **Holdout** | Grading material the agent never sees |
| **Manipulation check** | Verifying the treatment *happened* before asking if it *worked* |
| **MCP** | Model Context Protocol — how the agent discovers/calls external tools |
| **MDE** | Minimum detectable effect — smallest true effect the design can reliably see (0.32 here) |
| **Misattribution** | Reporting results under the wrong condition label (e.g., wrong model) |
| **Null result** | "No effect" — a first-class, publishable finding under pre-registration |
| **Orthogonal vs aligned gate** | Whether satisfying the gate coincides with fixing the task's defect |
| **Payload** | Machine-readable arming instructions for a treatment arm |
| **p-hacking / selection effect** | Choosing comparisons because they look good, which invalidates naive statistics |
| **p-value** | Probability of data at least this extreme under "no effect" |
| **Power** | Probability of detecting a real effect of given size |
| **Pre-registration** | Binding hypotheses, endpoints, and interpretations before data exists |
| **Publish-the-null** | Pre-commitment that every outcome is reported |
| **Reachability** | Whether execution can flow from one function to another (the code-graph question) |
| **Rung** | A pre-declared step on the treatment-escalation ladder (availability → instructed → enforced) |
| **Seed** | Fixed RNG start for reproducible randomization |
| **Stop hook** | A script the agent CLI runs when the agent tries to finish; rung 3's enforcement point |
| **Structural invariant** | A rule about code shape (who can reach what), checkable statically |
| **Substrate** | The call-graph construction algorithm (rta here) |
| **Tri-state verdict** | CLEAR / BLOCK / NO-STRUCTURAL-SIGNAL |
| **Trial** | One agent × one task × one container × one outcome |

<a name="appendix-b"></a>
## Appendix B — Evidence map & reproduction

**The seven experiments** (all ledgers chain-verified; every number in this
document recomputed from these):

| experiment | what it was | trials | bare | treat | Δ | $ |
|---|---|--:|:--:|:--:|:--:|--:|
| `recon` | rung 1, permission-crippled (bug evidence, not calibration) | 160 | 46/80 | 47/79 | +0.02 | 15.10 |
| `recon2` | rung 1 (availability), instrument fixed | 160 | 46/80 | 47/80 | +0.01 | 15.48 |
| `instructed` | rung 2 (mandate) | 160 | 47/80 | 48/79 | +0.02 | 20.33 |
| `smoke-enforced` | rung 3 smoke, m-class (worst case) | 12 | 0/6 | 0/6 | 0.00 | 3.72 |
| `reach-enforced` | rung 3, reach class, pre-registered | 70 | 15/35 | 23/35 | +0.23 | 9.57 |
| `reach-confirm` | confirmatory r4/r5 ×12 reps | 48 | 5/24 | 16/24 | +0.46 | 5.88 |
| `reach-sonnet` | cross-model mirror (sonnet-5) | 70 | 25/35 | 30/35 | +0.14 | 40.55 |

Telemetry-measured total: **$110.63** (plus ~$5 of unmeasured early pilot).
Wall clock, first lock to last trial: ~25 hours (2026-07-08 03:25 →
2026-07-09 04:05 UTC).

**Pins:** claude CLI 2.1.202 (native bun) · trial image
`claude-code-groundwork:pinned10` = `sha256:cc69af6d2e5c…` · grader image
`sha256:5da3a95221d2…` (unchanged all program) · models
`anthropic/claude-haiku-4-5-20251001`, `anthropic/claude-sonnet-5` ·
verdi-go `GROUNDWORK_REF=v0.0.0-20260707142329-7e8df2bb315a`, Go 1.25.11 ·
corpus `groundwork-v0` (16 tasks).

**Where each claim lives:**

- Pass/fail + assertions: `runs/consistency/<E>/ledger.ndjson` (`grade`
  events; `binary_score`, per-assertion results incl. the gate's tri-state)
- Enforcement loop anatomy: `workspaces/trial-*/artifacts/groundwork-enforce.jsonl`
- Adoption: `…/artifacts/groundwork-mcp.jsonl` (init line vs `call` lines)
- Attestation & cost: `…/artifacts/agent_log.json` (`modelUsage`, denials, $)
- Transcripts: `…/artifacts/claude-session/` (or `bench serve --root runs/consistency`)
- Final code (e.g., the r5 sync-audit verification): `workspaces/trial-*/`
- The grading command: `runs/consistency/<E>/holdouts/<task>/holdout.json`;
  the gate wrapper: `images/grader/verdi-groundwork-check`
- The treatment definition (prompts, hook source): `images/reference/claude-code-groundwork/agent.py`
- First-draft claims and their corrections: `REPORT.md` and
  `INDEPENDENT-REVIEW.md` (this walkthrough follows the review's numbers
  wherever the two disagree)

**Reproduction:**

```bash
uv run python scripts/flagship/author_consistency.py --corpus-out scratch/groundwork-v0/expt \
  --out runs/consistency/<E> --workflow <availability|ground_verify|ground_verify_enforced> \
  [--tasks gw-r2,gw-r5,...] [--model anthropic/<id>] --reps <N> --ceiling <$> --trial-image <digest>
uv run bench plan runs/consistency/<E>/experiment.yaml --ledger .../ledger.ndjson --actor <you>
uv run --env-file .env bench contamination probe runs/consistency/<E> --manifest scratch/groundwork-v0/corpus-manifest.json --actor <you>
uv run --env-file .env bench run runs/consistency/<E> --engine harbor --corpus-manifest ... --actor <you>
VERDI_GRADER_IMAGE=<grader> uv run bench grade runs/consistency/<E> --runner docker --actor <you>
uv run python scripts/flagship/attest_models.py runs/consistency/<E>
uv run bench verify-chain runs/consistency/<E>/ledger.ndjson
uv run bench serve --root runs/consistency --port 8383
```
