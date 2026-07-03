# verdi-bench consolidated review audit — validated findings and capability-readiness plan

**Date:** 2026-07-03 · **Supersedes:** `verdi-bench-review.md` (2026-07-02) and
`verdi-bench-review-update.md` (2026-07-02) — both retained as historical records.
**Scope:** all nine stories + M0, at commit `01641cd`.
**Method:** every critical/major/high finding in both prior audits independently
re-verified against the code (nine subsystem verification passes; all cited
lines re-located; key behaviors reproduced empirically — the wrong-corpus fence
bypass, the pooled/imputed judge preference, the byte-identical-reimport
calibration wipe, the path traversal, the alias-regex false-passes, and the
fail-closed escapes were each run live). Suite state at validation:
**210 passed, 3 import-linter contracts kept**.

Line numbers below are verified against the current working tree, not copied
from the prior audits.

---

## 1. Validation verdict on the prior audits

Both audits are substantially accurate: of roughly seventy-five distinct
findings, all but the items below verified **CONFIRMED** at the cited or
relocated lines. The suite-state claims (210/3), the "verified sound" lists,
and both systemic diagnoses reproduce. The corrections that matter:

| Prior finding | Correction |
|---|---|
| **C3 (first audit, Critical): "The judge is not outcome-blind"** | **Refuted as a code defect.** The EVAL-2 spec explicitly allowlists holdout results in the judge packet: AC-2 (`eval2.spec.md:43`) — "allowlist-built with exactly task prompt, workspace diff, **holdout results**, and rubric"; §Packet builder (`eval2.spec.md:176-180`) repeats it; decision **D002** (resolved by jyang) reads "outcomes-only: task prompt + workspace diff + holdout results + rubric … It should judge the results; it will never see the contestants." In this project's vocabulary "outcome-blind" means **identity-blind**. The code implements the spec correctly. What survives is a documentation/disclosure item (JD-1 below): the master-plan §1 wording should be aligned, and analysis must disclose that `judge_preference` is not statistically independent of `holdout_pass_rate` — by design. The first audit's recommendation to "drop holdout results from the packet" must **not** be executed. |
| EVAL-4 "escaping exceptions skip artifact redaction" | Wrong for every listed exception: `redact_artifacts` runs at `seam.py:94` **before** `get_adapter` (`seam.py:97`), and the other escapes fire before the engine produces artifacts. The escapes and the skipped `trial_infra_failed`/`executed_order` events are confirmed (RN-15); the redaction claim is dropped. |
| "no `len(arms)==2` constraint" | Partially wrong: `arms: list[Arm] = Field(min_length=2)` (`schema/experiment.py:104`) rejects 1-arm designs. What's missing is a cap and name uniqueness — 3 arms and duplicate arm names are accepted (PL-10). |
| "anthropic provider raises KeyError/IndexError" | The anthropic provider uses `.get()` throughout (`providers/anthropic.py:28-29`) and does **not** raise; error-shaped 200s become `CANT_JUDGE(parse)` — fail-closed, but the wrong machine-readable reason. Openai/google raise as claimed (JD-3). |
| "grade baseline: five docker hiccups quarantine" | **Understated**: `clean` flips on the first failed run (`baseline.py:59-62`), so a *single* infra hiccup quarantines a healthy task (GR-8). |
| "`_scan_proxy_log` can't distinguish concurrent trials" | As written the scan does key on `trial={id}`; the real defect is that only the fake engine emits that tag — a restatement of RN-11, not a separate parsing bug. |
| EVAL-9 "AC-5/AC-7 reporting is dead code" | Slightly overstated: `process_kappa_by_dimension` and `score_telemetry_correlation` are unit-tested; they are **production-unreachable** (no verb, no render), which is the operative problem (PR-5). |
| "docker marker declared (pyproject/conftest)" | Declared in `pyproject.toml:37-38` only; conftest declares no marker. Zero docker-marked tests confirmed (XC-1). |
| "`test_ac4_mde_computed` is vacuous" | True of `tests/test_eval3_power.py:20` (`assert res["mde"] is None or res["mde"] <= 0.5` — a tautology over the swept deltas). A second, non-vacuous test of the same name exists in `tests/test_eval3_lock.py:51` — itself a problem: duplicate AC test names defeat name-based coverage tooling (XC-2). |
| Second audit's "new commits touched only cli/events/power/confounds/query/builders" | File list incomplete (misses `analyze/report.py`, `analyze/cli.py`), but the operative conclusion holds: every first-audit finding site is byte-untouched by those commits, so "every first-audit finding still stands" was and remains correct. |

Everything else in both audits carries forward as confirmed. The consolidated
register in §3 is now the single authoritative findings list.

## 2. Systemic diagnosis

The prior audits' patterns all reproduce, and validation strengthened two and
added one:

1. **The fake path is built and tested; the real path is broken or unreachable — stronger than previously reported.** Beyond the audited holes, the grader image is pinned to a hardcoded all-zeros digest (`grade/container.py:83`) so Docker grading cannot start at all, and the Harbor engine never delivers the task prompt or arm configuration into the container (`engines/harbor.py:99-127`) — a real trial cannot know its task and the two arms are indistinguishable inside the container. No code path in this repo has ever executed a holdout in a container, and no docker-marked test exists to notice.
2. **Correct primitives, missing connective tissue.** Confirmed across all nine stories: no `bench judge`, `bench review build`, `bench process score`, or corpus-admission verb; quarantine, admission, calibration recording, escalation config, and the `CalibrationVariance` loader all have zero production callers.
3. **Fail-closed escape hatches.** Confirmed in every stage: attempted operations that end with zero ledger events exist in judge, grade, run, analyze, review, and process (register below).
4. **Statistics on the wrong population.** Confirmed and reproduced: pooled/imputed judge preference, fabricated-N null simulations, nominal instead of realized IPW weights, disagreement-biased pooled kappa, infra failures as flake evidence.
5. **NEW — the evidence chain is never actually consulted.** `verify_chain` has zero production callers outside `bench verify-chain`: `assert_lock`, the run/grade gates, corpus admission, and analyze all read the ledger as plain NDJSON (`plan/lock.py:103-124`, `ledger/query.py:46-48`, `corpus/admit.py:29-46`). A naive tamper — rewriting the lock line without recomputing the chain — passes every downstream gate. Ledger-adjacent state (corpus manifests, calibration status, quarantine) lives in mutable JSON with no chain anchor. The instrument builds tamper evidence and then never looks at it.

## 3. Consolidated findings register

Severity: **P0** — invalidates results or fakes an integrity guarantee; **P1** —
breaks a spec-required capability or a §7.2/blinding invariant; **P2** —
correctness/robustness defect; **P3** — minor/hygiene. Origin: A1 = first
audit, A2 = second audit, NEW = surfaced during this validation.

### 3.1 Grade (EVAL-5)

