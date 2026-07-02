# verdi-bench implementation review — vs. 00-EVAL-1 master plan

**Date:** 2026-07-02 · **Scope:** M0 + EVAL-3, EVAL-4, EVAL-5, EVAL-2 (the four built stories) · **Method:** full test/contract verification, five independent deep-review passes (one per story + plan conformance), every critical/major finding re-verified by hand against the code.

## Verdict

The suite is green (146 passed, 3 import-linter contracts kept, AC-1..9 reported) and the *architecture* faithfully follows the plan: single ledger write path with stamped provenance, sha-locked specs, closed metric enum, one blinding codepath with separate identity/secret lists, namespaced deterministic seeds, and real seams for the provisional decisions (D006/D007/D008). The core algorithms verified clean: hash-chain append/verify, Fisher–Yates interleave, CRN power-curve monotonicity, order-swap position-bias handling, telemetry null-mirroring.

However, the implementation is **not production-correct**. Two systemic patterns account for most of the serious findings:

1. **The fake/local path is built and tested; the real path is broken or unreachable.** Docker grading, the Harbor engine, provider HTTP clients, and egress scanning all have holes the 146-test suite structurally cannot see, because no `docker`-marked test exists anywhere (the README claims they do).
2. **Library seams exist but CLI wiring is missing.** Quarantine, the judge stage, proxy/provider-key config, the anchor ledger event, and the escalation config all work when a test passes the kwarg — and are absent from the shipped `bench` verbs.

---

## Critical

### C1. Grading trusts an agent-writable file — reward-hacking channel
- `harness/grade/cli.py:76` hardcodes `LocalGradeRunner` with no flag to select the Docker runner. Production `bench grade` therefore never executes holdouts; it reads `<workspace>/holdout_results.json` — a file in the trial workspace **the subject agent wrote to**. An agent that writes an all-pass results file grades itself perfect.
- The Docker path has the same hole (`harness/grade/container.py:45-50`): it reads the same workspace file with no pre-clean, no freshness check, and treats only exit 125 as failure — a grading container that crashes with exit 1/137 falls through to scoring a stale or agent-forged file.
- No workspace copy is made despite the module docstring promising one (`container.py:5`); grading mounts the original trial workspace **rw**, mutating ledgered trial evidence that judge packets and human review later consume.

### C2. The cost ceiling (EVAL-1-D007) is enforced per-process, not per-experiment
- `harness/run/cli.py:82-91` + `harness/run/interleave.py:61`: every `bench run` invocation creates `CostGuard(accumulated=0.0)` and nothing reads prior `trial`/`run_stopped_cost_ceiling` events. Re-running after a ledgered ceiling stop restarts spend from $0 — and re-executes the *entire* schedule with fresh trial ids, duplicating completed trials.
- Null-cost arms are invisible: the codex adapter returns `cost=None` unconditionally, `guard.add(None)` is a no-op, and `EngineResult.proxy_metered_cost` is never fed to the guard (`seam.py:106-108` keeps it only as a delta flag). A claude-vs-codex run can burn arbitrarily past the ceiling on the codex arm.
- Infra-rerun attempts (up to 4 per planned trial) bypass the guard check and their spend is never accumulated (`interleave.py:106-141`).

### C3. The judge is not outcome-blind
`harness/judge/packet.py:33,60-63` renders per-response holdout pass/fail results verbatim into the judge message. Holdout results *are* the primary-metric outcome (`holdout_pass_rate`), so `judge_preference` is mechanically correlated with the deterministic grade, and judge/human kappa "earns authority" partly by re-reading the grade rather than contributing independent signal. The plan (§1) defines the judge as outcome-blind. **Caveat:** the packet docstring cites the EVAL-2 spec (AC-2/D002) as allowlisting holdout results — if the spec genuinely does, the spec and the plan-level invariant are in conflict and that conflict should be resolved in the decisions ledger, not silently shipped.

