# verdi-go (flowmap + groundwork) × verdi-bench — integration and experiment plan

> `PROPOSAL` · 2026-07-07 · Tracks A (harness integration + flagship experiment) and B
> (verdi-go development loop). Track C (workspace observability / J-lens) is deliberately a
> separate, gated plan: [`workspace-observability-plan.md`](workspace-observability-plan.md).
> A pointer doc lives in verdi-go at `docs/design/verdi-bench-integration.md`.

## 0. Summary

verdi-go ("flowmap" the producer + "groundwork" the judge) emits deterministic,
three-valued verdicts about Go services — PROVEN / VIOLATED / CANT-PROVE — with
fail-closed epistemics and byte-stable output. verdi-bench is a pre-registered A/B
instrument for agent stacks. This plan integrates them at exactly two seams, both of
which verdi-bench's design record already anticipates:

1. **Arm-side (subject under test):** a payload-gated trial image gives one arm the
   `groundwork mcp` toolchain + skill, enabling the pre-registered experiment the
   verdi-go repo designed but deliberately declined to self-run (drill **E4**,
   `verdi-go/docs/groundwork/drills.md`): *does the deterministic ground → edit →
   verify loop improve agent outcomes?* — including the headline "tool-armed Haiku
   vs Opus baseline" comparison that is already verdi-bench's worked example
   (`docs/usage-guide.md` §9).
2. **Harness-side (grader backend):** the existing fixture-only
   `harness/grade/plugins/groundwork.py` gains its real shell-out, and groundwork
   verdicts become usable as deterministic grading signal for Go-workspace tasks —
   per-rule assertion vectors (plugin) and binary pass/fail (`command` holdout).

Explicit non-goals: verdi-go does **not** enter `verdi-base`, the harness core, or any
non-Go-task path. No verdi-go code changes are required; it is consumed as pinned
binaries.

## 1. Background and motivation

### 1.1 What verdi-go's own evidence establishes

verdi-go's A/B postmortem (`verdi-go/docs/groundwork/ab-testing-postmortem.md`,
2026-06) is the load-bearing prior for experiment design here. Its verdicts, tested
seven ways across two model tiers:

- **No per-instance capability edge** over a capable grep+read agent — on discovery
  (Tiers 1/7/8), policy application (9c), and deep double-interface reachability (9d),
  for both Opus 4.8 and Haiku 4.5 (9e). The gate and a grep agent share the same
  static-resolvability frontier; a corpus of discovery questions will (correctly)
  find nothing.
- **A calibration edge** (Tier 7): the tool abstains where agents confidently
  over-claim "safe" — the false-SATISFIED failure mode.
- **The one surviving in-loop result** (Tier 9b): on a realistic feature task whose
  natural implementation violates an architectural invariant, agents without the gate
  shipped the violation 2/2 (while reviewing everything else diligently); agents with
  the gate verdict shipped it 0/2. Control 9c located the mechanism: agents *can*
  apply the rule when pointed at `policy.json`; they just don't consult it unprompted.
  The value is **systematic in-loop surfacing**, not capability.
- **One untested residual** where a capability edge might exist: a multi-implementation
  interface with only one live impl (VTA computes the live set; a hand-tracer must
  enumerate and infer wiring).

The postmortem's own methodology failures map one-to-one onto verdi-bench features —
which is the core argument that this experiment belongs here:

| postmortem failure (owned in §8 there)           | verdi-bench mechanism                                   |
|--------------------------------------------------|---------------------------------------------------------|
| contaminated re-run; agents mutated the tree      | hermetic per-trial containers, insulation (EVAL-4)      |
| "same strong model" unverified until asked        | `arm.model` sha-locked at plan time                     |
| over-determined traps → false null                | pre-registered task commitment + k=5 flake baseline     |
| n=1 everywhere, no power analysis                 | MDE/power gate at lock (`plan/power.py`)                |
| assert-without-verify, post-hoc readings          | hash-chained ledger, fenced official renders, A/A selfcheck |

### 1.2 The seams already exist

