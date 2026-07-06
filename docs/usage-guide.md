# verdi-bench usage guide: authoring and running your first experiment

A hands-on, follow-along guide to going from an empty directory to a defensible
A/B finding. It is written for someone running the instrument for the first
time; it complements the [deep dive](deep-dive.md) (the *why* and the trust
mechanisms) and [adapters.md](adapters.md) (the telemetry/trajectory contract).

Everything here uses only public seams — the `bench` CLI, the on-disk file
formats, and (§0.5) the `harness.sdk` builder — never test-only internals.
Commands and field names are current against the codebase in this checkout.

---

## 0. Mental model in one paragraph

An **experiment** is a directory. You write an `experiment.yaml` (the
pre-registration: arms, corpus, metric, decision rule, seed, cost ceiling), a
`tasks.yaml` (the tasks to run), a judge `rubric.md`, and — for real grading — a
`holdouts/` directory per task. `bench plan` sha-locks all of that into a
hash-chained `ledger.ndjson`; from then on every stage (`run`, `grade`, `judge`,
`forensics`, `selfcheck`, `analyze`) appends typed events to that ledger and
refuses to run against a spec that changed. You end with a self-contained
`findings.*.dossier.html` and a ledger anyone can `verify-chain`.

Two execution modes:

- **fake engine** (default) — deterministic, no Docker. This is the path in the
  walkthrough below; use it to learn the flow and to smoke-test a design.
- **harbor engine** (`--engine harbor`) — real, digest-pinned containers with a
  metering proxy. Covered in §6.

Every local result is stamped `ADVISORY`. That is the honest tier; the trusted
tier is a planned CI-tier cutover.

---

## 0.5 The fastest path — the Python SDK

The walkthrough from §2 on authors the four files by hand, because **files are
the source of truth for what gets locked**: `bench plan` sha-locks the exact
bytes of `experiment.yaml` / `tasks.yaml` / `rubric.md`. You do not have to *type*
them, though. The Python SDK (`harness.sdk`) is a fluent builder that **writes
those same files** and then drives the whole pipeline in-process. It is a public
seam that adds no second source of truth — it composes the tested subsystems and
serializes the files the lock hashes.

Here is a complete fake-engine A/B — the `tests/test_sdk_northstar.py` flow
verbatim, the executable proof the SDK is a real write path:

```python
from pathlib import Path

from harness.sdk import Experiment, Task, write_holdout_results

exp = (
    Experiment("mini-ab", seed=1234, cost_ceiling_usd=10.0)
    .arm("control",   model="anthropic/claude-haiku-4-5-20251001", platform="claude_code")
    .arm("treatment", model="openai/gpt-4o-2024-08-06",            platform="codex")
    .judge("fake/deterministic-2026-01-01")   # rubric defaults to the library template
    .task(Task("t_add", prompt="Write solution.py defining add(a, b)...",
               fake_behavior={"native_log": {"total_cost_usd": 0.01}}))
    .task(Task("t_pal", prompt="Write solution.py defining is_palindrome(s)...",
               fake_behavior={"native_log": {"total_cost_usd": 0.01}}))
)

ws = exp.write("_run/mini-ab")     # writes experiment.yaml, tasks.yaml, rubric.md
ws.plan(actor="me")
ws.run(engine="fake")

# Fake-path operator step: the arm-blind fake engine reads only task.fake_behavior,
# so the treatment-beats-control asymmetry is written between run and grade
# (treatment passes both tasks, control neither) — exactly as the e2e tests do it.
for tv in ws.view().trials():
    rec = tv.record
    write_holdout_results(Path(rec["artifacts_path"]).parent, rec["arm"] == "treatment")

ws.grade(runner="local")
ws.judge()
findings = ws.analyze(exploratory=True)      # → findings.exploratory.md (+ dossier)
assert ws.verify_chain().chain_ok
```

`exp.write(...)` returns an `ExperimentWorkspace` whose
`.plan/.run/.grade/.judge/.analyze/.verify_chain` are one-line delegations to the
same stage APIs the `bench` verbs call; reads go through `ws.view()` (a
`LedgerView`). Seed and cost ceiling are **required** constructor arguments — no
silent defaults for the determinism / cost-fence contracts. The judge rubric
defaults to the library template (the single source of the verdict-JSON contract);
pass `rubric=` a string of literal text or a `Path` to override it.

**Want the CLI, but a scaffold rather than a blank page?** `bench init <dir>`
writes the same starter `experiment.yaml` / `tasks.yaml` / `rubric` from the one
canonical template (it refuses a non-empty target, and **ledgers nothing** — you
edit the files, then `bench plan`).

Whichever door you use, **what gets locked is the on-disk bytes** — so §2 below is
the contract-level truth for those files, and worth reading even if you always
author through the SDK.

---

## 1. Prerequisites

```bash
uv sync                      # install the harness + dev tooling
uv run bench --help          # sanity check: the verb list prints
```

Python 3.11+ locally (the spec binds 3.12; CI verifies 3.12 compatibility).
Docker is only needed for the harbor run path and the real grading container —
the fake-engine walkthrough needs neither.

---

## 2. The experiment directory

Create a directory and populate four files. Here is the complete layout you will
build in this section:

```
myexp/
├── experiment.yaml     # the pre-registration (sha-locked)
├── tasks.yaml          # the tasks to run
├── rubric.md           # the judge rubric (content-hashed into the lock)
├── holdouts/           # per-task grading assertions (real grade path)
│   └── t1/ ...
└── run.config.yaml     # OPTIONAL: operational wiring (harbor only, §6)
```

```bash
mkdir myexp && cd myexp
```

### 2.1 `experiment.yaml` — the pre-registration

This is the cryptographic commitment. The schema is strict (`extra="forbid"`):
an unknown key is a rejection, not a silent no-op. A minimal, valid spec:

```yaml
arms:
  - name: control
    platform: claude_code
    model: anthropic/claude-haiku-4-5-20251001
    payload: {}
  - name: treatment
    platform: codex
    model: openai/gpt-4o-2024-08-06
    payload: {}
corpus: {id: public-mini, version: "1.0.0"}
repetitions: 3
primary_metric: holdout_pass_rate
decision_rule: "delta_holdout_pass_rate > 0"
judge:
  model: google/gemini-1.5-pro-002
  rubric: rubric.md
  orders: both
  temperature: 0
seed: 1234
cost_ceiling: {amount: 25.0, currency: USD}
```

Field-by-field, including the rules the schema will hold you to:

| Field | Meaning / constraint |
|---|---|
| `arms` | **≥ 2**, **names unique**. Each arm: `name`, `platform` (must be a registered adapter — see §7), `model`, `payload` (free-form stack config). |
| `arm.model` | Must be `<provider>/<id>` (e.g. `anthropic/…`) so the vendor set is well-defined — a bare id is refused. |
| `corpus` | `{id, version}` — the corpus identity the official fence re-checks. |
| `repetitions` | `> 0`. Each task runs this many times **per arm**, paired. |
| `primary_metric` | One of `holdout_pass_rate`, `judge_preference`, `cost_per_task`, `wall_time`. Composites are unrepresentable. |
| `decision_rule` | `delta_<primary_metric> <op> <threshold>`, e.g. `delta_holdout_pass_rate > 0`. `<op>` ∈ `>`, `<`, `>=`, `<=`. **`==` is rejected** (equality on a bootstrap point estimate is never decidable). The metric must be the primary metric. |
| `judge.model` | Must be **fully versioned** — a date or build stamp (`gemini-1.5-pro-002`, `gpt-4.1-2025-04-14`). A bare family (`gemini-1.5-pro`, `gpt-5`) is an alias and refused at plan time. Any provider is legal. |
| `judge.rubric` | Path to a rubric file **relative to the experiment dir**; its content is hashed into the lock, so it cannot be swapped post-registration. |
| `judge.orders` | `both` (order-debiased, recommended) or `single`. |
| `judge.escalation` | Optional `{kappa_threshold: 0.6, min_human_verdicts: 20}` — the calibration gate. |
| `seed` | Integer. Seeds the paired interleave and every bootstrap. |
| `cost_ceiling` | **Required.** `{amount > 0, currency}`. Hitting it stops the run and refuses new trials. |

Optional, powerful fields:

