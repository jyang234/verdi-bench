# verdi-bench — Phase 8 plan: close the production-readiness register

**Date:** 2026-07-04 · **Follows:** the production-readiness & competitive audit
(`verdi-bench-production-readiness-audit.md`, this branch, `3f639f1`).
**Source of record:** that audit's §5 (findings) and §7 (roadmap), with every
High finding re-confirmed against source before this plan was written.
**Branch:** plan authored on `claude/codebase-audit-competitive-s72y5d`;
implementation follows the established convention — merge this plan, then cut the
implementation branch from `main`.

## Context

The Phase-7 register (the *verification residue*) was dispositioned and merged.
This audit was a fresh, capability-oriented pass: does each advertised feature
deliver its promise in production, not just on the fake path? The answer is
"substantially yes, and unusually honestly" — but with a consistent structural
pattern the register below encodes:

> The guarantees are strongest where they are self-contained and weakest at the
> seams to the outside world — the ledger read path, the browser, the container
> proxy, the web ceremony endpoints.

Five findings are High. Three of them (PRA-H1, PRA-H2, PRA-H3) are small,
self-contained, and voidance-of-a-headline-claim severe — they belong in this
session. Two (PRA-H4 the metering proxy; PRA-H5 the browser CI tier) have an
*infra-shipping* component that cannot be completed in a code-only session and
are split into a code half (done here) and an infra half (called out
explicitly). The Medium band is mostly session-sized. The Low/Info band is
largely a documentation truth-up that is cheap and high-trust-yield.

**Phase 8's job is a terminal disposition for every register item**: a fix with
an owning test that fails on regression, or a recorded human decision accepting
the behavior. The completeness map (§Completeness) is checked off item by item
at exit. Nothing falls out silently.

### Can this be one session?

Mostly. The plan is organized so the **safe, self-contained slices (8A–8D, 8F,
8H, 8J)** form a single consolidated session that closes three of five High
findings and the bulk of the Mediums and Lows. The **infra-coupled work**
(8E-proxy, 8I-browser-CI) is separated because it needs a real Squid/Playwright
environment and a human call on deployment posture — attempting it inside a
code-only session would produce untested config, exactly the "fake path built,
real path unverified" failure mode this audit flags. Recommended split:

