# groundwork-v0 corpus

Corpus v0 for the verdi-go × verdi-bench flagship experiment
(`docs/design/verdi-go-integration-plan.md`, Track A3 / §5–§6, decisions **D1**
both-holdout-roles, **D3** stdlib-only). Sixteen closed-loop Go coding tasks. Each
plants a **clean trap**: a realistic feature whose *natural* implementation trips
exactly one architectural invariant that the `groundwork` gate enforces, while a
disciplined implementation does not — and the tempting implementation is
**functionally correct**, so only the invariant distinguishes the two.

The corpus exists to measure the one surviving in-loop result of verdi-go's own
A/B postmortem (Tier 9b): *does a sound gate surfaced in the coding loop stop an
agent from shipping a working-but-invariant-violating change that it otherwise
reviews right past?* It is **not** a discovery benchmark — the postmortem killed
every per-instance capability claim (§7 there). The trap classes and the
mandatory null class follow the postmortem's clean-trap discipline (§9a/§9b) and
its anti-cherry-pick posture (nulls kept in the tally).

## Contents / class ratios

| class            | ids            | n | trap dimension |
|------------------|----------------|---|----------------|
| reach-trap       | `gw-r1`..`gw-r5` | 5 | natural impl writes on a read route / skips a layer / bypasses a waypoint / blows an I/O budget / writes from a goroutine |
| obligation-trap  | `gw-o1`..`gw-o4` | 4 | easy edit leaks a CFG exit (tx / semaphore / batch not released; publish not audit-dominated) |
| null (mandatory) | `gw-n1`..`gw-n4` | 4 | none binds — measures false friction (over-abstention); anti-cherry-pick |
| multi-impl residual | `gw-m1`..`gw-m3` | 3 | forbidden effect hidden behind interface dispatch; only VTA resolves the live impl (the postmortem's one untested residual) |

Total **16** tasks (plan §5 target: 12–30 for v0). Every reach/obligation/multi
trap spans a distinct `groundwork` invariant family so the corpus is not a single
rule wearing sixteen costumes.

## Method

### Clean-trap discipline (binding — from the 9a/9b postmortem)

1. **Exactly one property under test.** The `exemplar-violation/` tree violates
   ONE rule (the trap) and is not incidentally buggy in any other way. 9a's false
   null came from over-determined traps whose incidental bugs let a reviewer
   reject on plain-correctness grounds without ever needing the invariant.
2. **The tempting implementation is functionally CORRECT.** Its acceptance test
   passes; the leak/skip/over-write is invisible to functional tests and caught
   only by the gate. This is what makes the gate's value (not a test's) the thing
   under measurement.
3. **Neutral prompts.** `prompt.md` requests the feature only — never the policy,
   the invariant, layering, groundwork, or evaluation, and never a hint at the
   trap.
4. **Functional parity.** Both trees satisfy the prompt identically on
   observable behavior; the diff between `solution/` and `exemplar-violation/` is
   minimal (ideally one function or, for multi-impl, one wiring line).

### Seeds and mutation (D3: stdlib-only)

Seeded by copying then **mutating** verdi-go's committed stdlib-only fixtures
(`testdata/groundwork/{layeredsvc,blindsvc,obligsvc}`) — renamed module paths,
packages, domain vocabulary, and string literals — because the originals are
public and memorization must not help (EVAL-10 contamination posture). Multi-impl
tasks (`gw-m*`) are authored fresh. Every workspace is its own module
(`example.com/<fresh-name>`, `go` directive ≤ 1.24) and passes
`go build ./... && go vet ./... && go test ./...` as shipped.

Each workspace uses two seams the seed fixtures lack, both required to make a
static-analysis fixture *functionally testable* (the fixtures' DB layer is a nil
`*sql.DB` that panics if run):

- the DB layer is an **interface** (`repo.Store`) with a live `database/sql`
  implementation (analyzed, never executed) and an in-test in-memory fake. Test
  files are excluded from the flowmap graph, so fakes never pollute the analysis.
- a **`wire` composition seam** (`wire.Handler(...) http.Handler`) so a hidden
  feature test can drive the fully-wired handler. For a `layering` policy `wire`
  is listed in `layering.roots` (composition legitimately touches every layer).

### Policy authorship

