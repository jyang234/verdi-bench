# verdi-bench: remediation specifications for the 2026-07 capability audit

**Companion to:** `verdi-bench-capability-audit-2026-07.md` (findings register).
**Source baseline:** all file:line references are to the tree at merge commit
`1a525b2` (identical harness tree to audited commit `9adb261`).
**Status:** PROPOSED — no spec here is approved for implementation until a
human signs off on it. Specs whose *Contract impact* section is non-empty
additionally require the explicit approval CLAUDE.md demands for versioned
contracts (schemas, ledger event formats, anything hash-chained or
pre-registered) before any code lands.

## How to read a spec

Each spec has: the invariant the finding breaks; the current behavior with
verified evidence; a concrete remediation design (with decision points that
need a human call marked **[DECIDE]**); contract impact; a test plan that
leads with a failing reproduction per the "reproduce before fixing"
directive; numbered acceptance criteria (AC-Fx.y) intended to be bound to
`test_ac*`-named tests at collection; and explicit non-goals.

Recommended implementation order (credibility-per-unit-effort, from the
audit's §7 roadmap): F-H4 → F-H6 → F-H5 → F-H7 → F-H3 → F-H2 → F-H1 →
Mediums (grouped) → documentation truth-ups.

---

# Part I — High findings

## F-H1 — Grade results must not transit the agent-writable workspace

| | |
|---|---|
| **Severity** | High |
| **Subsystems** | `grade` (host side); external `verdi-bench/grader` image (entrypoint); docs |
| **Effort** | Large (split across this repo and the grader image) |
| **Depends on** | nothing; blocks the "graders cannot hallucinate" claim |

### Invariant violated

The Layer-0 deterministic grade is the instrument's root authority. Its
integrity must not depend on the good behavior of agent-authored code. Today
the authoritative verdict is a file the agent's code can rewrite.

### Current behavior (verified)

- `harness/grade/container.py:175-183` (`build_grade_command`) launches
  `docker run --rm --network none` with the workspace copy mounted
  **read-write** at `/workspace` and `/holdouts` mounted `:ro`. The holdout
  command carries **no** `--user`, `--cap-drop ALL`, or
  `--security-opt no-new-privileges` — the plugin command
  (`build_plugin_command`, `container.py:199`) has all three.
- The grader image's entrypoint (external to this repo; documented in
  `docs/usage-guide.md:157-160`) writes `/workspace/holdout_results.json`.
  The host reads exactly that path back (`container.py:119-127`).
- GR-1 fresh-copy grading (`_run_on_fresh_copy`, `container.py:294-327`)
  deletes only a *pre-existing* forged results file. It cannot prevent an
  in-run overwrite: holdout tests import and execute agent code in-process,
  so an `atexit` hook or daemon thread registered by the agent's solution can
  rewrite `holdout_results.json` with an all-pass vector after the grader
  writes it. The forged grade is then ledgered under the `grader: "docker"`
  trust marker.
- `proc.stdout` is already captured (`container.py:102`,
  `capture_output=True`) and currently unused — a ready-made trusted channel.
- The existing adversarial test
  (`tests/test_eval5_grade.py:373-398`,
  `test_grade_ignores_forged_results_and_protects_evidence`) covers only the
  pre-existing-file case, not an in-run overwrite.
- Secondary leak: `/holdouts` is read-only but *readable* by agent code
  executing at grade time, exposing expected assertions.

### Remediation design

**Phase A — host side (this repo).**

1. **Move the results channel to fenced stdout.** The grader entrypoint emits
   the results JSON inside an unambiguous fence (e.g. a
   `VERDI_HOLDOUT_RESULTS_V1` begin/end sentinel pair) on stdout;
   `DockerGradeRunner.run_holdouts` parses the fenced block from
   `proc.stdout` instead of reading `Path(workspace) / HOLDOUT_RESULTS`.
   Fail-closed parsing: zero fenced blocks → terminal
   `GradingContainerError` ("grader produced no results", today's
   missing-file outcome); **two or more** fenced blocks (an agent `atexit`
   hook printing its own forged fence) → `MalformedHoldoutOutput`; malformed
   JSON inside the fence → `MalformedHoldoutOutput`. Exit-125 →
   `GraderUnavailableError` stays as is.
   - **[DECIDE] stdout fence vs. results-only volume.** Recommendation:
     stdout. A results volume writable only by a grader uid still shares the
     container with in-process agent code and needs uid choreography to be
     meaningful; the fence rides the existing `capture_output=True` with no
     new mounts and works under `--network none`. The duplicate-fence rule
     closes the "agent prints its own fence" counterattack, *provided
     Phase B runs tests in a subprocess* (see below) so agent code cannot
     write to the entrypoint's stdout after results are emitted.
2. **Harden the holdout grade command to parity with the plugin command:**
   add `--cap-drop ALL`, `--security-opt no-new-privileges`, and a
   non-root `--user` to `build_grade_command`.
3. **Keep the advisory path distinguishable.** `LocalGradeRunner`
   (`grades_in_place=True`, `grader_name="local"`) may keep the file-read
   transport; it is already ledgered as advisory and must not be silently
   promoted.
4. **Plugin symmetry.** `plugin_results.json` has the same forgeable
   transport (`container.py:275-278`). Apply the same fence, or explicitly
   defer with a tracked follow-up — **[DECIDE]**; recommendation: same
   change, same PR series, second commit.

**Phase B — grader image (external, documented interface).**

The entrypoint runs the holdout tests in a **subprocess under a distinct
non-privileged uid**, collects per-test outcomes from that subprocess, and
only the entrypoint process (whose lifetime outlasts every agent-registered
hook in the test subprocess) writes the single fenced results block to
stdout. Update `docs/usage-guide.md` with the new grader-image contract and
a reference entrypoint sketch (mirroring the in-repo `run_plugin.py` model).

**Holdout readability leak — [DECIDE].** Truly hiding expected values from
the process that executes the tests is not generally achievable (the tests
are the holdouts). Recommendation: disclose the limitation in
`docs/deep-dive.md` beside the grading-isolation claims rather than
pretending to isolate it; optionally note that per-test-file subprocess
isolation reduces cross-test adaptation.

### Contract impact (approval required)

- **Ledgered content: unchanged.** The grade event payload
  (`assertions` vector, `binary_score`, optional `fractional_score`,
  `grader`, `override_of` — `harness/ledger/events.py:240-272`) does not
  include the transport path; `parse_holdout_output`'s shape
  (`deterministic.py:51-77`) is preserved. No hash-chain or event-schema
  change.
- **Grader-image interface changes** (workspace file → stdout fence). This
  is an operator-facing contract in `usage-guide.md`. Recommendation: hard
  cut with a versioned fence sentinel (`…_V1`) and a loud, terminal error
  when the fence is absent — no silent file fallback (a fallback would
  reopen the hole).
- `build_grade_command`'s argv is asserted by unit tests
  (`tests/test_eval5_container.py:12-43`); those tests change because the
  *intended behavior* changes under this approved spec — flagged here so the
  change is pre-agreed, not test-tampering.

### Test plan (reproduce first)

1. **Failing reproduction (must fail on current code):** a
   `DockerGradeRunner` test whose faked `subprocess.run` emits legitimate
   FAIL results through the grader channel *and* plants an all-pass
   `holdout_results.json` in the workspace copy (simulating the `atexit`
   forgery). Assert the ledgered grade is FAIL. Today the forged file wins.
2. Fence parser units: no fence → `container_failure`; duplicate fence →
   `malformed_holdout_output`; bad JSON → `malformed_holdout_output`;
   exit-125 → transient. One event per attempt preserved.
3. AC-1 extensions: holdout argv carries `--cap-drop ALL`,
   `no-new-privileges`, `--user`; `/holdouts` still `:ro`; `--network none`
   unchanged.
4. `@pytest.mark.docker` e2e against the updated grader image (repo
   convention is the `docker` marker + `-m "not docker"` deselection).

### Acceptance criteria

- **AC-F1.1** In the docker path, the ledgered grade derives exclusively
  from the entrypoint-emitted fenced channel; bytes of any
  `holdout_results.json` in the workspace copy never influence it.
- **AC-F1.2** A workspace whose code overwrites or creates
  `holdout_results.json` during/after the grade run still yields the true
  grade (planted-violation test).
- **AC-F1.3** An ambiguous channel (0 or ≥2 fences, unparseable JSON) fails
  closed with the existing terminal reasons — never scored.
- **AC-F1.4** The holdout grade command carries the same hardening flags as
  the plugin command plus a non-root `--user`.
- **AC-F1.5** `grader="local"` grades remain distinguishable and advisory.
- **AC-F1.6** Grade event payload shape is byte-identical for identical
  results (existing event tests untouched).

### Non-goals

Eliminating the inherent exposure that agent code executes at grade time;
fully hiding holdout expected values (disclosed instead, per [DECIDE]);
changing the `LocalGradeRunner` advisory path.

---

## F-H2 — Ship `bench corpus baseline` and define the baseline workspace contract

| | |
|---|---|
| **Severity** | High |
| **Subsystems** | `corpus` (CLI verb, model), `grade` (baseline library), docs |
| **Effort** | Medium (verb) + Medium (workspace-contract model change) |
| **Depends on** | a human decision on the workspace basis (see [DECIDE]) |

### Invariant violated

"A ledgered clean flake baseline is an admission prerequisite" must be
executable through the tool surface, and a `flake_baseline` event should
attest that a baseline actually *ran* — not merely that someone called the
event constructor.

### Current behavior (verified)

- `flake_baseline()` (`harness/grade/baseline.py:37-96`) has **zero**
  production callers. The only in-repo caller of anything baseline-shaped is
  the property-test entrypoint (`harness/corpus/admit.py:240`), which
  fabricates the event directly via `events.record_flake_baseline(...)` with
  self-supplied results. Both CLI-flow tests do the same
  (`tests/test_eval8_cli.py:95-97,167-169`).