| ID | Sev | Origin | Finding (verified evidence) |
|---|---|---|---|
| GR-1 | P0 | A1-C1 | `bench grade` hardcodes `LocalGradeRunner` (`grade/cli.py:76`), which reads `<workspace>/holdout_results.json` (`grade/container.py:61-66`) — a file the subject agent can write. An all-pass forged file grades itself perfect. No flag selects the Docker runner. |
| GR-2 | P0 | A1-C1 | Docker runner: only exit 125 raises (`container.py:38-53`); exit 1/137 falls through to scoring a stale/forged workspace file. No pre-clean, no freshness check; `HoldoutRun.exit_status` stored but never consulted. |
| GR-3 | P1 | A1-C1 | No workspace copy despite docstring/spec promise (`container.py:3-5`, `eval5.spec.md:21`); workspace mounted **rw** (`container.py:79`; `test_eval5_container.py:24-26` asserts non-ro) — grading mutates ledgered trial evidence. |
| GR-4 | P0 | NEW | Grader image pinned to `"verdi-bench/grader@sha256:" + "0"*64` (`container.py:83`) — a nonexistent placeholder digest with no config hook. Even fully wired, Docker grading cannot execute. |
| GR-5 | P0 | NEW | `_load_grade_tasks` reads `fake_holdout_output`/`fake_plugin_output` from the experiment's `tasks.yaml` and writes it into the workspace pre-grade (`grade/cli.py:36-44, 93-96`). tasks.yaml is outside the lock (PL-7), so anyone who edits it scripts every grade. |
| GR-6 | P1 | A1-C4 | Malformed holdout JSON on the Docker path raises bare `ValueError` (`container.py:53`) that `grade_trial` doesn't catch (`deterministic.py:115-124`) → aborts `bench grade` mid-loop, no `cant_grade` event. |
| GR-7 | P1 | A1-C4 | Unknown `task_id` / missing `artifacts_path` silently `continue` (`grade/cli.py:84-90`) — no `cant_grade`, no warning, never regraded (not added to `already`). No `unknown_task` reason exists in the enum. |
| GR-8 | P1 | A1 | Flake baseline records `GradingContainerError`/`ValueError` as `passed: False` (`baseline.py:56-60`) — a **single** infra hiccup quarantines a healthy task, ledgered as flake fact. |
| GR-9 | P2 | A1 | Baseline reuses the same rw workspace across all k runs (`baseline.py:48-53`) — stale results from run *i* can be re-scored as run *i+1*; not independent replicates; contradicts its own "unmodified workspace" docstring. |
| GR-10 | P2 | A1 | `k=0` ledgers `verdict: "clean"` with zero evidence (no `k >= 1` validation). Quarantine keyed by `task_id` with sha discarded (`baseline.py:84-91`) — a clean baseline for a new version un-quarantines the old flaky version; contradicts the spec's "quarantines that task *version*" (`eval5.spec.md:29`) though tests present it as deliberate → needs decision D-2. |
| GR-11 | P3 | NEW | `cant_grade` is terminal: folded into `already` (`grade/cli.py:68-69`), so a transient daemon outage permanently blocks regrading with no override path. |
| GR-12 | P3 | NEW | `except Exception → actor="unknown"` around `getpass.getuser()` (`grade/cli.py:71-74`; same pattern `run/cli.py:76-79`) — silent swallow feeding ledger provenance. |
| GR-13 | P3 | NEW | Baseline evidence records only `{run, passed}` (`baseline.py:58`) — a quarantine verdict cannot be audited from the ledger alone. |

### 3.2 Run (EVAL-4)

| ID | Sev | Origin | Finding (verified evidence) |
|---|---|---|---|
| RN-1 | P0 | A1-C2 | Cost ceiling per-process: fresh `CostGuard(accumulated=0.0)` each invocation (`run/cli.py:82-91`, `interleave.py:61`, `budget.py:17`); no ledger read of prior `trial`/`run_stopped_cost_ceiling` events; a re-run after a ceiling stop restarts from $0 and re-executes the whole schedule with fresh trial ids. |
| RN-2 | P0 | A1-C2 | Null-cost arms invisible: codex adapter returns `cost=None` unconditionally (`adapters/codex.py:25`); `guard.add(None)` is a no-op (`budget.py:20-22`); `proxy_metered_cost` never feeds the guard and vanishes entirely when telemetry cost is null (`seam.py:106-108`). |
| RN-3 | P1 | A1-C2 | Infra reruns (up to 4 attempts) bypass the guard check and failed-attempt spend is never accumulated (`interleave.py:72, 106-140`). |
| RN-4 | P0 | NEW | **Harbor never delivers the prompt or arm configuration to the container**: `build_run_command` (`harbor.py:99-127`) produces `docker run … {image}` with no command/env/file carrying `request.prompt`, `arm.model`, or `arm.payload`. A real trial cannot know its task; the A and B arms are indistinguishable inside the container. The real execution path is non-functional beyond running the image's default entrypoint. |
| RN-5 | P1 | A1 | Quarantine unwired: `schedule()` honors the kwarg (`interleave.py:56, 65-69`) but `bench run` never calls `load_quarantine()` (`run/cli.py:82-91`); only the test passes it. |
| RN-6 | P1 | A1 | Redaction fails open on unknown suffixes (`redact.py:50-51`): suffix-allowlist skip; `.bak`, `.out`, `.tsx` unscanned, and `.env.local` has `Path.suffix == ".local"` so even the env family leaks. Contradicts the module's own "must not silently skip a file". |
| RN-7 | P1 | NEW | Redaction covers only `workspace/artifacts/`, not the workspace (`seam.py:94`; harbor mounts the whole workspace rw with injected keys in env, `harbor.py:124`). Secrets echoed into any other workspace file persist — and grading reads the workspace. |
| RN-8 | P1 | A1 | PEM pattern matches only the BEGIN header (`blind/core.py:116`); key body survives redaction (verified by execution). |
| RN-9 | P1 | A1 | `config.provider_keys` values never added to redaction patterns (`seam.py:94`; `redact_extra_patterns` never populated, `types.py:106`) — keys whose shape isn't in `_SECRET_PATTERNS` persist verbatim. |
| RN-10 | P1 | A1 | Timeout kills the docker CLI, not the container (`harbor.py:76-81`); the container keeps running and writing into the still-mounted workspace **after** redaction ran. No `docker kill/stop` anywhere in the module. |
| RN-11 | P1 | A1 | Egress detection parses only the fake engine's format (`harbor.py:182-198` requires `trial={id}` + `DENY` prefix; exactly `fake.py:49`) → real violations ledger as `False`. Nothing creates or verifies the `verdi-metered` network (single mention: the `--network` flag, `harbor.py:114`). |
| RN-12 | P2 | A1 | Image pinning unenforced: `resolve_digest` may return None and `run()` proceeds with `image_digest=None` (`harbor.py:129-137, 158`); no `--pull=never`. Violates D005. |
| RN-13 | P1 | A1 | `bench run --engine harbor` builds `RunConfig(engine=eng, concurrency=…)` only (`run/cli.py:75`) — no proxy (→ `--network none`), no provider keys, no quotas (no spec field exists); `egress.proxy_config` has zero callers. Metering (AC-3) and key injection (AC-8) unreachable from the CLI. |
| RN-14 | P2 | A1 | Ledgered infra reason reads `task.fake_behavior["infra_reason"]` (`interleave.py:126`) — a documented FAKE-ENGINE-ONLY field; `EngineResult` has no failure-reason field, so real engines can only ever ledger the placeholder. |
| RN-15 | P1 | A1-C4 | `QuarantinedTaskError` (`interleave.py:65-69`), bare `KeyError` on unknown task/arm (`interleave.py:79-80`), `HoldoutLeakError` (`seam.py:66-70`), and `UnknownPlatformError` (`seam.py:97`) all escape `schedule()` mid-loop — no `trial_infra_failed`, and the `executed_order` event (AC-4) is skipped, leaving ledgered trials with no order record. (Correction: redaction is *not* skipped — see §1.) |
| RN-16 | P3 | NEW | `redact.py:54-56` silently skips unreadable files (`except OSError: continue`) at the sole write barrier. |
| RN-17 | P3 | NEW | `_read_native_log` maps corrupt telemetry JSON to `{}` (`harbor.py:171-176`) — silent all-null telemetry instead of a loud infra failure. |
| RN-18 | P3 | A1/NEW | Dead/misleading: `Outcome.not_started_cost_ceiling` never constructed (`adapters/base.py:44`); `CostGuard.stopped` never set and its docstring claims behaviors that live in `interleave.py`; `sk-ant-` pattern fully shadowed by `sk-` (`core.py:108-109`, verified); `contention_caveat` stamps from a `--concurrency` knob while execution is strictly serial (`seam.py:102`, `interleave.py:64`); re-runs append multiple `executed_order` events with undefined downstream semantics; the judge `FakeProvider` silently replays its last scripted response when exhausted (`judge/providers/fake.py:22`). |

