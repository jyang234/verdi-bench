# Control-run reuse (scoped-B) — implementation plan

Status: **proposal, awaiting sign-off.** Touches two versioned contracts (the
experiment/run config surface and the hash-chained ledger); per `CLAUDE.md`
those need explicit human approval and a compatibility story before code lands.

## Goal

Let a user iterating on a *contender* stack skip re-running an unchanged
*control* arm over the same task set. Reused control data is a
convenience/cost-saving signal for iteration — it is **exploratory-only and can
never back an official decision**. Final validation is a normal run with both
arms freshly interleaved.

## The one principle everything follows from

Exclusion-from-official is **structural, not a flag.** Reused data lands in the
ledger under *distinct event types* — `reused_trial`, `reused_grade`,
`reused_judge_verdict` — and the official analyze path
(`compute_findings`, `_holdout_values`, `_telemetry_values`,
`_judge_preference_rates`) queries only the native `TRIAL` / `GRADE` /
`JUDGE_VERDICT` types. The official path therefore *cannot see* reused data,
the same way `TrialRequest` has no holdout field: the guarantee is
unrepresentable to break, not merely forbidden. `compute_findings` stays
byte-unchanged; a reuse run's official render naturally yields "no paired task
data" for the contender pair (its control side was never freshly run), which is
the correct, honest refusal.

## Scope (v1)

**In:** reuse for `holdout_pass_rate`, `cost_per_task`, `wall_time`, and
`judge_preference` — all exploratory-only. Full-fingerprint preflight gate.

**Out (follow-ups):**
- Judge↔human calibration (kappa/IPW) over reused verdicts — the human-review
  frame doesn't line up with a fresh-vs-stale pair. Reused verdicts feed
  neither official nor exploratory calibration.
- Contamination/confound analysis over the reused arm — those are per-ledger,
  time-scoped signals; the reused arm's probes aren't imported. Disclosed as
  not-run in the exploratory block.
- Reusing more than one arm, or reusing across differing `repetitions`
  (fingerprint requires an exact match in v1).

## Guarantees preserved

- Official fence, paired bootstrap, decision rule, MDE, contamination
  asymmetry fence: **untouched** (they never read `reused_*`).
- Insulation: the snapshotted control diff was already redacted at source-trial
  time; the judge packet validator re-scans. Blinding holds — arm identity is
  not in the diff, so a reused control is just another A/B side.
- Determinism / import contracts: fingerprint + preflight + the analyze reuse
  path import **no LLM client**. Only the reused *judge* assembly lives in the
  judge subsystem (the LLM tier), matching existing boundaries.

## Reuse model: an exported bundle, not a live source workspace

Remote/ephemeral containers mean a source run's trial *workspaces* are gone by
the next session — but the judge reads its diff off the live workspace
(`_read_workspace_diff(artifacts_path)`). So reuse is mediated by a
**self-contained bundle** exported while the source artifacts are still alive:

```
bench control-cache export --from <source_experiment_dir> --arm <control_arm> --out <bundle>
```

Pure read of the source ledger + workspaces. The bundle contains, per
`(task_id, repetition)` of the control arm:
- the source `trial_record` (telemetry, provenance),
- the source `grade` event (assertions / holdout results / binary score),
- the **bounded judged-diff snapshot** string (exactly `_read_workspace_diff`'s
  output, already capped at `TOTAL_DIFF_CAP` = 512 KB),

plus a manifest: the **control fingerprint**, source experiment id, source
ledger head hash, and a content sha over the bundle itself.

At run time the bundle is imported (see Preflight). Snapshotting the diff at
export decouples reuse from workspace lifetime — the load-bearing piece that
makes cross-session reuse work at all.

## Components (dependency order)

### 1. Control fingerprint — `harness/run/control_reuse.py` (new)

A pure canonical hash (no LLM imports) over everything that must be identical
for a reused control to be comparable. Each component sourced from its owning
subsystem:

| Component | Source | New work? |
|---|---|---|
| Task content (prompt, canaries, plugins, holdouts_dir path) | `corpus.commit.task_content_sha` (per task) | reuse existing |
| **Holdout script bytes** | corpus cache content hash (corpus subsystem) | **confirm exposure; add hash in `corpus/` if absent** |
| **Grader plugin identity/version** | grade subsystem | **confirm exposure; add version hash in `grade/` if absent** |
| Arm definition | canonical serialize of `Arm` (name, platform, model, payload, training_cutoff, aux_models, model_hosts) | new (small) |
| Execution env | `image` digest, `engine`, `harbor_version`, `quotas`, proxy `allowlist` + `infra_hosts` | new (small) |
| `repetitions` | spec | new (small) |

Computed **two ways** and compared byte-for-byte: the bundle's recorded
fingerprint vs. the current experiment's *intended* fingerprint (current spec's
control-arm definition + current corpus/holdouts/grader + current
`run.config.yaml` env). The env side compares current-intended vs
source-recorded — the current environment must be able to run the control arm
identically or preflight refuses.

> Before coding, confirm where holdout-script and grader-plugin hashes already
> live. If they aren't exposed, the hash functions are added in `corpus/` and
> `grade/` respectively (owning subsystems), not in `run/` — the fingerprint
> only *composes* them.

### 2. Ledger events *(seam change — needs approval)* — `harness/ledger/events.py`

Additive, versioned, legacy ledgers render byte-identically:

- `control_reused` — one per imported bundle. Fields: source experiment id,
  source ledger head hash, bundle sha, the fingerprint, control arm name,
  list of imported `(task_id, repetition)` cells. `assert_chain` covers it.