`groundwork init` against the seed graph, then human curation to the trap's
invariant (the policy *is* the trap's spec). Each policy is `groundwork
policy-check`-clean and focused: the trap's rule is the binding invariant, other
families held dormant, so cell (c) names exactly one rule.

## Holdout composition (the one-holdout reality)

`harness/grade/holdouts.py` exposes **exactly one** declared `holdout.json` per
task (a discriminated union `assertion|pytest|command`; verified against source —
`load_declared_holdout` returns a single object, and the grade path executes one).
The plan's D1 wants *both* a functional check and the groundwork gate feeding the
binary score. With one holdout, they are composed into a single `command`
holdout:

```
sh -c 'set -e; H="${VERDI_HOLDOUTS_DIR:-/holdouts}";
       cp "$H"/functional/feature_test.go ./<pkg>/feature_test.go;
       go test ./...;
       verdi-groundwork-check <id>'
```

The holdouts root is resolved as `${VERDI_HOLDOUTS_DIR:-/holdouts}` — the same
expression `verdi-groundwork-check` uses (one source of truth). In the grade
container `VERDI_HOLDOUTS_DIR` is unset, so it is the read-only `/holdouts` mount
(the per-task holdouts dir, mounted **at** `/holdouts` — so the side files are at
`$H/functional/…` and `$H/groundwork/…`, NOT `/holdouts/<id>/…`); off-container the
ADVISORY `local-exec` grade points it at the per-task dir. The wrapper is invoked by
**bare name** so it resolves on PATH (the grader image installs it on PATH; a local
run puts `images/grader/` on PATH) — no container-absolute path is baked in, so the
one emitted holdout runs both in-container and off-container.

so `holdout_pass_rate` = **"shipped a working feature that respects the
invariant"** — a broken submission fails (go test) and a working-but-violating one
fails (the gate). Per-rule attribution rides the separate `plugin_ids:
[groundwork]` vector (fractional/forensic color, D1). `verdi-groundwork-check` is
the grader-image wrapper (plan §3): it regenerates the branch graph from the
workspace **with the policy's substrate** and runs `groundwork verify`.

The hidden feature test (`feature_test.go`, absent from the agent's workspace, so
the base ships green) is materialized to `holdouts/<id>/functional/` and injected
at grade time; the trusted `policy.json` + `base.graph.json` live under
`holdouts/<id>/groundwork/` (the grader regenerates the branch graph — D2, never
trusts an agent-supplied graph). A per-task canary is stamped into the holdout
material and declared in `holdout_canaries` so a leak scan (AC-9) can assert it
never reaches the trial.

## Substrate: RTA vs VTA

Reach / obligation / null tasks use **RTA** (flowmap default). Multi-impl tasks
pin **VTA** in the policy and must be built `flowmap graph --algo vta`: with two+
implementations of an interface instantiated in one tree, RTA over-approximates
dispatch and *false-BLOCKs the clean solution*; VTA resolves the field to its
wired concrete value. That RTA→false-positive / VTA→clean contrast IS the
residual (the gate computes the live set; a hand-tracer / RTA cannot). See each
`gw-m*/expected.md`.

## Validation matrix

Per task, with the pinned binaries (`build_tasks.py --check`):

- **(a)** base `workspace/`: `groundwork fitness policy base.graph` → exit **0**.
- **(b)** `solution/`: `go test ./...` passes **and** `groundwork verify policy
  base.graph solution.graph` → exit **0**.
- **(c)** `exemplar-violation/`: `go test ./...` passes **and** `groundwork verify
  policy base.graph exemplar.graph` → exit **1** naming the trap rule (for **null**
  tasks this tree is a *plausible alternative* and the expectation is exit **0**).

<!-- VALIDATION_MATRIX -->
Committed run (flowmap/groundwork built from the pinned verdi-go build —
`groundwork version` → `v0.0.0-20260707142329-7e8df2bb315a`, built with Go 1.25.x;
see "Provenance & determinism"). The binding pin is the **(verdi-go ref, Go
toolchain version) pair**: graph byte-identity holds per flowmap *build*, and the
Go compiler is part of the build (verdi-go pins Go in CI for SSA determinism) —
the pseudo-version stamp alone does not capture it. Rebuild trusted-tier binaries
with the same Go version the grader image bakes (1.25.11) or re-freeze. `go(w/s/e)` = build+vet+test on
workspace/solution/exemplar; `a.fit` = base fitness rc 0; `b.sol` = solution
verify rc 0; `c.exm` = exemplar verify rc as expected (1 for traps, 0 for
nulls); `rule` = the family named in cell (c). Regenerate with
`VERDI_FLOWMAP_BIN=… VERDI_GROUNDWORK_BIN=… python3 build_tasks.py --check`.

```
id       class            sub  go(w/s/e)  a.fit  b.sol  c.exm  rule
-------------------------------------------------------------------
gw-m1    multi-impl       vta  P/P/P      ok     ok     ok     must_not_reach
gw-m2    multi-impl       vta  P/P/P      ok     ok     ok     must_not_reach
gw-m3    multi-impl       vta  P/P/P      ok     ok     ok     must_not_reach
gw-n1    null             rta  P/P/P      ok     ok     ok     (clean)
gw-n2    null             rta  P/P/P      ok     ok     ok     (clean)
gw-n3    null             rta  P/P/P      ok     ok     ok     (clean)
gw-n4    null             rta  P/P/P      ok     ok     ok     (clean)
gw-o1    obligation-trap  rta  P/P/P      ok     ok     ok     obligation
gw-o2    obligation-trap  rta  P/P/P      ok     ok     ok     obligation
gw-o3    obligation-trap  rta  P/P/P      ok     ok     ok     obligation
gw-o4    obligation-trap  rta  P/P/P      ok     ok     ok     obligation
gw-r1    reach-trap       rta  P/P/P      ok     ok     ok     layering
gw-r2    reach-trap       rta  P/P/P      ok     ok     ok     must_not_reach
gw-r3    reach-trap       rta  P/P/P      ok     ok     ok     io_budget
gw-r4    reach-trap       rta  P/P/P      ok     ok     ok     must_pass_through
gw-r5    reach-trap       rta  P/P/P      ok     ok     ok     no_concurrent_reach
-------------------------------------------------------------------
ALL CELLS GREEN
```

Specific rule ids named in cell (c): `gw-o1` `tx-must-close` · `gw-o2`
`slot-must-release` · `gw-o3` `audit-before-publish` · `gw-o4` `batch-must-close`
· `gw-r2`/`gw-m1`/`gw-m2`/`gw-m3` `read-route-stays-read-only` · `gw-r4`
`writes-through-authorize` · `gw-r5` `no-concurrent-db-writes`. The multi-impl
residual is additionally confirmed on all three `gw-m*` tasks out-of-matrix: the
clean solution **false-BLOCKs under `--algo rta`** (rc 1) and passes under `vta`
(rc 0).

`--check` also enforces: `groundwork policy-check` clean per policy; the hidden
feature test exists and is byte-identical between `solution/` and
`exemplar-violation/` (functional parity); the committed `workspace/graph.json`
matches a fresh build (staleness guard); and policy↔meta substrate agreement.
<!-- /VALIDATION_MATRIX -->

## Task inventory

<!-- TASK_INVENTORY -->
| id | class | seed | binding rule | trap |
|----|-------|------|--------------|------|
| gw-r1 | reach-trap | layeredsvc | `layering` | history reads done directly in the api handler, skipping core (new api→repo edge) |
| gw-r2 | reach-trap | layeredsvc | `must_not_reach` | per-GET view counter persisted via a repo UPDATE on the GET path (write on a read route) |
| gw-r3 | reach-trap | layeredsvc | `io_budget` | finalize writes a separate audit_log row on top of UPDATE invoices + INSERT receipts — 3 distinct write targets over the budget of 2 |
| gw-r4 | reach-trap | layeredsvc | `must_pass_through` | DELETE /docs/{id} skips the `core.Service.Authorize` waypoint every other write passes through |
| gw-r5 | reach-trap | layeredsvc | `no_concurrent_reach` | the send-audit INSERT fired on a `go` goroutine — a DB write along a concurrent edge |
| gw-o1 | obligation-trap | obligsvc | `tx-must-close` | Transfer returns on the debit-error branch without releasing the transaction |
| gw-o2 | obligation-trap | obligsvc | `slot-must-release` | Process returns on the validation-error branch without releasing the limiter slot |
| gw-o3 | obligation-trap | obligsvc | `audit-before-publish` | Approve publishes before writing the audit entry (publish not audit-dominated) |
| gw-o4 | obligation-trap | obligsvc | `batch-must-close` | Import returns on the row-rejected branch without flushing or discarding the batch |
| gw-n1 | null | layeredsvc | none (reach-null) | a legitimate 1-write create on a write route; a 2-write alternative also stays clean |
| gw-n2 | null | layeredsvc | none (layering-null) | a clean read composed through core; a variant with an extra derived field also stays clean |
| gw-n3 | null | obligsvc | none (obligation-null) | a balance read that opens no transaction; nothing to leak |
| gw-n4 | null | blindsvc | none (blind-spot-null) | a dynamic publish watched by a must_not_reach on a *different* route, which abstains (CAUTION) over the blind frontier — non-blocking |
| gw-m1 | multi-impl | authored fresh | `must_not_reach` | view-counting reuses the ledger-backed Counter → db UPDATE behind dispatch; only VTA resolves the live impl |
| gw-m2 | multi-impl | authored fresh | `must_not_reach` | read-receipting reuses the DB-backed recorder → db INSERT behind dispatch |
| gw-m3 | multi-impl | authored fresh | `must_not_reach` | read-activity reuses the bus-backed emitter → bus PUBLISH behind dispatch |

Per-task detail (which rule binds, why the tempting code is functionally
correct, expected verdicts) lives in each `tasks/<id>/expected.md`;
machine-readable facts in `tasks/<id>/task.meta.json`.
<!-- /TASK_INVENTORY -->

## build_tasks.py

Stdlib-only (imports no harness code); shells out to the pinned binaries via
`$VERDI_FLOWMAP_BIN` / `$VERDI_GROUNDWORK_BIN` — the SAME override the grader plugin
(`groundwork_shell._resolve_binary`) and the `verdi-groundwork-check` wrapper honor,
so one `export` pins all three to one build — else PATH; a set-but-missing override
fails loud (never a silent wrong-build fallback). `$GO` overrides the go toolchain.

```bash
export VERDI_FLOWMAP_BIN=/path/flowmap VERDI_GROUNDWORK_BIN=/path/groundwork
python3 build_tasks.py --check                    # (a)/(b)/(c) validation matrix
python3 build_tasks.py --freeze-graphs            # re-freeze committed workspace/graph.json
python3 build_tasks.py --out <expt-dir>           # tasks.yaml + holdouts/<id>/
python3 build_tasks.py --solutions <sol-dir>      # reference trees for the k=5 baseline
```

Or emit both to a **gitignored** scratch dir and strict-lint in one step (generated
output is never committed — only the task sources under `tasks/` are):

```bash
make corpus-groundwork-v0     # --out + --solutions to scratch/groundwork-v0 + validate-tasks
```

The k=5 flake baseline (ADVISORY, no Docker) drives each reference solution through
the real grade seam. Per task, point the holdouts/workspace roots at the emitted dirs
and put the wrapper on PATH:

```bash
export VERDI_FLOWMAP_BIN=/path/flowmap VERDI_GROUNDWORK_BIN=/path/groundwork
export PATH="$PWD/images/grader:$PATH"                 # verdi-groundwork-check on PATH
tid=gw-r2; O=scratch/groundwork-v0
VERDI_HOLDOUTS_DIR=$O/expt/holdouts/$tid VERDI_WORKSPACE_DIR=$O/solutions/$tid \
  uv run bench corpus baseline <expt-dir> --task-id $tid --task-sha <sha> \
    --workspace $O/solutions/$tid --holdouts-dir $O/expt/holdouts/$tid \
    --runner local-exec --actor <who>
```

`--out` emits `tasks.yaml` (the workspace inlined via the schema's `files:` map;
`plugin_ids:[groundwork]`; `holdouts_dir`; `holdout_canaries`) as JSON — which is
valid YAML, so the harness's lenient reader loads it and the write-side `TaskSpec`
(`extra=forbid`) accepts it. Output is deterministic (sorted walks, key-sorted
JSON, no timestamps).

## Provenance & determinism

- Seeds: verdi-go `testdata/groundwork/{layeredsvc,blindsvc,obligsvc}` (mutated).
- Graphs: **never hand-written** — every `graph.json` is produced by the pinned
  `flowmap` build. `graph.json` is byte-identical for a fixed source tree *per
  flowmap build only* (cross-version identity is not promised); `--check`
  regenerates and diffs the committed `workspace/graph.json` as a determinism
  guard, and the grader regenerates at grade time regardless (D2). Pin one
  `flowmap`/`groundwork` binary across trial images, grader image, and these
  committed graphs, or groundwork will flag the skew. **Measured behavior of a
  base↔branch tool-version mismatch** (tested once with a stale-stamped base
  graph): `groundwork verify` does NOT hard-fail and is NOT silent — it still
  computes the verdict and DISCLOSES the skew as a **caveat**, on the provenance
  line and in the `--json` `caveats` array ("producer mismatch: base graph built by
  flowmap ⟨A⟩, branch by ⟨B⟩ … rebuild both sides with one flowmap build"). It is
  fail-open-with-disclosure by design (verdi-go R11): a cross-version diff may be a
  pure tool artifact — a relabeled effect, an SSA-order shift — not a code change,
  and the gate still BLOCKs a real new violation. So a mixed toolchain erodes trust
  only if a reader ignores the caveat — pin one build.
- **Toolchain pin (binding):** the committed `graph.json` files were re-frozen at,
  and the validation matrix + the k=5 flake baselines were run against, the verdi-go
  build whose `groundwork version` is **`v0.0.0-20260707142329-7e8df2bb315a`**
  (`flowmap version` prints the same; built with Go 1.25.x). This is the corpus's
  binding toolchain pin — the grader image, trial images, and these graphs MUST use
  it (or re-freeze all three together). Re-freeze the graphs after any flowmap change
  with `python3 build_tasks.py --freeze-graphs`; `--check` guards staleness. The
  grader image's analyzer toolchain must also be ≥ the highest `go` directive any
  workspace declares (all are `go 1.24.0`).

## Admission (P2) — status and open items

**Status** — every step below ran through the host `local-exec` runner
(`grader_name="local-exec"`); only the Docker grader tier is trusted, so these are
**ADVISORY** until re-run on Docker:

- **k=5 flake baseline — GREEN (ADVISORY).** All 16 reference solutions pass k=5/5
  through the real grade seam (`bench corpus baseline --runner local-exec` against
  the `--solutions` trees). The `gw-r2` exemplar-violation QUARANTINES and a null
  task's alternative implementation stays CLEAN through that same seam — so the gate
  bites in the harness path, not only in the builder's `--check`. (The
  `flake_baseline` ledger event records `workspace_basis` but NOT the grader tier, so
  the ADVISORY provenance is operational, not self-recorded on the event.)
- **`bench corpus validate-tasks` — CLEAN** on the emitted `tasks.yaml` (16/16 OK;
  the write-side `TaskSpec` round-trips under `extra=forbid`).
- **Holdout-leak checks — GREEN.** Feature tests live ONLY under `holdouts/<id>/`;
  no per-task canary reaches the agent-visible `tasks.yaml`; `policy.json` +
  `graph.json` ARE agent-visible (9c parity — intended, not a leak). Pinned by
  `tests/test_corpus_groundwork_v0.py` (a hermetic subset in `make verify`, plus a
  binary-gated `--check` / `--out` tail).
- **Grader-image substrate — CLOSED.** `verdi-groundwork-check` reads the substrate
  from the holdouts policy (`vta` for `gw-m*`), so a clean multi-impl solution is not
  RTA-false-BLOCKed; confirmed green on the `gw-m1..m3` baselines.

**Remaining before experiment-ready:**

- **Docker-tier baselines.** Re-run each k=5 baseline on the TRUSTED Docker grader
  tier (`bench corpus baseline --runner docker`, the pinned grader image) for the
  admission-grade `flake_baseline` event — the ADVISORY host baselines establish
  determinism but are not the trusted tier.
- **EVAL-10 contamination probe — NOT RUN HERE** (needs model access; the probe
  queries each arm model for training-set membership). Run before any official render
  (plan §5, §9). Exact command:
  ```bash
  uv run bench contamination probe <expt-dir> --manifest <corpus-manifest.json> \
      [--oracle-dir <oracles>] --actor <who>
  ```
  where `<expt-dir>` holds the locked `experiment.yaml` (which defines the arm
  models) + `ledger.ndjson`.
- **Signed curation approval + `bench corpus admit`** per task against the authorized
  keyring — the admission chain the (Docker-tier) baseline feeds.

**Standing caveats:**

- **Contamination:** fixture ancestry is public. Seeds are mutated, but the probe
  above is mandatory before an official render.
- **Functional-holdout API pinning:** the injected `feature_test.go` drives the
  service through its `wire.Handler` / `core.Service` construction seam, so it
  pins that seam's signature. Prompts establish the seam; a correct implementation
  matches it. Standard for a hidden acceptance test, but noted so reviewers judge
  prompt specificity deliberately.
- **Scale:** graphs are ~10–40 nodes; findings must scope claims to corpus size
  (plan §9). Larger services (`loansvc`-class) join once the grader image bakes a
  `GOMODCACHE`.