### 3.3 Plan / lock / ledger (EVAL-3)

| ID | Sev | Origin | Finding (verified evidence) |
|---|---|---|---|
| PL-1 | P1 | A1 | Power gate never consults the design: `n = variance_source.n_tasks` (default 50) (`power.py:44, 145`); `spec.repetitions` and corpus size ignored; CLI injects no source (`harness/cli.py:46-52`, `lock.py:63-65`). Also NEW: omitting `hypothesized_effect` skips the gate entirely with nothing ledgered (`lock.py:70`). |
| PL-2 | P1 | A1 | Lock TOCTOU: parse (`lock.py:61`), multi-second sim (`lock.py:65`), then re-read for hashing (`lock.py:89` → `:38`). Fix: hash the bytes you parsed. |
| PL-3 | P1 | A1 | Re-lock not refused (two `experiment_locked` events, `assert_lock` keys `locks[0]`, `lock.py:115`); in the underpowered path the ack event precedes the lock (lock isn't genesis, contradicting `lock.py:60` and the genesis test's assumption); `mde=None` ledgered as `null` into a `float`-annotated field (`events.py:133`). |
| PL-4 | P1 | A1 | `bench anchor` writes no ledger event (`harness/cli.py:86-97` calls only `anchor_head`); `record_chain_anchor` (`events.py:144-153`) is test-only. Anchoring is also wall-clock-stamped at the CLI seam. |
| PL-5 | P1 | A1/A2 | `CalibrationVariance` has no loader (`power.py:48-59`, TODO holder; sole constructor is a test). Nothing in `harness/corpus/` reads manifest calibration runs into a variance source — every production lock is `assumption_based_mde`. |
| PL-6 | P0 | NEW | **Downstream stages never verify the chain.** `assert_lock` reads via `find_events` (plain JSON iteration, `query.py:46-48`) without `verify_chain`; `bench run` (`run/cli.py:60`), `bench grade` (`grade/cli.py:64`), analyze, and corpus admission (CO-5) all gate on unverified ledger content. Rewriting the lock line's `spec_sha256` in place passes every gate unless someone manually runs `bench verify-chain`. |
| PL-7 | P0 | A1 | `tasks.yaml` outside the lock: lock hashes only `experiment.yaml` (`lock.py:36-38, 89`); run loads tasks unhashed (`run/cli.py:25-42`); grade's `task_sha` is self-attested (`grade/cli.py:39`: `t.get("task_sha") or _task_sha(t)`). Prompts/canaries/holdout scripts (GR-5) swappable post-lock with no detection. |
| PL-8 | P2 | NEW | Inconsistent `experiment_id`: `bench plan` stamps the YAML stem — literally `"experiment"` for the standard layout (`harness/cli.py:44`) — while run/grade stamp the directory name (`run/cli.py:80`, `grade/cli.py:75`). One ledger, two ids. |
| PL-9 | P2 | A1 | Validation duplicated between `_prevalidate` and pydantic validators (`schema/experiment.py:118-176`); named-error contract lost via `model_validate` (generic `ValidationError` instead of e.g. `MissingCostCeilingError`); copies already differ in edge handling. |
| PL-10 | P2 | A1 (corrected) | `arms` has `min_length=2` but no cap and no name-uniqueness (`experiment.py:104`) — 3 arms and duplicate names accepted in a paired A/B instrument. |
| PL-11 | P3 | A1 | Decision-rule DSL admits `==` on a bootstrap float (`experiment.py:51, 58`) — a rule that can never fire locks cleanly. |
| PL-12 | P3 | NEW | `hypothesized_effect` unbounded (`experiment.py:112`): negative values are always "underpowered", values > 1 always pass. |
| PL-13 | P3 | NEW | `_last_line` returns a partial final line when the ledger lacks a trailing newline and `append_event` concatenates the next event onto it (`chain.py:44-74, 102-113`) — append should refuse rather than compound corruption. |
| PL-14 | P3 | NEW | The acknowledged-underpowered path emits two events per invocation (`lock.py:82-99`), so the advertised one-event-per-operation property is false for a documented code path and the property test never exercises it. |

### 3.4 Judge (EVAL-2)