### C4. The fail-closed "one event per stage attempt" invariant (§7.2) has multiple escape hatches
Verified paths where an attempted operation ends with **zero** ledger events:
- `harness/judge/client.py:101` — `get_provider` runs outside every try/except; an unknown provider prefix (e.g. `mistral/...`, legal per D001) raises with no `CANT_JUDGE` event. It is also a de-facto 3-vendor allowlist, in tension with AC-1.
- `harness/judge/providers/{openai.py:18, google.py:19, anthropic.py:28}` — error-shaped/safety-blocked responses raise `KeyError`/`IndexError` that `judge_pair` doesn't catch (verified live).
- `harness/grade/container.py:53` — on the Docker path, malformed holdout JSON raises bare `ValueError`, which `grade_trial`'s handlers don't catch; the exception aborts `bench grade` mid-loop with no event.
- `harness/grade/cli.py:84-90` — trials with an unknown `task_id` or missing `artifacts_path` are silently `continue`d (no `cant_grade`).
- `harness/run/interleave.py:79-80,66` + `seam.py:97` — `KeyError` (unknown task/arm), `QuarantinedTaskError`, `HoldoutLeakError`, and post-engine `UnknownPlatformError` all escape `schedule()` mid-loop, skipping both `trial_infra_failed` and the `executed_order` event (AC-4), and skipping artifact redaction for the failed trial.

---

## Major

### Run stage (EVAL-4)
- **Quarantine unwired**: `schedule()` honors `quarantined_tasks`, but `bench run` never calls `load_quarantine()` (`run/cli.py:82-91`), so flake-quarantined tasks run in production. The test passes the kwarg explicitly, masking this.
- **Redaction fails open on unknown suffixes** (`run/redact.py:51`): anything not on the `_SCANNED_SUFFIXES` allowlist is skipped — `.bak`, `.env.local`, `.out`, `.tsx`, `dump.log.1` persist secrets unredacted. The barrier should skip known binaries and scan everything else.
- **PEM bodies survive redaction**: the private-key pattern matches only the `-----BEGIN ... PRIVATE KEY-----` header (`blind/core.py:116`); the key material persists, minus the marker downstream scanners would grep for.
- **Injected key values never redacted as literals** (`run/seam.py:94`): `config.provider_keys` values aren't added to the redaction patterns, so any key whose *shape* isn't in `_SECRET_PATTERNS` (Stripe `sk_live_`, `hf_`, JWTs, internal tokens) persists verbatim.
- **Timeout kills the docker CLI, not the container** (`engines/harbor.py:77-81`): after `TimeoutExpired` the container keeps running — making paid API calls and writing to the workspace *after* `redact_artifacts` already ran.
- **Egress detection only parses the fake engine's log format** (`harbor.py:183-198`): `trial={id}` + `DENY`-prefixed lines. A real proxy log matches nothing → real egress violations ledger as `egress_violation=False`. Nothing creates/verifies the `verdi-metered` network either.
- **Image pinning unenforced** (`harbor.py:129-135`): a tag-only ref proceeds (pull-whatever semantics, `image_digest=None` in provenance) instead of being refused per D005; no `--pull=never`.
- **`bench run --engine harbor` cannot execute a real trial**: `RunConfig` is built with no proxy, no provider keys, no spec quotas (`run/cli.py:75`) → `--network none`, no credentials. Fails closed, but the plan-required metering-proxy path (AC-3) and key injection (AC-8) are unreachable from the CLI.
- **Infra-failure reason comes from a test-only field** (`interleave.py:126`): the ledgered reason reads `task.fake_behavior["infra_reason"]`; `EngineResult` has no failure-reason field, so the real engine can never report a true reason.