- `harness/grade/plugins/groundwork.py` ships today with the verdict mapping
  (`pass/fail/NO-STRUCTURAL-SIGNAL → passed/failed/abstain`, unknown → abstain,
  rule ids preserved) and is **fixture-only**: a production task declaring it fails
  closed with `GroundworkUnavailableError` "until the real groundwork shell-out
  ships" [F-M-O1]. This plan ships it.
- `docs/usage-guide.md` §9 documents the asymmetric-arm pattern this experiment needs:
  shared per-task image, `payload`-gated tool activation, per-arm credential
  isolation, declared egress. ("A genuinely different base container image per arm is
  not a schema field today" — the asymmetry is realized inside one image.)

### 1.3 What verdi-go offers as a grader (contract facts)

Verified against the verdi-go tree (`cmd/groundwork/main.go`, `cmd/flowmap/main.go`):

- **Exit codes**: `0` clean · `1` a computed verdict failed the gate · `2` operational
  error. A harness can distinguish "change failed the gate" from "gate failed to run".
- **Machine-readable output**: `groundwork review|verify --json` emit canonical,
  byte-stable JSON; `fitness` offers `--sarif` (annotation use cases) only, not `--json`
  (an upstream usage-string drift that advertised `fitness --json` was fixed in verdi-go).
- **Determinism**: `graph.json` is byte-identical for a fixed source tree **per flowmap
  build** (CI regenerates fixtures and `git diff --exit-code`s them). Cross-version
  identity is explicitly not promised — pin one binary everywhere.
- **Runtime split**: `flowmap graph` type-checks the full module (needs a Go toolchain
  and resolvable deps); `groundwork` consumes only JSON artifacts (no toolchain, no
  network, no source).
- **Trust boundary** (verdi-go's own doctrine, `docs/groundwork/distilled-learnings.md`):
  graph integrity is the single point of failure. **The grader must regenerate the graph
  from the workspace; an agent-supplied graph forges any verdict.**
- **Stamp discipline**: `flowmap graph --stamp <sha>` binds a graph to a source
  identity; groundwork `--expect <sha>` refuses mismatches
  (`GROUNDWORK_REQUIRE_STAMP=1` makes it mandatory).
- **Install/pin pattern**: `go install github.com/jyang234/golang-code-graph/cmd/{flowmap,groundwork}@<ref>`
  (the `setup-groundwork` GitHub action refuses an empty ref); binaries self-report
  their version (`internal/buildinfo`).

## 2. Placement (the architecture decision)

| component | lives in | role | pinning |
|---|---|---|---|
| `flowmap` + `groundwork` binaries | **grader image** (`VERDI_GRADER_IMAGE`) | regenerate graph from graded workspace; evaluate policy verdicts | image digest-pinned; groundwork version recorded in grade provenance |
| same binaries + `groundwork-workflow` skill + MCP config | **per-task trial image** (`claude-code-groundwork`) | treatment-arm tooling, payload-gated | image digest-pinned per task; version echoed in trial artifacts |
| `policy.json`, base `graph.json`, `.flowmap.yaml` | task assets: committed in the **workspace** (both arms can read them — 9c parity) and under **`holdouts_dir`** (grading copies the agent cannot touch) | the invariant spec + trusted base facts | sha-covered by task commitment |
| nothing | `verdi-base`, harness core, non-Go paths | — | — |

Rationale for the non-goals: `verdi-base` is deliberately domain-general and
extend-only; groundwork is Go-specific. Baking it into the universal base couples the
instrument to one task domain and (worse) ambiently exposes the tool to control arms.
The plugin registry + per-task images are the designed extension points, and both are
digest-pinned, so the ledger's provenance story extends to verdi-go versions for free.

Trust boundaries preserved by construction:

- Holdouts (including the grading copies of policy/base-graph) mount read-only at
  `/holdouts` **only in the grade container**, never the trial container.
- Grading runs in a fresh-copy `--network none` container
  (`harness/grade/plugins/launch.py`); the in-trial agent can at most corrupt its own
  workspace copy of `graph.json`, which only misleads *itself* — grading regenerates.
- The judged diff excludes `artifacts/` (`harness/judge/assemble.py`), so treatment
  tool residue routed there never reaches the blinded judge.

## 3. Track A1 — the real grader shell-out

**Change**: `harness/grade/plugins/groundwork.py::_rule_verdicts` gains the real path;
the fake path (`task.fake_plugin_output`) is retained for the no-docker test tier.

Real path, executed inside the grade container on the fresh workspace copy:

1. Locate task assets under `/holdouts/<task>/groundwork/`: `policy.json`,
   `base.graph.json` (CI-built, stamped), optional `flows/` corpus.
2. `flowmap graph <workspace-module> --stamp <workspace_sha>` → `branch.graph.json`.
   The branch graph is regenerated with the call-graph algorithm the **holdouts**
   `policy.json`'s top-level `substrate` field declares (`--algo rta|vta|cha`, default
   `rta` when absent; derived from the trusted holdouts copy only, never the workspace),
   so the multi-impl task classes whose policies are pinned to VTA grade on that
   substrate — under default RTA's dispatch over-approximation a clean solution can
   falsely verify as blocked. Failure to build/type-check → the plugin raises → terminal
   `cant_grade(plugin_error)` (a workspace that does not compile is separately caught by
   functional holdouts; the distinction is preserved in assertion details).
3. `groundwork review policy.json base.graph.json branch.graph.json --json` — the
   two-graph review whose top-line verdict is BLOCK / STRUCTURALLY-CLEAR /
   NO-STRUCTURAL-SIGNAL (see D1). Exit `2` → raise (operational); exit `0/1` → parse
   canonical JSON.
4. Map per-rule verdicts through the existing `_VERDICT_MAP`; rule ids preserved;
   unknown verdicts → `abstain` with detail. `NO-STRUCTURAL-SIGNAL` is **never** a pass.

Grader image additions: pinned Go toolchain (match verdi-go CI's pin, currently
1.25.x — the analyzer's toolchain must be ≥ the highest `go` directive any task
workspace declares), pinned `flowmap`/`groundwork` binaries, and (only if the corpus
outgrows stdlib-only modules) a baked `GOMODCACHE`. The grade container is
`--network none`; nothing may be fetched at grade time.

**Binary-score path** (independent of the plugin): a task whose acceptance criterion
*is* the invariant declares a `command` holdout —

```json
{"kind": "command", "argv": ["/usr/local/bin/verdi-groundwork-check", "<task-id>"]}
```

where the wrapper script runs steps 1–2 above, then `groundwork verify policy.json
base.graph.json branch.graph.json --json`, and exits with groundwork's exit code
(0 pass, 1 fail), mapping exit 2 to a loud non-zero distinct failure. Plugin
assertions feed fractional scoring only; the `command` holdout feeds
`holdout_pass_rate` — the primary metric.

**Tests** (per repo directives — no fixes by inspection, reproduce first):
- unit tier: fake-path behavior unchanged; real-path parse/mapping against committed
  groundwork `--json` fixtures (including an exit-2 case and an unknown-verdict case).
- `docker`-marked tier: real-container grade of (a) a planted-violation workspace that
  must produce a `failed` rule assertion and a failing command holdout, (b) the
  reference solution that must pass, (c) a blind-spot task that must `abstain` and not
  affect the binary score, (d) binary absent from image → terminal
  `cant_grade(plugin_error)`, never a silent empty vector.

## 4. Track A2 — the `claude-code-groundwork` trial image

Fork of `images/official/anthropic-claude-code`, extended with:

- pinned `flowmap` + `groundwork` binaries at a non-PATH staging location;
- the `groundwork-workflow` skill (from `verdi-go/.claude/skills/groundwork-workflow/`);
- an entrypoint that reads `/verdi/request.json` and, **iff**
  `payload.tools` includes `groundwork`: links the binaries onto PATH, writes the
  agent's MCP config to launch
  `groundwork mcp graph.json --policy policy.json --log /workspace/artifacts/groundwork-mcp.jsonl`
  (stdio; the CLI spawns it), and installs the skill. Otherwise it does none of that —
  same image bytes for every arm, asymmetry realized exactly as usage-guide §9
  prescribes.

Insulation and hygiene notes:

- groundwork is fully local: no egress, no credentials → nothing to add to
  `model_hosts`/`infra_hosts`, no per-arm key story needed for it.
- Tool output and the MCP call log go to `/workspace/artifacts/` (persisted for
  analysis; excluded from the judge diff). Nothing groundwork-branded may be written
  loose into `/workspace`.
- Add `groundwork` / `flowmap` literals to the per-experiment arm canaries (D6) so a
  treatment diff that mentions the tool in comments cannot leak an arm asymmetry to
  the judge unnoticed.
- The workspace's own `graph.json` is agent-writable; that is acceptable
  (self-deception only — grading regenerates). The MCP server flags on-disk graph
  staleness on every response, which is itself realistic adopter behavior.

## 5. Track A3 — corpus

Four task classes, all closed-loop coding tasks (realistic feature request + tests to
satisfy), differing in the trap dimension:

| class | trap | seeds |
|---|---|---|
| reach-trap | natural implementation adds a write reachable from a read route / crosses `must_not_reach` / bypasses `must_pass_through` | `layeredsvc` (has exactly this policy), Tier-9b shape |
| obligation-trap | easy edit leaks a CFG exit (tx not closed, audit-after-publish, semaphore leak) | `obligsvc` |
| **null (anti-cherry-pick, mandatory)** | none — policy binds nothing, or the correct change is policy-clean; measures false friction (over-abstention, refusals, token burn) | any |
| multi-impl residual | interface with several impls, one live; feature requires knowing which | new fixture (the postmortem's untested residual) |

Corpus mechanics:

- **Seed from verdi-go's stdlib-only fixtures** (`layeredsvc` 168 LOC, `blindsvc` 157,
  `obligsvc` 377 — hermetic, committed policies/goldens), **mutated** (renamed
  packages/domains, altered strings) both to blunt training-set memorization and
  because the originals are public. `loansvc` (618 LOC, DB+bus+HTTP) joins once the
  grader image bakes its small module set. Run EVAL-10 contamination probes before any
  official render regardless.
- Per task: workspace (`files:` inline or per-task image) = Go module + `.flowmap.yaml`
  + `policy.json` + committed `graph.json`; prompt; functional holdouts
  (`pytest`/`command`) + the groundwork `command` holdout; `plugin_ids: [groundwork]`;
  a reference solution; the k=5 flake baseline (which will also catch any groundwork
  nondeterminism before a task is admitted).
- Policy authorship: `groundwork init` against the seed graph, then human curation —
  the policy is the trap's spec, so it gets the same review a CODEOWNER would give it.
- Size: v0 = 12–30 tasks across the four classes; final N per cell set by the MDE gate
  from pilot variance, not by hand.

## 6. Track A4 — flagship experiment (pre-registration sketch)

**Design: 2×2 factorial** — model tier × groundwork access — so the tool effect is
attributable. The user-facing headline (haiku-grounded vs opus-bare) is a planned
contrast inside it, not the whole design; alone it confounds model swap with tool
addition.

```yaml
# illustrative — field names/ids resolved against harness/schema at lock time
arms:
  - {name: opus-bare,      platform: claude_code, model: anthropic/claude-opus-4-8-20260101,   payload: {}}
  - {name: opus-grounded,  platform: claude_code, model: anthropic/claude-opus-4-8-20260101,   payload: {tools: [groundwork], workflow: ground_verify}}
  - {name: haiku-bare,     platform: claude_code, model: anthropic/claude-haiku-4-5-20251001,  payload: {}}
  - {name: haiku-grounded, platform: claude_code, model: anthropic/claude-haiku-4-5-20251001,  payload: {tools: [groundwork], workflow: ground_verify}}
multi_arm_correction: holm
primary_metric: holdout_pass_rate
decision_rule: "delta_holdout_pass_rate >= <MDE-backed threshold>"
# judge: third-vendor model (advisory; identity-blind regardless)
# repetitions, seed, cost_ceiling: set at lock from the calibration pilot
```

Pre-registered interpretation notes (write these into the spec so nobody re-litigates
them post-hoc):

1. The groundwork `command` holdout applies the same gate the treatment arms have
   in-loop. **This is the design, not deck-stacking**: both arms have identical
   epistemic access (`policy.json` is readable in every workspace; postmortem 9c shows
   agents apply it when they look); the treatment differs only in having it surfaced.
   The claim under test is "gate-in-loop prevents what gate-at-merge rejects."
2. Expected effect is concentrated in the grounded-vs-bare contrasts on trap classes;
   null tasks are expected null and are kept in the tally (anti-cherry-pick).
3. A null overall is a publishable, useful result (it bounds verdi-go's claim to
   "CI backstop, not in-loop uplift") — the postmortem's publish-the-null posture
   carries over.

Pipeline: `plan → run (harbor) → grade (docker) → judge → forensics scan →
selfcheck → analyze`, plus `contamination probe` before any `--official` render.
Budget note: the two Opus arms dominate spend; shake the whole pipeline down on the
fake engine + `LocalGradeRunner` first, then a small harbor **calibration pilot**
(also supplying `CalibrationVariance` for the MDE gate), then lock.

**Exploratory (watermarked) secondaries** — the tuning telemetry:

- cost/tokens/wall-time per arm; per-class success breakdowns.
- **Tool-usage funnel**, computed from `artifacts/groundwork-mcp.jsonl` × trajectory v3:
  - `grounded_before_edit`: first `ground` call precedes the first `file_edit` step;
  - `checked_after_last_edit`: a `fitness`/`ground` call follows the final edit;
  - `verdict_heeded`: no trial ships a violation that the MCP log shows was surfaced.

If budget forces staging: run grounded-vs-bare per model tier as two 2-arm
experiments and reuse controls (`bench control-cache export` / `--reuse-control`,
exploratory tier), accepting weaker cross-tier claims.

## 7. Track B — verdi-bench as verdi-go's development loop

Once the corpus exists, verdi-go iteration becomes a **tool-version A/B**:

- Same model both arms; the shared image bakes `groundwork@vA` and `groundwork@vB` at
  distinct staging paths; `payload` selects which one the entrypoint exposes.
  Versions self-report via `buildinfo` and land in provenance.
- `--reuse-control` makes candidate-vs-main cheap (re-run treatment only, exploratory).
- `bench card emit` / `card compare` tracks a fixed task set across verdi-go releases
  (compare refuses across different task sets/metrics — version the corpus
  deliberately).
- The funnel metrics are the tuning signal verdi-go cannot generate internally: when a
  change to the skill wording, an MCP tool description, or a new lens moves
  `grounded_before_edit` or `verdict_heeded`, you know *which* part of
  ground → edit → verify to iterate on. This operationalizes the quantitative half of
  drill E4 exactly as verdi-go instrumented it (`--log` + `groundwork transcript`)
  while keeping the AI-in-the-loop measurement outside verdi-go's own test suite —
  the reason E4 was parked.

Cadence: exploratory run per meaningful verdi-go change; a locked official run only
for milestone claims (e.g. the scorecard's 📋-designed E4 row graduating to 📐-measured).

## 8. Decisions (recommended option first)

- **D1 — grading role.** Use **both**: `command` holdout (binary gate on the
  invariant, feeds the primary metric) **and** the plugin (per-rule assertion vector,
  fractional/forensic color). Alternative: plugin-only — rejected, abstains must not
  silently soften the pass bar, and binary score is holdout-only by design.
- **D2 — graph provenance at grade time.** **Regenerate from the workspace copy**
  inside the grade container. Alternative: trust a committed branch graph — rejected
  outright (verdi-go's own single-point-of-failure doctrine).
- **D3 — corpus hermeticity.** **stdlib-only modules for v0**; bake `GOMODCACHE` into
  the grader + trial images when `loansvc`-class tasks join.
- **D4 — arm count.** **4-arm factorial with `holm`** if budget allows; else staged
  2-arm with control reuse. The factorial is the scientifically complete form.
- **D5 — judge.** Third-vendor model (blinding holds regardless; removes
  self-preference questions from the finding).
- **D6 — arm canaries.** Add `groundwork`/`flowmap` literals to the experiment's
  canary set. Cheap, closes a real (if minor) judge-leak channel.
- **D7 — MCP log location.** `/workspace/artifacts/groundwork-mcp.jsonl` — persisted,
  judge-excluded, agent-writable (it is treatment-side telemetry, not evidence against
  gaming; gaming detection stays with forensics).

## 9. Risks and honest caveats

- **The null is live.** The postmortem killed every capability claim under controls;
  if the corpus drifts discovery-shaped or agents consult `policy.json` unprompted
  more than in June's n=2, the effect may be small. Power for the trap-class contrast;
  publish either way.
- **Determinism at the seams.** Byte-stable output holds per flowmap build only — one
  pinned binary across trial images, grader image, and committed base graphs, or
  groundwork itself will (correctly) flag mismatch. Go toolchain skew (analyzer < task
  `go` directive) fails loudly at load; pin image toolchains to verdi-go CI's pin.
- **Scale honesty.** Fixture graphs are ~40 nodes; verdi-go's only larger data point
  is one 891-node service (~2s, from its correctness-expansion record). Findings must
  scope claims to the corpus size; grow larger services over time.
- **Contamination.** Fixture ancestry is public; mutate seeds and run EVAL-10 probes.
- **Comparability.** If any telemetry field is arm-asymmetric it is excluded and
  flagged (`telemetry_null_asymmetry`) per usage-guide §9 — do not fudge.
- **Grading latency.** `flowmap graph` on stdlib-only fixtures is sub-second-to-seconds;
  budget grade-container time generously anyway (it also runs functional holdouts).

## 10. Phases and acceptance

Each phase ends with `make verify` green; docker-marked tests accompany every
container-path change. No phase starts before the previous one's exit criteria hold.

- **P0 — grader shell-out** (Track A1). Exit: the four docker-marked cases in §3 pass;
  fake path unchanged; grade provenance records the groundwork version.
- **P1 — trial image** (Track A2). Exit: with `payload.tools=[groundwork]` the agent
  session lists the MCP tools and the skill; with empty payload it has neither; image
  bytes identical across arms; a smoke trial shows a clean judge diff and the MCP log
  in `artifacts/`.
- **P2 — corpus v0** (Track A3). Exit: 12–30 tasks admitted; every reference solution
  passes its k=5 flake baseline; `bench corpus validate-tasks` clean; holdout-leak
  checks green; contamination probe run.
- **P3 — pilot.** Exit: full pipeline end-to-end on the fake engine; small harbor
  calibration run ledgered; `CalibrationVariance` feeding the MDE gate; funnel metrics
  computing from real artifacts.
- **P4 — flagship run** (Track A4). Exit: locked spec (with §6's interpretation
  notes), run, grade, judge, forensics, selfcheck pass, analyze; official render +
  result card. Track B begins its cadence after P4.

## 11. Non-goals

- No verdi-go binaries or Go toolchain in `verdi-base` or the harness runtime.
- No groundwork-derived signal in the judge packet or in any non-Go grading path.
- No modification of verdi-go required; upstream requests (if any arise) go through
  its normal review, not this plan.
- No LLM anywhere in the deterministic grading tier (unchanged; groundwork is
  LLM-free by construction — that is the point).

## 12. Reference index

verdi-bench: `harness/grade/plugins/__init__.py` (registry, isolation),
`harness/grade/plugins/groundwork.py` (stub to complete), `harness/grade/holdouts.py`
(`command` kind), `harness/grade/runners.py` (grade container, `/holdouts` mount),
`harness/grade/baseline.py` (k=5), `harness/schema/experiment.py` (Arm, `payload`,
`multi_arm_correction`), `harness/schema/tasks.py` (`files`, `holdouts_dir`,
`plugin_ids`), `harness/run/engines/harbor.py` (mounts, networks, digest pinning),
`harness/judge/assemble.py` (diff exclusions), `harness/blind/core.py` (canaries),
`docs/usage-guide.md` §9 (asymmetric-arm recipe).

verdi-go: `cmd/groundwork/main.go` (exit codes, `--json`, `--expect`),
`cmd/groundwork/mcp.go` (11 read-only tools, `--log`), `cmd/flowmap/main.go`
(`graph`, `--stamp`), `.github/actions/setup-groundwork/action.yml` (pinned install),
`internal/buildinfo/` (version), `testdata/groundwork/{layeredsvc,blindsvc,obligsvc}`
and `testdata/fixtures/loansvc` (corpus seeds), `docs/groundwork/drills.md` (E4),
`docs/groundwork/ab-testing-postmortem.md` (design priors),
`docs/groundwork/evaluation-playbook.md` (method), `docs/groundwork/scorecard.md`
(claims by evidence class).