| ID | Sev | Origin | Finding (verified evidence) |
|---|---|---|---|
| JD-1 | P2 (docs) | A1-C3 reclassified | Holdout results in the judge packet are **spec-mandated** (see §1). Required actions are documentation-side: align master-plan §1 wording ("outcome-blind" → "identity-blind" or define the term), record the clarification against D002, and add an analysis-side disclosure that `judge_preference` is correlated with `holdout_pass_rate` by design. **No packet change.** |
| JD-2 | P1 | A1-C4 | `get_provider` runs before the try envelope (`client.py:101`); unknown prefixes (legal per D001) raise `ProviderError` with no `CANT_JUDGE` event (`providers/base.py:33-45`) — also a de-facto 3-vendor allowlist contradicting the module's own AC-1 claim. |
| JD-3 | P1 | A1-C4 (corrected) | Error-shaped/safety-blocked 200 responses raise uncaught `KeyError`/`IndexError` in openai (`openai.py:18`) and google (`google.py:19-20`) — escape with no event (`client.py:141-147` catches neither). Anthropic does not raise (`.get()` chain) but misclassifies the failure as `CANT_JUDGE(parse)`. |
| JD-4 | P2 | A1 | Degenerate kappa: all-A/all-A ×20 returns `kappa=1.0, sufficient=True` (`calibrate.py:41-42, 73-81`; verified by execution). Deliberate per code comment, statistically wrong — kappa is undefined at chance agreement 1 → decision D-5. |
| JD-5 | P1 | A1 | `pairs_from_ledger` joins on `comparison_id=None` (verdicts without ids pair with each other); duplicate judge verdicts last-write-win; `CANT_JUDGE` enters kappa as an ordinary category (`calibrate.py:97-114`). |
| JD-6 | P1 | A1 | Alias regex `\d+\.\d+` false-passes `google/gemini-1.5-pro` and `openai/gpt-4.1` (`judge_config.py:27`; verified) — the mutable aliases AC-5 exists to reject; the error message's own example demands `-002`. |
| JD-7 | P2 | A1 | `_vendor` returns the whole string for prefix-less models (`confounds.py:21-22`), so an anthropic judge over `claude-3-5-…` (no `anthropic/` prefix) yields `overlap=False`; `Arm.model` is an unvalidated bare `str` (`experiment.py:30-34`). |
| JD-8 | P2 | A1 | Prompt-injection surface: raw diff interpolation under markdown headers, one-line system prompt, no fencing (`packet.py:57-68`). A content-keyed injection maps to the same arm in both orders, so D003's order-consistency check (`client.py:161-167`) reads it as a legitimate win at confidence 0.8. |
| JD-9 | P1 | A1 | Judge unwired: no `bench judge` verb (`harness/cli.py:109-116` loads run/grade/corpus/analyze/review/process only); `judge_pair` has zero production callers; spec-derived canary literals never reach `validate_identity_free`; `EscalationConfig` referenced nowhere — `kappa_by_class` re-hardcodes `0.6/20` (`calibrate.py:58-59`), so the D006 seam is dead. |
| JD-10 | P3 | A1 | Parsed judge confidence discarded, replaced with hardcoded 0.8/0.5 (`client.py:46` vs `:174`); google API key in the URL query string (`google.py:17`) → leaks into proxy logs. |
| JD-11 | P2 | NEW | `orders: "single"` accepted (`judge_config.py:59`) and never flagged anywhere, though the spec requires "single allowed only for smoke runs; **flagged**" (`eval2.spec.md:194`) — a full experiment can silently skip D003 debiasing. |
| JD-12 | P2 | NEW | Verdict `confidence: float` (`judge/schema.py:55`) contradicts the spec's event schema `"confidence": "low|medium|high"` (`eval2.spec.md:226`) — a hash-chained contract deviation with no recorded decision → decision D-4. |
| JD-13 | P3 | NEW | Connect-phase timeouts surface as `URLError` → `provider_error` instead of `timeout` (`_http.py:25-28`); response-label assignment is deterministic AB/BA rather than the spec's "assigned randomly per call" (`eval2.spec.md:184-185`); `packet_sha256` covers the order-independent content but not the rendered message/system prompt (`packet.py:79-93`), so provenance can't detect framing changes. |

### 3.5 Analyze (EVAL-6)

| ID | Sev | Origin | Finding (verified evidence) |
|---|---|---|---|
| AN-1 | P0 | A2-A1 | `_judge_preference_values` reads **every** `judge_verdict` with no comparison/arm-pair filter and imputes any non-A/B winner (incl. `CANT_JUDGE`) as 0.0 (`report.py:151-161`); the same pooled deltas feed every arm pair (`report.py:350-355`). Reproduced: 3-arm experiment with true effects +1/−1 reports `mean_delta 0.0, n=11` for **both** comparisons. No task clustering (each verdict its own bootstrap cluster) → anti-conservative CIs. The A↔arm mapping is assumed (`report.py:349`), never recorded anywhere in the verdict/provenance schema — a swapped packet silently flips the sign. |
| AN-2 | P0 | A2-A2 | The official calibration fence checks only `calibration.status` (`report.py:565-578`); no cross-check of `corpus_id`/`semver` against `spec.corpus` or the task shas actually run. Reproduced: official render accepted `TOTALLY-DIFFERENT-CORPUS@9.9.9` against a `public-mini@1.0.0` spec. The shipped tests bake the mismatch in (`test_eval6_analyze.py:33-39, 228-236` vs `fixtures/builders.py:102`). |
| AN-3 | P1 | A2-A3 | Refused official renders unledgered: `CalibrationIncompleteError` escapes `analyze/cli.py:65-84` with zero events (reproduced); no `CANT_ANALYZE` event type exists. Success path writes findings files first, event second. |
| AN-4 | P1 | A2-A4 | CI-method selection runs at the lock's assumed params with silent defaults `(0.5, 0.3, 50)` (`report.py:200-206`), not the experiment's realized N; the null model is correlated Bernoulli regardless of metric (`report.py:328-329` before the metric branch), so cost/wall-time primaries select their CI method under a paired-binary null. Reproduced coverage difference 0.96 vs 0.78 at N=4. |
| AN-5 | P2 | A2-A5 | `render_html` wraps lines in `<p>` with no escaping, no jinja2 (`report.py:692-704`); `<script>` in an arm name lands verbatim (reproduced). The review packet escapes correctly — the fix pattern exists in-repo. |
| AN-6 | P1 | A2-A6 | `[computed]`/`[judgment]` claim tags exist nowhere in `harness/` or `tests/` (grep-verified); `test_ac6_finding_provenance` (`test_eval6_analyze.py:258-269`) tests provenance fields only. The §6 row must stay `enforced_by: review`. |
| AN-7 | P1 | NEW | Judge-preference effect sizes are fabricated: `a_vals=[max(d,0)]`, `b_vals=[max(-d,0)]` from clipped deltas feed `cliffs_delta` (`report.py:354-355, 393`) — the reported Cliff's delta for judge-preference primaries is statistically meaningless. |
| AN-8 | P2 | NEW | `decides_positive` recorded on the raw observed delta regardless of significance (`report.py:403-407`); only the markdown render gates on detection — any consumer of `findings.json` reads `decides_positive: true` for a null result. |
| AN-9 | P2 | NEW | Orphan grades (no matching trial record) silently dropped (`report.py:133-135`) — a ledger inconsistency shrinks n with no error or flag. |
| AN-10 | P3 | NEW | CI-method selection runs at `n_boot=500` (`nullsim.py:28`) but the deployed bootstrap uses `n_boot=10_000` (`stats.py:25`) — the chosen method was never evaluated at the resample count actually used. |
| AN-11 | P3 | A2-A7 | Confirmed minors: `CIMethod` seam not config-flippable (hardcoded in `compute_findings`, no CLI/spec knob); ADVISORY tier never surfaces in findings/renders; `ClusterRobustTCI` silently drops zero-SE resamples (`ci.py:101-104`); BCa `z0` biased low on discrete deltas (strict `<`, `ci.py:120`); `findings_rendered.experiment_id` is the directory basename (`cli.py:76`) while the document's id comes from the lock; `fractional_score` recorded in grade events but never read by analysis (`report.py:136`). |
| AN-12 | P2 | A2-D6 | Official `findings.json` includes the process section and hashes it into the ledgered `findings_sha256` (`report.py:443`; `cli.py:72-83`); only the official *markdown* omits it (`report.py:581-604`). The EVAL-9 spec's AC-6 contemplates labeled inclusion rather than exclusion → decision D-3. |