- **Session 1 (this plan's core):** 8A, 8B, 8C, 8D, 8F, 8G, 8H, 8J — all
  code+test, no external infra. Closes PRA-H1/H2/H3 and ~15 Mediums/Lows.
- **Session 2 (infra):** 8E-proxy (ship proxy config + real-proxy docker e2e)
  and 8I (browser CI job). Needs Docker+Squid and node+Playwright in CI.

If you want it truly single-session, we drop 8E-proxy's *ship-the-proxy* item to
the doc-softening fallback (PRA-H4-alt below) and defer 8I; everything else
lands together.

---

## Register of findings (by severity)

IDs are stable (`PRA-` = production-readiness audit) and map to the audit doc.
"Owning test" names the regression guard each fix must add.

### High

| ID | Finding | Subsystem | Slice |
|---|---|---|---|
| **PRA-H1** | Reader (`splitlines()`) / verifier (`split(b"\n")`) divergence: a chain-valid event containing U+0085/U+2028/U+2029 makes every read gate crash — a poison-event DoS. | ledger | 8A |
| **PRA-H2** | CSRF on the author ceremony: no Origin/Host/token check, so a visited page can forge an `experiment_locked` genesis event. | author | 8D |
| **PRA-H3** | `context_overflow` crashes `judge_pair` (missing `CantJudgeReason` member) and writes no verdict event — voids AC-8 for OpenAI judges. | judge | 8B |
| **PRA-H4** | Metering proxy is not in the repo, its attribution is agent-spoofable, and it fails *open* when its log is missing — egress/attribution/cost claims unverified end to end. | run | 8E (split) |
| **PRA-H5** | 10 browser-driven ACs never execute in CI (hardcoded local paths + presence-based AC gate). | tests/CI | 8I (split) |

### Medium

| ID | Finding | Subsystem | Slice |
|---|---|---|---|
| **PRA-M1** | Consumer-side lock TOCTOU: every stage re-reads the spec after `assert_lock`. | plan + all stages | 8C |
| **PRA-M2** | Contamination CLI skips `assert_lock`/`assert_chain` entirely. | contamination | 8C |
| **PRA-M3** | Double-lock race: `AlreadyLockedError` check outside the append lock; `assert_lock` never refuses `>1` lock. | plan | 8C |
| **PRA-M4** | Multi-arm official render emits k−1 simultaneous 95% decisions with no correction or disclosure. | analyze | 8F |
| **PRA-M5** | Symlink in workspace leaks host-file contents into the blind judge packet. | judge | 8B |
| **PRA-M6** | Grader plugins run unsandboxed in the host process against the agent-controlled workspace. | grade | 8G |
| **PRA-M7** | Timeout kill swallows all errors, then redaction runs over a possibly-live container's workspace. | run | 8E-code |
| **PRA-M8** | Spend lost on the post-engine exception path (`trial_infra_failed` carries no cost). | run | 8E-code |
| **PRA-M9** | Dead proxy yields valid-looking "completed" trials (no liveness preflight). | run | 8E-code |
| **PRA-M10** | Serve fail-closed only on `/api/status`; every other ledger-reading route renders tampered content. | serve | 8D |
| **PRA-M11** | Corpus admission torn state + `--candidate-json` content never checked against approved sha. | corpus | 8H |
| **PRA-M12** | Public re-import silently resets post-import task state (quarantine → admitted). | corpus | 8H |
| **PRA-M13** | Transient `CANT_JUDGE`/`CANT_SCORE` treated as permanent on re-run (no retry path). | judge + process | 8B |
| **PRA-M14** | CI-method label can misstate the interval actually computed (BCa/cluster fallback). | analyze | 8F |
| **PRA-M15** | Forensics "clean fixture must not flag" is weaker in production (no pristine baseline at runtime). | forensics | 8G |
| **PRA-M16** | CI-only DNS-rebinding read of unblinded operator data (no Host validation). | serve + author | 8D (folds into H2/M10) |

### Low / Info

| ID | Finding | Slice |
|---|---|---|
| **PRA-L1** | NaN/Infinity accepted into the "canonical" ledger line (non-RFC-8259). | 8A |
| **PRA-L2** | `attested_by` defaults to `"unknown"`/`"cli-user"` (the sentinel `actor.py` bans); `anchor-plus-attestation-v1` implies non-existent crypto. | 8C |
| **PRA-L3** | Skip-marker detector omits `xfail`; `transient_holdout_tamper` matches prose substrings; detector evasion classes under-disclosed. | 8G |
| **PRA-L4** | Redaction skips `.tar`; judge re-scan checks identity but not secrets. | 8B/8E-code |
| **PRA-L5** | `bench anchor` non-atomic across its two writes; absent from the one-event property sweep. | 8A |
| **PRA-L6** | Ledger import contract's source list is hand-maintained, fails open for a new subsystem (harbor has an AST backstop; ledger does not). | 8I-code |
| **PRA-L7** | AC binding is presence-based (a skipped/assertion-free `test_ac*` satisfies the gate). | 8I-code |
| **PRA-L8** | Doc drift: CLAUDE.md "scaffolded" list; README Status table stops at EVAL-12; deep-dive "three of five"/"550+ tests"; adapters.md trajectory "v2". | 8J |
| **PRA-L9** | `--memory` without `--memory-swap`; no `--cap-drop`/`no-new-privileges`/`--pids-limit`; docker CLI stderr discarded on daemon error; `_with_trial_auth` silently skips attribution on a creds-bearing `proxy_url`. | 8E-code |
| **PRA-L10** | Negative tail offset returns 409 where the docstring says 400; HEAD/OPTIONS fall to stdlib 501; approval signatures have no replay scope. | 8D/8H |

---

## Decisions to confirm before implementation

Per CLAUDE.md, direction-setting choices are resolved by the human **before**
the owning slice and appended as `resolved` events to `review.decisions.ndjson`.
Recommendations with trade-offs, not open questions:

- **REVIEW-D-P8-1 (PRA-M4, slice 8F) — multi-arm decision policy.** Recommend
  **`primary-pair-official, rest-exploratory`**: in an official render, only the
  first (pre-registered primary) `arms[0] vs arms[1]` pair carries a
  "rule MET/NOT MET" decision; every additional pair renders its CI and effect
  but is labeled *exploratory* and carries no decision status. Rationale: the
  spec pre-registers exactly one `decision_rule`; a decision on k−1 pairs is
  itself unregistered. Alternative: **`holm-corrected-family`** — apply
  Holm–Bonferroni across the k−1 pairs and disclose it. Rejected as default
  because it silently redefines the pre-registered single decision into a family
  the operator never registered; offer it behind a `--multi-arm-correction`
  flag if wanted. Either way, the *disclosure* (that >2 arms were compared) is
  non-optional.
- **REVIEW-D-P8-2 (PRA-M6, slice 8G) — grader-plugin isolation.** Recommend
  **`containerize-plugins`**: run registered plugin graders under the same
  network-less fresh-copy container discipline as holdout assertions, so the
  no-LLM + no-network guarantee the docs imply becomes true for the whole grade
  tier. Trade-off: plugins that legitimately need host tooling (the
  `groundwork.py` shell-out) must declare it and run in the grading image.
  Alternative: **`document-as-trusted`** — keep plugins host-side, but state in
  the seam docstring and the deep-dive that plugins are *trusted operator code*,
  not sandboxed, and are outside the network-less guarantee. Cheaper, honest,
  but leaves a host-side code-execution surface against agent-controlled files.
  Recommend containerize; fall back to document if the grading image can't host
  the groundwork toolchain this session.
- **REVIEW-D-P8-3 (PRA-H4, slice 8E) — metering-proxy posture.** Recommend
  **`ship-config-plus-fail-loud`**: (a) vendor a reference Squid/devcontainer
  proxy config that produces the exact JSONL attribution schema the engine
  parses, add a real-proxy docker-marked e2e asserting deny-logging and
  attribution, and require the proxy to authenticate the per-trial credential;
  (b) regardless of (a), make a configured-but-missing proxy log **fail loud**
  (ledgered flag or `infra_failed`), add a proxy liveness preflight, and treat
  unattributed in-window egress as the trial's. Item (b) is code-only and lands
  this session; item (a) is the infra half (Session 2). Alternative fallback
  **PRA-H4-alt** if (a) is deferred: soften the README to state explicitly that
  hermetic egress, per-trial attribution, and cost enforcement for
  non-self-reporting arms **require an external metering proxy that is not
  bundled**, and are advisory until the Session-2 e2e exists. The fail-loud half
  (b) must ship either way — a fail-*open* cost guard is the more dangerous
  defect.
- **REVIEW-D-P8-4 (PRA-M13, slice 8B) — transient CANT retry.** Recommend
  **`exclude-transient-from-skip-set`**: mirror grade's `TRANSIENT_CANT_GRADE`
  carve-out so `bench judge`/`bench process` re-attempt a transient-reason
  `CANT_*` on re-run, while terminal reasons stay skipped. Alternative:
  **`add-retry-flag`** (`--retry-terminal` analog) — more explicit but more
  surface; the grade tier already set the `TRANSIENT_*` precedent, so matching
  it is the lower-drift choice.
- **REVIEW-D-P8-5 (PRA-L2, slice 8C) — attestation honesty.** Recommend
  **`route-through-resolve_actor`**: `attested_by` uses the same
  refuse-on-unresolvable path as every other actor, eliminating the `"unknown"`
  default; rename `method` from `anchor-plus-attestation-v1` to a string that
  does not imply cryptographic attestation (e.g. `anchor-plus-actor-v1`) until
  real attestation lands. The property-sweep entrypoints that ledger with
  `attested_by="unknown"` get a fixture actor. Additive on-disk (the field stays
  a free-form string); no chain migration.

Items with **no decision needed** (pure correctness, additive, or
documentation) proceed directly: PRA-H1, PRA-H2, PRA-H3, PRA-M1/M2/M3, PRA-M5,
PRA-M7/M8/M9, PRA-M10, PRA-M11/M12, PRA-M14/M15, and all Lows except L2.

---

## Slices (technical spec)

Each slice states the target files, the change, and the **owning test** that
must fail before the fix and pass after. All slices end with `make verify`
green.

### Slice 8A — ledger read-path & canonicalization (PRA-H1, L1, L5)

**PRA-H1 — reader/verifier parity.**
- `harness/ledger/query.py:83,152`: replace `str.splitlines()` with a
  `b"\n"`-only split that mirrors `verify_chain` exactly. Concretely, read bytes
  and split: `raw = path.read_bytes(); lines = raw.split(b"\n")`; drop a single
  trailing empty element (the terminating newline) and treat a non-empty final
  element as a truncated tail (raise the same `TruncatedLedgerError` semantics
  the append path uses, rather than silently parsing a partial line). Decode each
  line `utf-8` and `json.loads` as today. For `tail_events`, keep the bounded
  tail read but split the recovered bytes on `b"\n"` only.
- Extract the split into one private helper (`_split_ledger_lines(data: bytes)`)
  so `verify_chain` and both readers share it and cannot drift again — the same
  "one canonicalization" discipline `canonical_line`/`hash_line` already model.
- **Owning test** (`tests/test_eval3_chain.py`): a Hypothesis property over
  arbitrary-unicode payload strings — build an event whose `reason`/free-text
  field is `st.text()` (explicitly including U+0085/U+2028/U+2029), append it,
  then assert `verify_chain(...).ok` **and** that `read_events`/`tail_events`
  round-trip it byte-for-byte. This is the chain's first property test and
  directly reproduces H1 (fails today, passes after).

**PRA-L1 — reject non-finite floats at append.** `harness/ledger/chain.py:48`:
add `allow_nan=False` to `canonical_line`'s `json.dumps`. Append-side only —
existing compliant chains never contain these, so no migration. **Owning test:**
`record_run_stopped_cost_ceiling(accumulated_cost=float("nan"), ...)` must raise
`ValueError` at append, not write `NaN` to disk.

**PRA-L5 — anchor atomicity + property coverage.** `harness/cli.py:152-156`:
write the external anchor line and the `chain_anchor` event under a single
failure boundary — write the ledger event first (so a crash leaves no
un-ledgered external checkpoint), or write the external file to a temp path and
`os.replace` only after the event is durable. Add `bench anchor` to the
one-event property sweep registry in `tests/test_eval3_property.py` so its
one-event guarantee is mechanically covered like every other verb. **Owning
test:** fault-inject a failure between the two writes and assert no orphaned
external checkpoint; the property sweep now includes `anchor`.

### Slice 8B — judge robustness (PRA-H3, M5, M13, L4-judge)

**PRA-H3 — context_overflow.** `harness/judge/schema.py:61`: add
`CONTEXT_OVERFLOW = "context_overflow"` to `CantJudgeReason` (additive; the
reason is stored on a free-form string field, so no event-schema change). This
alone stops the `ValueError`-inside-`except` crash because
`provider_failure_reason` already returns that literal. Belt-and-suspenders:
in `harness/judge/client.py`, catch `ProviderContextOverflow` before the generic
`ProviderError` (mirroring `harness/process/score.py:270-274`, which already does
this). **Owning test** (`tests/test_eval2_client.py` or `_providers`): a
`FakeProvider` that raises `ProviderContextOverflow`; assert `judge_pair` writes
**exactly one** `CANT_JUDGE(context_overflow)` verdict event and does not raise.
Reproduces H3.

**PRA-M5 — symlink leak.** `harness/judge/assemble.py:_read_workspace_diff`:
skip symlinks (`if p.is_symlink(): continue`) or `p.resolve()` and confine to the
workspace root before `read_text`, mirroring the grade container's
`copytree(symlinks=True)` stance. Also stop `rglob` from descending symlinked
directories. **Owning test:** plant a symlink in a fake workspace pointing at a
host file with secret content; assert the target's content never appears in the
assembled packet.

**PRA-M13 — transient CANT retry** (per REVIEW-D-P8-4). `harness/judge/cli.py`
and `harness/process/cli.py`: compute the idempotency skip set excluding
transient-reason `CANT_JUDGE`/`CANT_SCORE` (define the transient subset the way
grade defines `TRANSIENT_CANT_GRADE`). **Owning test:** a ledger with a prior
`CANT_JUDGE(timeout)`; re-running `bench judge` re-attempts that comparison; a
prior `CANT_JUDGE(identity_leak)` (terminal) stays skipped.

**PRA-L4-judge — secret defense-in-depth.** Add a secret-corpus re-scan of the
assembled judge packet (raise `RedactionLeakError` on a hit), matching
`harness/process/packet.py`'s belt-and-suspenders. Cheap; closes the gap where a
missed trial-time redaction reaches the judge. **Owning test:** a packet with a
planted `sk-…` secret raises.

### Slice 8C — lock integrity at the seams (PRA-M1, M2, M3, L2)

**PRA-M1 — kill the consumer-side TOCTOU.** Change `assert_lock` to read the spec
bytes **once**, verify the sha against the recorded lock, parse *those same
bytes*, and return `(event, spec)`. Update every caller to use the returned spec
instead of a second `ExperimentSpec.from_yaml`: `run/cli.py:65-66`,
`grade/cli.py:153-154`, `judge/cli.py:47-48`, `analyze/cli.py:48-49,112-113`,
`review/cli.py:51-52`, `process/cli.py:67-68`, `forensics/scan.py:109-110`. This
is the same hash-then-parse discipline `lock.py` already uses at lock time
(PL-2). **Owning test:** swap the spec file between the sha check and the
consumer read (monkeypatch `from_yaml` to mutate on second call) and assert the
stage uses the locked bytes — i.e. the second read no longer exists to exploit.

**PRA-M2 — contamination lock gate.** `harness/contamination/cli.py:66`: call
`assert_lock` (now returning the spec) before loading, exactly as every other
stage does. **Owning test:** a post-lock-mutated spec makes
`bench contamination probe` refuse with a lock mismatch.

**PRA-M3 — double-lock race + `assert_lock` guard.** In `lock.py`, move the
`AlreadyLockedError` check under the same exclusive lock that guards the append
(re-check the ledger for an existing lock after acquiring the flock, before
appending). Make `assert_lock` refuse when `len(locks) > 1` (cheap after-the-fact
catch). **Owning test:** two serialized append attempts against a ledger that
already has a lock both refuse; a hand-built ledger with two `experiment_locked`
events makes `assert_lock` raise.

**PRA-L2 — attestation honesty** (per REVIEW-D-P8-5). Route `attested_by` through
the `resolve_actor` refuse-on-unresolvable path; give the property-sweep
entrypoints a fixture actor; rename the `method` string. **Owning test:** locking
with an unresolvable actor and no explicit `--attested-by` refuses (no `"unknown"`
reaches the ledger).

### Slice 8D — web security on the operator surfaces (PRA-H2, M10, M16, L10-web)

**PRA-H2 — CSRF on the author ceremony.** In `harness/author/server.py`
`do_POST`: reject any POST whose `Origin` header is absent or not the server's
own origin; validate `Host` against the bound loopback origin; require
`Content-Type: application/json` (reject the `text/plain` no-cors bypass).
Because the server is single-user loopback, an Origin allowlist of exactly the
served origin is sufficient and needs no token plumbing; if we want
defense-in-depth, embed a per-launch nonce in `AUTHOR_PAGE` and require it on
`/api/draft` and `/api/lock`. **Owning test:** a POST with a foreign/absent
`Origin` (or `text/plain` body) to `/api/lock` is refused with no
`experiment_locked` event written; the legitimate same-origin JSON POST still
works. Reproduces H2.

**PRA-M16 — Host validation** folds into the same handler on both servers
(`author/server.py`, `serve/server.py`), closing the DNS-rebinding read of
unblinded operator data. **Owning test:** a request with a foreign `Host` header
is refused.

**PRA-M10 — serve fail-closed parity.** Gate every ledger-reading serve route
(`/api/events`, `/api/timeline`, `/api/trial`, `/api/compare`) and
`write_bundle` on `verify_chain`, matching `/api/status`; cache the verdict by
`(size, mtime)` so it stays O(1) on the hot path. On a broken chain, withhold the
data the same way status does rather than rendering tampered events. **Owning
test:** against a tampered ledger, every data route returns the withheld/error
state, not tampered content; a clean ledger is unaffected.

**PRA-L10-web:** fix the negative-tail-offset 409→400 and give HEAD/OPTIONS the
deliberate 405+`Allow` instead of stdlib 501. Cosmetic; folded in here.

### Slice 8E — run/harbor hardening (PRA-M7, M8, M9, H4-code-half, L9)

Code-only half of the metering-proxy work (per REVIEW-D-P8-3 item b); the
ship-the-proxy + real-proxy-e2e half is Session 2.

**PRA-H4-code / PRA-M9 — fail loud, not open.**
- `harness/run/engines/harbor.py:378-381`: a configured `proxy.log_path` that
  does not exist (or has zero lines for a trial that had network access) must
  **not** return "no attempts, no violation, no cost." Raise/ledger
  `infra_failed(proxy_log_missing)` or attach a loud flag. A malformed line is
  likewise surfaced, not silently skipped.
- Add a **proxy liveness preflight** before the run loop when `proxy_url` is
  configured: fail the run loudly rather than producing null-telemetry
  "completed" trials against a dead proxy.
- Treat in-window egress lines with a wrong/absent trial credential as a
  run-level violation (the attribution the agent can spoof must not silently
  vanish).
**Owning tests:** missing-log run raises/ledgers a flag (not silent zero); a
dead-proxy preflight refuses; an unattributed in-window line surfaces.

**PRA-M7 — verified kill before trusting redaction.** `harbor.py:121-130`:
`_kill` must confirm `docker kill`/`docker wait` succeeded; on failure the trial
becomes `infra_failed(kill_failed)` **before** redaction is trusted, so a secret
written by a still-live container after the scrub cannot persist unredacted.
**Owning test** (docker-marked, Session-2-adjacent but the code lands now): a
kill that fails is not silently swallowed — asserted against a monkeypatched
runner today, real-container in Session 2.

**PRA-M8 — cost on the exception path.** Add a cost field to
`trial_infra_failed` and feed it from the engine result before the
`RedactionError`/`TrajectoryCorruptError` path discards it
(`interleave.py:274-277`), so post-engine failures still count against the
ceiling and survive resume. Additive event field (absent = pre-change). **Owning
test:** an arm that fails redaction after spending still has that spend counted
by the `CostGuard`.

**PRA-L9 — container hardening + attribution honesty.** Add `--cap-drop=ALL`,
`--security-opt no-new-privileges`, `--pids-limit`, and `--memory-swap` equal to
`--memory` to `build_run_command`; surface docker CLI stderr on daemon errors;
make `_with_trial_auth` refuse loudly when `proxy_url` already carries userinfo
(silent attribution loss). Document a DOCKER-USER/host-gateway block as part of
the deployment contract (the "internal" network still reaches the host gateway —
PRA-M1-network). **Owning test:** argv assertions for the new flags; a
creds-bearing `proxy_url` refuses.

### Slice 8F — analyze multi-arm correctness (PRA-M4, M14)

**PRA-M4 — multiple-comparison policy** (per REVIEW-D-P8-1). In
`harness/analyze/report.py`, restrict official "rule MET/NOT MET" status to the
primary `arms[0] vs arms[1]` pair; render additional pairs' CIs and effects
under an explicit *exploratory* label with no decision. Add a non-optional
disclosure line whenever `len(arms) > 2`. If the human picks the Holm
alternative, gate it behind `--multi-arm-correction=holm` and disclose the
correction in the render. **Owning test:** a 3-arm official render carries a
decision on exactly one pair, labels the rest exploratory, and emits the >2-arm
disclosure; a 2-arm render is unchanged.

**PRA-M14 — honest CI-method label.** `harness/analyze/ci.py` /
`harness/analyze/stats.py`: return the *realized* method (or a `fallback: true`
field) from `interval()` when BCa degrades to percentile or `cluster_robust_t`
falls back, and surface it in `BootstrapResult`/findings so the render label
matches the interval actually computed. **Owning test:** a small-N input that
forces the BCa→percentile fallback makes the findings method read `percentile`
(or `bca (fallback→percentile)`), not a bare `bca`.

### Slice 8G — grade/forensics precision & isolation (PRA-M6, M15, L3)

**PRA-M6 — plugin isolation** (per REVIEW-D-P8-2). Either run registered plugin
graders under the network-less fresh-copy container discipline, or (fallback)
document them as trusted host-side code and state the isolation asymmetry in the
`grade/plugins` seam docstring and the deep-dive. Recommended path adds a
container entrypoint for plugin execution mirroring holdout grading. **Owning
test:** containerized — a plugin cannot open a network socket; documented — a
docstring/deep-dive test asserts the disclosure exists.

**PRA-M15 — forensics production baseline.** `harness/forensics/scan.py:164`:
assemble `pristine_files` from the corpus task seed instead of hardcoding `{}`,
so the "clean fixture must not flag" guarantee holds at runtime, not only in
tests; where no baseline exists, downgrade attribution to a low-confidence
`edited-file-only` stamp rather than a bare flag. **Owning test:** a trajectory
that edits a file which *already contained* a `@pytest.mark.skip` (pristine
baseline supplied from the seed) does **not** flag `test_skip_insertion`.

**PRA-L3 — detector honesty.** Add `xfail`/`expectedFailure` to `_SKIP_MARKERS`
(with the required vocabulary-version bump the closed-enum test forces);
restrict `transient_holdout_tamper` path hits to `tool_call`/`file_edit` steps
(not prose narration); add one sentence per detector module naming the evasion
class the mechanical tier does not catch (assertion-literal forms it misses;
rename-evadable containment). **Owning test:** an `xfail` insertion now flags; a
prose mention of a holdout path does not.

### Slice 8H — corpus admission integrity (PRA-M11, M12, L10-corpus)

**PRA-M11 — torn admission + content check.** `harness/corpus/cli.py:294-317`:
validate the save destination *before* ledgering `task_admitted`; move the
embedded-copy write and `manifest.save()` inside the failure boundary; make
`admit_task` refuse an already-admitted candidate (so a re-run cannot append a
second `task_admitted`). In `admit --candidate-json`, recompute the candidate
content sha and refuse on mismatch with `task_sha`. **Owning test:** a late-save
failure leaves no advanced ledger with a `pending-curation` manifest; a re-run
refuses; a tampered candidate file is rejected before admission.

**PRA-M12 — re-import state preservation.** `harness/corpus/public.py:93-133`:
carry per-task state (quarantine, `baseline_ref`, `canary_sha256`) across a
same-semver re-import the way `calibration` is already carried, or refuse loudly
when the prior manifest diverges from import defaults. **Owning test:** a
quarantined public task stays quarantined (or the re-import refuses) rather than
silently returning to `admitted`.

**PRA-L10-corpus — approval replay scope.** Document the accepted risk (approval
payload has no nonce/expiry/ledger binding, so a withdrawn approval is
re-ledgerable) in the attestation module and the deep-dive; a full revocation
model is out of scope for this phase. **Owning test:** none (documentation);
noted in the completeness map as a recorded decision.

### Slice 8I — verification-layer & CI (PRA-H5-split, L6, L7)

**PRA-H5 — browser CI (Session 2 / infra).** Add a CI job provisioning node +
Playwright with a `VERDI_REQUIRE_BROWSER=1` fail-closed fixture (mirroring
`VERDI_REQUIRE_DOCKER`) and make the browser paths env-configurable
(`tests/fixtures/browser.py:22-23`). This is the infra half; it cannot be
validated in a code-only session because it needs the browser toolchain in CI.
**Owning test:** a guard test analogous to `test_eval_phase7_ci_guard.py`
asserting the browser job cannot go green by skipping.

**PRA-L7 — AC gate hardening (code-half, lands now).** Extend
`tests/ac_coverage.py` to flag `test_ac*` functions that are `@pytest.mark.skip`-
decorated or collected-but-skipped, and report them at session end, so a named
AC test that never executes no longer silently satisfies the gate. **Owning
test:** a planted skip-decorated `test_ac*` is reported by the hook (matching the
existing meta-test style).

**PRA-L6 — ledger contract backstop.** Add a completeness test (or an AST sweep
like harbor's) asserting every `harness/*` package appears in the ledger import
contract's source list, so a new subsystem importing `harness.ledger.chain`
directly is caught. **Owning test:** the sweep fails if a package is missing from
the contract source list.

### Slice 8J — documentation truth-up (PRA-L8)

Cheap, high-trust-yield, no code risk:
- `CLAUDE.md`: remove "scaffolded" from analyze/review/process/corpus; add
  forensics/contamination/status/serve/author to the subsystem list.
- `README.md`: extend the Status table through EVAL-21; soften the docker-CI
  sentence to match what the 3 docker tests actually prove (per PRA-H4/M7:
  egress/kill are fake-verified until Session 2).
- `docs/deep-dive.md`: fix "three of the five"/"Two structural contracts" →
  seven; "550+ tests" → current count; add the reviewer-isolation and
  observability contracts to the narrative.
- `docs/adapters.md`: trajectory schema v2 → v3, add the `detail` field.
- Extend `tests/test_readme_consistency.py` to also pin the deep-dive's contract
  and test counts, so this class of drift is machine-caught next time. **Owning
  test:** the consistency test now covers the deep-dive counts.

---

## What must split out of a single session

Honest accounting, so nothing is presented as done that isn't:

1. **PRA-H4 ship-the-proxy (8E item a).** A reference Squid/devcontainer proxy
   config plus a real-proxy docker-marked e2e asserting deny-logging and
   attribution needs a Docker+Squid environment and a human call on deployment
   posture (REVIEW-D-P8-3). The **fail-loud code half (8E item b) lands this
   session**; without the infra half, the README must carry the PRA-H4-alt
   softening so the hermetic/attribution/cost claims are not advertised as
   verified.
2. **PRA-H5 browser CI (8I infra).** Provisioning node + Playwright in CI and
   proving the fail-closed switch works needs the CI environment. The **AC-gate
   hardening (PRA-L7) and path-configurability code land this session**; the CI
   job itself is Session 2.
3. **PRA-M6 containerize-plugins (8G)** *if* the grading image cannot host the
   groundwork toolchain in this session — falls back to the documented-as-trusted
   posture, with the container path deferred.
4. **PRA-M7 real-container kill test** — the code lands now (verified against a
   monkeypatched runner); the docker-marked real-container assertion rides with
   Session 2's docker work.

Everything else — 8A, 8B, 8C, 8D, 8F, 8H, 8J, and the code halves of 8E/8G/8I —
is code+test with no external dependency and forms the consolidated Session 1.

---

## Completeness map (checked off at exit)

Every register item resolves to **Fixed+test**, **Doc**, or **Split** (with the
Session-2 owner named). No item may silently fall out.

| ID | Disposition | Slice | Owning test |
|---|---|---|---|
| PRA-H1 | Fixed+test | 8A | chain unicode round-trip property |
| PRA-H2 | Fixed+test | 8D | CSRF-refused lock POST |
| PRA-H3 | Fixed+test | 8B | one CANT_JUDGE(context_overflow) event |
| PRA-H4 | Split (code half fixed+test; proxy ship → S2) | 8E | missing-log fail-loud; real-proxy e2e (S2) |
| PRA-H5 | Split (code half fixed+test; CI job → S2) | 8I | AC-skip report; browser CI guard (S2) |
| PRA-M1 | Fixed+test | 8C | consumer TOCTOU closed |
| PRA-M2 | Fixed+test | 8C | contamination refuses mutated spec |
| PRA-M3 | Fixed+test | 8C | double-lock refused |
| PRA-M4 | Fixed+test (decision D-P8-1) | 8F | 3-arm official decision policy |
| PRA-M5 | Fixed+test | 8B | symlink content absent from packet |
| PRA-M6 | Fixed+test or Doc (decision D-P8-2) | 8G | plugin no-network or disclosure |
| PRA-M7 | Fixed (code) + S2 real-container test | 8E | kill-failed → infra_failed |
| PRA-M8 | Fixed+test | 8E | exception-path spend counted |
| PRA-M9 | Fixed+test | 8E | dead-proxy preflight refuses |
| PRA-M10 | Fixed+test | 8D | all serve routes withhold on tamper |
| PRA-M11 | Fixed+test | 8H | torn admission + content sha |
| PRA-M12 | Fixed+test | 8H | quarantine survives re-import |
| PRA-M13 | Fixed+test (decision D-P8-4) | 8B | transient CANT re-attempted |
| PRA-M14 | Fixed+test | 8F | realized CI-method label |
| PRA-M15 | Fixed+test | 8G | pristine-baseline no-flag |
| PRA-M16 | Fixed+test | 8D | foreign-Host refused |
| PRA-L1 | Fixed+test | 8A | NaN append refused |
| PRA-L2 | Fixed+test (decision D-P8-5) | 8C | unresolvable attestor refused |
| PRA-L3 | Fixed+test | 8G | xfail flags; prose does not |
| PRA-L4 | Fixed+test | 8B/8E | judge secret rescan; .tar scanned |
| PRA-L5 | Fixed+test | 8A | anchor atomicity + property sweep |
| PRA-L6 | Fixed+test | 8I | contract source-list completeness |
| PRA-L7 | Fixed+test | 8I | AC-skip reported |
| PRA-L8 | Doc | 8J | deep-dive counts pinned |
| PRA-L9 | Fixed+test | 8E | hardening flags in argv |
| PRA-L10 | Fixed+test / Doc | 8D/8H | 400 code; approval-replay documented |

## Sequencing

1. Confirm decisions D-P8-1 … D-P8-5 (append `resolved` to
   `review.decisions.ndjson`).
2. Session 1, in dependency order: **8A** (chain helper is reused), **8C**
   (`assert_lock` signature change touches every stage — do it before slices
   that also edit those CLIs), then **8B, 8D, 8F, 8G, 8H** in parallel-safe
   order, then **8E-code**, **8I-code**, **8J**. `make verify` after each slice.
3. Session 2 (infra): 8E-proxy (ship config + real-proxy docker e2e), 8I-CI
   (browser job), and the deferred real-container kill/plugin tests.
4. Exit gate: the completeness map is fully checked; `make verify` green; 7 (or
   8, if PRA-L6 adds one) import contracts kept; the README no longer advertises
   any claim the code does not yet deliver.