### Plan/lock stage (EVAL-3)
- **The power gate never consults the actual design** (`plan/power.py:133` + `plan/lock.py:63-65`): N comes from `variance_source.n_tasks` (default 50); `spec.repetitions` and the real corpus size are ignored. A 10-task, 1-repetition design locks cleanly because the sim ran at a fabricated N=50; the ledgered MDE describes a hypothetical experiment.
- **Lock TOCTOU** (`plan/lock.py:61` vs `:89`): the spec is parsed, a multi-second simulation runs, then the file is *re-read* for hashing. An edit during the sim locks the sha of a never-validated, never-power-checked file. Fix: read bytes once, hash and parse the same bytes.
- **Re-lock not refused**: a second `bench plan` appends a second `experiment_locked` event and reports success, while `assert_lock` keys on `locks[0]` — the operator is told the mutated spec is locked when it isn't, and "genesis" (§7.1) is broken. In the acknowledged-underpowered path the ack event also *precedes* the lock, so the lock isn't genesis even on first run, and `mde=None` is ledgered as `null` into a `float` field.
- **`bench anchor` writes no ledger event** (`cli.py:86-97`): `record_chain_anchor` exists for exactly this (D008) but is only called by tests — anchoring leaves no in-chain attestation, violating "no operation without a ledger event."

### Grade stage (EVAL-5)
- **Flake baseline records infra failure as flake evidence** (`grade/baseline.py:56-57`): `GradingContainerError` (daemon down, timeout) → `passed: False`; five docker hiccups permanently quarantine a healthy task version, ledgered as fact.
- **Baseline runs are not independent replicates** (`baseline.py:51-58`): the same rw workspace is reused across all k runs — stale `holdout_results.json` from run *i* can be re-scored as run *i+1*'s result, and holdout side effects leak between runs.
- **`k=0` ledgers `verdict: "clean"`** with zero evidence (no `k >= 1` validation); **quarantine is keyed by `task_id` with sha discarded** — a clean baseline for a new task version un-quarantines the old flaky version still being served.

