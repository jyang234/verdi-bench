# verdi-go × verdi-bench integration — execution record and operational handoff

> `EXECUTION RECORD` · 2026-07-07 · covers the implementable phases of
> [`verdi-go-integration-plan.md`](verdi-go-integration-plan.md) (Tracks A/B, P0–P3-local)
> and the code-side half of [`workspace-observability-plan.md`](workspace-observability-plan.md)
> (Track C P0 kit + schema draft). Orchestrated as dispatch → implement → review →
> fix → re-review loops; every phase was independently re-verified by the reviewer
> (separately built binaries, re-run suites, re-run validation matrices) before merge.

## 1. What was built, per phase

| phase | delivered | verification evidence |
|---|---|---|
| **A-P0** grader shell-out | Real `plugins/groundwork.py` path over `review --json`; `groundwork_shell.py` subprocess layer; read-only `/holdouts` mount added to the plugin container; `verdi-groundwork-check` command-holdout wrapper (exit 0/1/**3**); grader image (Go 1.25.11, ref-pinned or prebuilt binaries) | 4 planted-fixture docker cases (CI-tier) + hermetic mapper tests + real-binary `LocalGradeRunner` tier; reviewer re-ran the full suite and the real-binary tier with independently built binaries |
| **A-P0 fixes** | Toolchain version recorded in the `groundwork:verdict` assertion detail (no schema change); pinned grader-image pip deps; **substrate-aware regeneration** (`--algo` from the holdouts policy's `substrate` in both grade surfaces); upstream verdi-go usage-string drift fixes (`fitness [--json]`, `[--sarif <out>]`) | Hermetic argv tests incl. workspace-decoy-substrate ignored; manual wrapper transcript; real-binary vta plumbing run; both repos' gates green |
| **A-P1** trial image | `images/reference/claude-code-groundwork/`: payload-gated entrypoint (pure plan/apply), control byte-identical to the official agent, treatment delta = exactly `--mcp-config`; **nothing written under `/workspace` except `artifacts/`**; vendored skill + PROVENANCE; CLI pinned to a skills-capable version (2.1.202; the official image's 1.0.44 predates Skills — a silent treatment-degrades-to-control hazard, caught and floor-tested) | 16 hermetic gating tests (argv delta, zero-write control, workspace rule via rglob); 2 docker smoke tests (CI-tier); reviewer re-ran tests + merged-tree gate |
| **Corpus v0** | `corpora/groundwork-v0/`: 16 tasks — 5 reach (one per rule family), 4 obligation, 4 null (anti-cherry-pick), 3 multi-impl residual (VTA-pinned; clean solutions false-block under RTA, confirmed — the postmortem's untested residual, now a task class); per-task workspace/solution/exemplar-violation trees, neutral prompts, curated policies, committed graphs; reproducible `build_tasks.py` with `--check` matrix | Authors validated every cell with real binaries; **reviewer independently re-ran the full 16-task matrix: ALL CELLS GREEN**; prompts spot-read for neutrality |
| **A-P2** admission | Builder `VERDI_*_BIN` overrides; **container-path bug fix** in the composite holdout (`/holdouts/<id>/functional` → `/holdouts/functional` — would have broken the gate in the real grade container; reproduced before fixing); bare-name wrapper resolution; toolchain re-pin + graph re-freeze; `bench corpus baseline --runner local-exec` (ADVISORY tier, fails loud on unknown runners); `make corpus-groundwork-v0`; leak-check tests | `validate-tasks` 16/16 OK; **k=5 baselines 16/16 all-pass (ADVISORY, local-exec)**; exemplar-violation QUARANTINES and null-alternative stays CLEAN through the real grade seam; leak checks green and pinned by tests |
| **A-P3 (local half)** | `scripts/shakedown/groundwork_pipeline.py` + `make groundwork-shakedown`: keyless plan → run(fake) → grade(local-exec, real gate per trial) → judge → selfcheck → analyze → verify-chain over all 16 tasks × 2 arms; `scripts/funnel_metrics.py` (+16 hermetic tests) computing `grounded_before_edit` / `checked_after_last_edit` / `verdict_heeded` from the real `mcp.go --log` shape | **12/12 trap tasks discriminate (solution PASS / exemplar FAIL), 4/4 nulls clean both arms, chain OK, dossier rendered — reproduced independently by the reviewer (7/7)** |
| **Track C kit** | `scripts/workspace-pilot/` (standalone; no harness imports): capture + A.21 scoring scripts with offline selftests, frozen 100-noun control set, corpus-seeded + realism-pair prompt set, sample fixtures, draft `workspace_trajectory` JSON Schema + [`workspace-trajectory-schema-draft.md`](workspace-trajectory-schema-draft.md) | Offline selftests + negative schema tests green (reviewer re-ran); **all GPU paths explicitly UNTESTED — banner + "Untested surfaces" list**; the pilot run is the user's GPU-box step |

verdi-go side: two usage-string drift fixes (`7a2ff4e`, `7e8df2b`), gates green. No
behavioral changes; the pointer doc (`verdi-go/docs/design/verdi-bench-integration.md`)
records the contracts verdi-bench leans on.

## 2. Trust posture (what is trusted vs advisory vs unexecuted)

- **Trusted here**: everything hermetic — the full pytest suite (final: 1490 passed /
  28 skipped, 10/10 import contracts), the corpus validation matrix, the funnel and
  gating unit tiers, both offline selftests. All re-run by the reviewer.
- **ADVISORY**: every grade produced in this environment ran on the host `local-exec`
  runner (`grader_name="local-exec"` ≠ `"docker"`): the 16×k=5 baselines, the
  exemplar/null seam probes, the shakedown's 32 trial grades. Deterministically green,
  but not the trusted tier.
- **Written, unexecuted (no Docker/GPU/keys here)**: grader + trial image builds and
  their docker-marked tests; the harbor path; the GPU pilot. Each is fail-closed in
  CI (`VERDI_REQUIRE_DOCKER=1`) or explicitly labeled (pilot kit banners).

## 3. Consolidated judgment-call ledger (accepted in review; veto cheaply)

1. Plugin consumes **`review --json`** (not the plan's `fitness --json`, which does not
   exist — upstream usage-string drift, fixed there; plan §1.3/§3 corrected). Command
   holdout uses `verify --json`.
2. Grade provenance rides the **verdict assertion's `detail`** (`; toolchain: flowmap
   <v>, groundwork <v>`), never the hash-chained event schema. Version-probe failure
   degrades to `unknown` without failing the grade.
3. `holdout.json` v1 supports **one holdout per task** → functional + gate composed
   into a single `sh -c` command holdout (feature test copied from the holdouts side
   at grade time; `_test.go` files are excluded from graphs, verified).
4. Trial image: **Go toolchain exposed to both arms** (task tooling; per-arm `go`
   would itself be an asymmetry); only `flowmap`/`groundwork` + skill + MCP config are
   payload-gated. Telemetry line logs the base CLI for parser parity; the treatment
   argv delta is recoverable from `artifacts/groundwork-mcp.jsonl`.
5. Corpus: 16 tasks (coverage-complete within the 12–30 window); committed workspace
   graphs **retain** the `tool` stamp as pinning provenance; gw-r4's thunk-based
   `Authorize` and base-omitted interface method are empirically-forced structural
   corrections (documented in its `expected.md`).
6. `bench corpus baseline --runner local-exec` added as the ADVISORY bridge (parity
   with `bench grade`); docker default untouched; unknown runner fails loud.
7. Shakedown realizes per-arm asymmetry by **materializing each arm's real tree**
   (solution vs exemplar) between run and grade — the fake engine is arm-blind by
   design; PASS/FAIL is the real gate's verdict, never a scripted stub.
8. `verdict_heeded` is operationalized on what the MCP log actually carries (call
   names + `isError`; **no rule ids, no timestamps in the log — deterministic by
   omission**): surfaced = a non-error `fitness`/`ground` call; shipped = the gate
   BLOCKed. Cross-source (log × trajectory) interleaving is **not fabricated**; absent
   log ⇒ `null` (not-applicable), never `false`.
9. Track C artifact adds a **`probe_readouts`** block beyond the plan's sketch so
   offline A.21 scoring is exact rather than top-k-approximated; realism pairs keep
   the literal probe words in exactly one variant (lexical-echo control).
10. Orchestrator-applied (this commit): the two documentation alignments in §5 below.

## 4. Genuine forks awaiting the human (do not resolve by default)

- **D4 — flagship arm count**: one locked 2×2 (`holm`) vs staged 2-arm experiments
  with control reuse. Price it against the harbor calibration pilot's cost-per-trial;
  the 2×2 is the scientifically complete form.
- **D5 — judge vendor**: third-vendor recommended (blinding holds regardless).
- **Upstream (verdi-go)**: (a) add rule ids / a response summary to the MCP `--log`
  emission (versioned, since the JSONL is a stated contract) — would upgrade
  `verdict_heeded` to the plan's per-rule form; (b) record grader tier on the
  `flake_baseline` ledger event — a versioned-contract change requiring explicit
  approval. Both flagged, neither implemented.

## 5. Documentation alignments applied in this commit (orchestrator edits)

- `corpora/groundwork-v0/README.md`: the toolchain pin is the **(verdi-go ref, Go
  toolchain version) pair** — graph byte-identity binds both (verdi-go pins Go in CI
  for SSA determinism); the pseudo-version stamp alone does not capture the compiler.
- `docs/design/verdi-go-integration-plan.md` §6: one-line note that `verdict_heeded`
  is operationalized per §3-item-8 above until the upstream `--log` gains rule ids.

## 6. Run-next checklist (environment-gated; in order)

1. **CI**: let the docker-marked tiers run (`VERDI_REQUIRE_DOCKER=1`): grader-image
   build + 4 grade cases; trial-image build + 2 smoke cases.
2. **Build + digest-pin images** (network + Docker): `docker build` grader and
   `claude-code-groundwork` with `--build-arg GROUNDWORK_REF=<the pinned ref>` (or
   prebuilt binaries built with the **pinned Go version**); record digests; set
   `VERDI_GRADER_IMAGE`.
3. **Docker-tier k=5 baselines** for all 16 tasks (`bench corpus baseline --runner
   docker`) → the admission-grade `flake_baseline` events; then signed curation
   approval + `bench corpus admit` per task.
4. **EVAL-10 contamination probe** (model access): command recorded in the corpus
   README admission section.
5. **Harbor calibration pilot** (keys + proxy + Docker): small run → per-trial cost +
   `CalibrationVariance` for the MDE gate; funnel metrics then run on **real**
   `groundwork-mcp.jsonl`.
6. **Lock ceremony** for the flagship (resolve D4/D5, MDE-backed threshold, §6's
   pre-registered interpretation notes) → run → grade → judge → forensics →
   selfcheck → analyze `--official`.
7. **Track C P0 GPU pilot** (user's GPU box): `scripts/workspace-pilot/README.md`
   runbook → send back artifacts/CSVs → the data-shape memo → freeze
   `workspace_trajectory` v1.

## 7. Standing caveats (carried, not new)

Public fixture ancestry (mutated seeds; probe before official) · ~10–40-node graph
scale (state scope in findings) · local-exec grades are ADVISORY by construction ·
the funnel's cross-source ordering limitation (§3.8) · GPU surfaces untested until
the pilot runs.
