# verdi-bench — Phase 2 plan: a real execution path

**Date:** 2026-07-03 · **Follows:** Phase 1 (merged, PR #6) ·
**Source of record:** `verdi-bench-review-consolidated.md` §5 Phase 2 + §3.2 (Run),
§3.1 (baseline: GR-8/9/10), §6 (readiness gate).
**Branch:** `claude/verdi-bench-phase-2-plan-g4t7k9`.

## Context

Phase 1 made results *integrity* real: the chain is verified at every stage
entry, the lock is TOCTOU-free and single-genesis, tasks are content-committed
into the lock, and grading runs from a fresh workspace copy behind a
`--runner {local,docker}` flag with a real transient/terminal `cant_grade`
taxonomy. What Phase 1 did **not** touch is the thing that produces the evidence
in the first place: `bench run --engine harbor` still cannot run a genuine
trial. The systemic diagnosis §2.1 stands verbatim in the current tree —
**the fake path is built and tested; the real path is broken or unreachable.**

Phase 2 is the review's "a real execution path" phase: make
`bench run --engine harbor` able to run a real, hermetic, metered, redacted
trial, and make the cost guard, quarantine, and baseline honest. This is heavy
Docker-integration work and it needs D-2 confirmed plus three new design
decisions (the trial-image contract, the operational run-config source, and the
proxy attribution mechanism) before the real-path slices land.

### Re-verification against the current tree (not `01641cd`)

The consolidated review was validated at `01641cd`, *before* Phase 1. I
re-located every Phase 2 finding against the current working tree. All of the
Run findings and the baseline findings reproduce at the (relocated) lines below;
Phase 1 shifted the surrounding code but closed none of them. Two Phase-1
interactions matter for scoping:

- **D-6 landed as the lightweight `task_commitment`, not the full manifest.**
  `bench run` reads `tasks.yaml` via `corpus/commit.load_task_dicts` and verifies
  `assert_task_commitment` (`run/cli.py:58-70`); it does **not** yet consult a
  `CorpusManifest`. So `CorpusManifest.is_schedulable` (CO-2) stays a **Phase 4**
  item, exactly as `commit.py`'s own coverage-boundary docstring states. Phase 2
  wires the *flake* quarantine (`load_quarantine`, RN-5), which is a distinct
  ledger-derived gate.
- **Grading already fresh-copies** (`grade/container.py:146-190`), so the
  baseline's GR-9 ("independent replicates") is *mostly* satisfied for the docker
  runner already — Phase 2 confirms/locks it with a test rather than rebuilding
  it, and the `GraderUnavailableError` transient class Phase 1 added is the seam
  GR-8 needs.

## Decisions

### Carried forward (resolved in Phase 1, constrain Phase 2)

- **D-6 boundary.** The corpus manifest as run/grade task source, and
  `is_schedulable` consulted by `bench run`, are **Phase 4** (the cache does not
  yet store holdouts). Phase 2 does not pull them forward. The flake-quarantine
  ledger gate (RN-5) is independent and is in scope.
- **EVAL-4-D004 (telemetry null-not-estimated).** Unmeasurable telemetry stays
  `null` in the *record*, never imputed, proxy only a cross-check delta. Phase 2
  respects this for the record; see the flagged judgment call below on the
  *guard*.

### To confirm at the start of Phase 2 (recommendation + trade-offs stated)

These are direction-setting; per CLAUDE.md they get explicit human resolution
before the owning slice, and each is recorded as a `resolved` event in the owning
`docs/design/specs/evalN.decisions.ndjson` cross-referenced to the review.

- **D-2 (GR-10) — quarantine keying: recommend `(task_id, task_sha)`.**
  The EVAL-5 spec is explicit — AC-2 "quarantines *that task version* … ledgered
  with the task sha"; the constraint reads "a task version cannot be scheduled
  without a clean ledgered flake baseline." Current `load_quarantine`
  (`baseline.py:75-91`) keys by `task_id`, latest-event-wins **across versions**,
  so a clean baseline for a *new* version silently un-quarantines the *old* flaky
  one. Recommend keying quarantine by `(task_id, task_sha)`.
  *Blast radius (why this needs sign-off):* (a) `load_quarantine` returns
  `set[tuple[str,str]]`; (b) the scheduler must know each planned task's version
  sha — add `task_sha` to the run-side `Task` (populated from the shared
  `corpus.commit.task_content_sha`, so run and grade agree on the version id) and
  check `(task_id, task_sha)`; (c) **one existing test changes** —
  `test_ac2_new_clean_baseline_clears_quarantine` (`test_eval5_baseline.py:65-76`)
  currently asserts a *different-sha* clean baseline clears the old quarantine,
  which is the GR-10 bug encoded as intent. Under D-2 it is rewritten: a
  *same-sha* clean re-baseline still clears (a genuinely fixed flake), a
  *different-sha* clean baseline leaves the old version quarantined. Per
  CLAUDE.md "changing a genuinely wrong test requires saying so explicitly and
  getting human agreement first" — this is that ask.
  *Alternative:* keep `task_id` keying and amend the spec. Not recommended — it
  contradicts pre-registered spec text and lets a re-mined task launder a flaky
  predecessor's quarantine.

- **D-8 (RN-4, NEW) — trial-image contract: recommend a read-only mounted
  `request.json` outside the workspace.** Harbor's `build_run_command`
  (`harbor.py:99-127`) ends `… {image}` with no channel carrying `prompt`,
  `arm.model`, or `arm.payload`, so a real trial cannot know its task and the A/B
  arms are indistinguishable inside the container. This needs a *contract* — a
  public seam between harness and trial image, so it wants a decision.
  Recommend: the harness writes `{prompt, model, arm, payload}` to a host temp
  file and bind-mounts it **read-only** at a fixed path (`/verdi/request.json`),
  *outside* `/workspace`; the pre-baked image entrypoint reads it. Rationale:
  keeps the request off the argv/`docker inspect` and off the graded workspace
  (a workspace-internal file would pollute the graded copy), agent can read but
  not mutate, and it mirrors the holdouts-mounted-ro pattern grading already
  uses. Arm identity *inside* the container is fine — insulation is about the
  judge/human not seeing identity and the agent not seeing holdouts/rubric, never
  about hiding the agent from itself.
  *Alternatives:* env vars (`VERDI_PROMPT`/`VERDI_MODEL`) — simpler but leaks into
  `docker inspect`, size-limited, awkward for multiline prompts; a
  workspace-internal file — pollutes grading. Recommend the ro mount; the choice
  is a stable contract, so it is documented and versioned even though greenfield.

- **D-9 (RN-13, NEW) — operational run-config source: recommend a run-config
  file + env for secrets; never the locked spec or the ledger.** Building a real
  `RunConfig` needs proxy URL, provider keys, quotas, allowlist. Keys must never
  enter `experiment.yaml` (sha-locked, pre-registered) or the ledger [AC-8].
  Recommend: proxy/allowlist/quotas come from a `run.config.yaml` (or CLI flags)
  resolved at `bench run`; provider-key **values** come from the process env by
  name (never written anywhere), matching the existing "NAME on argv, value from
  env" injection in `harbor.py:117-122`. Quotas are pinned identically for both
  arms [D003] and recorded in provenance (already are). Keeping quotas *out* of
  the locked spec is deliberate: they are operational, not part of the
  pre-registered comparison, and pinning-by-recording in provenance is what the
  spec's AC-6 verifies.
  *Alternative:* add quota fields to `experiment.yaml`. Rejected — bloats the
  pre-registered contract with operational knobs and invites lock churn.

- **D-10 (RN-11, NEW) — proxy attribution + log format: recommend per-trial
  proxy-auth token + structured JSONL, emitted by both engines.** Egress
  detection today parses only the fake engine's `DENY {host} trial={id}` string
  (`harbor.py:182-198`) and nothing creates/verifies the `verdi-metered` network.
  A real metering proxy needs (a) per-trial attribution that does not depend on
  the agent cooperating, and (b) a real log format. Recommend: the harness injects
  `HTTP(S)_PROXY=http://<trial_id>:<token>@proxy:port`, so the proxy sees the
  trial id in the CONNECT credential and stamps every line; the proxy writes
  structured JSONL `{"trial","host","decision":"allow|deny","ts",...}`; the
  `verdi-metered` docker network is created/verified to reach only the proxy; and
  `_scan_proxy_log` parses that JSONL keyed on `trial`. The **fake engine emits
  the same JSONL** so one parser serves both engines (the seam's whole point).
  *Alternative:* per-trial network + proxy instance (stronger isolation, heavier
  setup). Recommend the auth-token approach for Phase 2; note per-trial network
  as a future isolation hardening.

### Judgment call flagged for cheap veto

- **Cost guard may consume `proxy_metered_cost` for *enforcement* when telemetry
  cost is null — this does not violate D004.** RN-2 wants the guard to see spend
  from null-cost arms (codex returns `cost=None`, `adapters/codex.py:25`). D004
  forbids *imputing* a null into the telemetry **record**. The guard is a budget
  *safety* mechanism, not a recorded metric: feeding it the proxy's metered cost
  for enforcement leaves `telemetry.cost=null` in the record (still flagged in
  `telemetry_nulls`, delta still surfaced) while preventing runaway spend on an
  arm that cannot self-report. I read enforcement ≠ measurement, so this is
  in-bounds. If you read D004 as "the proxy figure may touch nothing budget-
  bearing either," veto and the guard stays conservative (null = $0, spend
  invisible) — say so and I'll leave RN-2's guard half out.

## Phasing within Phase 2

Ordered so the decision-free hardening lands first (immediately on approval),
then the D-2 baseline slice, then the real-path core (needs D-8/D-9/D-10), then
the docker-marked exit test. Each slice is one logical change (1–2 atomic
commits), each ships a reproduce-first failing test, and `make verify` is green
before every commit. Line numbers are the current tree.

### 2A — redaction hardening · RN-6, RN-7, RN-8, RN-9, RN-16 · P1 (no new decision)
The sole write barrier between raw capture and persisted artifacts fails open.
- **Scan-all-except-known-binaries (RN-6):** `redact.py:18-23,51` uses a suffix
  *allowlist* — `.bak/.out/.tsx` unscanned, and `.env.local` has
  `Path.suffix == ".local"` so the env family leaks. Invert to a **known-binary
  denylist** (`_BINARY_SUFFIXES` already exists at `:24-27`): scan every file
  whose suffix is not a known binary.
- **Whole workspace, not just artifacts (RN-7):** `seam.py:94` redacts only
  `result.artifacts_dir`; Harbor mounts the whole workspace rw with injected keys
  in env, and grade reads the workspace. Redact the trial **workspace** (minus
  the ro request mount from D-8) before it persists, not just `artifacts/`.
- **Full PEM body (RN-8):** `blind/core.py:116` matches only the `-----BEGIN …-----`
  header; the key body survives. Match through `-----END … PRIVATE KEY-----`
  (multiline, non-greedy).
- **Provider-key values as literal patterns (RN-9):** `config.provider_keys`
  values are never added to the scrub set (`seam.py:94` passes only
  `redact_extra_patterns`, `types.py:106-107`). Add each injected key **value**
  as a `re.escape`'d literal pattern so a key whose *shape* isn't in
  `_SECRET_PATTERNS` (Stripe `sk_live_`, `hf_`, JWT, internal tokens) still
  scrubs.
- **Fail loudly on unreadable files (RN-16):** `redact.py:55-56`
  `except OSError: continue` silently skips at the one barrier — raise a
  `RedactionError(path)` instead (a crash beats a silently un-redacted artifact).
- **Reproduce-first:** a workspace file `secrets.bak` / `.env.local` containing a
  key survives current redaction (fails), scrubs after; a PEM block's body
  survives current, scrubs after; an injected non-standard-shape key value
  survives current, scrubs after; an unreadable file raises rather than skips.
  Extends `test_eval4_redaction.py` (which already covers yml/toml/non-utf8).

### 2B — cost-guard correctness & resume · RN-1, RN-2, RN-3 · P0 (no new decision)
The ceiling is per-process with three bypasses.
- **Rebuild from the ledger + skip executed cells (RN-1):** `interleave.py:61`
  starts a fresh `CostGuard(accumulated=0.0)` and re-executes the whole schedule
  with new trial ids. Seed the guard from prior `trial` events'
  `trial_record.telemetry.cost`, and build a resume set of
  `(task_id, arm, repetition)` cells that already have a completed/timeout `trial`
  event; skip those cells in `derived_order`. Result: a re-run resumes rather than
  duplicates, and a ceiling-stopped re-run rebuilds spend at/over the ceiling and
  starts nothing (the ceiling is pre-registered/immutable — correct).
- **Count proxy cost when telemetry cost is null (RN-2):** the guard's
  `add(None)` is a no-op (`budget.py:20-22`) and `proxy_metered_cost` only feeds a
  delta flag (`seam.py:106-108`). Per the flagged judgment call, feed the guard
  the proxy-metered cost for *enforcement* when telemetry cost is null; the record
  is untouched.
- **Guard inside the infra-rerun loop + accumulate failed-attempt spend (RN-3):**
  `_run_with_infra_reruns` (`interleave.py:106-140`) retries up to 4× with no
  guard check and never accumulates a failed attempt's spend. Check the guard
  before each attempt and add any spend an infra-failed attempt still incurred.
- **Reproduce-first:** a two-trial fixture that stops at the ceiling, then a
  second `schedule()` on the same ledger executes **zero** new trials (today it
  re-runs both with fresh ids); a null-telemetry-cost + proxy-metered fixture
  crosses the ceiling and stops (today invisible → never stops); infra retries
  accumulate spend. Extends `test_eval4_cost.py`.

### 2C — fail-safe scheduling & honest infra reasons · RN-15, RN-14 · P1 (no new decision)
Per-trial exceptions escape `schedule()` mid-loop and the ledgered infra reason
is a fake-only field.
- **Wrap per-trial exceptions → `trial_infra_failed`, always land `executed_order`
  (RN-15):** `QuarantinedTaskError` (`interleave.py:66`), bare `KeyError` on
  unknown task/arm (`:79-80`), `HoldoutLeakError` (`seam.py:68`), and
  `UnknownPlatformError` (`get_adapter`, `seam.py:97`) all escape the loop, so
  `record_executed_order` (`interleave.py:102`) never runs and the trials already
  executed have no order record (AC-4 violated). Wrap each planned trial so a
  per-trial failure ledgers `trial_infra_failed` and continues, and land
  `executed_order` in a `finally`.
- **Failure reason from the engine, not `fake_behavior` (RN-14):**
  `interleave.py:126` reads `task.fake_behavior["infra_reason"]` — a FAKE-ONLY
  field, so real engines can only ledger the placeholder. Add
  `failure_reason: Optional[str]` to `EngineResult` (`types.py:73-91`); Harbor sets
  it (`daemon_error`, `grader_unavailable`, …), the fake engine sets it from
  `fake_behavior`, and the seam threads it onto the record so the scheduler
  ledgers `result`-derived reasons.
- **Reproduce-first:** a schedule where one task id is unknown / a canary leaks /
  a platform is unknown still emits `executed_order` and a `trial_infra_failed`
  with a real reason (today it raises and skips the order event). Extends
  `test_eval4_lifecycle.py` / `test_eval4_interleave.py`.

### 2D — baseline correctness + honor quarantine · GR-8, GR-9, GR-10, RN-5 · P1/P2 (needs D-2)
- **Transient ≠ flake (GR-8):** `baseline.py:56-57` catches
  `(GradingContainerError, ValueError)` → `passed=False`, so a single
  grader-unavailable hiccup quarantines a healthy task. Catch the transient
  `GraderUnavailableError` (Phase 1's subclass) separately: it aborts the baseline
  as "could not establish" (no clean, no quarantine verdict — honest), never a
  flake fact. Only a genuine holdout failure sets `passed=False`.
- **Independent replicates (GR-9):** confirm each of the k runs grades a fresh
  copy — the docker path already does via `container.run` →
  `_run_on_fresh_copy` — and lock it with a test that a run-`i` artifact cannot be
  re-scored as run `i+1`. Record per-run evidence richer than `{run, passed}` so a
  quarantine is auditable from the ledger (GR-13 rider, cheap here).
- **Require `k >= 1` (GR-10a):** `k=0` skips the loop and ledgers `clean` with zero
  evidence (`baseline.py:51`). Refuse `k < 1` loudly.
- **Version-scoped quarantine (GR-10b, D-2):** per D-2, `load_quarantine` returns
  `(task_id, task_sha)`; add `task_sha` to the run `Task`; the scheduler checks
  `(task_id, task_sha)`. Rewrite `test_ac2_new_clean_baseline_clears_quarantine`
  per the D-2 blast radius (with sign-off).
- **Wire `load_quarantine` into `bench run` (RN-5):** `run/cli.py:89-98` never
  passes `quarantined_tasks`; only the test does. Load it from the ledger and pass
  it. (The *producer* — corpus admission calling `flake_baseline`, which today has
  zero production callers — is Phase 4 / CO-8; Phase 2 makes `bench run` *honor*
  the ledger.)
- **Reproduce-first:** a transient grader outage does not quarantine (today it
  does); `k=0` is refused; a new-version clean baseline leaves the old version
  quarantined and the old version is unschedulable while the new is schedulable;
  `bench run` refuses a quarantined version end-to-end through the CLI. Extends
  `test_eval5_baseline.py`.

### 2E — RunConfig from spec/CLI + image pinning · RN-13, RN-12 · P1/P2 (needs D-9)
- **Build a real `RunConfig` (RN-13):** `run/cli.py:82` builds
  `RunConfig(engine, concurrency)` only — no proxy (→ `--network none`), no keys,
  default quotas. Per D-9, resolve proxy/allowlist/quotas from `run.config.yaml`
  (or flags) and provider-key values from env-by-name; call `egress.proxy_config`
  (zero callers today) to build the `ProxyConfig`. Metering (AC-3) and key
  injection (AC-8) become reachable from the CLI.
- **Refuse tag-only images; require digest; `--pull=never` (RN-12):**
  `harbor.py:131` proceeds with `image_digest=None` when `resolve_digest` returns
  None, and `build_run_command` has no `--pull=never`. Fail closed unless a digest
  resolves (violates D005 otherwise); add `--pull=never` so a trial never silently
  pulls an unpinned tag.
- **Reproduce-first:** `bench run --engine harbor` with a config produces a
  command carrying proxy env + key NAMEs + pinned quotas (unit-test
  `build_run_command`); a tag-only image with no resolvable digest is refused
  (today it runs with `image_digest=None`); `--pull=never` present. Extends
  `test_eval4_egress.py` and a new `test_eval4_harbor_command` unit test.

### 2F — trial-image contract: deliver prompt + arm config · RN-4 · P0 (needs D-8)
The single most fundamental real-path hole.
- Per D-8, write `{prompt, model, arm, payload}` to a host temp file, bind-mount
  it read-only at `/verdi/request.json` (outside `/workspace`), and document the
  image entrypoint contract. `build_run_command` gains the ro mount; the request
  file is excluded from the redaction sweep target (2A) and from the graded
  workspace copy (it is not under `/workspace`).
- **Reproduce-first:** `build_run_command` includes the ro request mount and the
  serialized request round-trips prompt + arm.model + payload (unit); a
  docker-marked test runs a minimal image whose entrypoint copies
  `/verdi/request.json` into an artifact and asserts the prompt + arm reached the
  container (today nothing carries them). Follows Phase 1's busybox-image pattern
  (`test_e2e_pipeline.py:88-136`).

### 2G — metering proxy, egress attribution, kill-on-timeout · RN-11, RN-10 · P1 (needs D-10)
- **Per-trial attribution + real log (RN-11, D-10):** inject the per-trial
  proxy-auth token, create/verify the `verdi-metered` docker network, have the
  proxy write structured JSONL, and rewrite `_scan_proxy_log` (`harbor.py:182-198`)
  to parse it keyed on `trial`; update the fake engine (`fake.py:43-49`) to emit
  the same JSONL so one parser serves both. Ship a minimal metering-proxy image
  for the docker-marked test (allowlist check + per-trial JSONL logging).
- **Kill the container on timeout, then redact (RN-10):** `run_container`
  (`harbor.py:76-81`) kills only the docker **CLI** on `TimeoutExpired`; the
  container keeps running and writing into the still-mounted workspace *after*
  redaction. Launch with a deterministic `--name` (derived from `trial_id`), and
  on timeout `docker kill` the named container and wait for it to die **before**
  the seam redacts, so redaction sees a final, static workspace.
- **Reproduce-first:** a non-allowlisted egress attempt through the real proxy
  produces a JSONL deny line attributed to the trial and an `egress_violation`
  flag (docker-marked); a timeout issues `docker kill <name>` and redaction runs
  only after the container is confirmed dead (unit via a fake runner that records
  call order + docker-marked). Extends `test_eval4_egress.py`.

### 2H — docker-marked end-to-end harbor trial + CI · Phase 2 exit · (integration)
A real end-to-end trial, the first to run a subject container:
- Real container reads its prompt/arm (2F), egress metered + a violation flagged
  through the real proxy (2G), a provider key injected as env and the injected
  literal redacted from captured artifacts (2A + 2E), image digest in provenance
  (2E), quotas applied (`docker inspect`).
- A ceiling-stop → re-run resumes rather than duplicates (2B); kill-on-timeout
  verified (2G).
- Gate the docker-marked suite in a labelled/scheduled CI job (CI runs
  `-m "not docker"` today, `ci.yml:21`); correct any README claims that outrun
  reality until the job is green (XC-7 rider).

## Phase 2 exit criteria (all testable)

Restating the review's §5 Phase 2 exit against the slices:

1. **A docker-marked end-to-end harbor trial passes** — real container, metered
   proxy, key injection, redaction of the injected literal (2H, depends on
   2A/2E/2F/2G).
2. **A ceiling-stop → re-run resumes rather than duplicates** (2B).
3. **Kill-on-timeout verified** — the container is dead before redaction (2G).
4. **A real trial knows its task and its arm** inside the container (2F).
5. **Quarantine is version-scoped and honored by `bench run`**; a transient
   grader outage does not quarantine a healthy task (2D, D-2).
6. **No per-trial exception escapes `schedule()`**; `executed_order` always lands
   (2C) — and the §6 "Cost ceiling declared and enforced" row flips (2B).

## Working method (per CLAUDE.md)

- **Reproduce before fixing:** every slice ships a test that fails first, passes
  after — no fixes by inspection.
- **`make verify` green** before each commit; never weaken/skip a test to get
  green. The `test_ac2_new_clean_baseline_clears_quarantine` rewrite (2D) is the
  one test that changes, and only with explicit D-2 sign-off.
- **Atomic commits**, one logical change; messages explain *why*.
- **Contract discipline:** D-8's trial-image contract and D-10's proxy log format
  are new public seams — documented and stable, with the fake and Harbor engines
  sharing one parser. No hash-chained/pre-registered contract changes in Phase 2
  (no new/edited ledger event schema; `failure_reason` rides an existing
  `trial_infra_failed` field). Add each confirmed decision (D-2, D-8, D-9, D-10)
  to the owning `evalN.decisions.ndjson` before its slice lands.
- **Single responsibility / boundaries:** fixes land in the subsystem that owns
  them; the `harbor-confined-to-seam` and `ledger-writes-only-via-events`
  contracts stay green (the CLI builds `RunConfig` and asks `get_engine` by name —
  it never imports Harbor). Completing the `.importlinter` source lists is Phase 6
  (XC-5), untouched here.
- Judgment calls (the guard/proxy-cost one above) are listed for cheap veto;
  direction-setting choices beyond D-2/D-8/D-9/D-10 get a check-in.

## Verification

- `uv run pytest -m "not docker" -q` green throughout (current post-Phase-1
  baseline **239 passed, 1 deselected**; the review's 210 predates Phase 1);
  Phase 2 adds reproduce-first tests per slice.
- `make verify` (full gate + import contracts) green before each commit.
- The new docker-marked suite (`uv run pytest -m docker`) exercises the real
  harbor path end-to-end (2H) — the first tests to run a *subject* container
  (Phase 1's docker test ran only a *grader* container).
- Manual end-to-end sanity: `bench plan → run --engine harbor → grade --runner
  docker` on a fixture experiment with local Docker; confirm the trial reads its
  prompt, a forced non-allowlisted egress is flagged, the injected key is redacted,
  and a re-run after a ceiling stop adds no trials.

## Scope of this approval

Approving authorizes executing **Phase 2 (2A–2H)** as atomic commits with
`make verify` green, and recording the four confirmed decisions (D-2, D-8, D-9,
D-10) in the decisions ledgers. 2A/2B/2C need no new decision and can start on
approval; 2D needs D-2; 2E/2F/2G need D-9/D-8/D-10 respectively. I'll report at
natural breakpoints and check in before Phase 3 (the fail-closed sweep). No PR
unless you ask.
