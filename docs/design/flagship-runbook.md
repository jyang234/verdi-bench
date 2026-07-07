# verdi-go × verdi-bench — flagship local-execution runbook

> `RUNBOOK` · 2026-07-07 · the copy-paste local sequence that expands the
> [`integration-execution-report.md`](integration-execution-report.md) §6
> "run-next checklist" into ordered, environment-gated steps. It authors and runs
> the [`verdi-go-integration-plan.md`](verdi-go-integration-plan.md) §6 flagship
> experiment (Track A4 / phase **P4**) after the P0–P3 code that already merged.
>
> **Audience:** the repo owner, on a local machine with **Docker**, an
> **`ANTHROPIC_API_KEY`** and **`OPENAI_API_KEY`** in `.env`. Every step lists its
> exact command(s), the evidence that it succeeded, and how to roll back / retry.
>
> **The human decisions this runbook encodes** (approved 2026-07-07):
> **D4** — run the harbor calibration pilot first under a **$10 ceiling**; author
> the locked 2×2 flagship *iff* the pilot-projected total spend ≤ a flagship
> ceiling the owner sets after seeing pilot numbers, else the staged haiku-first
> 2-arm as the first official run. **D5** — the judge is the **OpenAI GPT-5.x**
> family, its exact fully-versioned id resolved at lock time from the owner's
> available models; judge key = `OPENAI_API_KEY`.
>
> The flagship kit is `scripts/flagship/` (`author_pilot.py`, `author_flagship.py`,
> `costmodel.py`) reusing `scripts/shakedown/_groundwork_lib.py`. All dollar
> figures below marked **ESTIMATE** are placeholders the pilot's own metering
> replaces — see the [cost model appendix](#appendix-a--cost-model-estimates).

---

## The pinned toolchain (bind everything to ONE build)

`groundwork`'s byte-stable output holds **per flowmap build only**. The corpus's
committed graphs, the grader image, and the trial image MUST all use the same
`(verdi-go ref, Go toolchain version)` pair, or `groundwork` will (correctly) flag
the skew as a caveat. From `corpora/groundwork-v0/README.md` "Provenance":

| pin | value |
|---|---|
| verdi-go ref (`GROUNDWORK_REF`) | `v0.0.0-20260707142329-7e8df2bb315a` (the pseudo-version `groundwork version` prints) |
| Go toolchain | **1.25.11** (the grader image bakes this; the graphs were frozen at 1.25.x; ≥ every task's `go 1.24.0` directive) |

Export the pin once so the builder, the grader plugin, and the wrapper all resolve
one build:

```bash
export VERDI_FLOWMAP_BIN=/abs/path/to/flowmap        # built at the pinned ref + Go 1.25.11
export VERDI_GROUNDWORK_BIN=/abs/path/to/groundwork
"$VERDI_GROUNDWORK_BIN" version                       # -> v0.0.0-20260707142329-7e8df2bb315a
```

---

## Step 0 — pull, sync, sanity gate (NO cost, NO Docker)

```bash
git fetch && git checkout flagship-draft
uv sync --all-extras                                  # provisions the venv + bench console script

# Build the pinned binaries (ref + Go version above), or `go install` them:
go install github.com/jyang234/golang-code-graph/cmd/flowmap@v0.0.0-20260707142329-7e8df2bb315a
go install github.com/jyang234/golang-code-graph/cmd/groundwork@v0.0.0-20260707142329-7e8df2bb315a
export VERDI_FLOWMAP_BIN=$(command -v flowmap) VERDI_GROUNDWORK_BIN=$(command -v groundwork)

make verify                    # full hermetic gate: pytest + import contracts
make groundwork-shakedown      # P3 local: fake engine, REAL gate, no keys/Docker
```

**Evidence of success**
- `make verify` → all tests pass, `10/10` import contracts green.
- `make groundwork-shakedown` → `P3 local pipeline: 7/7 OK`, the discrimination
  table showing **12/12 trap tasks discriminate** (solution PASS / exemplar FAIL)
  and **4/4 null tasks clean** both arms, chain OK, dossier rendered. This proves
  the groundwork gate discriminates through the whole pipeline before a cent is
  spent.

**Rollback / retry** — this step mutates nothing outside `scripts/shakedown/_run/`
(gitignored). If the shakedown can't find binaries it fails loud with the
`export VERDI_*_BIN` hint; set them and re-run. A stale venv: `rm -rf .venv &&
uv sync --all-extras`.

---

## Step 1 — docker-marked CI tiers, locally

The docker-marked tests are deselected by the fast suite; run them once Docker is
up to exercise the container paths (grader-image build + 4 grade cases; trial-image
build + 2 smoke cases).

```bash
docker info >/dev/null                        # daemon must be reachable
VERDI_REQUIRE_DOCKER=1 uv run pytest -m docker -q
```

**Evidence** — the docker tier passes; `VERDI_REQUIRE_DOCKER=1` makes a missing
daemon a loud failure rather than a silent skip.

**Rollback / retry** — read-only w.r.t. the repo. Docker not installed → install
it; a flaky build → re-run the single test with `-k <name>`.

---

## Step 2 — build + digest-pin the two images; set `VERDI_GRADER_IMAGE`

Two images (plan §2). Both MUST be built with the pinned Go version so their
`flowmap`/`groundwork` match the committed graphs.

```bash
# 2a. grader image — regenerates the branch graph + evaluates the gate, network-less.
docker build -t verdi-grader:pinned \
  --build-arg GROUNDWORK_REF=v0.0.0-20260707142329-7e8df2bb315a \
  images/grader
#   (or, if building binaries out of band: --build-arg with the PREBUILT flowmap/
#    groundwork copied in, built with Go 1.25.11 — see images/grader/Dockerfile.)

# 2b. trial image — payload-gated claude-code-groundwork (control byte-identical to
#     the official agent; treatment delta = exactly --mcp-config).
docker build -t claude-code-groundwork:pinned \
  --build-arg GROUNDWORK_REF=v0.0.0-20260707142329-7e8df2bb315a \
  images/reference/claude-code-groundwork

# 2c. digest-pin both and record the digests (never run against a mutable tag).
GRADER_DIGEST=$(docker inspect --format='{{index .RepoDigests 0}}' verdi-grader:pinned)
TRIAL_DIGEST=$(docker inspect --format='{{index .RepoDigests 0}}' claude-code-groundwork:pinned)
export VERDI_GRADER_IMAGE="$GRADER_DIGEST"     # the docker grade tier reads this
echo "grader=$GRADER_DIGEST"; echo "trial=$TRIAL_DIGEST"   # keep both for the authoring --trial-image
```

**Evidence** — both `docker inspect` lines print a `…@sha256:…` digest; a trial
container with `payload.tools=[groundwork]` lists the MCP tools + skill, and with
an empty payload has neither, while the image bytes are identical across arms
(the P1 smoke tests already assert this in CI).

**Rollback / retry** — images are content-addressed; a bad build is discarded by
rebuilding. If `RepoDigests` is empty (never pushed), push to a local registry or
use the `sha256:` from `docker images --digests`. **Do not** proceed with a
floating tag — the ledger's provenance needs the digest.

---

## Step 3 — docker-tier k=5 baselines → signed curation → `bench corpus admit`

Re-run each task's flake baseline on the **trusted** Docker grader tier (the
in-repo baselines are ADVISORY `local-exec`), then admit each task against the
authorized keyring. First materialize the corpus to a scratch dir:

```bash
make corpus-groundwork-v0            # build_tasks --out scratch/groundwork-v0/{expt,solutions}; validate-tasks
O=scratch/groundwork-v0
```

Per task (all 16 ids `gw-{r1..r5,o1..o4,n1..n4,m1..m3}`), run the k=5 baseline on
Docker and record the `flake_baseline` event, then approve + admit:

```bash
tid=gw-r2
sha=$(uv run python - <<PY
from harness.corpus.commit import task_content_sha, load_task_dicts
d={t["id"]:t for t in load_task_dicts("$O/expt")}["$tid"]; print(task_content_sha(d))
PY)
uv run bench corpus baseline $O/expt --task-id $tid --task-sha $sha \
  --workspace $O/solutions/$tid --holdouts-dir $O/expt/holdouts/$tid \
  --runner docker --actor "$(git config user.email)"

# signed curation approval (the approver must NOT be the miner; keyring is authorized)
uv run bench corpus approve $O/expt --candidate-id $tid --task-sha $sha \
  --signing-key <approver-ed25519.key> --approver "$(git config user.email)"
uv run bench corpus admit $O/expt --manifest <corpus-manifest.json> \
  --candidate-id $tid --task-sha $sha --baseline-ref <baseline-event-id> \
  --keyring <authorized-keyring.json> --actor "$(git config user.email)"
```

**Evidence** — each baseline ledgers one `flake_baseline` verdict `clean` with
`grader_name="docker"` (the trusted tier); `bench corpus validate-tasks $O/expt`
is `16/16 OK`; each `admit` moves the task to `admitted` in the manifest.

**Rollback / retry** — a `quarantined` baseline means the reference solution is
flaky under the real gate: stop and investigate the task, don't admit it. `admit`
is two-phase (PRA-M11): a post-ledger persist failure surfaces as a loud
`persist_error` (exit 1) with the admission already on the chain — reconcile the
manifest, do not re-admit. Keyring in legacy list format → migrate it (the CLI
names the refusal).

---

## Step 4 — EVAL-10 contamination probe (model access)

Fixture ancestry is public; the seeds were mutated, but the probe is mandatory
before any `--official` render (plan §5/§9). It queries each arm model for
training-set membership, so it needs the **locked** experiment (which defines the
arm models) — run it in step 6/7 against the pilot/flagship dir. Command shape:

```bash
uv run --env-file .env bench contamination probe <expt-dir> \
  --manifest <corpus-manifest.json> [--oracle-dir <oracles>] --actor "$(git config user.email)"
```

**Evidence** — one `contamination_probe` event per arm model; overlap below the
pre-registered `overlap_threshold`. **Rollback / retry** — a broken holdout/probe
refuses *before* ledgering (`ContaminationProbeRefusal`), so a re-run after fixing
the probe is clean.

---

## Step 5 — metering proxy up (BOTH hosts) + `run.config.yaml`; how the judge key flows

The harbor engine confines trial egress to a metering proxy and reads its log for
per-trial attribution + cost enforcement. The **flagship kit authors the
`run.config.yaml` for you** (managed proxy, both hosts, per-arm keys) — this step
is the reference for what it writes and how the OpenAI judge key actually reaches
the judge.

**What the kit writes** (`_groundwork_lib.run_config`):

```yaml
proxy:
  managed: true                       # the harness stands the proxy up + tears it down
  allowlist: [api.anthropic.com, api.openai.com]   # arms (anthropic) + the OpenAI judge (D5)
  log_path: metering/verdi.jsonl      # per-trial cost/attribution JSONL you read in step 6
provider_key_names_by_arm:            # NAMES only; VALUES read from env, never persisted
  opus-bare:      [ANTHROPIC_API_KEY]
  opus-grounded:  [ANTHROPIC_API_KEY]
  haiku-bare:     [ANTHROPIC_API_KEY]
  haiku-grounded: [ANTHROPIC_API_KEY]
```

**How the judge key actually reaches the judge — the truthful account (verified in
the code).** `provider_key_names` / `provider_key_names_by_arm` inject keys into
the **trial (arm) containers** only (`harness/run/settings.py:load_run_settings`).
The **judge is a different seam**: `bench judge` runs as a **host process**, and
each provider client reads its key **directly from the process environment** —
`harness/judge/providers/openai.py` calls `require_key("OPENAI_API_KEY")`, which is
`os.environ["OPENAI_API_KEY"]` (`harness/judge/providers/_http.py`). So:

- the arms authenticate via `ANTHROPIC_API_KEY` injected per-arm from
  `run.config.yaml`;
- the **judge authenticates via `OPENAI_API_KEY` in the environment where you run
  `bench judge`** — put it in `.env` and invoke `uv run --env-file .env bench judge …`.
  It is **not** listed in `run.config.yaml` (that would be a category error — the
  judge is not an arm). The judge's HTTP client honors the environment's
  `HTTP(S)_PROXY`, so if you export the metering proxy's URL for the judge process
  its egress is metered too — which is why `api.openai.com` is on the allowlist.

Bring the managed proxy up out of band only if you want it independent of a run
(the kit's `proxy.managed: true` already handles it per-run):

```bash
uv run bench proxy up      # optional; `bench proxy down` to tear down
```

**Evidence** — after a run, `metering/verdi.jsonl` has one `{"trial","host","decision"}`
line per request; every trial reaches an allowlisted host with `decision":"allow"`;
any `deny` is an egress violation. **Rollback / retry** — a configured-but-missing
proxy log fails a trial loud (`infra_failed(proxy_log_missing)`) rather than
reporting zero cost (PRA-H4); fix the proxy and re-run.

---

## Step 6 — the calibration PILOT (D4 first move, $10 ceiling)

Author the two pilot experiments (scaled to `--ceiling`; default 10), then run the
grade-only sequence and read the measured per-trial cost + calibration variance.

```bash
O=scratch/groundwork-v0                 # from step 3
P=runs/pilot
uv run python scripts/flagship/author_pilot.py \
  --corpus-out $O/expt --out $P --ceiling 10 --trial-image "$TRIAL_DIGEST"
```

`author_pilot` prints the projected-cost table and REFUSES loudly if the design's
(conservative, estimated) projection exceeds the ceiling. At `--ceiling 10` it
emits, deterministically:
- `$P/calibration-haiku/` — **haiku bare-vs-grounded** over a stratified subset
  covering **all four classes** (the `CalibrationVariance` source);
- `$P/opus-cost-slice/` — **opus bare-vs-grounded** over a small round-robin slice
  (a binding trap + the null) — the cost-per-opus-trial measurement.

Run each (the pilot **skips the judge** — grade-only calibration; the placeholder
judge is never invoked). Plan → run → grade BOTH; but only the **haiku** experiment
feeds `CalibrationVariance` (the opus slice is cost-only — calibrating it would
overwrite the manifest's variance with opus's `p`):

```bash
for E in $P/calibration-haiku $P/opus-cost-slice; do
  uv run bench plan $E/experiment.yaml --actor "$(git config user.email)"
  uv run --env-file .env bench run  $E --engine harbor
  uv run bench grade $E --runner docker
done

# CalibrationVariance from the HAIKU experiment only (p/rho/n_tasks → the MDE gate):
uv run bench corpus calibrate $P/calibration-haiku \
  --manifest <corpus-manifest.json> --kind full --actor "$(git config user.email)"
```

**Read the numbers the flagship needs:**

```bash
uv run bench status $P/calibration-haiku          # per-arm cost + trials (heartbeat projection)
uv run bench status $P/opus-cost-slice
# cost-per-trial(tier) = per_arm.cost / per_arm.trials, from the metering attribution;
# CalibrationVariance p/rho/n_tasks are recorded by `bench corpus calibrate` in the manifest.
```

**Evidence** — each experiment locks (one `experiment_locked`), the harbor run
produces digest-pinned trials, `bench grade --runner docker` writes trusted grades,
and `bench corpus calibrate` advances the manifest to `full-run-validated`. The
`metering/verdi.jsonl` + `bench status` give the measured `cost-per-trial-haiku`,
`cost-per-trial-opus`, and the calibration `p`.

**Rollback / retry** — the `cost_ceiling` on each experiment is the runaway-stop:
if opus is dearer than the conservative estimate, the run halts with a ledgered
`run_stopped_cost_ceiling` and you still have per-trial cost from the trials that
ran. `run` is resumable (idempotent per cell); re-invoke to continue ungraded
cells. The pilot is throwaway — delete `$P` and re-author to change `--ceiling`.

> **Owner may run the pilot at `--ceiling 50`** for a better `CalibrationVariance`:
> it buys the full 16-task subset, **2 reps** (the correlated-rep clustered-variance
> signal the MDE model uses), and a larger opus slice. See the appendix tiers.

---

## Step 7 — the FLAGSHIP: author, LOCK CEREMONY (D4 + §6 notes), run, render

### 7a. Author the chosen design from the measured pilot inputs

```bash
F=runs/flagship
uv run python scripts/flagship/author_flagship.py \
  --corpus-out $O/expt --out $F \
  --judge-model openai/<EXACT-VERSIONED-ID> \      # D5 — resolved at lock (see below)
  --flagship-ceiling <USD you set after the pilot> \
  --cost-per-trial-haiku <measured, step 6> \
  --cost-per-trial-opus  <measured, step 6> \
  --pilot-manifest <corpus-manifest.json> \        # supplies CalibrationVariance (PL-5)
  --target-mde 0.2 --trial-image "$TRIAL_DIGEST"
```

`author_flagship`:
1. computes **MDE-driven repetitions** via `plan/power.py` (`CalibrationVariance`
   from `--pilot-manifest`; else `--cal-p`; else `AssumedVariance`, flagged
   `assumption_based_mde` loudly);
2. **projects total spend** for the 2×2 and the staged haiku-first design from the
   measured per-trial costs;
3. prints the **D4 decision table** and applies the rule; and
4. authors the chosen design — the 2×2 (`multi_arm_correction: holm`) or the staged
   2-arm — with the payload asymmetry, seed, `--flagship-ceiling`, the MDE-driven
   reps, `decision_rule: delta_holdout_pass_rate >= <target-mde>`, and
   `hypothesized_effect: <target-mde>` (so `bench plan`'s power gate enforces it).

#### The D4 rule (what the table implements)

> Choose the **locked 2×2** ({opus,haiku}×{bare,grounded}, `holm`) **iff** the
> projected 2×2 total spend ≤ `--flagship-ceiling`; **otherwise** the **staged
> haiku-first** 2-arm (haiku ± grounded) as the first official run. Example table
> (measured opus ≫ haiku, ceiling too low for 4 arms × the MDE reps):

```
D4 DECISION TABLE   (flagship-ceiling = $40.00; judge add-on $0.0000/judgment)
  rule: choose the locked 2x2 IFF projected_total(2x2) <= flagship-ceiling; else staged haiku-first.
  design               arms  trials   solve$    judge$      total$   <= ceiling?
  2x2 (holm)              4     192    220.80      0.00      220.80           NO
  staged haiku-first      2      96     28.80      0.00       28.80          yes  <== CHOSEN
  CHOSEN: staged
```

#### The D5 judge-id placeholder convention (NEVER invent an id)

The schema demands a **fully-versioned** judge id — a bare `openai/gpt-5` is an
*alias* and is rejected at plan (`AliasJudgeIdError`). `author_flagship` **never
defaults** the judge and validates the `openai/` prefix loudly. At lock, resolve
the exact id from the owner's available OpenAI GPT-5.x models and pass it verbatim:

- **Placeholder convention** (docs only, never committed as a real id):
  `openai/gpt-5.x-YYYY-MM-DD` where the date/build stamp is what makes it
  "versioned" (the `_VERSIONED` rule in `harness/schema/judge_config.py`).
- Resolve the real one from the OpenAI models list, e.g.
  `openai/gpt-5.1-2025-11-01` (illustrative — use *your* available id). Confirm it
  with `client.models.list()` / the provider console before locking.

### 7b. The LOCK CEREMONY — write the §6 interpretation notes, then lock

The schema has **no free-prose field**, so these plan-§6 pre-registered
interpretation notes are recorded HERE and committed alongside the experiment
(e.g. `$F/PRE-REGISTRATION.md`) **before** the lock, so nobody re-litigates them
post-hoc — verbatim:

> 1. The groundwork `command` holdout applies the same gate the treatment arms
>    have in-loop. **This is the design, not deck-stacking**: both arms have
>    identical epistemic access (`policy.json` is readable in every workspace;
>    postmortem 9c shows agents apply it when they look); the treatment differs
>    only in having it surfaced. The claim under test is "gate-in-loop prevents
>    what gate-at-merge rejects."
> 2. Expected effect is concentrated in the grounded-vs-bare contrasts on trap
>    classes; null tasks are expected null and are kept in the tally
>    (anti-cherry-pick).
> 3. A null overall is a publishable, useful result (it bounds verdi-go's claim to
>    "CI backstop, not in-loop uplift") — the postmortem's publish-the-null posture
>    carries over.

Then the pre-registration + official pipeline:

```bash
# contamination probe FIRST (step 4), against the authored flagship dir:
uv run --env-file .env bench contamination probe $F --manifest <corpus-manifest.json> \
  --actor "$(git config user.email)"

uv run bench plan $F/experiment.yaml --actor "$(git config user.email)" \
  --corpus-manifest <corpus-manifest.json>          # PL-5: recomputes MDE from CalibrationVariance,
#                                                     enforces the power gate on hypothesized_effect
uv run --env-file .env bench run  $F --engine harbor
uv run bench grade $F --runner docker
uv run --env-file .env bench judge $F               # OPENAI_API_KEY from the env (D5), NOT run.config
uv run bench forensics scan $F --actor "$(git config user.email)"
uv run bench selfcheck $F --actor "$(git config user.email)"   # A/A coverage selfcheck; official needs it green
uv run bench analyze $F --official --corpus <corpus-manifest.json> --html \
  --actor "$(git config user.email)"
uv run bench card emit $F --corpus <corpus-manifest.json>      # the citable result card
```

**Evidence** — one `experiment_locked` carrying the MDE payload + (if
`hypothesized_effect` powered) no `power_gate_skipped` flag; the run's trials are
`engine=="harbor"` and digest-pinned; grades are `grader_name="docker"`; the judge
appends one verdict per comparison; `selfcheck` passes; `analyze --official`
renders `findings.official.*` behind the fence; `card emit` writes the result card.
`bench verify-chain $F/ledger.ndjson` is green throughout.

**Rollback / retry** — the spec is sha-locked at `plan`: a post-lock edit of
`experiment.yaml` fails every downstream verb (`LockMismatchError`) — to change the
design, start a fresh dir and re-author. An **underpowered** design refuses at
`plan` (fail-closed) unless you pass `--acknowledge-underpowered` (a ledgered
acknowledgment). `run` is resumable; `grade`/`judge` skip already-settled trials.
`analyze --official` fail-closes to a ledgered `cant_analyze` (a first-class
disposition) rather than a wrong render — read it, fix the cause, re-run.

---

## Step 8 — Track B cadence + Track C GPU pilot (pointers)

- **Track B (verdi-go's dev loop)** — once the corpus is admitted, verdi-go
  iteration is a **tool-version A/B**: same model both arms, one image baking
  `groundwork@vA` / `groundwork@vB` at distinct staging paths, `payload` selecting
  which the entrypoint exposes; `bench control-cache export` + `--reuse-control`
  make candidate-vs-main cheap (exploratory tier); `bench card emit` / `card
  compare` track a fixed task set across releases. Cadence: an exploratory run per
  meaningful verdi-go change; a locked official run only for milestone claims
  (plan §7). The funnel signal (`scripts/funnel_metrics.py` over real
  `groundwork-mcp.jsonl`) tells you *which* part of ground→edit→verify to iterate.
- **Track C (workspace observability / GPU pilot)** — a separate, gated plan
  ([`workspace-observability-plan.md`](workspace-observability-plan.md)). The P0
  kit is `scripts/workspace-pilot/` (standalone, no harness imports); its GPU paths
  are **UNTESTED** until the owner runs them on a GPU box — follow
  `scripts/workspace-pilot/README.md`, send back artifacts/CSVs, then the data-shape
  memo freezes `workspace_trajectory` v1.

---

## Appendix A — cost model (ESTIMATES)

**These are estimates the pilot's metering replaces.** They exist only to *size*
the pilot under a ceiling and to give an order-of-magnitude flagship projection.
`scripts/flagship/costmodel.py` is the single source; the numbers below follow
from its documented constants.

**Per-trial token profile** (one agentic Go coding trial — read a few files, run
`go test`, iterate a handful of turns), billed **GROSS** (no prompt-cache credit,
a deliberately conservative over-estimate — the real cached cost is *lower*):

| | input tokens | output tokens |
|---|---|---|
| per trial | 200,000 | 20,000 |

**List price** per 1M tokens (Claude model catalog, 2026-06-24 — ESTIMATE; the
owner's invoiced pricing and the pilot's metering govern):

| tier (arm model) | input $/1M | output $/1M | ⇒ est. $/trial |
|---|---|---|---|
| opus  (`anthropic/claude-opus-4-8-*`)  | 5.00 | 25.00 | **1.50** |
| haiku (`anthropic/claude-haiku-4-5-*`) | 1.00 |  5.00 | **0.30** |

**Why $10 is pilot-scale, not flagship-scale.** Opus dominates. The *minimal*
pilot — haiku subset 4 (×1 rep) + opus slice 2 — projects **$8.40** at these
estimates, so $10 funds it with headroom; a $4 ceiling is refused loudly. The
pilot is *sampling* opus cost, not running the flagship.

**Pilot tiers** (what each ceiling buys, from `plan_pilot`'s greedy ladder):

| `--ceiling` | haiku subset | reps | opus slice | trials | est. total |
|---|---|---|---|---|---|
| $10 | 6 (all 4 classes) | 1 | 2 (trap+null) | 16 | ~$9.60 |
| $50 | 16 (whole corpus) | 2 | 8 (all 4 classes) | 80 | ~$43.20 |

**Order-of-magnitude flagship projection** (16 tasks; solve cost dominates — the
two Opus arms; judge is an advisory add-on, run once, default excluded). Using the
*measured* pilot per-trial costs `co` (opus) and `ch` (haiku) and the MDE-driven
`reps`:

| design | trials | solve cost | when chosen (D4) |
|---|---|---|---|
| **2×2 (holm)** | `16·reps·4` | `16·reps·(2·co + 2·ch)` | if ≤ `--flagship-ceiling` |
| **staged haiku-first** | `16·reps·2` | `16·reps·(2·ch)` | otherwise |

Worked (illustrative, ESTIMATE): at the estimate costs and `reps=3`, the 2×2 solve
is `16·3·(2·1.50 + 2·0.30) = $172.80` and staged is `16·3·2·0.30 = $28.80` — so a
flagship ceiling of, say, $200 keeps the full 2×2; a $40 ceiling forces staged. The
kit computes this from *your* measured numbers, so replace the estimates before
reading the decision.