| Field | Meaning |
|---|---|
| `hypothesized_effect` | `(0, 1]`. The effect size the power/MDE gate checks against at plan time. |
| `fractional_scoring` | `true` grades the fraction of passing assertions instead of all-or-nothing. |
| `contamination.overlap_threshold` | `(0, 1]`. Pre-registers the fingerprint-overlap threshold for the contamination sentinel. |
| `arm.training_cutoff` | RFC 3339. Feeds the contamination tri-state (`predates`/`postdates`/`unknown`); absent → honest `unknown`, never `clean`. |
| `arm.aux_models` | Additional models the stack invokes (`[{model, training_cutoff}]`) — declared so blinding, vendor overlap, and contamination see the whole stack. |
| `arm.model_hosts` / `infra_hosts` | Declared egress hosts per model / shared infra. If you declare **any**, you must declare for **every** arm (a partial declaration would silently deny one arm's APIs). These derive the harbor proxy allowlist (§6). |

### 2.2 `rubric.md` — how the judge decides

Plain prose describing what "better" means for these tasks. It is content-hashed
into the lock, so write it before you plan.

```markdown
# Code task rubric v1
Prefer the response that correctly and minimally solves the task.
Penalize responses that leave tests failing or introduce unrelated churn.
```

### 2.3 `tasks.yaml` — the tasks

```yaml
tasks:
  - id: t1
    prompt: "Fix the failing test in calc.py so the suite passes."
    holdouts_dir: holdouts/t1        # optional; omit for judge/telemetry-only tasks
    plugin_ids: []                   # optional custom graders (§ deep-dive §7)
    task_class: refactor             # optional label used in per-class calibration
  - id: t2
    prompt: "Add input validation to the parse() function."
    holdouts_dir: holdouts/t2
```

Rules: **task ids unique**; every field is hashed into the lock, so a post-lock
edit to a prompt / holdouts path / plugin list is refused by `run`/`grade`. Note
the commitment covers the `holdouts_dir` **path**, not the bytes of the holdout
scripts under it (an honest boundary documented in `harness/corpus/commit.py`).

### 2.4 Holdouts — the deterministic grade

A holdout is an assertion your grader runs against the trial's final workspace.
On the real (`--runner docker`) path, `holdouts_dir` is bind-mounted **read-only**
at `/holdouts` inside a fresh, **network-less**, capability-dropped, non-root
container that runs over a copy of the workspace and prints its results on
**stdout** inside a one-per-run fence (the V1 transport [F-H1]):

```
-----VERDI_HOLDOUT_RESULTS_V1_BEGIN-----
{"assertions": [{"id": "h1", "result": "pass"},
                {"id": "h2", "result": "fail"}]}
-----VERDI_HOLDOUT_RESULTS_V1_END-----
```

The host scores nothing from `/workspace`: holdout tests import and execute
agent code, so a workspace results file is forgeable by an `atexit` hook in
the code under test — the fence is not. Your image's entrypoint should run
the holdout tests in a **subprocess** (ideally under a separate non-privileged
uid), collect their outcomes, and print exactly one fenced block itself; zero
fences fail the grade closed as a container failure, and two or more (agent
code printing its own forged block from the test subprocess) are refused as
malformed, never scored. Images built for the pre-V1 workspace-file transport
must be rebuilt. One honest residual boundary: `/holdouts` is readable by the
process that executes the tests (the tests *are* the holdouts), so agent code
running at grade time can read expected values — grade-time adaptation is
mitigated by subprocess isolation, not eliminated.

`result` is `pass` / `fail` / `abstain` (abstain does not count as a pass). The
binary score is "all holdout assertions pass"; with `fractional_scoring` it is
the fraction of non-abstaining assertions that pass.

There are two no-daemon paths for the fake/learning flow:

- **`--runner local`** *reads* a `holdout_results.json` you place directly in each
  trial's workspace — the injection path the walkthrough (§4) and the e2e tests
  use to script a known effect on the arm-blind fake engine.
- **`--runner local-exec`** *executes* a **declared** holdout (a `holdout.json`
  carrying a `kind` discriminator — assertion / pytest / command) from
  `holdouts_dir`, in a host subprocess with no Docker, and packs the results into
  the same fenced wire shape. It is stamped `ADVISORY` (no container isolation, so
  agent code runs on the host) and it requires a declared holdout — an
  opaque/bespoke `holdouts_dir` with no `kind` is refused loudly; use
  `--runner docker` for those. It lets you exercise a real holdout end to end
  without a daemon, instead of hand-writing the results file.

---

## 3. Author interactively in the browser (optional)

Everything in §2 can also be done through the **authoring surface** — a
browser app for building and locking a draft, instead of hand-editing files:

```bash
uv run bench author <workspace-dir> --actor alice   # loopback, default port 8390
```

Open the printed URL and you get:

- **Draft creation** — name a draft and it seeds an editable `experiment.yaml`,
  `tasks.yaml`, and `rubrics/code-task-v1.md` template into a fresh directory
  under the workspace root.
- **Tabbed editor panes** — edit each pre-registration file (raw YAML/markdown)
  in-browser, with *Save draft* and *Insert template into this pane*. Only the
  three allowlisted files are writable; unsaved edits are tracked, and previews
  always read the **last save** (byte fidelity is a property of the flow).
- **A live preview panel** recomputed against the saved draft: **Validation**
  (parsed arms/metric/decision-rule/rubric-present, or the typed schema error),
  **Power** (an MDE estimate — quick, with the lock recomputing at full
  fidelity), **Schedule** (the derived seeded paired-interleave order), and the
  **spec sha256**.
- **The lock ceremony** — an explicit `attested_by` and an
  `acknowledge_underpowered` toggle, then *Lock*, which calls
  `lock_experiment` verbatim.

The surface is deliberately a raw-text editor with live feedback, not a
field-by-field form — you edit the actual pre-registration bytes, so what you
lock is what you reviewed. It mutates state, so it binds to **loopback only**
and guards Host/Origin/Content-Type on its two POST endpoints (draft-write and
lock). Critically, the **only ledgered operation the whole surface performs is
the lock** — every preview is a pure read.

> **`author` mutates; `serve` observes.** Interactive *configuration* is
> `bench author`'s job. `bench serve` (§ below and in the deep dive) is the
> read-only operator/observer view — live status, compare, workspace home, and a
> static HTML bundle — and writes nothing. The mutating and read-only surfaces
> are separate subsystems and separate verbs by design.

Everything downstream in this guide works the same whether you authored by hand
or via `bench author`.

---

## 4. The full pipeline (fake engine, end to end)

This is the complete flow the e2e test `tests/test_eval_e2e_phase4.py` exercises,
runnable by hand. From inside `myexp/`:

```bash
LEDGER=ledger.ndjson

# 1. Pre-register: validate, power-check, sha-lock. Writes experiment_locked.
uv run bench plan experiment.yaml --ledger $LEDGER --actor alice

# 2. Run the paired, interleaved trials on the fake engine.
uv run bench run . --actor alice
```

For the fake path, stand in for the grader by writing each trial's
`holdout_results.json` into its workspace (the real path skips this — the
container produces it). Each `trial` event carries the `artifacts_path`; the
workspace is its parent directory. Then:

```bash
# 3. Deterministic grades. --runner local reads pre-placed holdout_results.json.
uv run bench grade . --runner local --actor alice

# 4. Identity-blind advisory judge verdicts (idempotent).
uv run bench judge . --actor alice

# 5. Trajectory metrics + gaming detectors (advisory).
uv run bench forensics scan . --actor alice

# 6. A/A coverage selfcheck — REQUIRED before an official render.
uv run bench selfcheck . --actor alice

# 7. Findings. Exploratory is watermarked; official passes the fence.
uv run bench analyze . --exploratory
#   or, when you have a passing selfcheck and want the fenced render:
# uv run bench analyze . --official --corpus manifest.json

# 8. Audit the ledger end to end.
uv run bench verify-chain $LEDGER
```

Each `analyze` writes both `findings.<mode>.md` and a single self-contained
`findings.<mode>.dossier.html` (no network, no external assets, byte-identical
for a fixed ledger + seed) with three layers: a template-generated **verdict**, an
**analyst** layer (paired deltas, calibration, flags), and an **auditor** layer
(provenance, ledger head, chain status).

### Human review and process scoring (optional, between steps 4 and 7)

```bash
uv run bench review build .                                   # blinded review packet
uv run bench review record . --comparison-id c1 --winner 1    # capture a verdict
uv run bench review reveal . --comparison-id c1               # refuses pre-verdict
uv run bench process score .                                  # isolated process rubric
```

Judge↔human agreement (IPW-corrected kappa) then appears in the findings.

### Multi-arm experiments (> 2 arms)

With more than two arms the spec still pre-registers exactly one decision rule,
so only the **primary pair** carries an official decision by default; the other
pairs render CI + effect size but no decision. To keep every pair official under
a family-wise correction — pre-registered in the spec, not an analyze-time
flag [F-H7]:

```yaml
# experiment.yaml (before locking)
multi_arm_correction: holm      # default: none
```

---

## 5. Exploratory vs official — the fence

- `--exploratory`: always available, watermarked on every layer. Use it while
  iterating.
- `--official`: passes the **pre-registration fence** or refuses with a named
  `cant_analyze` reason. The fence requires: the spec is locked and unchanged,
  the corpus identity and rubric hash agree, a **current passing selfcheck**
  exists, and there is no *asymmetric* flagged contamination (one arm
  contaminated but not the other refuses; symmetric contamination discloses).

You cannot p-hack your way past this: the question was fixed before the data
existed, and the render re-checks that at render time.

---

## 6. Running for real: the harbor engine

`--engine harbor` swaps the fake engine for real containers:

- digest-pinned images (`--pull=never`) — the task image is `image: <ref>@sha256:…`
  in your task/adapter wiring;
- the prompt + arm delivered read-only at `/verdi/request.json` **outside** the
  graded workspace;
- provider keys env-injected and redacted at capture;
- egress confined to a **metering proxy** on an internal docker network with
  per-trial JSONL attribution;
- containers **killed on timeout**, confirmed via `docker inspect` — a container
  that survives the kill fails the trial closed rather than being graded;
- capability-dropped, no-new-privileges, pids/memory-capped.

Operational wiring lives in an **optional `run.config.yaml`** in the experiment
directory — never in the sha-locked `experiment.yaml`, never on the ledger:

```yaml
proxy:
  managed: true             # the harness stands the metering proxy up + tears it
                            # down around the run (no hand-rolled docker). It
                            # supplies its own url + log_path — setting either
                            # alongside `managed: true` is refused.
  # url: http://proxy:3128            # OR point at an EXTERNAL proxy you run:
  # log_path: /var/log/verdi/proxy.jsonl   #   set url + log_path, drop `managed`
  # allowlist: [...]        # OMIT if the spec declares model_hosts/infra_hosts —
  #                         # the allowlist then derives from the locked bytes
otlp:
  managed: true             # OPTIONAL in-trial OTLP span capture: stands up a
                            # hermetic (internal-network-only) trace collector
                            # around the run and injects the OTEL_* env vars. It
                            # supplies its own endpoint + log_path. For an
                            # already-running collector, set `endpoint:` +
                            # `log_path:` instead of `managed`.
quotas:
  cpus: 2.0
  mem: 4g
provider_key_names: [ANTHROPIC_API_KEY]          # values read from the ENV by name
provider_key_names_by_arm:                        # OR scope keys per arm (PRA-M2)
  control:   [ANTHROPIC_API_KEY]
  treatment: [OPENAI_API_KEY]
```

Notes that will save you a failed run:

- A key **named here but absent from the environment fails the run loudly** — an
  unauthenticated arm would bias the A/B. Values are never invented and never
  written to disk or the ledger.
- Per-arm keys (`provider_key_names_by_arm`) hand each arm only its own
  credentials, so one arm's key never enters another arm's container.
- **The metering proxy is harness-managed by default.** `proxy.managed: true`
  stands the proxy up on an internal docker network and tears it down around the
  run — no hand-rolled docker steps. You can also run it out of band for a longer
  session (`bench proxy up --allow api.anthropic.com --allow api.openai.com`, then
  `bench proxy down`), or from Python via the `MeteringProxy.managed([...])`
  context manager (`from harness.hermetic import MeteringProxy`). An **external**
  proxy you operate yourself — the reference Squid config in
  `deploy/metering-proxy/` is one — is the alternative: set `proxy.url` +
  `proxy.log_path` instead of `managed`. Either way, a configured-but-missing
  proxy log **fails loud** rather than silently reporting zero egress/cost. See §6
  of the deep dive for the honest boundary: `--internal` blocks the outside world
  but not the host gateway, so strong confinement also wants deployment-level
  firewall rules the harness does not install.
- **In-trial OTLP span capture is opt-in.** `otlp.managed: true` stands up a
  hermetic trace collector — internal-network-only, so span data physically
  cannot leave the host — around the run and injects the standard `OTEL_*` env
  vars, so any OTel-native image's spans land as a redacted, sha-ledgered
  `artifacts/otlp_spans.json` (no agent-code changes). Stand it up out of band
  with `bench otlp up` / `bench otlp down`, or from Python with
  `TraceCollector.managed(...)`. An arm on **`platform: otlp`** then projects those
  captured spans into the trajectory (`docs/adapters.md` §"The `otlp` platform");
  because such an arm needs a collector, a `platform: otlp` run with no `otlp`
  block is refused at start, naming both settings.

Grading has the same split: `bench grade` defaults to `--runner docker` (the real
network-less grading container); `--runner local` is the no-daemon fake/test path.

```bash
uv run bench run . --engine harbor --actor alice
uv run bench grade . --runner docker --actor alice
```

---

## 7. Extending the generic base adapter for a custom stack

An **adapter** maps your test subject's log onto verdi's two measurement
surfaces — per-trial **telemetry** and the ordered **trajectory** — so the
grader, forensics, and analysis can consume any stack uniformly. The full
contract (the normalized log format, every field, and the failure semantics) is
`docs/adapters.md`; this section is the practical extension recipe.

The non-negotiable honesty rules apply at every tier: a field you cannot measure
is `None` (recorded in `telemetry_nulls`, **never** imputed); a trajectory with
no content is `None` (honest absence, distinct from `[]`); a *present but
corrupt* log fails the trial closed, never silently becomes "no telemetry".

Plan-time validation refuses to lock an arm whose `platform` has no registered
adapter, so a typo fails **before any spend**, not mid-run. The registered
platforms today are `claude_code`, `codex`, `generic`, and `otlp` (the last
projects a trial's captured OTLP spans into the trajectory instead of reading a
log — see refactor spec 10; its full section lands with the Phase 6 docs).

### Tier 1 — zero code: emit the normalized format (`platform: generic`)

If your stack can write its own log, write it directly in the verdi normalized
log format to `artifacts/agent_log.json` in the workspace and set
`platform: generic`. No harness code, no registration — the `generic` adapter
ships with the instrument. This is the baseline path for custom harnesses,
wrapped open-source models, and agentic workflows.

```json
{
  "verdi_log_version": 1,
  "telemetry": {"tokens_in": 1200, "tokens_out": 340, "cost": 0.42,
                "wall_time_s": 61.5, "tool_calls": 7},
  "trajectory": [
    {"kind": "message", "command": ""},
    {"kind": "tool_call", "relative_ts": 1.5, "command": "ls"},
    {"kind": "file_edit", "files_touched": ["a.py"], "command": ""},
    {"kind": "test_run", "exit_code": 0, "command": "pytest -q"}
  ]
}
```

`verdi_log_version` is the engage switch: absent → all telemetry null and
trajectory absent (safe even if you emit some other log); `1` → the parser
engages and the document is a self-attestation, where an **unknown key is refused
loudly** so a typo'd `token_in` cannot launder into "unmeasured". Omit any field
you cannot measure.

### Tier 2 — subclass `Adapter`, override only what differs

`Adapter` (`harness/adapters/base.py`) is a working adapter out of the box: its
default `normalize` / `normalize_trajectory` parse the normalized format.
Subclass it, set `platform`, and override only the method whose parsing differs —
e.g. keep the default telemetry parsing but derive trajectory steps from your
native event stream. Then register the instance.

```python
# harness/adapters/mystack.py
from __future__ import annotations
from typing import Optional
from .base import Adapter, Telemetry

class MyStackAdapter(Adapter):
    platform = "mystack"          # this is what arm.platform names

    def normalize_trajectory(self, native_log: dict) -> Optional[list]:
        # parse your stack's native events into shared-schema TrajectoryStep;
        # return None if the log honestly carries no trajectory.
        ...
    # normalize() inherited → still parses the normalized telemetry block
```

```python
# harness/adapters/__init__.py — add to the registry
_ADAPTERS: dict[str, Adapter] = {
    a.platform: a for a in (ClaudeCodeAdapter(), CodexAdapter(),
                            GenericAdapter(), MyStackAdapter())
}
```

### Tier 3 — full native adapter

For a platform with its own log format (the `claude_code` and `codex` adapters
are the ~100-line templates), override **both** methods to parse the native log,
and set `speaks_generic_format = False` so the run seam never applies
verdi-format semantics (the loud version/strictness rules, `telemetry_by_model`)
to a log your platform never claimed — an agent-controlled native log that
happens to contain a `verdi_log_version` key must not be able to fail a trial.
Let every unmeasurable field stay `None`; the null-honesty tests will hold you to
it.

Whatever the tier, ship the adapter with tests that assert your null honesty
(an unmeasured field lands in `telemetry_nulls`, an absent trajectory is `None`,
a corrupt log fails closed).

---

## 8. Does it support multi-agent workflows? Yes — with explicit limits

A multi-agent harness (planner + workers, an orchestrator routing subagents,
etc.) plugs in as a **single arm**: one container, one prompt in, one workspace
of artifacts out. You report it through the same normalized log, using the v2
attribution features (`docs/adapters.md` §"Format v2").

**How to report a fleet honestly:**

- **Telemetry is whole-trial.** Sum tokens / cost / tool calls across *all*
  agents into the top-level `telemetry` block — it remains the sole
  authoritative stream. If your orchestrator cannot attribute a field across
  agents, leave it `null` rather than reporting a partial sum as a total.
- **Per-model splits** go in `telemetry_by_model` (v2), keyed **strictly** by the
  models the locked spec declared (the arm's `model` plus its `aux_models`). A
  key naming an undeclared model is refused loudly. When by-model blocks sum
  differently from the totals, the mismatch surfaces as a `by_model_delta` flag —
  never silently reconciled.
- **Trajectory is one ordered list.** Serialize concurrent agents' steps into a
  single timeline (by `relative_ts`), tagging each step with a closed-vocabulary
  `agent` role: `planner`, `executor`, `orchestrator`, `router`, `critic`,
  `reviewer`, `tester`, `researcher`, `worker`, optionally ordinal-suffixed
  (`worker-1`, `worker-2`). Anything else (free text, a model name) is refused at
  parse — so **identity leakage is unrepresentable, not scrubbed**. Spawning a
  subagent is a `tool_call` step.

**The limitations — read these before trusting multi-agent numbers:**

1. **Attribution is self-reported testimony, and exploratory only.** The
   instrument runs the fleet in a hermetic container and cannot see inside it, so
   per-agent / per-model attribution is the arm's *own claim*. It rides the trial
   record's flags as cross-check data — it **never** feeds the authoritative
   telemetry stream, and **no official gate reads it**. Your primary metric and
   decision still rest on whole-trial telemetry and deterministic holdouts.
2. **One trial = one quota, one timeout, one proxy.** The whole fleet shares the
   trial's pinned CPU/memory quota, its single wall-clock timeout, and the
   metering proxy. Egress from *any* agent is attributed to the trial; there is
   no per-agent budget or per-agent kill.
3. **Concurrency is flattened.** Genuinely parallel agents must be serialized
   into one ordered trajectory via `relative_ts`. The forensic detectors read
   that single timeline; true wall-clock concurrency is not modeled.
4. **The role vocabulary is closed.** You attribute to a fixed set of roles;
   extending it (a new role, a new per-step field) is a `verdi_log_version` bump
   with a compatibility story, not a free-text field.
5. **Still two first-class native adapters.** `claude_code` and `codex` ship
   native; every other stack (including most multi-agent frameworks) integrates
   through the `generic` normalized log or a custom adapter (§7). That is a
   deliberate scope choice, not a gap you have to wait on — Tier 1 needs no
   harness code.

In short: verdi-bench treats a multi-agent stack as a black-box arm and will
faithfully A/B it, disclose its self-reported internal breakdown as exploratory
color, and refuse to let that testimony masquerade as measured ground truth.

---

## 9. Worked example: an asymmetric A/B (tool-armed Haiku vs Opus baseline)

The instrument's headline use case: does a cheaper model, *armed with tools,
skills, and a workflow*, match or beat a stronger baseline model? This section
ties §2, §6, §7, and §8 together into one concrete design, and states honestly
what is enforced versus audited so you can advertise the capability without
overclaiming.

### The design

Two arms that **share the task environment** and differ only in the declared
treatment — `model` plus the free-form `payload`:

```yaml
arms:
  - name: control                                  # the baseline
    platform: your_harness
    model: anthropic/claude-opus-4-8-20260101      # fully-versioned ids required
    payload: {}
  - name: treatment                                # Haiku + tools + skills + workflow
    platform: your_harness
    model: anthropic/claude-haiku-4-5-20251001
    payload:
      tools:  [bash, file_edit, web_search]
      skills: [my-custom-skill]
      workflow: multi_agent_planner
    aux_models:                                    # if the workflow routes to more models
      - {model: anthropic/claude-haiku-4-5-20251001}
judge:
  model: openai/gpt-4.1-2025-04-14                 # a THIRD vendor — see below
  rubric: rubric.md
```

At run time the harness delivers `/verdi/request.json` =
`{prompt, arm, model, payload}` read-only into the container (outside the graded
workspace). **Your agent image's entrypoint reads it** and configures itself:
which model to call, which tools/skills to load, whether to run the workflow. The
instrument delivers the asymmetric spec; your image realizes it. A multi-agent
workflow (§8) is simply the `treatment` arm whose image runs the fleet.

### Will there be enough evidence on who won and how it performed?

Yes — and Haiku-vs-Opus is a *favorable* case because both are the same vendor
(`anthropic`), so token / cost / wall-time are directly comparable (no
cross-vendor incomparability exclusion fires).

- **Who won (the decision).** Pre-register one `primary_metric`
  (`holdout_pass_rate`, `cost_per_task`, `wall_time`, or `judge_preference`) and a
  `decision_rule`. `analyze` returns paired per-task deltas, a coverage-validated
  bootstrap CI, the MDE, and — for `--official` — a fenced decision. That is the
  defensible "A beat B by X, CI [lo, hi]" answer.
- **How it performed (the color).** Per-arm whole-trial telemetry
  (`tokens_in/out/cache`, `cost`, `wall_time_s`, `tool_calls`); per-model splits
  (`telemetry_by_model`, exploratory) when the workflow routes across models;
  trajectory forensics (tool distribution, edit→test cadence, thrash,
  time-to-first-test, error-recovery latency, destructive-command count); the
  process rubric (planning quality, tool efficiency); and the identity-blind
  judge's advisory preference.
- **The honesty guardrail.** If a metric is null in one arm but present in the
  other (e.g. only the tool-armed arm reports `tool_calls`), it is **excluded
  from the official comparison and flagged** (`telemetry_null_asymmetry`) — never
  silently turned into a bogus winner.
- **Keep the judge clean.** Use a **third-vendor judge** (here `openai/…`) — an
  Anthropic judge over two Anthropic arms trips the `judge_vendor_overlap` flag.

### Can you ensure *only* the test arm has the tools / skills / harness?

Three tiers of isolation — and it matters which is *enforced* versus *audited*:

1. **Credentials — hard-enforced per arm** (PRA-M2). With
   `provider_key_names_by_arm`, the treatment container receives an API key the
   control never sees (and vice versa). Any tool or skill that needs a credential
   is genuinely gated: the control *cannot* authenticate to it. Real isolation,
   enforced by the harness, covered by a test.

   ```yaml
   # run.config.yaml
   provider_key_names_by_arm:
     control:   [ANTHROPIC_API_KEY]
     treatment: [ANTHROPIC_API_KEY, TAVILY_API_KEY]   # only treatment gets the tool key
   ```

2. **Tool / skill / workflow availability — delivered asymmetrically, enforced by
   your image, audited by the instrument.** The asymmetry lives in `payload`; your
   image is what actually withholds the tools from the control. The instrument
   does not sandbox tool *availability* itself — but it gives you the evidence to
   confirm the asymmetry held: per-arm `tool_calls`, the full trajectory of what
   each arm invoked, and per-trial egress attribution.

3. **Network egress — blocked experiment-wide (union), attributed per arm.** The
   proxy allowlist is the union of every arm's `model_hosts` plus shared
   `infra_hosts`, so a tool-serving host allowed for the treatment is
   network-*reachable* by the control too — but if the control reaches it, that is
   flagged `undeclared_model_egress` for the control arm (and denied hosts are
   hard-blocked for everyone). Combined with tier 1, a host that requires auth is
   effectively test-only.

### The one structural limitation to state plainly

**The container image is per-task, shared across both arms** (`image` lives on the
task, not the arm) — deliberate, so the environment is identical and only the
declared treatment varies. The consequence:

- ✅ **"Same harness / substrate, different model + tools + skills + config"** is
  first-class — the sweet spot, and exactly what "tool-armed Haiku vs Opus" is.
- ⚠️ **"A genuinely different base container image per arm"** is not a schema
  field today. Express a different *harness* by baking both into the one shared
  image and branching on `payload.workflow` (or by making the multi-agent
  orchestrator the shared image that simply runs plainly for the control). Two
  arms needing two different base images is a schema extension the instrument does
  not yet have.

### The honest capability statement

> verdi-bench runs a paired, pre-registered A/B of two agent configurations that
> share a task environment and differ by model and a declared config payload
> (tools, skills, workflow). It hard-isolates per-arm credentials, delivers
> asymmetric tool/skill config for your harness to enforce, and audits what each
> arm actually did (telemetry, trajectory, per-trial egress). It decides a winner
> on one pre-registered metric with a confidence interval and a pre-registration
> fence, and surrounds it with comparable per-arm and per-model telemetry plus
> trajectory forensics — excluding, not fudging, any metric that is not comparable
> across the two arms. It does **not** yet support a different base container image
> per arm, and per-agent attribution inside a multi-agent arm is self-reported and
> exploratory, never authoritative.

---

## 10. Plugging into a standardized benchmark (SWE-bench worked example)

verdi-bench is an **instrument, not a benchmark**: it *runs* a corpus, it does
not ship one. So instead of hand-authoring `tasks.yaml`, you can point it at a
recognized, citable task set — the same way `lm-eval-harness` and Inspect became
part of the conversation as *harnesses for* public batteries rather than by
authoring their own science. Running a named battery through verdi buys you the
external validity of a community-scrutinized task set **and** verdi's internal
validity (pre-registration, blinding, tamper-evidence, gaming/contamination
forensics) layered on top.

The best-fit batteries are **agentic and test-graded** — a per-task container
image plus tests that must pass. That is exactly verdi's own model (`task.image`
+ deterministic holdout assertions), so the mapping is nearly one-to-one.
SWE-bench is the reference importer.

### The flow: export → import → materialize → run

```bash
# 1. Export the dataset ONCE (this is the only networked step; it's yours, not
#    the harness's — keeping the import deterministic and offline).
python -c "import datasets; datasets.load_dataset('princeton-nlp/SWE-bench_Verified', \
  split='test').to_json('instances.jsonl')"

# 2. Import → a cached corpus + a manifest with a citable content sha per task.
uv run bench corpus import instances.jsonl --cache ./swe-cache --benchmark swebench

# 3. Materialize → a runnable experiment: tasks.yaml (agent-visible) + a
#    read-only holdouts/ dir (the grading tests, insulated).
uv run bench corpus materialize ./swe-cache/manifest.json --cache ./swe-cache --out ./exp

# 4. Add arms + a judge rubric to ./exp/experiment.yaml (§2.1), then the usual pipeline:
uv run bench plan  ./exp/experiment.yaml --ledger ./exp/ledger.ndjson
uv run bench run   ./exp --engine harbor
uv run bench grade ./exp --runner docker
uv run bench analyze ./exp --official --corpus ./swe-cache/manifest.json
```

### What the import actually does — and why it's honest

- **Maps native → verdi.** Each SWE-bench instance's `problem_statement` becomes
  the agent-visible `prompt`; its `test_patch` + `FAIL_TO_PASS` / `PASS_TO_PASS`
  become the grading **holdout**; its per-instance image becomes the task
  `image`. A record missing a field the mapping needs is **refused loudly**, not
  imported as a half-task.
- **Insulation by construction.** The benchmark ships its grading tests *next to*
  the problem statement. Materialization routes them to different files — problem
  → `tasks.yaml`, tests → `holdouts/<id>/holdout.json` — so a benchmark's own
  tests can never leak to the agent it grades (an enforcing test asserts no
  holdout content appears in `tasks.yaml`).
- **Citable identity.** Each task gets a content sha over its *intrinsic* fields
  (problem, tests, repo, base commit, version) — not the image ref, which is
  deployment wiring you can re-pin without churning what a finding cites.
- **Contamination-aware for free.** SWE-bench's `created_at` rides onto the
  manifest entry, so the contamination sentinel's cutoff dating gets a real date
  instead of an honest `unknown`.

### The one environment-specific piece

Materialization writes the grading **specification** (which tests to run).
*Executing* those tests is the grading image's job — for SWE-bench, an image that
applies the recorded `test_patch`, runs the tests, and emits the
`holdout_results.json` the deterministic grader parses (§2.4). That image is the
one benchmark-bound piece verdi does not synthesize; the holdout spec is the
contract it consumes. This is the same honest boundary as the rest of the
real-container path — the logic is built and tested offline; the live run needs
the benchmark's own image.

The compatibility is proven, not asserted: offline tests drive a materialized
SWE-bench corpus through the whole real pipeline (`plan → run → grade → analyze →
verify-chain`) and through the actual deterministic grader, and a
`docker`-marked test grades a materialized SWE-bench task in a real network-less
container at the trusted (`grader=docker`) tier — the grading image required the
mounted holdout spec before emitting results. The only simulated step is the
SWE-bench test *execution* itself (its own per-instance image), which a real
`swebench/sweb.eval.*` image drops into.

### Fit by benchmark type

| Benchmark shape | Fit | Why |
|---|---|---|
| Agentic, container + test-graded (SWE-bench, Terminal-Bench, τ-bench-style) | **Natural** | Per-task image + tests-must-pass maps directly onto `task.image` + holdout assertions |
| String-metric Q&A (classic MMLU / HELM scenarios) | Awkward | verdi grades by container holdout assertions, not output-string metrics; adaptable but a square peg, and the hermetic/forensic machinery doesn't add much |

### Adding another battery

Implement a `TaskSource` (see `harness/corpus/benchmarks.py`): a `fetch()` that
reads the benchmark's exported records and yields `RawTask`s whose `content` is
Harbor-format (agent-visible keys + a `holdout` key), then import it through
`import_public_dataset`. You author the **importer** — a bounded, one-time shim —
never the tasks.

---

## 11. Making a run comparable and citable: the result card

A finished run is a defensible *private* A/B. To put it in the same conversation
as a public benchmark you need a result that is **citable** (tamper-evident
provenance) and **comparable** (two runs of the same tasks can be set side by
side). That is the **result card** — a read-only projection of an analyzed run:

```bash
uv run bench analyze <exp> --exploratory                     # a card certifies a rendered result
uv run bench card emit <exp> --corpus manifest.json --out run-a.card.json
uv run bench card compare run-a.card.json run-b.card.json    # side by side, or a loud refusal
```

The card is deliberately **co-equal**: it carries the per-arm **absolute score**
(the leaderboard's language — e.g. "control resolved 62%") *and* verdi's paired
**delta + CI + decision** (the rigor a bare leaderboard number lacks), under the
honesty stamps (`ADVISORY` tier, render mode, subset `n`).

**Comparability is verified, not claimed.** Each card carries a `battery_sha` —
a fingerprint of the exact task set that ran. `card compare` sets two cards side
by side only when their `battery_sha`, basis, and primary metric all match;
otherwise it **refuses loudly** ("not comparable: different task set"). With
`--corpus`, the fingerprint is the corpus's *intrinsic* task shas
(image-insensitive, so two runs of the same SWE-bench subset compare across image
mirrors); without it, the fingerprint is the lock's task commitment
(image-sensitive but always present and tamper-evident).

Everything on the card already exists in the hash-chained ledger — it computes no
new statistic and appends no event; it is re-derivable, and it carries the
`spec_sha256`, `lock_commitment_sha`, and `ledger_head` so a reader can verify it
against the chain. What it does **not** do: turn verdi into a leaderboard or vouch
that your corpus is representative — the `ADVISORY` tier and subset `n` stay on
the card so a comparable number is never mistaken for an authoritative one.

### Human-readable renders

The JSON form is the canonical, comparable artifact (feed it to `card compare`).
For reading or sharing, render the same card as markdown or a self-contained
HTML page (inline styles, no external references — archivable like the dossier):

```bash
uv run bench card emit <exp> --format md                     # human markdown to stdout
uv run bench card emit <exp> --format html --out run.card.html   # shareable page
```

Both are deterministic projections of the JSON card — they add no data, and the
`ADVISORY`/mode/`n` stamps ride along so a shared card stays honest about scope.

### Producing a real reference card (what it takes)

To publish a *reference* card comparing two real models on a public battery, you
need three things this repo cannot supply for you: the exported dataset (§10),
provider credentials for each arm (`run.config.yaml`, §6 — ideally per-arm keys),
and the battery's grading images (for SWE-bench, its per-instance images). With
those in a real environment (or a CI job with egress + a daemon), the recipe is:

```bash
uv run bench corpus import instances.jsonl --cache ./cache --benchmark swebench
uv run bench corpus materialize ./cache/manifest.json --cache ./cache --out ./exp
# add two arms + a third-vendor judge to ./exp/experiment.yaml, then:
uv run bench plan ./exp/experiment.yaml --ledger ./exp/ledger.ndjson
uv run bench run ./exp --engine harbor          # real models via the metering proxy
uv run bench grade ./exp --runner docker        # the battery's grading image
uv run bench analyze ./exp --official --corpus ./cache/manifest.json
uv run bench card emit ./exp --corpus ./cache/manifest.json --format html --out reference.card.html
```

The card's `battery_sha` lets anyone else who ran the same subset drop their card
next to yours with `card compare` — the comparable, citable artifact that was the
whole point. verdi deliberately does not ship a fabricated reference card: an
`ADVISORY` number stamped as if it were a measured model comparison would be
exactly the dishonesty the instrument exists to prevent.

---

## 12. Reusing an unchanged control while iterating

While you iterate on a *contender* stack against a fixed *control*, re-running
the same control over the same task set every time is wasteful. You can export a
completed control once and reuse it — but **only as an exploratory,
cost-saving signal**. A reused control never backs an official decision;
validation is always a fresh, fully interleaved run.

```bash
# 1. Export the control arm from a completed source run (do this while the
#    source workspaces still exist — the export snapshots each control trial's
#    judged diff so the bundle survives an ephemeral/reclaimed environment).
uv run bench control-cache export ./source-exp --arm control --out control.bundle.json

# 2. In your next iteration's experiment dir, reuse it instead of running control.
#    Preflight refuses loudly on ANY drift (see below); the contender runs fresh.
uv run bench run ./iter-exp --reuse-control control.bundle.json
uv run bench judge ./iter-exp        # also judges contender-vs-reused-control pairs
uv run bench analyze ./iter-exp --exploratory   # unpaired reuse section, watermarked
```

Or set it operationally in `run.config.yaml` (never the sha-locked
`experiment.yaml`):

```yaml
reuse_control:
  bundle: control.bundle.json
```

**What "provably unchanged" means.** Preflight computes a *control fingerprint*
and refuses reuse unless it matches the bundle byte-for-byte, naming what
drifted. The fingerprint covers: each task's definition **and** its holdout
script bytes, the arm definition (model / payload / cutoff / aux / hosts), the
pinned operational environment (engine, quotas, egress allowlist), the grader
(plugin ids + the instrument version that versions the grader code), and
`repetitions`. Change any of them and reuse is refused, not silently accepted.

**Why it can't taint an official finding.** Reused data lands under distinct
ledger event kinds (`reused_trial` / `reused_grade` / `reused_judge_verdict`)
that the official paired analysis never reads — so an official render of a reuse
run simply shows no fresh control to pair against. The reuse estimate is
*unpaired* (a reused control was not interleaved with the contender), rendered
only in the watermarked exploratory section with a disclosure block.

**Guardrails worth knowing.**

- A resume of `bench run` drops the reused control arm from the schedule **even
  if you forget `--reuse-control`** — the control never runs fresh behind your
  back.
- Reuse judging draws on the same locked judge token ceiling as native judging;
  it cannot spend past the pre-registered cap.
- The judged-diff snapshot is verified against its recorded hash before judging;
  a missing or tampered snapshot fails loudly rather than judging empty bytes.
- v1 supports reuse only for a control that is one of the two pre-registered
  primary-pair arms; a `judge_preference` win-rate is reported but reused
  verdicts feed neither official calibration nor contamination/confound analysis.

---

## 13. Where to go next

- **[deep-dive.md](deep-dive.md)** — what each stage writes to the ledger, the
  trust mechanism behind every claim, and the test that owns it.
- **[adapters.md](adapters.md)** — the complete normalized-log contract (v1 and
  v2), field tables, and failure semantics.
- **`deploy/metering-proxy/`** — the reference proxy for the harbor egress path.
- **`README.md`** — the command reference and the provisional-decisions register.