- `bench corpus admit` *requires* the clean baseline
  (`admit.py:195-199` raising `BaselinePrerequisiteError`;
  `has_clean_baseline`, `admit.py:60-70`, keyed by `task_sha`,
  latest-event-wins).
- **The workspace semantics are undefined and wrong for mined tasks.** A
  mined `Candidate` (`corpus/mine.py:56-81`) stores `workspace_ref` = the MR
  *parent* (pre-fix) tree, and its holdouts are the MR's added tests — i.e.
  every mined task is fail-to-pass by construction. Baselining the
  "unmodified workspace" (per the `baseline.py` module docstring) runs
  fail-to-pass tests against the pre-fix tree: 5/5 fail, permanent
  quarantine. **No reference-solution artifact exists anywhere in the corpus
  model** (`TaskEntry`, `registry.py:79-118`, has no solution field;
  SWE-bench holdouts carry `test_patch` and `base_commit` but no fix).
- Detection power is undisclosed: k=5 zero-tolerance misses a per-run flake
  of rate *p* with probability (1−p)^5 (≈ 90.4% at p = 2%).

### Remediation design

1. **New verb `bench corpus baseline`** following the `admit`/`calibrate`
   wiring (`corpus/cli.py`: `@corpus_app.command`, `--actor` via
   `_resolve_actor_or_exit` [GR-12], `ledger_path = experiment_dir /
   "ledger.ndjson"`). Args: `experiment_dir`, `--manifest`, `--candidate-id`,
   `--task-sha`, `--k` (default `DEFAULT_K`, `k < 1` already refused by the
   library, GR-10). It must call `flake_baseline()` — never
   `record_flake_baseline` directly — so preflight (GR-8), fresh-copy runs
   (GR-9), and the auditable per-run assertion vectors (GR-13) all apply.
   Grader outage (`GraderUnavailableError`) exits non-zero with **nothing
   ledgered** (inconclusive ≠ quarantine), matching the library contract.