### 3.6 Corpus (EVAL-8)

| ID | Sev | Origin | Finding (verified evidence) |
|---|---|---|---|
| CO-1 | P1 | A2-B1 | Boundary enforcement is declaration-only: `assert_boundary` validates the declared string (`registry.py:121-141`); `save()` never checks the destination (`registry.py:215-221`; reproduced — internal manifest saved inside the instrument repo); `bench corpus mine --out` writes ticket text + holdout contents anywhere (`corpus/cli.py:68-89`). `test_ac5_boundary_enforced` tests only the declared field — the §6 row stays `enforced_by: review`. |
| CO-2 | P1 | A2-B2 | `is_schedulable` has zero production callers (`registry.py:180-183`); `bench run` reads `tasks.yaml` and never consults a manifest — pending/quarantined tasks run, grade, and feed findings. |
| CO-3 | P1 | A2-B3 | `import_terminal_bench` never loads the prior manifest nor calls `assert_valid_successor` (`public.py:74-121`) — same-semver mutation silently rewrites the cache — and rebuilds `Calibration()` from scratch (reproduced: `full-run-validated` → `none` after a byte-identical re-import). |
| CO-4 | P1 | A2-B4 | `record_calibration_run` (`registry.py:186-198`) has no CLI verb or run hook; status lives in mutable manifest JSON loaded via `--corpus` (`analyze/cli.py:60-65`) — hand-editing the status passes the official fence (compounds AN-2). |
| CO-5 | P2 | A2-B5 | Admission gate reads the ledger via `find_events` with no `verify_chain` (`admit.py:17, 29-46`) — a hand-forged ledger admits a task (instance of PL-6). |
| CO-6 | P2 | A2-B6 | Path traversal via registry-supplied `task_id` (`public.py:95`; reproduced — `../../escaped` wrote outside the cache); no dataset-level checksum pinning. |
| CO-7 | P2 | A2-B7 | `corpus review` prints holdout **paths** only (`cli.py:107-109`) — the human gate cannot do the solution-leakage check it exists for; approver is `getpass.getuser()` with no attestation or self-approval bar (`cli.py:20-24, 122`); `admit_task` mutates memory only — nothing saves the manifest, no admission event type exists. |
| CO-8 | P1 | NEW | The mine→admit pipeline is disconnected end-to-end: `mine` writes a standalone candidate JSON; `admit_task` requires the candidate to already be a manifest `TaskEntry` (`admit.py:63-66`), but no code inserts a mined candidate into any manifest, and no `admit` CLI verb exists. The AC-4 admission gate is reachable only from tests, and only via hand-edited (unguarded, per CO-4/CO-5) manifests. |
| CO-9 | P3 | NEW | Re-import leaves removed tasks' cache blobs behind (manifest/cache drift); `corpus subset` records the draw only in the mutable manifest — unledgered, same tamper class as CO-4. |

### 3.7 Review (EVAL-7)