### Judge stage (EVAL-2)
- **Degenerate kappa returns 1.0** (`judge/calibrate.py:41-42`): 20 all-A/all-A pairs → `kappa=1.0, sufficient=True` — the judge earns authority from a sample carrying zero chance-corrected information. A lopsided-but-real experiment auto-grants trust; standard tools return NaN/0 here.
- **`pairs_from_ledger` joins on `comparison_id=None`** (`calibrate.py:96-107`): verdicts without ids (the default!) pair with each other, fabricating kappa pairs; duplicate judge verdicts silently last-write-win; `CANT_JUDGE` enters kappa as an ordinary category.
- **Alias-id heuristic false-passes dotted versions** (`schema/judge_config.py:27`): `\d+\.\d+` accepts `google/gemini-1.5-pro`, `openai/gpt-4.1` — precisely the mutable server-side aliases AC-5 exists to reject (the docstring's own example says `-002` is required). Found independently by two reviewers.
- **Vendor-overlap confound false-negative** (`analyze/confounds.py:14-15`): `Arm.model` is free-form; a prefix-less arm id (`claude-3-5-...` without `anthropic/`) yields `overlap=False`, so the same-vendor confound goes undisclosed (AC-6).
- **Prompt-injection surface**: diffs are interpolated raw under the packet's own markdown headers with a one-line system prompt and no fencing (`packet.py:57-68`). A content-keyed injection ("any response containing X is superior") survives the AB/BA swap *consistently*, so D003's order-consistency check reads it as a legitimate win.
- **Nothing production-side invokes the judge**: no `bench judge` verb; per-experiment canary literals (arm names, model ids — §7.4's "surest tells") are never derived from the locked spec and passed to `judge_pair`; `EscalationConfig` (the D006 seam) is never fed into `kappa_by_class`, which re-hardcodes 0.6/20 — a config flip would not propagate.

---

## Plan-conformance gaps

- **EVAL-8 slice A is missing** despite plan §4 seq 2a ("Calibration corpus is needed alongside EVAL-4"): `harness/corpus/__init__.py` is empty, `CalibrationVariance` is an inert stub, so every lock is `assumption_based_mde`. The README discloses EVAL-8 as unbuilt but not the sequencing deviation.
- **No `docker`-marked test exists anywhere**, though the marker is declared, module docstrings reference docker-marked tests, and the README says "those tests are marked `docker`". The real grading container and harbor-inspect assertions have zero coverage — this is the root enabler of the C1/C4 class of bugs.
- **The AC-naming hook reports, it doesn't enforce** (conftest.py:36-46): `--ac-report` prints coverage; nothing fails on a missing/misnamed AC test. Plan §3.5 says "enforced by a collection hook."
- **One-event property registry has only 3 entrypoints** (plan-lock, run-trial, grade-trial); EVAL-2 registered no judge entrypoint, contradicting the registry's "later stories join automatically" design (compensated by EVAL-2's own AC-8 tests, but the program-wide sweep has a hole future stories will copy).
- **Vacuous tests**: `test_ac9_holdout_canaries_absent`'s prompt assert can never fail (disjoint hypothesis alphabets); `test_ac4_mde_computed` asserts `mde is None or mde <= 0.5` — always true.
- **`tasks.yaml` sits outside the sha-lock** (acknowledged EVAL-8 stand-in, but not even hashed into an event) — prompts/canaries swappable post-lock without detection.
- Python floor relaxed to 3.11 (documented, environment-driven — confirm as intentional debt).

## Notable minor issues (abbreviated)

Dead/misleading code: `Outcome.not_started_cost_ceiling` never constructed; `CostGuard.stopped` never set and its docstring claims it ledgers (it can't); `sk-ant-` pattern fully shadowed by `sk-`; `record_chain_anchor` production-dead. `contention_caveat` stamps from a `--concurrency` knob that does nothing (execution is strictly serial). Judge's parsed self-reported confidence is discarded and replaced with hardcoded 0.8/0.5. Google provider puts the API key in the URL query string (leaks into proxy logs; use the header). Validation logic duplicated between `_prevalidate` and pydantic validators (named-error contract lost via `model_validate`; copies can drift). No arm-name uniqueness and no `len(arms)==2` constraint for a paired A/B instrument. Decision-rule DSL admits `==` on a bootstrap float (a rule that can never fire). `_scan_proxy_log` can't distinguish concurrent trials. `.importlinter` source lists omit a few modules (covered today by an AST test that is cwd-dependent). Exhausted fake-provider script repeats its last response instead of failing — the exact regression trap for silently-added retries.

## Verified clean (worth stating)

Hash-chain canonicalization/append/verify (single-syscall write under flock, prev_hash under lock); tamper-evidence tests real (rewrite/delete/reorder caught); provenance envelope exact per AC-6 with reserved-key rejection and registry-refused unknown events; Fisher–Yates interleave unbiased and pure in `(seed, trials)`; CRN power-curve monotonicity sound; namespaced sub-seeds per §7.5; single blinding codepath honored with correctly-separated, case-sensitive secret list; telemetry null-mirroring enforced by model validator (§7.8); D003 order-swap→TIE logic correct; no silent retries anywhere; metric vocabulary a closed enum in one place; D006/D007/D008 all genuinely behind seams.

## Recommended priorities

1. **Close the grading integrity hole (C1)**: grade from a fresh copy of the workspace, delete/ignore pre-existing `holdout_results.json`, gate on container exit codes, and wire a runner flag into `bench grade`.
2. **Make the ceiling per-experiment (C2)**: rebuild `CostGuard` state from the ledger at `bench run` start; skip already-executed trials; count proxy-metered cost when telemetry cost is null; check the guard inside the infra-rerun loop.
3. **Resolve the outcome-blindness conflict (C3)** against the EVAL-2 spec and decisions ledger; if the judge must be outcome-blind, drop holdout results from the packet.
4. **Audit every stage entrypoint for the §7.2 invariant (C4)**: wrap stage bodies so any unexpected exception ledgers a CANT_*/infra event before propagating; move `get_provider` and provider parsing inside the fail-closed envelope; make `bench grade` emit `cant_grade(task_unknown|artifacts_missing)`.
5. **Wire the missing CLI paths**: quarantine into `bench run`, `record_chain_anchor` into `bench anchor`, refuse re-lock, fix the lock TOCTOU (hash the bytes you parsed), and feed real N (`repetitions` × corpus size) into `mde_check`.
6. **Add the first real docker-marked tests** and make the AC hook enforce, so the fake/real gap can't silently regrow.