2. **Define the baseline workspace basis — [DECIDE], direction-setting.**
   - **(a) Recommended: baseline the reference solution.** Add an optional
     reference-solution seam to the corpus model (for mined tasks: the MR
     *head* sha, which the miner already sees; for SWE-bench: the
     gold-patch-applied tree), materialize it beside the holdouts, and define
     baseline = k grading runs against that tree with **all-pass required**.
     This measures exactly the flake question ("do the holdouts pass
     deterministically when the task is truly solved") and keeps
     `verdict: "clean"` meaning what `has_clean_baseline` already assumes.
     Cost: a schema change to `extra="forbid"` pydantic models (versioned
     contract — see below) and materializer support.
   - (b) Alternative: determinism-only baseline against the unmodified
     `workspace_ref` (clean = all k runs produce identical assertion
     vectors, stable-fail acceptable). No new artifacts, but it silently
     redefines "clean" away from all-pass and weakens what admission
     attests. Not recommended.
   - Where a corpus has no reference solution, the honest outcome is that
     the prerequisite cannot be satisfied — admission refuses, as designed.
3. **Make ran-vs-fabricated distinguishable.** Add an additive, optional
   `workspace_basis` field (e.g. `"reference_solution"`) to the
   `flake_baseline` event payload (constructor
   `harness/ledger/events.py:293-314`), following the repo's
   insert-only-when-present convention. **[DECIDE]** whether
   `has_clean_baseline` should *require* the field once shipped (strict) or
   accept legacy events (compatible); recommendation: accept legacy for
   existing chains, require it for newly planned experiments.
4. **Disclose the operating characteristic.** Document
   P(miss | flake rate p, k) = (1−p)^k in `docs/deep-dive.md` next to the
   admission-prerequisite claim, and pin it with a small property test.
   Raising `DEFAULT_K` is a **[DECIDE]** knob — the module docstring forbids
   loosening zero-tolerance, and raising k is the doctrinally consistent
   lever (agent-free, so cost is k container runs per admission);
   recommendation: keep k=5 default, allow `--k` up-tuning, disclose the OC.
5. **Property-sweep registration.** Register a `corpus-baseline` entrypoint
   via `register_entrypoint` (with a `prepare` that supplies a completing
   fake runner) and add it to `EXPECTED_ENTRYPOINTS`
   (`tests/test_eval3_property.py:40-58`): exactly one `flake_baseline`
   event per completed invocation; zero on transient outage.

### Contract impact (approval required)

- **Corpus model schema change** (option a): new optional
  reference-solution field on `Candidate`/`TaskEntry` — both are
  `extra="forbid"`; interacts with `Candidate.content_sha()` and
  `assert_valid_successor`/`retrigger_baselines` (`registry.py:200-234`).
  **[DECIDE]** whether the reference ref is part of `content_sha` (changing
  the solution re-triggers curation) — recommendation: yes, it is task
  content.
- **`flake_baseline` event**: additive optional `workspace_basis` field —
  sanctioned additive-field convention, but still a ledger-event change
  requiring sign-off.
- CLI surface addition (`bench corpus baseline`) — new public verb,
  document in README/usage-guide.

### Test plan (reproduce first)

1. **Failing reproduction:** a CLI test invoking `bench corpus baseline` on
   a materialized candidate and asserting one ledgered `flake_baseline`
   event whose results came from k real (faked-runner) grading runs — fails
   today because the verb does not exist. Plus an admission-flow test that
   goes mine → approve → **baseline (verb)** → admit with no direct
   `record_flake_baseline` call — replacing the fabrication in
   `test_eval8_cli.py` flows (the old fabricating tests stay valid for the
   event constructor itself).
2. Workspace-contract tests: baseline resolves the reference-solution tree;
   a fail-to-pass candidate with a correct reference baselines clean; a
   flaky holdout (scripted `SeqGradeRunner` with one FAIL among k)
   quarantines through the verb.
3. Transient outage through the verb: non-zero exit, zero events, no
   quarantine (mirrors `test_ac2_transient_grader_outage_is_not_flake`).
4. Operating-characteristic property test pinning (1−p)^k.
5. Property-sweep entry (`corpus-baseline`) keeps the one-event invariant.

### Acceptance criteria

- **AC-F2.1** `bench corpus baseline` runs `flake_baseline()` against the
  contracted workspace and ledgers exactly one event per completed run.
- **AC-F2.2** The event records the workspace basis; the full
  mine→approve→baseline→admit flow works end-to-end through CLI verbs only.
- **AC-F2.3** Grader outage is inconclusive: non-zero exit, nothing
  ledgered, no quarantine.
- **AC-F2.4** For a fail-to-pass task, baseline grades the
  reference-solution tree and `clean` means all-pass × k.
- **AC-F2.5** The k-run detection power is documented and pinned by test.
- **AC-F2.6** The `corpus-baseline` entrypoint is in the property sweep's
  closed set.

### Non-goals

Changing zero-tolerance semantics; back-filling baselines for
already-admitted tasks (they keep their legacy events); building golden
solutions for corpora that lack them.

---

## F-H3 — Commit workspace bytes so forensic/contamination evidence is chain-verifiable

| | |
|---|---|
| **Severity** | High |
| **Subsystems** | `grade` (hash at grade time), `ledger` (additive event field), `forensics`, `contamination` |
| **Effort** | Medium-Large |
| **Depends on** | a canonical workspace-walk definition (see [DECIDE]) |

### Invariant violated

"A record is never evidence unless its bytes matched the chain"
(`forensics/scan.py` docstring). True today only for trajectories. The
end-state detectors read live disk, so history is tamper-evident while the
evidence history points at is not — the one real break in "you cannot
quietly edit history."

### Current behavior (verified)

- Forensics reads the live workspace at scan time:
  `harness/forensics/scan.py:148,158` via `_read_text_files` (`:40-58`,
  `rglob`, skips `{artifacts, .git, __pycache__}`, drops non-UTF-8). No
  hash, no chain check.
- Contamination does the same: `read_solution`
  (`harness/contamination/scan.py:58-83`, skips `artifacts/` and
  `holdout_results.json`, `errors="replace"`).
- Trajectories, by contrast, are committed and verified: sha computed and
  ledgered at run time as an additive top-level `trajectory_sha` on the
  `trial` event (`ledger/events.py:175-196`; computed in
  `run/trajectory.py:123-135`, persisted read-back-verified), and both
  consumers verify via the shared `resolve_trajectory`
  (`run/trajectory.py:221-251`) whose closed status vocabulary
  (`verified | absent | missing_artifact | sha_mismatch | corrupt`) becomes
  named coverage gaps (`forensics/scan.py:130-139`). The canonical
  tamper→gap test is
  `tests/test_eval16_step_forensics.py::test_ac6_verified_only_deterministic_ungated`.
- Attack: after run/grade, anyone with disk access deletes the hardcoded
  literal or leaked-holdout copy from the workspace, then runs
  `bench forensics scan` / `bench contamination probe`; the resulting
  chain-anchored report is "clean" and citable forever.

### Remediation design

Mirror the trajectory mechanism end to end:

1. **Canonical workspace walk — [DECIDE], prerequisite.** The three
   existing walks disagree (forensics: skips
   `{artifacts, .git, __pycache__}`, strict UTF-8; contamination: skips
   `artifacts/` + grader output, `errors="replace"`; judge assemble:
   skips symlinks + `artifacts/` + grader output, symlink-escape-hardened
   per PRA-M5). Define ONE canonical solution walk in a shared helper —
   recommendation: the `judge/assemble.py:41-77` definition (it is the
   hardened one), extended to hash raw bytes (not decoded text) so binary
   files are committed too. Both scanners keep their own *read* filters but
   verify against the canonical hash.
2. **Commit at grade time.** In the grade loop
   (`grade/cli.py:214-241` already resolves
   `workspace = Path(rec["artifacts_path"]).parent` per trial), compute
   `workspace_sha256` over the canonical walk (sorted relpaths + file
   bytes, hashed with the chain's canonicalization conventions — reuse
   `run/trajectory.py:123-131` `canonical_bytes` for the manifest
   structure) and record it as an **additive, optional field on the
   `grade` event** (`record_grade`, `events.py:240-272`), following the
   `grader`/`override_of` insert-only-when-present precedent.
   - **[DECIDE] grade event vs. trial event.** Trial-time commitment
     (run stage) would be earlier and arguably stronger, but the run stage
     ends before grading and the grade stage re-walks anyway; grading is
     when the workspace becomes *evidence*. Recommendation: grade event,
     with the disclosed caveat that a `grader="local"` hash is only as
     trustworthy as that advisory path.
3. **Verify at scan time.** Add a `resolve_workspace(workspace_root,
   ledgered_sha)` helper with the same closed vocabulary as
   `resolve_trajectory`. Forensics: an unverifiable workspace becomes a
   per-trial coverage gap `{"trial_id", "reason": "workspace_" + status}`
   in a new additive `coverage.workspace_gaps` list (the
   `record_forensics_report` validator's subset check at
   `events.py:672` passes unchanged; the exact-set test assertion at
   `tests/test_eval11_forensics.py:257` must be updated — flagged here as
   an agreed behavior change, not tampering). Trials with unverified
   workspaces contribute **no end-state flags and no clean claims** —
   exactly the AC-6 "partial coverage is data" discipline.
   Contamination: an unverifiable workspace joins `ScanReport.skipped`
   as `workspace_unverified:<trial_id>` and is surfaced per F-M7's
   remediation (probe event carries skipped/alarms).
4. **Legacy chains.** Events without `workspace_sha256` yield status
   `absent` → disclosed gap, never a hard failure (same rule as
   trajectories with no ledgered sha).

### Contract impact (approval required)

- **`grade` event**: additive optional `workspace_sha256` (+
  `workspace_walk_version` int so a future walk change is explicit).
  Sanctioned additive-field convention; still a ledger-event change.
- **`forensics_report` event**: additive `coverage.workspace_gaps` key.
- No changes to chain canonicalization or existing field semantics; old
  ledgers verify unchanged and render with disclosed gaps.

### Test plan (reproduce first)

1. **Failing reproduction:** run→grade→**mutate a workspace file**
   (delete the planted literal) → `forensics scan` — assert the scan
   either flags or discloses; today it reports clean with full coverage.
   Mirror of the trajectory tamper test.
2. Hash determinism: same workspace → same `workspace_sha256`
   (ordering-independent, binary-safe, symlink-skipping).
3. Verified path: untampered workspace scans with zero workspace gaps and
   unchanged flags (regression on planted/clean fixtures).
4. Contamination: tampered workspace → `skipped` entry, not a clean flag.
5. Legacy: a ledger whose grade events lack the field renders with
   `workspace: absent` gaps, no crash.

### Acceptance criteria

- **AC-F3.1** Grading ledgers a canonical `workspace_sha256` per trial.
- **AC-F3.2** Both scanners verify workspace bytes against the ledgered
  hash before treating them as evidence.
- **AC-F3.3** A post-grade workspace mutation is disclosed as a named
  per-trial gap in the chain-anchored report — never silently clean.
- **AC-F3.4** Legacy events without the field degrade to disclosed
  `absent` gaps.
- **AC-F3.5** The canonical walk is a single shared helper used for
  hashing; a walk-definition change requires a version bump.

### Non-goals

Committing full workspace *contents* to the ledger (hash-only, like
trajectories); protecting against tampering that occurs *before* the grade
stage runs (that window is disclosed in the design doc instead).

---

## F-H4 — Enforce the cost ceiling on `max(self-report, proxy)`

| | |
|---|---|
| **Severity** | High |
| **Subsystems** | `run` (enforcement), `status` (duplicate helper) |
| **Effort** | Small |
| **Depends on** | nothing — recommended first implementation |

### Invariant violated

The pre-registered `cost_ceiling` (a required field of the sha-locked spec,
`schema/experiment.py:240-250`) must bound real spend. Today it bounds
whatever the arm says about itself.

### Current behavior (verified)

- `_enforcement_cost` (`harness/run/interleave.py:92-101`) returns
  `telemetry_cost if telemetry_cost is not None else proxy_metered_cost` —
  the proxy figure is consulted **only** when self-report is null [RN-2].
  An arm reporting `0.0` accumulates nothing; `CostGuard.would_exceed()`
  (`run/budget.py`) never trips; every repetition runs.
- The same null-preferring selection is duplicated in
  `harness/status/aggregate.py:206-211` and reached from three interleave
  call sites (`:129` resume seeding, `:316` live add, `:375` infra
  attempts).
- The cross-check delta `flags.proxy_cost_delta` is computed once
  (`run/seam.py:255`, only when both figures exist) and **never read** by
  any enforcement, aggregation, or display code — and has zero test
  coverage.
- Reliability context: `proxy_metered_cost` comes from
  `_scan_proxy_log` (`run/engines/harbor.py:426-477`); a *configured but
  missing* proxy log already fails loud (`ProxyLogMissingError` →
  run aborts, PRA-H4/M9), but an unconfigured proxy means no cross-check
  figure exists at all — `max()` can only bite when the proxy is present.

### Remediation design

1. Change `_enforcement_cost` to return
   `max(telemetry_cost, proxy_metered_cost)` when **both** are non-null;
   otherwise whichever is present; else `None`. Update its docstring to
   state the trust rationale: the proxy is the out-of-band meter, the
   telemetry figure is the arm's claim, and enforcement takes the larger
   because under-reporting must not buy budget.
2. Apply the identical rule to the duplicate in
   `status/aggregate.py:206-211` (or better: move the helper to one shared
   location — `run/budget.py` is the natural owner — and import it from
   both, eliminating the duplication; note `status` must not import
   run-stage internals if a contract forbids it — verify with
   `lint-imports` and fall back to keeping two textually-identical
   functions with cross-referencing comments if the contract blocks it).
3. **D004 is preserved untouched:** the record's `telemetry.cost` stays
   null-preserving (`TrialRecord._nulls_match_telemetry`,
   `adapters/base.py:106-117`); the fix changes only the enforcement
   figure, never imputes into the record. The existing seam comment
   (`run/seam.py:250-252`) already says "do NOT reconcile" *about the
   record* — enforcement-side max() does not contradict it, but reword the
   comment to say so explicitly.
4. `proxy_cost_delta` stays an advisory rendering signal (now indirectly
   load-bearing through max()); add the missing test coverage for its
   computation.

### Contract impact

**None on versioned contracts.** The ceiling *amount* is locked spec bytes;
the enforcement *selection rule* is pure harness code, serialized nowhere
(verified: not in spec bytes, not in any event payload; the
`run_stopped_cost_ceiling` event shape `{accumulated_cost, ceiling}` is
unchanged). D004 constrains the fix but is not modified by it. No live
locked specs exist that a semantics change could strand. Human sign-off is
still required per the "human decides" directive because enforcement
semantics change observable behavior (a run that previously completed can
now stop at the ceiling — which is the point).

### Test plan (reproduce first)

1. **Failing reproduction (in `tests/test_eval4_cost.py`, beside
   `test_ac7_proxy_cost_enforced_when_telemetry_null`):** fake engine with
   `fake_behavior` telemetry cost `0.0` and `proxy_metered_cost` per trial
   large enough to cross the ceiling — assert the run stops with
   `run_stopped_cost_ceiling` and does not execute later cells. Fails
   today (guard accumulates 0.0).
2. Both-present-honest case: telemetry ≥ proxy → telemetry drives (no
   behavior change).
3. Telemetry-null case: existing `test_ac7_proxy_cost_enforced_when_telemetry_null`
   must stay green, including its D004 assertion
   (`telemetry.cost is None` in the record).
4. Resume seeding: a prior under-reported trial's spend re-accumulates
   under max() on rerun (extend `test_ac7_rerun_resumes_not_duplicates`
   pattern).
5. Status consistency: `bench status` spend figure equals the enforcement
   accumulation for the same ledger.
6. New unit coverage for `proxy_cost_delta` computation at the seam.

### Acceptance criteria

- **AC-F4.1** With both figures present, the guard accumulates
  `max(telemetry, proxy)`; an arm self-reporting 0 cannot spend past the
  ceiling when the proxy meters real spend.
- **AC-F4.2** `telemetry.cost` in the ledgered record remains exactly the
  self-reported value or null (D004).
- **AC-F4.3** Enforcement and `bench status` spend aggregation use the same
  selection rule (single shared helper, or contract-blocked duplicates with
  a pinning test asserting identical behavior).
- **AC-F4.4** Null-telemetry and null-proxy cases behave exactly as today.

### Non-goals

Requiring a metering proxy (its absence stays a disclosed operator
responsibility per `deep-dive.md §6`); mid-trial aborts (the guard remains
refuse-to-start); reconciling the *recorded* telemetry.

---

## F-H5 — Bind the result card to the fenced render

| | |
|---|---|
| **Severity** | High |
| **Subsystems** | `analyze` (card) |
| **Effort** | Small |
| **Depends on** | nothing; pairs naturally with F-H6 |

### Invariant violated

The card's own docstring: "the card certifies a rendered result." A card
stamped with a render's mode must project exactly the data that render
fenced.

### Current behavior (verified)

- `_rendered_mode` (`analyze/card.py:46-54`) reads only
  `rendered[-1]["mode"]` from the last `findings_rendered` event and
  discards the event's two binding fields — `rendered_head_hash` and
  `findings_sha256` (payload of `record_findings_rendered`,
  `ledger/events.py:422-446`; emitted at `analyze/cli.py:86-93` where
  `findings_sha256 = sha256(findings.model_dump_json())`, the exact bytes
  of `findings.json`).
- `build_card` (`card.py:115-117`) recomputes `compute_findings` over the
  *current* ledger; its provenance block (`card.py:184-192`) mixes the
  fresh `ledger_head` with the stale `mode` — the two are never
  cross-checked.
- The markdown/HTML render path has the guard the card lacks:
  `_assert_head_hash` (`report.py:1095-1107`) verifies the chain and
  refuses on any head change. `build_card` bypasses `render_markdown`
  entirely, inheriting none of its three guards.
- Consequence: after an official render, quarantine a trial or
  `--retry-terminal` a grade, then `bench card emit` — an
  `"official"`-stamped card whose numbers match no fenced render and no
  ledgered `findings_sha256`.

### Remediation design

In `build_card`, after `_rendered_mode`-equivalent lookup (switch to
`latest_event(ledger, FINDINGS_RENDERED)` and keep the whole event):

1. **Chain check:** run `verify(ledger_path)`; refuse (`CardError`) on a
   broken chain — parity with `_assert_head_hash`.
2. **Freshness check:** refuse unless the latest `findings_rendered` event
   is the **final event in the ledger** (no events of any kind appended
   after it). This is deliberately stricter than inventing a "data-bearing
   event kinds" taxonomy, which does not exist in the codebase and would
   be a new silent-failure surface; the refusal message says exactly what
   to do ("N event(s) appended since the last render — re-run
   `bench analyze` before emitting a card").
   Note the off-by-one: `rendered_head_hash` is the head *before* the
   render event itself was appended, so the correct check is positional
   (render event is last), not `rendered_head_hash == current_head`.
3. **Byte binding:** compute
   `sha256(findings.model_dump_json())` for the recomputed findings and
   refuse on mismatch with the event's `findings_sha256`. This also
   catches parameter drift (e.g. a different `--corpus` argument at card
   time than at analyze time) — a true positive, fail loud.
4. **Carry the binding on the card:** add `rendered_head_hash` and
   `findings_sha256` to the card's `provenance` block so a third party can
   check the card against the chain without recomputing.

The CLI already maps `CardError` → exit 2 (`analyze/cli.py:228-235`), so
no CLI change is needed beyond the message.

### Contract impact (approval required)

- **Card schema:** two additive provenance fields. `CARD_SCHEMA_VERSION`
  (`card.py:30`) bumps 1 → 2 — **[DECIDE]**; recommendation: bump, since
  the card is the citable artifact and consumers may key on the binding
  fields' presence. `serialize_card` byte-determinism is preserved
  (sorted keys); existing card bytes change only by the added keys.
- No ledger event changes — both binding fields already exist on the
  render event.

### Test plan (reproduce first)

1. **Failing reproduction (in `tests/test_analyze_card.py`, using the
   `_graded_analyzed` fixture):** after analyze, append a data-bearing
   event (e.g. a `record_grade` override or forensic quarantine), then
   `build_card` → expect `CardError`; today it emits an official-stamped
   card. Follow the `test_card_requires_a_prior_analyze` refusal template.
2. Happy path: fresh analyze → card emits, and card provenance's
  `rendered_head_hash`/`findings_sha256` equal the render event's.
3. Sha-mismatch path: card built with a different corpus-manifest argument
   than analyze used → refusal naming the sha mismatch.
4. Broken chain → refusal.
5. CLI: staleness surfaces as exit 2 with the re-run-analyze message.

### Acceptance criteria

- **AC-F5.1** `build_card` refuses when any event post-dates the latest
  `findings_rendered` event.
- **AC-F5.2** `build_card` refuses when the recomputed findings' sha256
  differs from the ledgered `findings_sha256`.
- **AC-F5.3** An emitted card carries the render binding in provenance,
  matching the ledgered render event.
- **AC-F5.4** A broken chain refuses card emission.
- **AC-F5.5** Refusals exit 2 with an actionable message.

### Non-goals

Changing what the card computes (it stays a projection); the two card
*Medium* findings (md crash on asymmetric contamination, HTML disclosure
parity) are specified separately in Part II but should ship in the same
card-hardening series.

---

## F-H6 — One source of truth for "detected" across markdown, dossier, and Holm

| | |
|---|---|
| **Severity** | High |
| **Subsystems** | `analyze` (report renderer) |
| **Effort** | Small |
| **Depends on** | nothing; pairs with F-H5 and F-H7 |

### Invariant violated

One `bench analyze` invocation must not produce two official artifacts that
disagree on whether an effect was detected.

### Current behavior (verified)

- The canonical decision: `compute_findings` sets
  `cf.decision["detected"] = boot.excludes_zero()` (`report.py:970`,
  `stats.py:58-60`) — from the deployed 95% CI.
- Under `--multi-arm-correction holm`, `_apply_holm` (`report.py:840-863`)
  **rewrites** `cf.decision["detected"]` from a two-sided recentered
  bootstrap p-value (`_two_sided_bootstrap_p`, `report.py:818-837`) — a
  *different estimator* than the deployed CI (which may be BCa or
  cluster-robust-t).
- The markdown renderer re-derives detection locally from the unadjusted
  CI: `detected = s["ci_low"] > 0.0 or s["ci_high"] < 0.0`
  (`report.py:1126`) and then, inconsistently, pulls `decides_positive`
  and `holm_p` from `cf.decision` (`report.py:1148-1159`) — so it can
  print "Effect detected … [Holm-adjusted, p=…]" for a pair Holm rejected.
- The dossier branches on `cf.decision.get("detected")`
  (`dossier.py:202-208`). When Holm fails to reject but the raw CI
  excludes zero, `findings.<mode>.md` says "Effect detected" while
  `findings.<mode>.dossier.html` says "No effect ≥ MDE detected" — from
  one invocation.
- The displayed interval stays per-comparison 95% with no family
  adjustment (`report.py:1136`), so decision and interval follow different
  procedures with no disclosure.

### Remediation design

1. **Single source of truth:** change `report.py:1126` to branch on
   `cf.decision["detected"]`, exactly as the dossier does. In the
   no-correction case this is behavior-identical (both derive from
   `excludes_zero`), so the change is only observable for Holm-rewritten
   pairs — the intended fix.
2. **Disclose the estimator split rather than eliminate it.** Deriving the
   Holm p from the deployed CI machinery would require inverting BCa /
   cluster-robust-t intervals into p-values — real statistical work with
   its own risks. Recommendation **[DECIDE]**: keep the recentered-bootstrap
   Holm p but disclose, in both renders, that under Holm the *decision*
   uses Holm-adjusted recentered-bootstrap p-values while the *displayed
   interval* remains the unadjusted per-comparison 95% CI. One sentence in
   the markdown comparison block and one dossier template line
   (`uncertainty` template context already flows through
   `_verdict_context`, `dossier.py:183`).
3. **Regression net:** a render-parity test that extracts the
   detected/not-detected verdict from both artifacts for every pair and
   asserts equality under `none` and `holm`.

### Contract impact

None on versioned contracts (renderer-only). `findings.json` bytes are
unchanged by fix 1 (the decision dict was already Holm-rewritten; only the
markdown's derivation changes). The added disclosure sentence changes
rendered artifact text — acceptable, renders are not hash-chained (their
sha is ledgered per render, and any new render gets a new
`findings_rendered` event).

### Test plan (reproduce first)

1. **Failing reproduction:** a 3-arm fixture (reuse
   `tests/test_eval6_analyze.py` `_PREF_ARMS`/`_pref_ledger` helpers)
   engineered so a pair's raw CI excludes zero while Holm fails to reject
   (crank `m` with one strong and one marginal pair). Assert markdown and
   dossier agree — fails today.
2. Parity holds under `none` (regression).
3. The Holm disclosure sentence appears in both artifacts iff
   `correction == "holm"`.
4. Existing `test_m4_*` Holm tests stay green.

### Acceptance criteria

- **AC-F6.1** For every comparison, markdown and dossier state the same
  detection verdict, under both correction policies.
- **AC-F6.2** `_comparison_lines` contains no local re-derivation of
  detection.
- **AC-F6.3** Under Holm, both artifacts disclose that the decision and
  the displayed interval use different procedures.

### Non-goals

Family-adjusting the displayed CI (disclosed instead); changing the Holm
p estimator (disclosed instead); the tiny-n floor (specified in F-H7,
which owns the decision path).

---

## F-H7 — Lock the multi-arm decision policy; floor `detected` on cluster count

| | |
|---|---|
| **Severity** | High |
| **Subsystems** | `schema` (locked spec), `analyze` (fence, decision path), `ledger` (render event field) |
| **Effort** | Medium |
| **Depends on** | explicit human approval — this touches the locked-spec contract |

### Invariant violated

"Decision rule sha-locked before any trial runs." In a >2-arm design the
decision *procedure* for the pre-registered primary pair can be chosen —
and changed — at analyze time.

### Current behavior (verified)

- `--multi-arm-correction {none|holm}` is a CLI flag
  (`analyze/cli.py:158-181`) consumed by `compute_findings`
  (`report.py:866-886`, applied at `:997-1030` when `n_pairs > 1`).
- It is **absent from `ExperimentSpec`** (`schema/experiment.py:240-267`,
  `extra="forbid"`), **recorded in no event**
  (`record_findings_rendered` stores only
  `mode, primary_metric, rendered_head_hash, findings_sha256`), and
  **checked by no fence** (`_assert_official_calibration`,
  `report.py:1338-1464`, runs six checks; none touch the correction). Two
  official renders with different corrections are both accepted.
- Under `none` the primary decision comes from the CI; under `holm` from
  the bootstrap p — two different official decision procedures for the
  same pre-registered pair.
- **Tiny-n edge:** no minimum-cluster floor exists anywhere in the
  detection path. With one task cluster, `paired_bootstrap` yields a
  zero-width CI, `excludes_zero()` is True (`report.py:970`,
  `stats.py:58-100`), and under Holm `_two_sided_bootstrap_p` on a single
  delta returns p ≈ 1/(n_boot+1) ≈ 1e-4. The selfcheck's <2-cluster
  fail-closed gates **only the primary pair**
  (`selfcheck.py:53-67` hard-codes `arms[0]/arms[1]`), so a single-task
  *secondary* pair is declared an official detected effect under Holm.

### Remediation design

1. **Pre-register the policy in the locked spec.** Add
   `multi_arm_correction: Literal["none","holm"] = "none"` as an optional
   field on `ExperimentSpec`, following the `ContaminationConfig`
   precedent (`experiment.py:168-178` — "living inside the locked spec
   bytes makes the threshold part of the cryptographic commitment").
   `compute_findings` reads it from the spec. The CLI flag is **removed**
   — **[DECIDE]**: removal vs. deprecation-with-refusal-on-conflict;
   recommendation: remove outright (exploratory users can vary the spec
   before locking; keeping an override on official renders defeats the
   point). For 2-arm designs the field is inert, as today.
2. **Record and fence it anyway (defense in depth).** Add the applied
   correction as an additive field on `findings_rendered`
   (`events.py:422-446`) and a seventh official-fence check: refuse an
   official render whose correction differs from any prior official
   render's recorded correction. This closes the gap for chains locked
   before the spec field existed.
3. **Minimum-cluster floor for `detected=True`.** In both decision paths
   (`report.py:970` and `_apply_holm`), a pair with
   `n_tasks < MIN_DETECTION_CLUSTERS` (**[DECIDE]** the floor value;
   recommendation: 2 — the same threshold the selfcheck/nullsim already
   treat as insufficient, `nullsim.py:110`) is never `detected=True`;
   its decision carries an explicit
   `"floor": "insufficient_clusters"` marker and the renders phrase it as
   structurally-insufficient rather than null (distinct from "no effect").
   This is a stats-behavior change on an edge case; the honest phrasing is
   the point — a zero-width CI is not evidence.

### Contract impact (approval required — largest of the seven)

- **Locked-spec schema change** (`ExperimentSpec` + field): the sanctioned
  migration pattern is Optional-with-default (`hypothesized_effect`
  precedent, `experiment.py:255-256` — and its note that no pre-existing
  locked specs can be bricked applies here too). Old locked specs parse
  unchanged with `"none"` semantics; the spec sha of *new* experiments
  covers the policy.
- **`findings_rendered` event**: additive `multi_arm_correction` field.
- **Official fence semantics**: one new refusal reason (a
  `cant_analyze` reason string, e.g. `correction_mismatch`) — additive to
  the closed reason vocabulary; document in the fence's reason table.

### Test plan (reproduce first)

1. **Failing reproductions:**
   a. 3-arm fixture: official render with `none`, then official render
      with `holm` — assert the second is **refused**; today both succeed.
   b. Single-task secondary pair under `holm` — assert `detected` is
      False with the insufficient-clusters marker; today it detects at
      p≈1e-4.
2. Spec-locking: `multi_arm_correction: holm` in `experiment.yaml` flows
   through lock → analyze with no flag; a spec without the field behaves
   as `none`.
3. Fence: render-event carries the correction; mismatch refuses with the
   named reason inside the one-event refusal envelope.
4. Floor: 2-cluster pair with a real effect still detects (floor is
   minimal, not conservative-creep).
5. Existing `test_m4_*` tests updated to drive the policy through the
   spec — flagged as an agreed interface change, not test tampering.

### Acceptance criteria

- **AC-F7.1** The multi-arm decision policy is read from the sha-locked
  spec; no analyze-time flag can change an official decision procedure.
- **AC-F7.2** The applied correction is ledgered on every render event.
- **AC-F7.3** An official render whose correction differs from a prior
  official render is refused with a named `cant_analyze` reason.
- **AC-F7.4** No pair with fewer than the floored cluster count is ever
  `detected=True`, in either correction mode, and the render says why.
- **AC-F7.5** Legacy locked specs (no field) analyze unchanged under
  `none`.

### Non-goals

Extending the selfcheck to gate secondary pairs (the floor handles the
degenerate case; full secondary-pair selfcheck is future work and should
be its own proposal); family-adjusting displayed CIs (F-H6 discloses).

---

# Part II — Medium findings

Medium specs are compact: problem with verified evidence, remediation,
test plan, contract note. The same [DECIDE] and reproduce-first rules
apply. IDs are grouped by the audit's §5 clusters.

## Group J — judge integrity at the seams

### F-M-J1 — Denial-of-judgment: cap the assembled diff; disclose per-arm CANT_JUDGE asymmetry

**Problem.** `_read_workspace_diff` (`judge/assemble.py:41-77`) concatenates
every non-symlink workspace file with **no size cap**; a canary substring
anywhere in the blob raises `IdentityLeakError`
(`judge/packet.py:186-204`, substring scan via `blind/core.py:39-46`).
Both `identity_leak` and `context_overflow` are **terminal**
(`TRANSIENT_CANT_JUDGE` is only `{timeout, provider_error}`,
`judge/schema.py:83-85`), and the re-run skip (`judge/cli.py:106-117`)
makes them permanent. The analyze reduction drops CANT_JUDGE tasks
silently (`report.py:382-398`, excluded-never-imputed) and **no code
counts CANT_JUDGE per arm**. A gamed arm can salt a canary or ship a huge
junk file only on trials it would lose — a biased missing-data mechanism
nothing discloses.

**Remediation.**
1. Deterministic diff budget in `_read_workspace_diff`: a per-file and a
   total byte cap (**[DECIDE]** values; recommendation: 64 KiB/file,
   512 KiB total — sized to typical context windows), truncating
   deterministically (sorted order, head-of-file, explicit
   `--- <path> [truncated at N bytes] ---` markers) so identical
   workspaces truncate identically and `packet_sha256` stays stable.
   Truncation is disclosed in the packet, not silent.
2. Per-arm CANT_JUDGE disclosure: count `judge_verdict` events with
   `winner=CANT_JUDGE` by reason by *physical arm* in
   `compute_findings`, render the table in markdown + dossier, and flag
   **asymmetry** (one arm's terminal-CANT_JUDGE rate materially above the
   other's) as a confound via the existing confound channel
   (`analyze/confounds.py` — the `_flag_provider_error_asymmetry` pattern
   at `:133-153` is the template).
3. Identity-leak stays terminal and fail-closed (blinding is
   non-negotiable) — the mitigation is *disclosure*, not weakening the
   scan.

**Tests.** Repro: an arm whose workspace salts a canary on losing trials
→ today `judge_preference` shifts with zero disclosure; assert the new
confound flag fires. Truncation determinism; oversize workspace no longer
yields `context_overflow`; per-arm table renders; symmetric CANT_JUDGE
does not flag.

**Contract.** No event changes (counts are derived at analyze time).
Findings document gains an additive disclosure block. Packet content
changes for oversize workspaces (packet_sha256 differs) — judge packets
are not pre-registered, so no lock impact.

### F-M-J2 — Identity corpus: scope the broad tokens, extend the product list

**Problem.** `blind/core.py:75-93`: `\bgoogle\b` and `\bassistant:\s`
(case-insensitive, substring scan) terminally kill judgment on any
Google-API or chatbot-transcript task; `claude(?:-…)?` and
`gemini(?:-…)?` lack word boundaries. Meanwhile ChatGPT, Grok, DeepSeek,
Qwen, Copilot, Cursor, Aider, Mistral, Llama are absent — underinclusive
where it matters.

**Remediation.** (1) Scope overbroad tokens to vendor context
(e.g. `\bgoogle\b` only adjacent to model/AI terms, or rely on the
per-experiment `arm_canaries` literals for vendor names that are actual
arm identities); (2) add word boundaries consistently; (3) extend the
product list with the missing 2024–2026 tools; (4) surface identity-leak
rates per task class in the judge summary so a corpus-wide FP pattern is
visible (pairs with F-M-J1's disclosure). **[DECIDE]** the exact pattern
set — it is a blinding-strength trade-off the human should sign off on;
the spec ships a proposed table with a rationale per pattern.

**Tests.** Repro: a task prompt legitimately containing "Google Cloud"
today yields terminal `identity_leak` — assert it judges after scoping;
each new product token has a positive detection test; the
signature-pinned allowlist property test stays green.

**Contract.** None (pattern list is not versioned). Note: *weakening* any
pattern needs the explicit human agreement this spec's approval provides.

### F-M-J3 — Judge cost tracking and a judge-scoped ceiling

**Problem.** Every provider discards the usage block of the API response
(`providers/anthropic.py:37-42`, `openai.py:33-38`, `google.py:36-41`
return text only); `VerdictProvenance` (`judge/schema.py:102-111`) has no
usage field; `JudgeConfig` has no budget; the experiment `cost_ceiling`
governs only trials. Judge spend is unbounded and invisible.

**Remediation.** (1) Providers return `(text, usage)` with a normalized
`{input_tokens, output_tokens}`; (2) record usage on
`VerdictProvenance` (additive field); (3) optional
`judge.cost_ceiling` on `JudgeConfig` (locked-spec schema, additive
Optional — same migration pattern as F-H7) enforced by the judge CLI
loop as refuse-to-start, mirroring `CostGuard`; ceiling trip ledgers a
typed event (e.g. `judge_stopped_cost_ceiling`). Token→USD conversion
requires a price table the instrument shouldn't own — **[DECIDE]**:
recommendation: ceiling denominated in **tokens**, not USD, avoiding a
mutable price dependency.

**Tests.** Usage extraction per provider (extend the happy-path response
fixtures in `test_eval2_providers.py`); ceiling refuse-to-start; one
typed event on trip; legacy verdicts without usage render fine.

**Contract.** Additive `usage` on verdict provenance (ledger event
change — approval); additive Optional `JudgeConfig` field (locked-spec
schema — approval); one new event kind.

### F-M-J4 — Provider retry/backoff, uniform output caps, and parse transiency

**Problem.** `providers/_http.py:31-42`: single attempt, fixed 120 s
timeout, no backoff — one HTTP 429 fails the call as `provider_error`.
Anthropic hardcodes `max_tokens: 2048` (`anthropic.py:32`) while
OpenAI/Google set no cap — a truncated Anthropic reply that cuts the JSON
becomes **terminal** `PARSE` (`client.py:189-191`), permanently skipping
the comparison.

**Remediation.** (1) Bounded, deterministic-jitter-free retry in
`post_json` for 429/5xx/timeout (e.g. 3 attempts, fixed 2/4/8 s sleeps —
**[DECIDE]** whether sleeping violates the determinism directive;
recommendation: retries live at the designated network seam where
wall-clock already exists, so fixed backoff is acceptable and seeded
jitter unnecessary); (2) uniform, explicit `max_output_tokens` across all
three providers (one shared constant, sized for the verdict JSON);
(3) reclassify `PARSE` as **transient** — a truncated/garbled reply is
not deterministic-for-a-fixed-packet the way `identity_leak` is;
`TRANSIENT_CANT_JUDGE` gains `"parse"` so re-runs retry it.
Keep `provider_failure_reason` (`providers/base.py:45-60`) in sync for
both `CantJudgeReason` and `CantScoreReason` (the known drift precedent).

**Tests.** Repro: scripted 429-then-200 → verdict succeeds (fails
today); truncated-JSON reply re-attempted on re-run after transiency
change; all three providers send the same output cap.

**Contract.** `TRANSIENT_CANT_JUDGE` membership is behavioral, not
serialized — but it changes which ledgered CANT_JUDGE events get retried,
so call it out at approval.

## Group S — statistical edges

### F-M-S1 — Break selfcheck selection/validation circularity

**Problem.** `coverage_from_deltas` (`nullsim.py:87-140`) selects the CI
method by coverage on 200 null draws, and `run_selfcheck`
(`selfcheck.py:91-93`) passes iff nominal lies in the Wilson band of
**that same** estimate — selection and validation share draws, biasing
the gate toward passing (winner's-curse on coverage).

**Remediation.** Validate on a fresh sub-seeded stream: after selection,
re-run the coverage simulation for the *selected method only* with
`sub_seed(seed, "selfcheck_validate")` and Wilson-test that independent
estimate. Raise default `n_sim` (**[DECIDE]**; recommendation 400 for the
validation pass — it simulates one method, so cost is comparable to
today). The ledgered selfcheck event gains additive
`validation_coverage`/`validation_n_sim` fields.

**Tests.** Repro is statistical: a property test demonstrating the
shared-draw pass rate exceeds the independent-draw pass rate on a
marginal-coverage fixture (seeded, deterministic). Determinism of the new
stream; fence still requires `current`+`passed`+method-match.

**Contract.** Additive fields on the `selfcheck` event (approval).

### F-M-S2 — Minimum-cluster floor for `detected` — folded into F-H7

Specified as F-H7 remediation item 3 (one owner for the decision path).
Cross-reference only; no separate work item.

### F-M-S3 — Reconcile displayed MDE to realized N

**Problem.** The null phrasing interpolates the plan-time MDE
(`MDEBlock.value` from the lock event, `report.py:502-515`) even when
quarantines/missing grades shrank `n_tasks` — "No effect ≥ MDE detected
(MDE=0.15)" overstates sensitivity when the realized power no longer
supports that MDE.

**Remediation.** At analyze time, recompute an *achieved* MDE at the
realized cluster count per comparison (reusing the plan-time
`mde_check` machinery against realized N — same variance source,
disclosed) and render both: "plan MDE 0.15; achieved at realized
n_tasks=9: ≈0.21". When realized N < plan N, the structural-null phrasing
uses the achieved figure. `MDEBlock` gains `achieved_value` (findings
document, not a ledger event).

**Tests.** Repro: quarantine tasks after plan, analyze — today the null
line shows the plan MDE unchanged; assert the achieved figure appears and
is larger. Equality when realized N == plan N.

**Contract.** Findings JSON gains an additive field (its sha is ledgered
per render; additive is safe — new renders get new events).

### F-M-S4 — Escalation gates on an interval, not a point; floor process kappa

**Problem.** `review/calibrate.py:64-78`: `escalate = k < 0.6` on the
IPW **point estimate** at `n ≥ 20`, no SE/CI; the n≥20 floor ignores IPW
effective sample size (floor weights of 5 shrink effective-n; Kish
n_eff can be far below 20). Process kappa is worse:
`min_pairs=1` defaults (`review/kappa.py:156`,
`process/calibrate.py:45`) — one pair renders "sufficient".

**Remediation.** (1) Bootstrap SE for the IPW kappa (cluster bootstrap
over comparisons, seeded) and gate escalation on the interval:
escalate iff the **upper** CI bound < threshold (confident-bad), flag
"insufficient precision" when the interval straddles it — **[DECIDE]**
gate polarity (upper-bound-below = escalate on confident failure vs.
lower-bound-below = escalate on any doubt); recommendation:
upper-bound-below for escalation plus a rendered inconclusive band.
(2) Replace the raw-n floor with Kish effective n
(`n_eff = (Σw)²/Σw²`). (3) Thread a real floor into process calibration
(`min_pairs` default aligned with the outcome tier's floor, not 1).

**Tests.** Repro: a k=0.55 point estimate with a wide interval escalates
today — assert it becomes "insufficient precision"; n_eff floor test with
floor-weighted samples; process one-pair "sufficient" repro flips.

**Contract.** Rendered calibration output changes; no event changes
(escalation state is derived). `EscalationConfig` may gain an optional
interval-policy field (locked-spec schema if so — approval).

## Group C — contamination probe evidence

### F-M-C1 — Canary is derivable from published data

**Problem.** `derive_canary` (`contamination/canary.py:29-42`) is
sha256 of a source-code namespace constant plus `task_sha` — and
`task_sha` is published in findings/card provenance
(`registry.py:166-178`, `report.py:1032`). An adversary can pre-derive a
rival arm's canaries, plant them in public text the arm will quote, and
manufacture an asymmetric flag that **blocks the official render**
(`report.py:1447-1461`) — a cheap denial-of-finding.

**Remediation.** **[DECIDE]** between: (a) derive from a per-corpus
out-of-band salt (a secret file referenced by, but never stored in, the
manifest; canary becomes `sha256(namespace : salt : task_sha)`), with
the salt's *hash* committed in the manifest for auditability — real
secrecy, real key-management burden; or (b) disclose the limitation
beside the near-zero-FP claim and add an asymmetry-override ceremony
(quarantine the poisoned task with a ledgered reason instead of blocking
the render). Recommendation: (b) now (honest, cheap, keeps the fence),
(a) as a follow-up if hostile-reviewer scenarios matter to users. The
existing per-task quarantine path already un-blocks the fence
legitimately.

**Tests.** For (b): planted rival-canary scenario renders the
denial-of-finding path in docs and the quarantine ceremony restores the
official render with the exclusion disclosed.

**Contract.** (a) would touch canary derivation = versioned identity
(major, approval + migration); (b) is docs + existing mechanisms.

### F-M-C2 — Oracle-prefix probe needs a control condition

**Problem.** The probe feeds the true oracle prefix and flags when the
continuation reproduces ≥50% of the remainder's winnowing fingerprints
(`probe.py:165-180`, `overlap.py:97-162`) — **no null baseline**.
Formulaic code a clean model can legitimately continue trips it; one FP
is asymmetric → official refused.

**Remediation.** Add a perturbed-prefix control per probe: also query the
model with a semantically-neutral perturbation of the prefix (e.g.
identifier-renamed — deterministic, seeded) and require a **margin**:
flag only if `score(true) - score(control) ≥ margin` (**[DECIDE]**
margin; recommendation 0.2, calibrated on the planted/clean fixtures)
*and* `score(true) ≥ threshold`. Record both scores and the margin on
the probe event (additive fields under `arms.*.evidence`).

**Tests.** Repro: a formulaic-completion fixture (a scripted provider
that "continues" boilerplate equally well with either prefix) flags today
— assert it no longer flags; a true-memorization fixture (verbatim
regurgitation only for the true prefix) still flags.

**Contract.** Additive probe-event evidence fields (approval);
doubles provider calls per probed (arm, task) — disclosed cost.

### F-M-C3 — Put `skipped`/`alarms` on the probe event

**Problem.** `ScanReport.alarms` (holdout-leak insulation, EVAL-4 AC-9)
and `.skipped` (unscanned trials) are printed to stderr only
(`contamination/cli.py:140-143`) and never ledgered; the
`contamination_probe` event carries only `overlap_flags` + arms — a
wiped-workspace UNSCANNED trial is indistinguishable from scanned-clean
in every downstream summary.

**Remediation.** Thread `alarms` and `skipped` into `run_memory_probe`'s
event payload as additive fields; `contamination_summary` and the
renders disclose unscanned counts per arm; **[DECIDE]** whether a
non-empty `alarms` (holdout leak) should refuse the official render like
asymmetry does — recommendation: yes, it is an insulation failure, and
the quarantine ceremony is the escape hatch. Coordinates with F-H3
(workspace verification adds `workspace_unverified` entries to the same
channel).

**Tests.** Repro: wiped-workspace trial probes today with no ledgered
trace — assert `skipped` lands on the event and renders; alarm-refusal
path if adopted.

**Contract.** Additive probe-event fields (approval); possible new fence
semantics ([DECIDE] above).

## Group I — isolation & hardening

### F-M-I1 — Serve verify-cache TOCTOU

**Problem.** `serve/server.py:75,169-184`: the chain verdict is cached
by `(st_size, st_mtime_ns)`; a same-size rewrite plus `os.utime()`
serves tampered events from `/api/events|timeline|trial|compare` while
uncached `/api/status` disagrees.

**Remediation.** Key the cache on a content hash: `verify()` already
reads the whole file, so hash the bytes in the same pass and cache
`{sha256: ChainResult}` — the hot path pays one file read + hash
(cheap) instead of a full chain re-verify, and a byte-identical file is
by definition untampered. Drop the size/mtime signature entirely.

**Tests.** Repro: mid-chain same-size byte swap + `os.utime()` restore →
today `/api/events` serves tampered content (extend
`test_m10_data_routes_fail_closed_on_broken_chain`, whose tamper
currently changes the size); assert 409 after fix. Cache still avoids
re-verification on unchanged bytes (call-count assertion on a verify
spy).

**Contract.** None.

### F-M-I2 — Run the resolved digest, not the tag

**Problem.** `run/engines/harbor.py:288-290,285`: the digest recorded in
provenance comes from a `docker inspect` separate from the
`docker run <tag>` — a tag repoint between the two runs "whatever the
tag points to" while provenance records the inspected digest.

**Remediation.** After `resolve_digest`, run by the immutable ref: use
`repo@sha256:<digest>` when a RepoDigest exists; when only a local image
Id resolved, pass the Id itself as the image argument (docker accepts
image Ids). Refuse (existing `unpinned_image` path) when neither
resolves. Provenance and execution are then the same ref by
construction.

**Tests.** Repro: assert `build_run_command`'s image argument equals the
resolved immutable ref, not the tag (fails today); pinned-`@sha256:`
passthrough unchanged; unresolvable still refuses.

**Contract.** None (argv change only; provenance field unchanged).

### F-M-I3 — Validate `--runner` against the closed set

**Problem.** `grade/cli.py:185`:
`LocalGradeRunner() if runner == "local" else DockerGradeRunner()` — any
typo silently selects docker (`analyze/cli.py:172-173,217-218` shows the
repo's own validation pattern).

**Remediation.** `if runner not in ("docker", "local"): raise
typer.BadParameter(...)` (exit 2).

**Tests.** Repro: `--runner dcoker` exits 2 with a message naming the
valid set (today it silently proceeds to docker).

**Contract.** None.

## Group T — test-infrastructure & observability

### F-M-T1 — AC-hook skip evasion

**Problem.** `tests/ac_coverage.py:91-101` inspects only per-function
decorators, so a module-level `pytestmark = pytest.mark.skip`, a
class-level skip, a bare `pytest.skip()` body call, or
`@skipif(True, ...)` disables an AC test while satisfying the presence
gate.

**Remediation.** Extend the AST scan: (1) module-level `pytestmark`
assignments (single mark or list) containing `skip`; (2) class
decorator lists for AC tests defined in classes; (3) top-of-body bare
`pytest.skip(...)` calls (an `ast.Expr` call whose attr chain ends in
`skip`, only when unconditional at function top level); (4) `skipif`
with a constant-true first argument (`ast.Constant(True)` — literal
only; no expression evaluation). Keep runtime-conditional `skipif`
legitimate, as designed.

**Tests.** Planted-violation fixtures for each of the four evasions in
`test_ac_hook.py` (module-mark, class-mark, body-skip, constant-true
skipif) each producing a named violation; legitimate `skipif` fixture
stays unflagged.

**Contract.** None.

### F-M-T2 — `bench status` must not read healthy on a nonexistent directory

**Problem.** `status/aggregate.py:53-57` maps an absent ledger to
`chain OK (empty)`; `bench status <typo>` renders a plausible
"not yet planned" experiment named after the typo'd basename, exit 0.

**Remediation.** In `status/cli.py`, require `experiment_dir` to exist
(exit 2 with "no such experiment directory") before computing status. An
existing directory with no ledger legitimately renders the empty state
(that IS "not yet planned"). Apply the same guard to the serve tier's
directory resolution for consistency (**[DECIDE]** — serve already 404s
unknown experiments via its allowlist; verify and align).

**Tests.** Repro: `bench status /no/such/dir` exits 0 with `chain OK`
today — assert exit 2. Existing-empty-dir still renders the empty state
exit 0 (the documented "exits 0 describing state" property is preserved
for real directories).

**Contract.** CLI behavior change on the error path only.

### F-M-T3 — Add `author`/`review` to an LLM-free contract

**Problem.** `.importlinter` has no-LLM contracts for grade,
contamination detectors, forensics deterministic tier, and
status/serve — but `harness.author` (the pre-registration ceremony) and
`harness.review` (the blinded reviewer surface) could import
`harness.judge.client` without tripping anything. Both are LLM-free
today; the guardrail is missing, not violated.

**Remediation.** New forbidden contract
`authoring-and-review-llm-free`: sources `harness.author`,
`harness.review` → forbidden `harness.judge.providers`,
`harness.judge.client` (indirect included, matching contract 7's shape).
Extend the planted-forbidden-import proof test to cover it, keeping the
"contracts proven load-bearing" property.

**Tests.** Planted `from harness.judge.client import ...` in a review
module under the existing plant-and-run-linter harness → linter fails.

**Contract.** None (structural tooling only).

## Group O — other Mediums

### F-M-O1 — Groundwork grader is a silent production no-op

**Problem.** `grade/plugins/groundwork.py:31-34` returns
`(task.fake_plugin_output or {}).get("rules", [])`; the production
branch is a comment. `_grade_tasks_from_dicts` (`grade/cli.py:33-40`)
never sets `fake_plugin_output`, so a production task declaring
`plugin_ids=["groundwork"]` gets zero groundwork assertions with no
signal.

**Remediation.** Fail loud: when `fake_plugin_output` is absent and no
real groundwork tooling is configured, raise `PluginError` →
`cant_grade(plugin_error)` (the existing terminal path) instead of
returning `[]`. Implementing the real shell-out is a separate feature
(**[DECIDE]** whether to build it or keep groundwork fixture-only and
say so); either way the silent path dies. If fixture-only is chosen,
refuse `plugin_ids=["groundwork"]` at plan/admit time for production
corpora.

**Tests.** Repro: production-shaped `GradeTask` with
`plugin_ids=["groundwork"]` and no fake output currently grades with the
plugin contributing nothing — assert `cant_grade(plugin_error)`.

**Contract.** None (behavioral fail-loud on an already-terminal path).

### F-M-O2 — Per-queue reveal enforcement

**Problem.** All reveal/verdict gates are keyed by single
`comparison_id` (`review/record.py:86-199`); a reviewer can reveal item
1 (seeing arm identities) and then record "blinded" verdicts for items
2..n. No queue entity exists — `select_for_review`
(`review/sample.py:115-139`) computes the reviewed set but never ledgers
it as a unit.

**Remediation.** Ledger the queue: a `review_batch` event (additive new
kind) carrying `batch_id` + the selected `comparison_ids` (+ seed and
strata for auditability), emitted by the selection step;
`reveal_comparison` gains a batch check — refuse any reveal while any
comparison in the same batch lacks a human verdict. Comparisons outside
any batch (legacy) keep per-item semantics, disclosed in the calibration
render.

**Tests.** Repro: two-item queue, verdict item 1, reveal item 1, verdict
item 2 — all succeeds today; assert the reveal is refused until both
verdicts exist. Legacy no-batch path unchanged.

**Contract.** New event kind + a reveal-gate semantics change
(approval). Additive: old chains have no batches and keep old semantics.

### F-M-O3 — Missing transcript must be CANT_SCORE, not scored-as-empty

**Problem.** `process/cli.py:27-36` promises "an empty transcript scores
fail-closed, never a fabricated one," but `score_trial_process`
(`process/score.py:180-263`) has no empty-transcript guard: the judge is
asked to score an empty string and fabricates per-dimension scores. The
docstring and the code disagree; the code is wrong.

**Remediation.** Guard at the top of `score_trial_process`: empty or
whitespace-only transcript → terminal
`CANT_SCORE(missing_transcript)` (new reason value on the
`CantScoreReason` enum), one event, no provider call.

**Tests.** Repro: trial with no `transcript.txt` currently produces real
dimension scores — assert `cant_score` with the named reason and zero
provider calls (spy).

**Contract.** New enum member on `CantScoreReason` (event field value —
additive; approval). Keep `provider_failure_reason` mapping in sync.

### F-M-O4 — `card emit --format md` crashes on asymmetric contamination

**Problem.** `card.py:293-297` does
`", ".join(contam.get("asymmetric", []))` but `asymmetric` is a list of
dicts (`contamination/summary.py:83-90`:
`{task_id, flagged_arms, unflagged_arms}`) → `TypeError` whenever an
asymmetric flag exists — and the crash is outside the `CardError`
handler (`analyze/cli.py:236-240`), so it's a traceback, not exit 2.

**Remediation.** Render each entry with the same phrasing report.py
already uses (`_asymmetry_line`, `report.py:1480-1486`); extract that
helper to a shared location or duplicate its format string with a
cross-reference comment. Wrap the render dispatch in the existing
CardError→exit-2 envelope so *any* future render error is a clean
refusal.

**Tests.** Repro: card fixture with a seeded asymmetric probe →
`render_card_markdown` raises TypeError today; assert it renders the
task/arm detail. CLI: same fixture through `card emit --format md` exits
cleanly.

**Contract.** None (render-only; note an asymmetric probe also blocks
*official* renders, so the fixture uses exploratory mode).

### F-M-O5 — HTML card must carry the disclosures the markdown card carries

**Problem.** `render_card_html` (`card.py:310-367`) has no Disclosures
section: confounds, contamination, forensic quarantines, and excluded
metrics — all present in the markdown card (`card.py:289-301`) — are
silently dropped from the HTML artifact. For a citable card, a
disclosure-free variant is the more shareable and therefore more
dangerous one.

**Remediation.** Add the Disclosures section to the HTML renderer with
content parity (same fields, same refusal-to-omit), built through the
same escaping discipline as the rest of the HTML card. Add a parity test
that walks `card["disclosures"]` and asserts each item's content is
present in both renders (the honest guarantee is structural parity, not
byte parity).

**Tests.** Repro: card with quarantines + confounds — assert HTML
contains them (fails today); parity test as regression net.

**Contract.** Card HTML output changes; JSON schema untouched.

### F-M-O6 — `retrigger_baselines` (AC-6) has no production caller

**Problem.** `registry.py:223-234` clears stale baselines after a semver
bump — invoked only by a test. The enforcement half of AC-6
(`check_semver_mutation`) is wired; the invalidation half is not: after
a real bump, changed tasks ride their stale baselines.

**Remediation.** Call `retrigger_baselines(previous)` on the production
bump path — wherever a new manifest version is produced from a previous
one (`corpus/public.py:154-158` is the seam that already copies
`baseline_ref` with a comment about re-validation). If no CLI bump verb
exists, this folds into F-H2's verb work as the bump-path integration
(**[DECIDE]** ordering; recommendation: land with F-H2).

**Tests.** Repro: bump a manifest with a changed task sha through the
production path — `baseline_ref` survives today; assert it is cleared
and status demoted, and that `bench corpus admit` then refuses until a
new baseline runs.

**Contract.** None (behavioral wiring of an existing tested method).

### F-M-O7 — Admission content check hashes a projection, not the file

**Problem.** `admit.py:123-144` verifies
`content_sha({workspace_ref, prompt, holdouts, groundwork_rules})` —
any field outside the four-key projection is unreviewed-but-admittable;
and the check runs only when `candidate_content` is supplied at all.

**Remediation.** (1) Extend `content_sha`'s projection to cover every
semantically-live candidate field (or, stronger, hash the canonical
serialization of the whole candidate minus volatile bookkeeping —
**[DECIDE]**: changing `content_sha` changes every `task_sha`, a
versioned identity; recommendation: introduce `content_sha_v2` used for
newly-mined candidates, keeping v1 verification for existing shas, and
record the version on the manifest entry). (2) Refuse projection-check
skips for production admission: make `--candidate-json` required on the
CLI unless an explicit `--no-content-check` is passed and ledgered in
the admission event.

**Tests.** Repro: candidate file with a mutated non-projected field
admits today under the approved sha — assert refusal under v2; v1 tasks
still verify; the no-content-check escape is ledgered.

**Contract.** Content-hash identity change (major — approval +
version-tagged migration); additive admission-event field for the
escape hatch.

### F-M-O8 — Anchor store: lock+fsync writes, tolerate corrupt lines, fail closed on empty

**Problem.** `ledger/anchors.py:62-67` appends with no flock and no
fsync (torn/interleaved lines possible — the ledger side is fsync'd,
the anchor side is not); `verify_against_anchor:92-132` crashes on a
corrupt line (`json.loads`/`rec["height"]` unguarded at `:114-115`) and
returns `ok=True, "0 anchor(s) verified"` on an **existing-but-empty**
store — an attacker who truncates the anchor file converts the
cross-check into a pass.

**Remediation.** (1) `write_anchor`: exclusive `flock` + `flush` +
`os.fsync` before close, matching the ledger append discipline.
(2) A corrupt/torn anchor line → `AnchorResult(False, "anchor store
corrupt at line N: …")` — a verdict, not a crash (the audit verb must
always answer). (3) Empty-or-blank store → `AnchorResult(False,
"anchor store exists but contains no anchors")`; with `checked == 0`
after a non-empty parse, likewise refuse rather than vacuous-pass
(**[DECIDE]** whether height-0-only stores count as empty;
recommendation: yes — zero *checked* anchors is the fail-closed
condition).

**Tests.** Repros: (a) truncate store to empty → `ok=True` today,
assert `ok=False`; (b) plant a torn line → crash today, assert a False
verdict naming the line; (c) concurrent-write interleave is hard to test
deterministically — test that the written bytes are flushed/synced via
an fsync spy instead.

**Contract.** None (the anchor file format is unchanged; verification
gets stricter — call out in release notes since a previously-"passing"
empty store now fails, which is the point).

# Part III — Low / Info findings

The Lows are deliberately spec-lite: each is a small, self-contained
change whose design is fully determined by the one-line remediation. Any
Low whose fix turns out to touch a versioned contract gets promoted to a
full spec before implementation. Rows are grouped; "Type" says what kind
of change it is.

| # | Finding (evidence) | Remediation | Type |
|---|---|---|---|
| L1 | `bench judge` has no `--actor`, defaults events to `"local"` (`judge/cli.py:24-27`), contradicting the README | Add `--actor` with the shared `_resolve_actor_or_exit` pattern; refuse rather than default, matching every other ledgering verb | code |
| L2 | Dead-in-production: `judge/calibrate.py` raw-pooled kappa superseded by the IPW seam | Delete the module (or fold any still-referenced helper into `review/kappa.py`); no dead code per CLAUDE.md | cleanup |
| L3 | Dead-in-production: pristine-diff attribution branch reachable only from tests (`forensics/scan.py` passes `pristine_files={}`) | Either wire pristine files from the materialized task content or delete the branch and its tests; the half-alive state is the defect | cleanup + [DECIDE] |
| L4 | Dead `policy` param in `score_trial_process` | Remove the parameter | cleanup |
| L5 | Redaction cannot catch base64/URL-encoded/reversed key forms (own-arm artifact only) | Disclose in `deep-dive.md` beside the redaction claims; optionally add encoded-form scans for the highest-entropy keys | docs (+opt. code) |
| L6 | Undisclosed detector FP/FN classes (deletion-as-insertion, path-representation mismatch nulling `holdout_tamper`, non-UTF-8 evasion, xfail omission at runtime) | Add a "known evasions" table to the forensics docs, mirroring the docstrings that already admit them; file follow-ups for the cheap ones (path normalization) | docs |
| L7 | `findings.json` unwatermarked and mode-ambiguous | Add `mode` and the standard watermark into the JSON document (additive field; new renders only) | code |
| L8 | Power-sim docstring mislabels a plain percentile bootstrap as "recentered-null" | Fix the docstring (or recenter, if the label was the intent — [DECIDE], recommendation: fix the label) | docs |
| L9 | Interleave reimplements `seeded_shuffle` inline | Call the shared helper; behavior-pinning test first since shuffle order is seed-visible | cleanup |
| L10 | Heavy fixed `waitForTimeout` sleeps in browser tests | Replace with condition-polling waits (`waitForSelector`/predicate) — flakiness, not correctness | tests |
| L11 | Three contract tests mutate live source files, restoring in `finally` (hard kill leaves the plant) | Copy the tree to `tmp_path` and plant there, or plant via an overlay module dir; never mutate the live tree | tests |
| L12 | `SeqGradeRunner` silently replays past exhaustion | Raise on exhaustion (test fixture honesty) | tests |
| L13 | Trial IDs use unseeded `uuid4` | Document as a designated identifier seam (uniqueness, not reproducibility) in the determinism section | docs |
| L14 | Typing gaps on ledger `path` parameters | Annotate (`Path \| str`), matching the repo's typed-Python convention | cleanup |
| L15 | `docs/adapters.md` documents trajectory schema v2; code is at v3 | Update the doc to v3 (`TRAJECTORY_SCHEMA_VERSION`, `run/trajectory.py:31`) and add the v2→v3 delta note | docs |

## Documentation truth-ups (from the audit's §7 roadmap)

Cheap, high-trust-yield; each is one PR: the flake-baseline operating
characteristic (F-H2 test plan produces the numbers); the "LLM-free
contract" wording aligned to the transitively-scoped contract it is; the
detector FP/FN classes (L6); the F-H1 holdout-readability disclosure; the
F-M-C1(b) canary-derivation disclosure if option (b) is chosen.

---

# Appendix — cross-cutting implementation rules

These bind every spec above:

1. **Reproduce before fixing.** Every spec's test plan leads with a test
   that fails on current code. If the reproduction cannot be made to fail,
   the finding is wrong — stop and say so.
2. **Contract changes need explicit human approval** before code lands:
   F-H1 (grader-image interface), F-H2 (corpus model + baseline event),
   F-H3 (grade + forensics events), F-H5 (card schema), F-H7 (locked spec
   + render event + fence reason), F-M-J3, F-M-S1, F-M-C2, F-M-C3,
   F-M-O2, F-M-O3, F-M-O7. All follow the additive-optional field
   convention with legacy chains degrading to disclosed absence, never
   hard failure.
3. **Existing tests that assert the old behavior** (AC-1 argv shapes,
   the forensics coverage exact-set, `test_m4_*` flag plumbing) change
   *because the approved spec changes intended behavior* — each spec
   names them up front so the change is pre-agreed, not tampering.
4. **One event per operation** holds for every new verb/event
   (`corpus-baseline`, `review_batch`, judge-ceiling stop), registered in
   the property sweep's closed entrypoint set.
5. **`make verify` after every slice.** Specs are sliced so each lands
   green; no spec is "done" with a red intermediate state.
6. **Ordering.** F-H4 → F-H6 → F-H5 → F-H7 → F-H3 → F-H2 → F-H1, then
   Mediums by group (J, S, C, I, T, O), then Lows/doc truth-ups —
   credibility-per-unit-effort, matching the audit's §7.