| ID | Sev | Origin | Finding (verified evidence) |
|---|---|---|---|
| RV-1 | P1 | A2-C1 | `record_human_verdict` checks neither an existing reveal nor an existing verdict (`record.py:37-60`) — verdict → reveal → second (unblinded) verdict accepted; duplicates enter `reviewed_kappa_items` (`sample.py:155-160`), `pairs_from_ledger` (`calibrate.py:102-114`), and the production-wired integrity rate (`report.py:222-234`). |
| RV-2 | P1 | A2-C2 | `bench review reveal` hardcodes `arm_identities={"1": "arm_a", "2": "arm_b"}` (`review/cli.py:75-78`) — the ledgered unblinding record is fiction; EVAL-9's reveal-keyed scoring inherits it. No per-comparison response-order randomization exists anywhere in `review/` (only the judge side randomizes). |
| RV-3 | P1 | A2-C3 | Pipeline unwired: docstring promises `build` (`cli.py:3`) but only `record`/`reveal` exist; `build_review_packet`, `select_for_review`, `reviewed_kappa_items`, `kappa_report` have zero production callers; nothing records which arm was "Response 1/2", so the human's `--winner A` maps to the judge's A/B only by unrecorded convention. |
| RV-4 | P1 | A2-C4 | `kappa_by_class` computes raw pooled Cohen's kappa over the disagreement-heavy reviewed set (`calibrate.py:55-82`), bypassing the D003 IPW seam (`review/kappa.py:98-159`), whose only production consumer is EVAL-9. Nuance: neither path is production-wired today (JD-9/RV-3) — but the coded escalation mechanism is the biased one. |
| RV-5 | P2 | A2-C5 | IPW weights use nominal 0.2, not realized `ceil(0.2n)/n` (`sample.py:126` vs `kappa.py:23, 113-115`) — verified: correct weight 3 for n=6, used 5; up to ~1.67× floor over-weighting; `kappa_report` doesn't even expose `floor_prob`. |
| RV-6 | P2 | A2-C6 | `actual_arm` never supplied by the CLI and no lookup exists (`cli.py:57-60`), so guess accuracy is structurally 0.0 whenever any reviewer answers `--arm-recognized` (`report.py:230-233`) — misreported as a measured zero, not unknown. Tests mask it by passing `actual_arm` directly. |
| RV-7 | P2 | A2-C7 | Mandatory/floor boundary recoverable from the packet's two independently id-sorted blocks (`sample.py:138`) — the id-order reset marks exactly which items are disagreements, contradicting the module's "ordering leaks nothing" claim and the plan's own risk note. |
| RV-8 | P3 | A2-C8 | All six confirmed: duplicate reveals allowed; refused reveal unledgered (`record.py:76-80`, `cli.py:79-81`); `verdict_event_id` holds the comparison id (`record.py:91` — events have no id field, so a true reference is currently unrepresentable); last-judge-verdict-wins joins; `CANT_JUDGE` as plain kappa category; a bare `append_human_verdict` closes the comparison (`calibrate.py:86-92`) but never unlocks reveal (`record.py:32` requires `integrity`). |
| RV-9 | P2 | NEW | Join inconsistencies and input holes: `reveal_comparison` takes the **first** judge verdict (`record.py:83-87`) while both kappa joins take the **last** — with duplicates, the reveal discloses a different verdict than kappa scores; `review record` accepts any comparison_id with no existence check (mistyped ids "close" nonexistent comparisons and silently drop from kappa); integrity-less verdicts still calibrate the judge (`sample.py:155-160` doesn't require `integrity`); the CLI omits `task_class` (`cli.py:53-56`), so every CLI-recorded verdict lands in the `"default"` class, defeating per-class escalation even if wired. |

### 3.8 Process (EVAL-9)

| ID | Sev | Origin | Finding (verified evidence) |
|---|---|---|---|
| PR-1 | P1 | A2-D1 | `{"scores": [3,4,5]}` (list, not dict) raises `AttributeError` at `score.py:117` past the `except (ValueError, JSONDecodeError)` (`score.py:185`) — reproduced escape, no `process_score` event, contradicting the function's "always appends exactly one event" docstring. |
| PR-2 | P1 | A2-D2 | `RedactionLeakError` from `build_process_packet` (`packet.py:76-80`, called at `score.py:168` outside any try) escapes with zero events — should be `CANT_SCORE(redaction_leak)`, matching the judge's `identity_leak` precedent (`judge/client.py:128-134`). |
| PR-3 | P2 | A2-D3 | `get_provider` immediately before the try (`score.py:178-179`) — unknown prefix escapes with no event (reproduced). |
| PR-4 | P2 | A2-D4 | A judge-declared per-dimension `"CANT_SCORE"` (exactly what the packet instructs, `packet.py:45-46`) is ledgered as reason `"unparsed"` (`score.py:118-120`); timeout/refusal collapse to `provider_error` (`score.py:181-182`); reasons are ad-hoc strings ("parse" vs "unparsed" both exist), not an enum. |
| PR-5 | P1 | A2-D5 | AC-5/AC-7 reporting unreachable: `bench process` registers only `record` (`process/cli.py:28`) though its docstring documents `score`; `process_kappa_by_dimension`/`score_telemetry_correlation` (`calibrate.py:36, 97`) have no production caller; the analyze process section and render contain no kappa, correlations, or `style_only` (`report.py:269-313, 646-660`), though plan M5 requires them. |
| PR-6 | — | A2-D6 | Tracked as AN-12. |
| PR-7 | P2 | NEW | `bench process record` silently maps a missing/typoed dimension key to `CANT_SCORE("human_cant")` and ignores unknown keys (`process/cli.py:49-51`) — a misspelled dim id degrades a real human score with no error. |
| PR-8 | P2 | NEW | Neither `record_human_process_score` nor `ProcessScore` validates `dimension_scores` against the rubric (`score.py:83-89, 201-233`) — unknown/subset/duplicate dims ledger cleanly stamped with that rubric_version. |
| PR-9 | P3 | NEW | `judge_vendor_overlap` collapses "spec unavailable" to a definite `False` in hash-chained provenance (`score.py:152`); the full-or-CANT_SCORE context gate uses a chars/4 heuristic (`score.py:93-95, 141`) instead of the provider tokenizer the plan specified — code-heavy transcripts can pass the gate, overflow the real context, and get mis-ledgered as `provider_error` without token counts. |

### 3.9 Cross-cutting / test infrastructure

| ID | Sev | Origin | Finding (verified evidence) |
|---|---|---|---|
| XC-1 | P0 (enabler) | A1 | Zero docker-marked tests (`pytest -m docker` collects nothing); marker declared only in `pyproject.toml:37-38`; README (`:69-70`) and docstrings (`harbor.py:16-17`, `grade/cli.py:5`, `grade/container.py:9, 59`) claim they exist; CI runs `-m "not docker"`, which deselects nothing. This is the structural enabler of GR-1/2/4 and RN-4 — the entire real path has zero coverage and nothing says so. |
| XC-2 | P1 | A1 | The AC hook only reports (`conftest.py:36-46`) — no failure on missing/misnamed AC tests, though the master plan §3.5 says "enforced". The regex conflates story-local AC numbers into one global 9-element set, so even an enforcing hook on it couldn't detect a story's missing AC; duplicate test names (`test_ac4_mde_computed` ×2) already defeat it. |
| XC-3 | P1 | A1 | One-event property registry has exactly 3 entrypoints (`plan/lock.py:146`, `run/interleave.py:162`, `grade/deterministic.py:176`); the sweep test hardwires those imports and asserts only non-emptiness (`test_eval3_property.py:11-30`) — the "later stories join automatically" design fails open, and EVAL-2/6/7/8/9 verbs sit outside the property. |
| XC-4 | P2 | A1 | Vacuous tests confirmed: `test_ac9_holdout_canaries_absent`'s prompt assertion uses disjoint alphabets (`test_eval4_insulation.py:29-42` — lowercase prompt vs uppercase+`CANARY_` canary) and never mutates the prompt; `test_eval3_power.py:20` is a tautology (§1). The deterministic leak-refusal test beside the first is real. |
| XC-5 | P2 | A1 | `.importlinter` contract-1 source list omits `harness.cli`, `harness.entrypoints`, `harness.version`, `harness.run.{cli,egress,redact,types}`, `harness.run.engines.fake`; contract-3 omits `harness.blind`, `harness.cli`, `harness.entrypoints`, `harness.version` (`harness/cli.py:71` imports `ledger.chain` directly — legal only because cli is unlisted). The compensating AST test uses relative `Path("harness")` (`test_eval4_seam.py:82`) — from any other cwd it scans nothing and passes vacuously (demonstrated); it compensates contract 1 only. |
| XC-6 | P3 | A1 | Python floor 3.11 vs plan's 3.12+ — disclosed in README, but nothing (CI matrix, syntax gate) verifies the claimed 3.12 compatibility. Confirm as intentional debt. |
| XC-7 | P3 | A1/NEW | README overstates: docker-marked tests (false); "full AC-1..AC-9 coverage per story" (mechanically unverifiable — the report is a global union); the ✅ table marks stories built whose spec-promised CLI surface is absent (no judge verb, no `review build`, no `process score`). Usage omits `corpus approve`. |

**Definitive CLI verb inventory** (validation artifact): top level `plan`,
`verify-chain`, `anchor`, `run`, `grade`, `analyze`; `corpus
import|subset|mine|review|approve`; `review record|reveal`; `process record`.
Spec-promised but absent: `judge` (any form), `review build`, `process score`,
any corpus admission verb. Wired-but-inert seams: quarantine, admission,
calibration recording, escalation config, `CalibrationVariance`.

## 4. Decisions required from the human (blocking, cheap to answer)

Per the "human decides" directive these need explicit resolution before the
corresponding fixes — each is a direction-setting call, not an implementation
detail:

- **D-1 (JD-1):** Confirm EVAL-2-D002 stands (holdout results stay in the judge
  packet); approve the master-plan/README wording fix ("outcome-blind" →
  identity-blind) and an analysis-side disclosure of the judge↔holdout
  correlation. Record in the decisions ledger.
- **D-2 (GR-10):** Quarantine keying — `task_id` (current, tested as deliberate)
  vs `(task_id, task_sha)` (what the spec's "task version" language implies).
- **D-3 (AN-12):** Official `findings.json` — strip the process section from the
  official artifact, or keep it labeled per the EVAL-9 spec's AC-6 reading. The
  ledgered `findings_sha256` contract depends on the answer.
- **D-4 (JD-12):** Verdict `confidence` — migrate to the spec's
  `low|medium|high` enum (hash-chained contract change needing a
  compatibility story) or amend the spec to the float.
- **D-5 (JD-4):** Degenerate-kappa policy — return undefined/insufficient on
  zero chance-corrected information instead of 1.0/sufficient.
- **D-6 (PL-7/GR-5/CO-2):** Task-content commitment — hash `tasks.yaml` into
  the lock event now, or accelerate the corpus-manifest path (`spec.corpus`
  resolved against a pinned manifest at run/grade) and delete the stand-in.
  Either way `fake_holdout_output` must stop being reachable from production
  grading.
- **D-7 (XC-6):** Confirm the 3.11 floor as intentional debt or add a 3.12
  compatibility gate.

## 5. Remediation plan to full capability readiness

Ordered so each phase's exit criterion is testable and later phases build on
earlier ones. IDs reference §3. Every phase lands with tests that reproduce the
defect first (repo directive), and `make verify` green.

### Phase 1 — results integrity: grading and the evidence chain
The instrument must stop trusting agent-writable and tamper-writable inputs.

- Grade from a fresh copy of the trial workspace; delete/ignore pre-existing
  `holdout_results.json`; mount ro where possible (GR-1, GR-2, GR-3).
- Gate on container exit codes (any nonzero ≠ 125 ⇒ `cant_grade(container_failure)`
  with the code recorded); consult `exit_status` (GR-2).
- Wire a `--runner {local,docker}` flag into `bench grade`; make the grader
  image a real, configurable pinned digest (GR-1, GR-4).
- Remove `fake_holdout_output` from the production load path — fake scripting
  reachable only via the fake engine/test fixtures (GR-5; pending D-6).
- Emit `cant_grade(unknown_task|artifacts_missing|malformed_output)` instead of
  silent continues and bare ValueErrors (GR-6, GR-7); allow regrade of
  transient `cant_grade` reasons (GR-11).
- Call `verify_chain` at every stage entry (`assert_lock` and the corpus
  admission gate verify before reading) (PL-6, CO-5).
- Hash-commit task content at lock time per D-6 (PL-7).
- Fix lock TOCTOU (hash the parsed bytes), refuse re-lock, make the lock
  genesis in the ack path, and type `mde` as optional (PL-2, PL-3).
- Wire `record_chain_anchor` into `bench anchor` (PL-4). Unify `experiment_id`
  stamping across plan/run/grade (PL-8).

**Exit:** an agent-forged results file cannot produce a grade; a hand-edited
ledger line fails every downstream verb; a docker-marked test executes a real
holdout in a container and passes; re-lock refused test green.

### Phase 2 — a real execution path
`bench run --engine harbor` must be able to run a genuine trial.

- Deliver prompt + arm configuration into the container (env/file/command
  contract with the trial image) (RN-4).
- Kill the container on timeout (named container + `docker kill`), and only
  then redact (RN-10).
- Refuse tag-only images; require digest resolution; `--pull=never` (RN-12).
- Build `RunConfig` from the spec/CLI: proxy config, provider keys, quotas
  (RN-13); create/verify the `verdi-metered` network and parse a real proxy
  log format, with per-trial attribution defined (RN-11).
- Rebuild `CostGuard` from the ledger at start; skip already-executed trials;
  count `proxy_metered_cost` when telemetry cost is null; check the guard
  inside the infra-rerun loop and accumulate failed-attempt spend (RN-1..3).
- Wire `load_quarantine()` into `bench run` (RN-5); stop reading
  `fake_behavior` for ledgered reasons — add a failure-reason field to
  `EngineResult` (RN-14).
- Redaction: scan-everything-except-known-binaries instead of a suffix
  allowlist; cover the whole workspace; redact full PEM bodies; add
  `provider_keys` values as literal patterns; fail loudly on unreadable files
  (RN-6..9, RN-16).
- Wrap `schedule()` so per-trial exceptions ledger `trial_infra_failed` and the
  `executed_order` event always lands (RN-15); baseline runs each get a fresh
  workspace copy and distinguish infra failure from flake evidence; require
  `k >= 1` (GR-8, GR-9, GR-10 per D-2).

**Exit:** a docker-marked end-to-end harbor trial (real container, metered
proxy, key injection, redaction of the injected literal) passes; a ceiling-stop
→ re-run resumes rather than duplicates; kill-on-timeout verified.

### Phase 3 — the §7.2 fail-closed sweep
One attempted operation ⇒ exactly one event, in every stage.

- Judge: move `get_provider` and provider parsing inside the fail-closed
  envelope; catch KeyError/IndexError; correct reason classification
  (timeout vs provider_error vs parse) (JD-2, JD-3, JD-13).
- Analyze: add `CANT_ANALYZE(reason)`; ledger refused official renders; event
  before (or atomically with) findings files (AN-3).
- Process: catch AttributeError-class parse escapes; `CANT_SCORE(redaction_leak)`;
  provider lookup inside the envelope; reason enum incl. judge-declared
  CANT_SCORE; validate scores against the rubric; error on unknown/missing
  dims in `process record` (PR-1..4, PR-7, PR-8).
- Review: ledger refused reveals; refuse duplicate reveals and post-reveal or
  duplicate verdicts; existence-check comparison ids (RV-1, RV-8, RV-9).
- Corpus: ledger admission (`task_admitted`), calibration runs, and subset
  draws; enforce the successor rule and preserve calibration on re-import;
  sanitize `task_id`; boundary check on **write destinations** (CO-1, CO-3,
  CO-4, CO-6, CO-9).
- Register an entrypoint for every stage in the one-event property registry
  and make the sweep discover registrations rather than hardwire imports
  (XC-3; PL-14 for the ack path).

**Exit:** fault-injection tests per stage prove no zero-event escapes; property
sweep covers all nine stages.

### Phase 4 — connective tissue: wire the pipelines
Every spec-promised capability reachable from `bench`, no test-only kwargs.

- `bench judge`: derive canary literals (arm names, model ids) from the locked
  spec; feed `EscalationConfig` through calibration (JD-9); flag
  `orders:"single"` (JD-11).
- `bench review build`: sampling → packet with per-comparison response-order
  randomization, **recording** the Response-1/2 ↔ arm mapping; reveal reads
  real identities from trial records; supply `actual_arm` and `task_class`
  (RV-2, RV-3, RV-6, RV-9). Order the packet without a recoverable
  mandatory/floor boundary (RV-7).
- Judge calibration through the IPW seam with realized inclusion
  probabilities; exclude/report `CANT_JUDGE` rather than pooling it; dedupe
  and consistently join verdicts (first/last-wins unified) (RV-4, RV-5, JD-5,
  RV-9).
- `bench process score` + surface kappa/correlations/`style_only` in analyze
  (PR-5).
- Corpus: admission verb; mine → manifest insertion with content sha;
  `is_schedulable` consulted by `bench run`; `record_calibration_run` invoked
  from the run path; curation review shows holdout content/diff; approver ≠
  miner attestation (CO-2, CO-7, CO-8).
- `CalibrationVariance` loader from manifest calibration runs into
  `bench plan`; power gate at real N (`repetitions` × corpus size); bound
  `hypothesized_effect`; ledger gate-skips (PL-1, PL-5, PL-12).

**Exit:** a complete fake-engine experiment runs
plan → run → grade → judge → analyze → review → process end-to-end through
`bench` verbs only, with judge calibration and process reporting appearing in
the rendered findings.

### Phase 5 — statistical correctness
Findings must describe the experiment that ran.

- Judge-preference analysis: filter by comparison/arm pair using the recorded
  mapping; never impute CANT_JUDGE; cluster bootstrap by task; drop the
  clipped-series effect sizes for a valid preference effect measure (AN-1,
  AN-7).
- Bind the official fence to corpus identity: manifest `corpus_id`/`semver`
  vs `spec.corpus`, and task shas vs the ledgered trials — and fix the shipped
  tests that pass mismatched manifests (AN-2).
- Nullsim at the experiment's realized N with metric-appropriate null models
  and matched `n_boot`; no silent parameter fallbacks (AN-4, AN-10).
- `decides_positive` gated on detection in the artifact, not just the render;
  orphan grades flagged loudly (AN-8, AN-9).
- Alias regex rejects dotted-version aliases (JD-6); vendor-overlap handles
  prefix-less models or the schema requires prefixed ids (JD-7); degenerate
  kappa per D-5 (JD-4); packet fencing/delimiting for injection resistance and
  a `packet_sha256` that covers what the judge actually sees (JD-8, JD-13).
- HTML escaping in `render_html` (AN-5); implement `[computed]/[judgment]`
  claim tags in the findings schema and renders, and make
  `test_ac6_finding_provenance` own them (AN-6); resolve AN-12 per D-3.

**Exit:** the reproduced pathologies (3-arm pooling, wrong-corpus fence,
fabricated-N coverage, alias false-passes, script injection) each have a
failing-then-fixed test.

### Phase 6 — enforcement infrastructure
Make the fake/real and spec/test gaps unable to regrow silently.

- Docker-marked test suite for the real grade + harbor paths, run in CI (at
  least a scheduled/labelled job); until then the README must not claim them
  (XC-1, XC-7).
- AC hook enforces: per-story expected-AC manifests, failure on missing or
  duplicate AC test names (XC-2).
- Complete `.importlinter` source lists; make the AST seam test cwd-independent
  (anchor on `__file__`) (XC-5).
- Vacuous tests replaced with assertions that can fail (XC-4); fake provider
  raises on script exhaustion; remove dead symbols (`not_started_cost_ceiling`,
  `CostGuard.stopped`, shadowed `sk-ant-`) or implement them (RN-18).
- README corrected to the verified state; §6 invariant rows flipped only when
  the owning test actually enforces (see §6).

**Exit:** `make verify` includes the enforcing AC hook; CI exercises (or
explicitly gates) the docker suite; README claims are all mechanically true.

## 6. Readiness gate — definition of full capability

The master plan's §6 invariant table and §9 definition of done, restated
against validated reality. A row flips from `enforced_by: review` only when
the listed condition holds:

| Invariant (§6) | Current status | Flips when |
|---|---|---|
| Arms insulated; no rubric/holdout content to the agent | Partially real (leak-refusal test genuine; property test vacuous; grade path agent-forgeable) | XC-4 fixed + Phase 1 exit |
| Fail closed; no operation without a ledger event | **Enforced (Phase 3)** — the one-event property sweep covers every ledgered stage operation (12 entrypoints, expected-set asserted); judge/process/review/analyze/corpus fail-closed | Phase 3 exit (all-stage property sweep) ✓ |
| Claims tagged [computed]/[judgment]; provenance stamped | Tags do not exist (AN-6); provenance real | Phase 5 (tags implemented + owned test) |
| Orchestrator can't tamper (chain tamper-evident) | Chain sound but never verified downstream (PL-6) | Phase 1 exit |
| experiment.yaml sha-locked; primary/rule immutable | Lock fence real; TOCTOU + re-lock + tasks.yaml holes | Phase 1 exit + D-6 |
| Local = ADVISORY | Stamped but never surfaced in findings (AN-11) | Phase 5 |
| Internal corpora never enter the instrument repo | **Enforced (Phase 3)** — boundary checked on the actual write destination in `save()` and `corpus mine --out` (CO-1) | Phase 3 (write-destination enforcement) ✓ |
| Cost ceiling declared and enforced | Schema yes; enforcement per-process with bypasses | Phase 2 exit |

**Before the first official finding** (master plan §9), additionally:
full-run calibration recorded through a ledgered path (CO-4 fixed — a
hand-editable JSON status does not satisfy EVAL-8 AC-2), the calibration fence
bound to corpus identity (AN-2), and EVAL-1-D008 (A/A + coverage selfcheck)
resolved — still OPEN in the idea ledger; the nullsim machinery it needs
exists but must first run at the real N (AN-4).

## 7. Verified sound (carry forward — worth protecting with regression tests)

Hash-chain canonicalization/append/verify (single-syscall write under flock;
rewrite/delete/reorder caught; atomicity fault-injection); provenance envelope
per AC-6 with reserved-key rejection and registry-refused unknown events;
Fisher–Yates interleave unbiased, pure in `(seed, trials)`; namespaced
sub-seeds throughout (interleave, floor sampling, stratification, bootstrap,
nullsim); single blinding codepath with genuinely thin judge/review/process
wrappers and separated, case-sensitive secret vs identity lists; telemetry
null-mirroring; D003 order-swap → TIE logic; closed `PrimaryMetric` enum
imported by both EVAL-3 validation and EVAL-9's negative test;
`unblinded: Literal[True]` firewall; process-disclosure-required render;
paired bootstrap genuinely paired with byte-identical recomputation; Cliff's
delta / BCa / IPW-kappa arithmetic correct in isolation (hand-recomputed);
stratification largest-remainder allocation; scrub-and-rescan packet
validation fail-closed on identity leaks; reveal-before-verdict refused; judge
verdicts never close comparisons; asymmetric-null exclusion disclosed;
exploratory watermark on every section; no silent retries.

## 8. Judgment calls made in this consolidation

1. **JD-1 reclassified** from Critical code defect to a documentation/
   disclosure item on the strength of the EVAL-2 spec text and the resolved
   D002 decision (quoted in §1). If the program-level intent was genuinely a
   holdout-blind judge, that is a spec change to relitigate via D-1 — the code
   should not move first.
2. Severity assignments (P0–P3) are mine; the prior audits' Critical/Major
   labels were kept where they survived validation and re-ranked where new
   findings (PL-6, RN-4, GR-4, GR-5) proved more fundamental.
3. One-line supersession pointers were added to the two prior audit docs;
   their bodies are untouched as historical records.
4. The phase ordering front-loads integrity (Phase 1) over the real execution
   path (Phase 2) on the argument that a green fake-path instrument that can
   be reward-hacked is more dangerous than an absent real path.