- `reused_trial` — carries a source `trial_record` verbatim plus
  `reused_from` provenance (source experiment id, bundle sha). One per control
  cell.
- `reused_grade` — the source grade payload plus `reused_from`. One per control
  cell.
- `reused_judge_verdict` — a fresh verdict over a (contender, reused-control)
  pair; same shape as `JUDGE_VERDICT` plus `reused_from`.

Compatibility story: purely additive event kinds; no existing event schema
changes; `find_events(kind)` for the native kinds returns exactly what it does
today. The one-event-per-verb property test gains four new registered verbs.

### 3. Diff snapshot — export side

At `control-cache export`, for each control trial compute
`_read_workspace_diff(artifacts_path)` (already bounded) and store the string in
the bundle keyed by `(task_id, repetition)`. Holdout results already ride the
grade payload, so only the diff string needs snapshotting.

### 4. Preflight validation — run-stage gate (run CLI, before `schedule()`)

Alongside `assert_lock` / `assert_task_commitment`:

1. Load the bundle; verify its self sha.
2. Recompute the current experiment's fingerprint for the named control arm.
3. Compare byte-for-byte to the bundle's recorded fingerprint. **Any mismatch →
   refuse loudly** with a typed error naming *which* component drifted
   (`ControlReuseFingerprintError: holdout scripts changed …`). This is the
   "provably unchanged, else preflight fails" requirement.
4. On match: append `control_reused`, then materialize the bundle's per-cell
   data as `reused_trial` + `reused_grade` events and stash the diff snapshots
   where the reused judge assembler reads them.
5. **Scheduling:** drop the control arm's cells from the derived order before
   `schedule()` — they are supplied by the bundle, not executed. `executed_order`
   then reflects only the freshly-run contender cells. (A run-stage filter on
   the derived order; the locked interleave property is untouched.)

Config surface: reuse is *operational*, so it lives in `run.config.yaml`
(`reuse_control: {bundle: <path>, arm: <name>}`) and/or a `bench run
--reuse-control <bundle> --control-arm <name>` flag — **never** in the
sha-locked `experiment.yaml`.

### 5. Reused judge assembly — `harness/judge/` (parallels `assemble.py`)

`comparisons_from_reuse(ledger, spec, bundle)`: pair each fresh contender
`TRIAL` with the matching `reused_trial` control per `(task, rep)`; contender
`ResponseArtifacts.diff` read live, control diff from the bundle snapshot,
holdouts from `reused_grade`. Feed the existing identity-blind, order-debiased
`judge_pair`; record `reused_judge_verdict`. The LLM call, blinding, and D003
order-swap are unchanged. `bench judge` learns to run this path when a
`control_reused` event is present.

### 6. Analyze — exploratory reuse section — `harness/analyze/report.py`

A new **unpaired** comparison (the official path is paired-only). Reads
`reused_trial` / `reused_grade` / `reused_judge_verdict`:

- Computed metrics (`holdout_pass_rate`, `cost`, `wall_time`): two-sample
  (fresh contender group vs reused control group) exploratory estimate — no
  paired-bootstrap pairing assumption to violate.
- `judge_preference`: exploratory per-task win-rate from `reused_judge_verdict`.

Rendered as an EXPLORATORY-watermarked section with `official_decision=False`,
plus a disclosure block: reused arm, source experiment id, fingerprint match,
and an explicit "contamination/confound/calibration checks not run over the
reused arm." The official `compute_findings` output is unchanged.

### 7. CLI/config

- `bench control-cache export --from … --arm … --out <bundle>`
- `bench run … --reuse-control <bundle> --control-arm <name>` (or
  `run.config.yaml`)
- `bench judge` auto-detects `control_reused` and runs the reuse assembly.
- `bench analyze` renders the exploratory reuse section; official render behaves
  exactly as today.

## Test plan

Per `CLAUDE.md` (reproduce-before-fixing, planted-violation + clean fixtures,
observable behavior):

- **Preflight refusals (planted mismatch, one per component):** holdout-script
  byte drift, grader-plugin version drift, arm-definition drift, env drift
  (image/quota/harbor), `repetitions` drift → each fails preflight with the
  right typed reason. A clean fixture matches and imports.
- **Structural exclusion:** a reuse run's official render produces no official
  decision for the contender pair ("no paired task data"); `compute_findings`
  byte-identical to a no-reuse ledger for the native events.
- **Exploratory output present:** computed unpaired estimate + judge win-rate
  render, watermarked, `official_decision=False`, with the disclosure block.
- **Judge over reuse:** reused verdicts recorded as `reused_judge_verdict`,
  excluded from `_judge_preference_rates`/`_judge_calibration`, included in the
  exploratory win-rate; blinding/identity-free packet validation passes on a
  snapshot diff.
- **Bundle tamper-evidence:** a mutated bundle fails its self-sha check;
  `assert_chain` still holds on the importing ledger.
- **One-event property:** the four new verbs each append exactly one event.
- **Import-linter:** `control_reuse` and the analyze reuse path import no LLM
  client; contracts stay green.
- `make verify` green before done.

## Open decisions still needed

1. **Approve the seam additions** (fingerprint config surface + the four
   additive ledger event types).
2. Bundle format/location convention (proposed: a content-hashed directory or
   single file under the experiment dir; not committed to the ledger, only its
   sha is).

## Implementation order

fingerprint → ledger events → diff snapshot + `control-cache export` →
preflight (import + schedule filter) → reused judge assembly → analyze
exploratory section → CLI wiring → tests. `make verify` after each slice.
