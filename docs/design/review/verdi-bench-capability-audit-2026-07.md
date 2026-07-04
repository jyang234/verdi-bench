# verdi-bench: code-quality & production-readiness audit (fresh pass)

**Audit date:** 2026-07-04 · **Commit:** `9adb261` (post-Phase-8; branch
`claude/codebase-audit-competitive-rh8o2u`) · **Method:** six parallel
subsystem passes reading every module and its tests in source, seven parallel
competitive-research passes against the current (mid-2026) eval-tool landscape,
and independent re-confirmation of every High finding against the actual lines.

**Scope.** The code quality and production-readiness of every shipped
subsystem, measured against the capabilities the README and
`docs/deep-dive.md` advertise — and a grounded competitive comparison against
the leading AI-evaluation tools. This is a *fresh* capability-oriented pass, not
a re-verification of the prior register: the Phase-7 residue audit
(`verdi-bench-audit-verification.md`) and the Phase-8 production-readiness audit
(`verdi-bench-production-readiness-audit.md`, the "PRA" register) are both
merged and their findings closed. This audit re-confirmed the headline PRA fixes
are present (the ledger reader/verifier split now shares one `split_ledger_lines`
splitter; `CantJudgeReason.CONTEXT_OVERFLOW` exists; the author server enforces
CSRF/Host via `harness/http_guard.py`; `deploy/metering-proxy/` ships a reference
config; a `VERDI_REQUIRE_BROWSER` CI job exists) and then went looking for what a
capability-oriented pass at greater depth would surface that the prior register
did not. It found a new tier of findings, several of them cutting at the
instrument's deepest guarantees.

---

## 1. Executive summary

verdi-bench remains what the prior audits called it: a genuinely unusual,
unusually *honest* instrument, engineered to a standard almost no evaluation
tool applies to itself. `make verify` is green at `9adb261` — ~798 fast tests
pass, all 7 import-linter contracts hold — and the verification culture (AC
tests bound to specs at collection, import contracts proven load-bearing by
planting forbidden imports, detectors owned by planted-violation fixtures) is
real and, on re-inspection, still the strongest this class of audit encounters.
The statistics are correct where it counts (bootstrap clustering, BCa with
mid-p bias correction and jackknife acceleration, cluster-robust-t, Holm,
Horvitz–Thompson IPW kappa, quadratic-weighted kappa — all re-derived by hand in
this pass and matching), the pre-registration fence is mechanically
unbypassable in-process, and blinding is enforced by construction with a
fail-closed canary scan on every response.

But this deeper pass confirms and sharpens the prior audit's central theme, and
extends it one layer down. The prior audit found the guarantees *strongest where
self-contained, weakest at the seams to the outside world* — the ledger read
path, the browser, the container proxy, the web ceremony. Those seams are now
hardened. This pass finds a second, more interesting pattern underneath:

> **The instrument's strongest guarantees rest on trust boundaries that the
> code draws one step too generously.** Layer-0 grading trusts a results file
> written inside the same container that runs agent-authored code; the
> forensic and contamination tiers chain-anchor a *report* computed over
> workspace bytes that were never themselves committed; the cost ceiling
> enforces on the number the arm reports about itself; the citable result card
> re-derives its numbers instead of binding to the ones that were fenced; and
> one component of the "sha-locked decision rule" is actually an unlocked
> command-line flag chosen at analysis time.

None of these is a repudiation of the design. Each is a specific place where an
advertised capability outruns the code that currently delivers it — and, to the
project's continuing credit, several are already half-disclosed in a docstring
next to the code that has them. The severity tally below is the honest state of
a strong instrument with a handful of deep, closable gaps, not a weak one.

### Severity tally (this pass)

| Severity | Count | Theme |
|---|---|---|
| **High** | 7 | grade-results forgeable inside the agent container; flake-baseline prerequisite has no production caller; unverified workspace bytes laundered into a chain-anchored forensic report; cost ceiling enforces on self-reported spend; result card certifies recomputed numbers under a stale mode stamp; Holm render/dossier contradiction; multi-arm decision procedure is an unlocked analyze-time flag |
| **Medium** | ~24 | judge denial-of-judgment (canary-salting / unbounded diff); contamination probe FP can block official renders; per-item (not per-queue) reveal; escalation gates on point-estimate kappa; serve verify-cache TOCTOU; AC-hook skip-evasion; `bench status` on a typo'd dir reads healthy; tiny-n "detected"; and more |
| **Low / Info** | ~35 | dead-in-production modules; overbroad/underinclusive identity corpus; judge lacks `--actor`; no judge cost tracking; disclosure gaps; hardening nits; doc drift |

**Verdict.** Architecturally sound and, for its stamped **ADVISORY** tier,
genuinely credible — more so than any mainstream competitor on the rigor axes it
cares about. It is **not yet production-hard for the tamper-*proof*, hermetic,
"you-cannot-game-it" tier its top-line language implies**, and this pass shows
the remaining gap is one level deeper than the last audit reached: not at the
outer seams (those are closed) but in the trust boundaries drawn around
grading, forensic evidence, cost, and the citable artifact. Close the seven High
findings and the marketing and the mechanism would finally say the same thing.

---

## 2. Methodology

Six independent subsystem passes read every module and its tests in one cluster
each: (1) schema/plan/ledger/anchors; (2) run/adapters/metering-proxy; (3)
grade/judge/blind; (4) analyze/review/process; (5) forensics/contamination/
corpus; (6) status/serve/author/CLI/test-infrastructure. Baseline was
established first: `make verify` is **green** at `9adb261` (~798 fast tests,
8 conditionally-skipped docker/browser tests, 7 import contracts kept). Repo
size is ~22k lines of harness across 137 modules and ~18.4k lines of tests
(800 test functions; 116 files carry AC-named tests). All statistical formulas
were verified by hand against the source, not inferred from docstrings.

Every High finding in §5 was then re-confirmed directly against the tree by the
lead pass — the grade container writing `holdout_results.json` into `/workspace`
(`grade/container.py:29-36,119-121`); `flake_baseline`'s only in-repo caller
being the property-test entrypoint (`corpus/admit.py:240`, `experiment_id="prop"`);
`_enforcement_cost` preferring the self-reported figure (`run/interleave.py:92-101`);
the result card recomputing findings with no head-hash bind (`analyze/card.py:46-54,115-117`);
the Holm decision path diverging between markdown (`report.py:1126`, unadjusted
CI) and dossier (`dossier.py:202-208`, adjusted decision); and
`multi_arm_correction` being a CLI flag absent from the locked spec
(`analyze/cli.py:158-181`). Seven competitive-research passes verified the
market landscape against first-party docs/source (the memo in §6 flags where a
vendor page was unreachable and a claim rests on search-indexed snapshots).

---

## 3. What is genuinely well built

The substance is real; stating it plainly first, because it is what would make
someone reach for this tool.

- **The statistics are serious and correct.** Paired bootstrap resamples *tasks*
  as clusters after reducing reps to per-task means (`analyze/stats.py:75-87`);
  judge verdicts are reduced to per-task win-rates before bootstrapping
  (`report.py:347-398`, owned by a test that fails if reps are treated as
  independent). BCa uses a textbook mid-p bias correction and the correct
  jackknife acceleration `a = Σd³/(6(Σd²)^1.5)` (`ci.py:156-166`); cluster-robust-t
  is a proper bootstrap-t with a disclosed degenerate-resample fallback. The CI
  *method* is selected by empirical coverage at the realized N under a recentered
  null (`nullsim.py`), and the A/A selfcheck validates the deployed method — a
  level of statistical self-scrutiny no mainstream eval tool ships.

- **The pre-registration fence is mechanically real.** The official render runs
  six checks (locked spec, corpus identity, ledgered calibration status,
  rubric-hash agreement, passing current selfcheck, contamination symmetry,
  head-hash binding), each with a distinct `cant_analyze` reason inside a
  one-event refusal envelope (`report.py:1338-1464`, `cli.py:53-77`); the HTML
  dossier delegates to the same fence rather than re-implementing it; re-lock is
  refused under an flock guard. No in-process fence bypass was constructible.

- **Blinding is by construction, not by hope.** The judge packet builder's
  function signature *is* the allowlist (`judge/packet.py:150-156`, pinned by a
  signature-inspecting property test); transcripts, telemetry, arm labels, model
  names, tool names, and timestamps are structurally unreachable; a canary scan
  derived from the *locked* spec (including aux model ids, fail-loud on an
  unreadable entry) refuses — never scrubs — a leaking packet, and re-scans every
  HTTP response. The prompt-injection fence uses a content-derived unforgeable
  delimiter whose *framing* is hashed into `packet_sha256`.

- **The IPW kappa is done right.** Horvitz–Thompson weights over an
  equal-probability floor sample using the *realized* `ceil(0.2n)/n` inclusion
  probability (`review/sample.py:142-150`), floor-only sensitivity rendered
  beside the headline, degenerate marginals returned as `None`-not-perfect in
  *both* kappa families at the same tolerance — the hand-check `1 − 21/221`
  verifies exactly.

- **The ledger append path is careful.** Single `os.write` under an exclusive
  `flock`, `fsync`, an explicit refusal to append onto a truncated final line,
  O(1) tail reads, and one canonical `split_ledger_lines` splitter shared by
  reader and verifier (closing the prior audit's F-H1). `allow_nan=False` at the
  append boundary keeps the chain checkable by external tooling. The
  "one typed event per operation" property is swept mechanically over a registry
  of all stage entrypoints.

- **Evidence protection in grading is strong at the file layer.** Fresh-copy
  grading deletes any agent-planted `holdout_results.json`, copies symlinks as
  links (no follow), handles hostile files/dirs/symlinks, and separates terminal
  from transient failures — with adversarial tests proving a forged all-pass file
  is ignored and the real container's FAIL wins.

- **The observability tier earns "read-only".** Ledger-untouched proven three
  ways (byte-compare, dir-digest, no registered event kinds); loopback plus a
  correct shared CSRF + DNS-rebinding guard (`http_guard.py`); XSS-safe DOM
  construction through a `textContent`-only `h()` helper; the static bundle's
  one dangerous sink (JSON-in-`<script>`) neutralized by escaping every `<` to
  `<` so `JSON.parse` still round-trips.

- **The verification layer polices itself.** The AC-coverage hook aborts real
  collection on planted violations; import contracts are proven load-bearing by
  planting forbidden imports and running the real linter; the fail-closed CI
  switches (`VERDI_REQUIRE_DOCKER`, `VERDI_REQUIRE_BROWSER`) each have a guard
  test. There is essentially zero skip/xfail debt.

The recurring virtue is intellectual honesty: many of this pass's own findings
were found *because* a docstring pointed at the limitation (the detector evasion
classes, the `pristine_files={}` production gap, the attestation replay note,
the "metering proxy is your responsibility" boundary in `deep-dive.md §6`). The
audit's job was largely to find where the outward-facing claims have not caught
up to what the code already admits about itself.

---

## 4. Claim-by-claim assessment

"Delivered" = the mechanism exists and is test-owned. "Partial" = it works but a
real gap undercuts the strength of the claim. "Overstated" = the marketing
language is stronger than the delivered tier.

| Advertised claim | Verdict | Basis |
|---|---|---|
| Spec sha-locked before trials; official render refuses unregistered questions ("you cannot p-hack it") | **Delivered (fence) / Partial (decision rule)** | The fence is mechanical and unbypassable in-process, and the lock re-verifies the whole chain before trusting the recorded sha. **But** the multi-arm decision *procedure* is an unlocked analyze-time CLI flag (F-H7), a genuine post-hoc degree of freedom on a >2-arm official decision. |
| One typed, provenance-stamped event per operation; hash-chained; verify-chain + anchors detect tampering ("you cannot quietly edit history") | **Delivered for ledgered operations** | Constructor-only writes enforced by contract; one-event property swept over all entrypoints; rewrite/delete/reorder of any line with a successor detected and tested. Anchors remain the (documented) mitigation for the chain's head/tail/forged-append blind spots, and the default anchor destination is a same-UID sibling file. |
| Graders cannot hallucinate; deterministic tier imports no LLM client; grade container is network-less | **Delivered (no-LLM, network-less) / Overstated (as tamper-proof authority)** | The import contract is real and transitive; the grade container is `--network none` with read-only holdouts. **But** the grader writes its verdict into the *agent-writable* `/workspace` inside the container that executes agent code, so a motivated agent can forge its own holdout result (F-H1). Layer-0 authority is spoofable in principle. |
| A ledgered flake baseline is an admission prerequisite | **Overstated** | `bench corpus admit` requires a clean baseline event, but `flake_baseline()` has **no production caller** — the only in-repo caller is the property-test entrypoint (F-H2). The guarantee is real in the library and unexecutable through the tool's surface. |
| Hermetic per-trial containers; insulated arms; cost ceiling enforced; egress metered | **Partial** | Digest-provenance, request-mount, per-arm key isolation, kill-on-timeout (confirmed via `docker inspect .State.Running`, not `--rm`), and fail-loud on a missing proxy log are real and tested. **But** the cost ceiling enforces on the arm's *self-reported* cost when present, so an arm reporting `0` spends past the ceiling with only an advisory flag (F-H4); and the image is run by tag with the digest resolved by a separate `inspect` (F-M). |
| Identity-blind advisory judge; order-debiased; advisory-only; calibrated | **Delivered, with a denial-of-judgment channel** | Blinding, order-debiasing, and IPW-kappa calibration are trustworthy and adversarially tested. **But** an arm can force *terminal* `CANT_JUDGE` on comparisons it would lose (salt a canary, or ship a huge workspace file → context overflow), biasing `judge_preference` via a missing-data mechanism the render does not disclose per-arm (F-M). |
| Gaming looked for via planted-violation-owned detectors; flags advisory, quarantine ledgered | **Delivered (disposition) / Partial (evidence integrity)** | Detectors, planted/clean fixtures, and the advisory-only tier are exactly as advertised; quarantine is ledgered, disclosed, and cannot silently drop a trial. **But** the detectors read *live workspace bytes* that are never committed to the chain, so the resulting chain-anchored `forensics_report` launders unverified disk state (F-H3), and several FP/FN classes are undisclosed. |
| Contamination sentinel; asymmetric contamination refuses official render | **Delivered (fence) / Overstated (probe evidence)** | The asymmetry fence is recomputed from the ledgered probe and is unbypassable. **But** the canary is a public function of the published `task_sha` (not a secret), and the oracle-prefix probe has no control condition — a false-positive probe (cheap to manufacture, or naturally occurring on formulaic code) blocks the official render (F-M). |
| Paired bootstrap, coverage-validated CI, MDE always reported, A/A selfcheck gates official | **Delivered, with edge-case gaps** | The core machinery is correct and coverage-validated. **But** the selfcheck validates the coverage-*selected* method on the same 200 draws that selected it (anti-conservative), tiny-n comparisons are declared "detected" with a zero-width CI, and displayed MDE is the plan-time constant, never reconciled to realized N (F-M). |
| Citable, comparable result card certifies a rendered result | **Overstated** | Comparability refusals across task sets/metrics are real and tested. **But** `card emit` recomputes findings over the *current* ledger and stamps the last render's mode, so post-render quarantines/re-grades yield an "official"-stamped card whose numbers match no fenced render (F-H5); the HTML card drops every disclosure the markdown card carries (F-M). |
| Every ledgering verb accepts `--actor`, refused rather than defaulted | **Partial** | True for `plan`, `grade`, `anchor`, author/review verbs. **But** `bench judge` has no `--actor` option and defaults its events to `"local"` (F-L), contradicting the README sentence. |
| Secrets don't leak into artifacts | **Delivered at the capture boundary** | Whole-workspace redaction, PEM-body scrubbing, non-UTF-8 handling, per-arm key isolation, values never on argv, read-back-verified trajectories. Residual gap is narrow: literal/shape redaction cannot catch base64/URL-encoded/reversed forms of a key (F-L, own-arm artifact only). |

---

## 5. Findings (prioritized, with evidence and remediation)

File:line references are to `9adb261`. Every High was re-confirmed against
source in this pass.

### High

**F-H1 — Deterministic grading trusts a results file written inside the same
container that executes agent-authored code.**
`harness/grade/container.py:29-36` documents `HOLDOUT_RESULTS =
"holdout_results.json"` as the file "the grader container writes … inside
`/workspace`", and `:119-128` reads exactly that path back and ledgers it as the
grade. GR-1 removes any *pre-existing* forged copy (`:294-317`), but the grader
writes the authoritative results into the agent-writable workspace copy during a
run in which holdout tests import and execute the agent's solution. A motivated
agent can register an `atexit` hook or background thread (in code the holdouts
import) that overwrites `holdout_results.json` with an all-pass vector after the
grader writes it; the harness then reads the forged file. Relatedly, `/holdouts`
is mounted read-only but *readable*, so agent code running at grade time can read
the expected assertions and adapt. The deterministic grade is the instrument's
Layer-0 authority — the "graders cannot hallucinate" claim — and it is spoofable
in principle. The forensic tier would look for holdout tampering, but its flags
are advisory (and see F-H3). *Confirmed in source.*
**Remediation:** have the grader emit results outside the workspace mount (a
results-only volume writable only by the grader entrypoint's uid, or capture via
container stdout), run holdout tests as a non-privileged uid distinct from the
results writer, and consider not exposing expected holdout values to the process
that imports agent code. Add a planted-violation test (an agent workspace that
overwrites the results file post-grade) that must still FAIL.

**F-H2 — The flake-baseline admission prerequisite has no production caller;
the guarantee it backs is currently unexecutable through the tool.**
`harness/grade/baseline.py:37` `flake_baseline()` is invoked in-repo only by the
property-test entrypoint (`harness/corpus/admit.py:240`, `experiment_id="prop"`,
`task_id="cand-prop"`, `_PROP_SHA`). No `bench` verb runs a baseline, yet
`bench corpus admit` *requires* a ledgered clean baseline (`admit.py:60-65,
195-198`) and the README/deep-dive advertise it as an admission prerequisite. In
practice an operator must hand-write a library-calling script — or call
`events.record_flake_baseline` with self-supplied `results`, which the ledger
chains happily though nothing ran. The baseline's "unmodified workspace"
semantics are also undefined for fail-to-pass tasks (a SWE-bench task
materialized pre-patch fails holdouts 5/5 and would always quarantine), and
k=5 zero-tolerance has weak detection power (a 2%-per-run flake passes ≈90% of
the time) that no claim discloses. *Confirmed in source.*
**Remediation:** add a `bench corpus baseline` verb that runs `flake_baseline`
against a defined workspace (baseline against the reference solution, all-pass
required); define and test the workspace contract; document the operating
characteristic or raise k for the agent-free admission gate.

**F-H3 — Forensic and contamination reports chain-anchor a verdict computed over
uncommitted workspace bytes.**
`harness/forensics/scan.py:148-167` builds `workspace_files` by reading the live
workspace at scan time; `harness/contamination/scan.py:58-83` does the same for
the solution. Trajectories *are* sha-verified against the chain (a tampered
trajectory becomes a named coverage gap, tested), but the workspace bytes the
end-state detectors read have **no** commitment anywhere. Anyone with disk access
can, after `run`/`grade`, delete the hardcoded literal or the leaked-holdout copy
from the workspace and then run `bench forensics scan` / `bench contamination
probe`; the resulting `forensics_report` / `contamination_probe` event is
chain-anchored "clean" and citable forever. The scan docstring's claim "a record
is never evidence unless its bytes matched the chain" is true only for
trajectories — the asymmetry is disclosed nowhere. This is the one place the
"you cannot quietly edit history" story actually breaks: history is
tamper-evident, but the *evidence the history points at* is not. *Confirmed in
source.*
**Remediation:** ledger a workspace content hash at trial/grade time (the grade
stage already walks the workspace) and have both scanners verify it or disclose a
per-trial `workspace_unverified` in `coverage`, mirroring the existing trajectory
coverage-gap mechanism.

**F-H4 — The cost ceiling is enforced on self-reported spend; an arm that
under-reports spends past the pre-registered ceiling.**
`harness/run/interleave.py:92-101` (`_enforcement_cost`) returns `telemetry_cost
if telemetry_cost is not None else proxy_metered_cost` — the out-of-band proxy
figure is used *only when self-report is null*. When an arm self-reports any
cost, that number drives the `CostGuard` even if the proxy metered far more. An
adversarial or buggy arm that emits `{"total_cost_usd": 0.0}` accumulates nothing,
`would_exceed()` never trips, and it runs every repetition regardless of real
spend. The seam computes `flags.proxy_cost_delta` but it is advisory and never
gates. This defeats the pre-registered `cost_ceiling` for exactly the
self-reporting arms it is meant to bound. *Confirmed in source.*
**Remediation:** enforce on `max(telemetry_cost, proxy_metered_cost)` when both
exist (proxy is the trusted meter), keeping `telemetry.cost` null-preserving in
the record per D004; at minimum treat a large positive `proxy_cost_delta` as a
ceiling-relevant signal, not merely a flag.

**F-H5 — The citable result card certifies recomputed numbers under a stale
"official" stamp.**
`harness/analyze/card.py:46-54` (`_rendered_mode` returns the *last*
`findings_rendered` event's mode) and `:115-117` (`build_card` recomputes
`compute_findings` over the *current* ledger). Nothing binds the two: the card
never checks that the last render's `ledger_head_hash` still corresponds to the
data it now projects. After `bench analyze --official` succeeds, an operator who
quarantines a trial or runs `--retry-terminal` appends events; `bench card emit`
then emits fresh deltas/CIs stamped `"mode": "official"` and `selfcheck: passed`
— a citable artifact whose numbers match no fenced render and no ledgered
`findings_sha256`. The markdown render itself *does* refuse a stale head
(`report.py:1095-1106`); the card path simply omits that guard, contradicting its
own docstring ("the card certifies a rendered result"). *Confirmed in source.*
**Remediation:** have `build_card` read the last `findings_rendered` event's
`ledger_head_hash` and refuse (`CardError`) if any data-bearing event post-dates
it, or verify the recomputed findings' sha256 equals the ledgered
`findings_sha256`.

**F-H6 — Under Holm correction, the markdown render and the dossier can disagree
about whether an effect was detected.**
`_apply_holm` rewrites `cf.decision["detected"]` from a two-sided bootstrap
p-value (`report.py:840-863`), but `_comparison_lines` re-derives detection from
the *unadjusted* 95% CI: `detected = s["ci_low"] > 0.0 or s["ci_high"] < 0.0`
(`report.py:1126`), while the dossier branches on `cf.decision.get("detected")`
(`dossier.py:202-208`). When Holm fails to reject but the raw CI excludes zero
(entirely possible — the Holm p uses a plain recentered percentile bootstrap,
while the deployed CI may be BCa or cluster-robust-t), one `bench analyze`
invocation writes `findings.<mode>.md` saying "Effect detected" and
`findings.<mode>.dossier.html` saying "No effect ≥ MDE detected". The displayed
CI also stays at the per-comparison 95% level with no family adjustment, so
decision and interval follow different procedures. *Confirmed in source.*
**Remediation:** make `_comparison_lines` branch on `cf.decision["detected"]`
(single source of truth), and either derive the Holm p from the same CI
machinery or disclose that decision and interval use different estimators.

**F-H7 — The multi-arm decision procedure is chosen at analyze time, not lock
time — a post-hoc degree of freedom on an "official" decision.**
`multi_arm_correction` is a CLI flag (`analyze/cli.py:158-181`) consumed by
`compute_findings` (`report.py:866-885`); it is not in the sha-locked spec and no
fence checks it. In a >2-arm design the analyst can run `--official` with `none`
(primary-pair decision from the CI) and, if unhappy, re-run `--official
--multi-arm-correction holm` (decision now from the bootstrap p) — two different
official decision procedures for the pre-registered primary pair, both
renderable, both fenced identically. The README sells "decision rule sha-locked
before any trial runs"; this is a decision-rule component that is not locked. (For
2-arm designs `n_pairs == 1` so the flag is inert.) A related edge case: under
`holm`, a single-task *secondary* pair with a zero-width bootstrap CI is declared
an *official* detected effect with p ≈ 1e-4, because the selfcheck floor gates
only the primary pair. *Confirmed in source.*
**Remediation:** pre-register the multi-arm policy in the locked spec, or make the
official fence refuse a second official render whose correction differs from the
first; add a minimum-cluster floor for any `detected=True`.

### Medium (grouped)

**Judge integrity at the seams.**
- *Denial-of-judgment:* `judge/assemble.py:63-77` concatenates every workspace
  file into the diff with no size cap, and any canary substring trips a terminal
  `CANT_JUDGE(identity_leak)`; both reasons are terminal, permanently excluding
  the comparison from `judge_preference` and calibration. A gamed arm can salt a
  canary (or a huge junk file → `context_overflow`) only on trials it would lose,
  a biased missing-data mechanism the render does not disclose per-arm. *Fix:*
  cap/truncate the assembled diff deterministically; report CANT_JUDGE counts per
  reason per arm and flag asymmetry as a confound.
- *Identity corpus is overbroad and underinclusive:* `blind/core.py:75-93`
  includes `\bgoogle\b` and `\bassistant:\s` (any Google-API or chatbot task
  silently zeroes judge coverage as a terminal `identity_leak`) yet omits
  ChatGPT/Grok/DeepSeek/Qwen/Copilot/Cursor/Aider/Mistral/Llama. *Fix:* scope the
  broad tokens to vendor context, extend the product list, surface identity_leak
  rates per task class.
- *No judge cost tracking or ceiling:* `judge_pair` makes two provider calls per
  comparison with no token accounting and no spend cap; the experiment
  `cost_ceiling` governs only trials. *Fix:* record provider usage on verdict
  provenance and honor a judge-scoped ceiling.
- *No in-call retry/backoff:* `providers/_http.py:31` has a fixed 120 s timeout
  and zero retries, so an HTTP 429 fails the whole batch closed; Anthropic caps
  output at a hardcoded 2048 tokens while OpenAI/Google set none, and a truncated
  JSON becomes a *terminal* `parse`. *Fix:* bounded backoff for 429/5xx, uniform
  max-output-tokens, reconsider `parse` as transient.

**Statistical edges.**
- *Selfcheck circularity:* `coverage_from_deltas` picks the method whose coverage
  is closest to nominal and `run_selfcheck` passes iff nominal lies in the Wilson
  band of *that same* 200-sim estimate (`selfcheck.py:91-93`) — selection and
  validation share the draws, biasing the gate toward passing and giving it little
  power. *Fix:* validate on a fresh sub-seeded stream; raise default `n_sim`.
- *Tiny-n "detected":* a single task cluster yields a zero-width CI that
  `excludes_zero()`, so n_tasks=1 is declared "Effect detected" with no floor
  outside the primary-pair selfcheck. *Fix:* a minimum-cluster floor for any
  `detected=True`.
- *MDE never reconciled to realized N:* the null phrasing interpolates the
  plan-time MDE even when quarantines/missing grades shrank n_tasks, overstating
  sensitivity. *Fix:* recompute or bound achieved MDE at realized N.
- *Escalation gates on a point estimate:* `review/calibrate.py:64-78` sets
  `escalate = k < 0.6` at n≥20 with no SE/CI, and the n≥20 floor ignores IPW
  effective sample size (floor weights of 5 make effective-n far smaller); process
  kappa is worse (`min_pairs=1`, so one pair renders "sufficient"). *Fix:*
  bootstrap/Fleiss SE and gate on the interval or Kish effective-n; thread the
  locked floor into process calibration.

**Contamination probe evidence.**
- *Canary is not secret:* `derive_canary` is a pure function of the published
  `task_sha` (`canary.py:29-43`), so an adversary can pre-derive a rival arm's
  canaries and plant them in public text to manufacture an asymmetric flag that
  *blocks the official render* — a cheap denial-of-finding. *Fix:* derive from a
  per-corpus out-of-band salt, or disclose the limitation beside the near-zero-FP
  claim.
- *Oracle-prefix probe has no control:* it flags when a strong model reconstructs
  ≥50% of the remainder's fingerprints, with no null baseline — formulaic code a
  clean model can legitimately continue trips it, and one FP is asymmetric →
  official refused. *Fix:* add a perturbed-prefix control and require a margin.
- *Unscanned trials/insulation alarms only on stderr:* the ledgered
  `contamination_probe` carries only `overlap_flags`; a wiped-workspace UNSCANNED
  trial is indistinguishable from scanned-clean in the summary. *Fix:* put
  `skipped`/`alarms` on the probe event and disclose unscanned counts.

**Isolation & hardening.**
- *Serve verify-cache TOCTOU:* `serve/server.py:169-184` keys the chain verdict on
  `(st_size, st_mtime_ns)`; a same-size rewrite + `os.utime()` serves tampered
  events from `/api/events|timeline|trial|compare` while `/api/status` (uncached)
  disagrees — a hole in the serve tier's "withheld on tamper" property. *Fix:* key
  on a content hash (verify already reads the whole file) or drop the cache.
- *Grade image run by tag:* `harbor.py:285` runs `request.image` while `:290`
  resolves the digest by a separate `inspect`, so a tag-only image runs "whatever
  the tag points to locally" with a TOCTOU window vs provenance. *Fix:* run the
  resolved `repo@sha256:digest`, or refuse tag-only images.
- *Unknown `--runner`/`--multi-arm-correction`-style typos:* `grade/cli.py:185`
  silently selects docker on any non-`local` value (analyze validates its flag; grade
  does not). *Fix:* validate against the closed set and exit 2.

**Test-infrastructure & observability soft spots.**
- *AC-hook skip evasion:* `ac_coverage.py:91-101` inspects only per-function
  decorators, so a module-level `pytestmark = pytest.mark.skip`, a class-level
  skip, a bare `pytest.skip()` body call, or `@skipif(True)` disables an AC test
  while satisfying the presence gate. *Fix:* scan `pytestmark` and class
  decorators; flag constant-true `skipif`.
- *`bench status <typo>` reads healthy:* `status/cli.py:88-105` treats an absent
  ledger as `chain OK (empty)`, so a mistyped directory renders as a plausible
  "not yet planned" experiment — a silently-wrong answer from the observability
  verb. *Fix:* require the directory to exist.
- *`author`/`review` in no LLM-free contract:* the authoring ceremony and the
  blinded reviewer surface could directly import `harness.judge.client` without
  breaking any contract; for the reviewer surface an LLM-client ban would be cheap
  and on-message. *Fix:* add the contract.

**Other Mediums:** groundwork grader is a silent production no-op
(`plugins/groundwork.py:31-34` reads only `fake_plugin_output`); per-item (not
per-queue) capture-then-reveal lets a reviewer unblind item 1 then record
"blinded" verdicts for the rest (`record.py:119-123`); a missing transcript is
judge-scored as empty rather than `CANT_SCORE` (`process/cli.py:27-36` docstring
vs `score.py` behavior); `card emit --format md` crashes on asymmetric
contamination (`card.py:294-297` joins dicts); the HTML card drops all
disclosures (`card.py:310-367`); `retrigger_baselines` (AC-6) has no production
caller (`corpus/registry.py:223-234`); admission's content check hashes a
projection, not the file (`admit.py:129-144`); the anchor store is
unlocked/unsynced and a corrupt line crashes verification
(`ledger/anchors.py:62-67,109-132`); `verify_against_anchor` fails open on an
empty store (`anchors.py:109-132`).

### Low / Info (representative)

`bench judge` has no `--actor` and defaults events to `"local"`, contradicting the
README (`judge/cli.py:24-27`); dead-in-production modules (`judge/calibrate.py`'s
raw-pooled kappa, superseded by the IPW seam; the pristine-diff attribution
branch reachable only from tests since `scan.py` passes `pristine_files={}`; a
dead `policy` param in `score_trial_process`); redaction cannot catch
base64/URL-encoded/reversed key forms (own-arm artifact only); several undisclosed
detector FP/FN classes (deletion-flagged-as-insertion, path-representation
mismatch nulling `holdout_tamper`, non-UTF-8 evasion, xfail omission at runtime);
`findings.json` is unwatermarked and mode-ambiguous; power-sim docstring
mislabels a plain percentile bootstrap as "recentered-null"; the interleave
reimplements `seeded_shuffle` inline instead of calling it; heavy fixed
`waitForTimeout` sleeps in browser tests (flakiest pattern in the repo); three
contract tests mutate live source files and restore in `finally` (a hard kill
leaves the plant in the tree); `SeqGradeRunner` silently replays past exhaustion;
trial IDs use unseeded `uuid4` (uniqueness, not reproducibility — worth stating as
a designated identifier seam); typing gaps on ledger `path` parameters against the
repo's own typed-Python convention; `docs/adapters.md` documents trajectory
schema v2 while the code is at v3.

---

## 6. Competitive positioning

verdi-bench does not compete on scale and should not be read as trying to. The
useful question is: *given an established tool already exists, why reach for this
one?* The seven-pass market scan below (all claims grounded in first-party docs
or source; unreachable vendor pages are flagged) answers it precisely — and,
importantly, is honest about where several of verdi-bench's "differentiators" are
rarer as *integration* than as *ideas*.

### The landscape (mid-2026)

The market has two centers of gravity. The **safety-institute / agent-benchmark**
axis has consolidated onto **UK AISI Inspect** (MIT; the de-facto standard —
METR is migrating off Vivaria onto it; Epoch AI runs its Benchmarking Hub on it)
plus **Harbor** (Laude Institute, Apache-2.0; the current state of the art for
parallel cloud-sandbox fan-out, thousands of environments via Daytona/Modal), with
**SWE-bench** and **Terminal-Bench** as the reference task batteries. The
**product / observability** axis is **LangSmith** and **Braintrust** (hosted,
commercial), plus open cores **Langfuse** (MIT), **Arize Phoenix** (Elastic
License 2.0), **W&B Weave** (Apache SDK, CoreWeave-owned), and the red-teaming
specialist **promptfoo** (MIT, being acquired by OpenAI). Academic batteries —
**lm-evaluation-harness** (the one place bootstrap/analytic stderr is standard),
**HELM** (in maintenance mode since June 2026) — round it out. Two 2026 signals
are worth noting: **OpenAI's hosted Evals platform shuts down entirely on
2026-11-30**, and **HELM entered maintenance mode**, so the "run-and-compare"
middle is thinning even as the rigor edges stay unoccupied.

### Where verdi-bench is genuinely ahead

These are capabilities that, after checking each competitor's current docs/source,
**no** mainstream tool ships as a first-class, test-enforced property:

1. **Pre-registration with an enforcing fence.** The sha-locked spec plus an
   official render that *refuses* unregistered questions is unique as shipping
   tooling. The idea is codified and converging in the literature (Prep-eval,
   "Preregistration for Experiments with AI Agents", BetterBench's audit of the
   gap), but no eval harness *binds* pre-registration to execution. Every product
   tool — LangSmith, Braintrust, Langfuse, Phoenix, Weave, promptfoo — lets you run
   first and pick the favorable metric afterward.
2. **A tamper-evident, hash-chained, externally-anchorable ledger.** No mainstream
   eval framework ships this; competitors' "audit logs" (LangSmith/Braintrust/W&B
   Enterprise) are conventional admin trails, and Inspect's score-edit provenance
   is append-only but not hash-chained. The primitive is a commodity elsewhere
   (Crosby–Wallach tamper-evident logs; EQTY Lab commercializes the neighborhood
   for AI governance), so this is rare *in eval tooling*, not novel in computing.
3. **Statistical seriousness at small N.** Paired trials with seeded interleave, a
   task-clustered paired bootstrap, a coverage-validated CI method, MDE always
   reported, and an A/A selfcheck gate. This is the deepest genuine edge on the
   *product* axis — LangSmith/Braintrust/Langfuse/Phoenix/Weave report score deltas
   with **no** CIs or significance testing at all. It is a *narrower* edge on the
   *research* axis: Inspect ships `stderr(cluster=...)` and `bootstrap_stderr`,
   lm-eval-harness ships bootstrap stderr, Epoch reports ±SE from repeated runs,
   and Chatbot Arena has done paired bootstrap CIs since 2023. What no one else
   combines is *paired trials in hermetic containers + a coverage-validated CI
   method + a pre-registered decision rule + an A/A coverage gate* as one
   instrument. (Anthropic's own "Adding Error Bars to Evals" prescribes exactly the
   paired-difference + clustered-SE recipe — so the statistics are field-standard
   method, well-executed, not a proprietary insight.)
4. **LLM-judge governance rather than judge-as-truth.** Identity-blind with canary
   verification, order-debiased, explicitly *advisory*, calibrated against blinded
   humans with IPW-corrected kappa, its one designed dependence disclosed in every
   render. This is a real bundle, but the honest framing is *integration*: blinding
   + position randomization ships off-the-shelf (DeepEval Arena G-Eval, MT-Bench,
   Arena-Hard-Auto), judge-vs-human calibration is productized (LangSmith Align
   Evals reports an agreement %, Phoenix publishes judge P/R/F1 against golden
   datasets), and kappa specifically is the one uncommon piece — most tools report
   agreement/F1, not a chance-corrected coefficient. verdi-bench is the only one
   that does *all* of it, deterministically, with the dependence disclosed — but
   each component individually has a 2024–2026 shipping precedent.
5. **Integrity tiers wired into the verdict.** Gaming forensics and a contamination
   sentinel, with the asymmetric-contamination fence actually refusing an official
   render. Again rarer as integration than as concept: forensics-of-cheating ships
   (Transluce **Docent**, open-source, with a "cheating" tag; METR's reward-hacking
   monitors; promptfoo's `verifier-sabotage` red-team plugin), and contamination
   sentinels are five years old (BIG-bench canary GUIDs, ETH's ConStat, LiveBench).
   What's uncommon is a detector/sentinel *built into a general A/B harness as a
   fence input* rather than a separate analysis step.
6. **A verification culture applied to the instrument itself.** AC-to-test binding
   at collection, load-bearing import contracts, planted-violation fixtures. This
   has no real analog in any competitor and is the least-imitable thing here.

The one-sentence version, honestly qualified: **verdi-bench is the only tool in
the field built to produce a *defensible A/B decision* — one you could put in a
procurement memo, a migration sign-off, or a published claim and expect to survive
a hostile reviewer — and it is unique less because any single mechanism is
unprecedented than because it integrates six of them behind one pre-registered,
hermetic, self-verifying fence.**

### Where it falls behind (and why that's often fine)

- **Scale and throughput.** Serial local execution, no fleet scheduler, no
  Kubernetes/cloud-sandbox path. Inspect has k8s/Modal/Daytona back-ends and eval
  sets with retry; Harbor fans out to thousands of parallel environments. This is a
  deliberate "rigor costs wall-clock" trade, but it rules out large batteries and
  high-volume iteration — the exact thing the research axis is best at.
- **Breadth.** Two native adapters plus a generic log format, versus Inspect's
  provider matrix; no bundled benchmark library (deliberate) versus `inspect_evals`
  (200+ implementations) or lm-eval-harness (60+ benchmarks). You bring your corpus.
- **Observability and monitoring.** No production tracing, no OpenTelemetry, no
  online monitoring/alerting. It is an experiment instrument, not an ops-time
  observability platform — a different category from LangSmith/Langfuse/Phoenix/
  Weave, all of which are OTel-native and built around live traces.
- **UX, collaboration, ecosystem.** A CLI plus minimal local web views versus
  polished hosted dashboards, annotation queues, playgrounds, and team workflows; a
  single repo with no PyPI package, no plugin ecosystem, no community, and a
  correspondingly high bus factor.
- **Security/red-teaming.** No prompt-injection or vulnerability probing — that is
  promptfoo's specialty (50+ plugins, adaptive attacks, now OpenAI-backed), and its
  `verifier-sabotage`/`sandbox-escape` coding-agent plugins are the closest anyone
  gets to verdi-bench's gaming-detection turf.
- **Sandbox maturity.** Inspect's sandboxing toolkit gives per-sample Docker/k8s
  isolation with network blocked by default, out of the box; verdi-bench's hermetic
  story still leans on an operator-supplied metering proxy and, per F-H1/F-H3/F-H4,
  has trust-boundary gaps a mature sandbox would not. This is the one area where a
  *stronger* competitor is also *ahead on verdi-bench's own turf*.

### When to reach for which

- **Reach for verdi-bench** when the deliverable is a *defensible decision* between
  two agent stacks — procurement, a migration/rollback call, an audit/compliance
  context, a publishable comparison — especially at small N, with
  adversarial-integrity concerns (gaming, contamination), and a need to hand a
  skeptic an auditable trail. Nothing else in the market is shaped for this.
- **Reach for Inspect AI** for large agentic evaluations needing mature, scalable
  sandboxing and a ready task library — and note it already ships clustered/
  bootstrap stderr, so it is the closest philosophical peer on rigor.
- **Reach for Braintrust / LangSmith** for day-to-day prompt iteration, production
  monitoring, judge-vs-human alignment loops, and team collaboration at scale.
- **Reach for Harbor / SWE-bench / Terminal-Bench** for standardized, parallel,
  leaderboard-grade agent batteries.
- **Reach for promptfoo** for security red-teaming and reward-hacking probes.

verdi-bench is a scalpel in a landscape of dashboards and batteries. That is its
value and its limit.

---

## 7. Remediation roadmap

Ordered by credibility-per-unit-effort — the earliest items close the widest gap
between advertised and delivered trust.

**Before advertising the tamper-proof / hermetic / "cannot-game-it" tier
(ship-blocking):**
1. **F-H1** — move grader results out of the agent-writable mount and run holdouts
   under a distinct uid; add a planted post-grade-overwrite test. *(Restores
   Layer-0 authority — the "graders cannot hallucinate" claim.)*
2. **F-H3** — commit a workspace content hash and have the forensic/contamination
   scanners verify or disclose it. *(Closes the one real break in "cannot quietly
   edit history".)*
3. **F-H4** — enforce the cost ceiling on `max(self-report, proxy)`. *(Small; closes
   the self-report loophole in the pre-registered ceiling.)*
4. **F-H7** — lock the multi-arm decision policy in the spec (or fence a
   correction change), plus a minimum-cluster `detected` floor. *(Closes a genuine
   post-hoc degree of freedom on official decisions.)*
5. **F-H2** — ship a `bench corpus baseline` verb and define its workspace
   contract, so the admission prerequisite is actually executable and audited.

**Next (artifact & render integrity):** F-H5 (card head-hash bind) and F-H6 (Holm
single-source-of-truth for detection) — both small, both keep the *citable*
artifacts honest, which for an instrument whose whole pitch is defensibility is the
category that most needs to be right.

**Then (harden the deeper seams):** judge denial-of-judgment (cap the diff, disclose
per-arm CANT_JUDGE asymmetry); contamination probe control condition + secret-salted
canary; escalation on an interval not a point estimate; the serve verify-cache
TOCTOU; the AC-hook skip-evasion; per-queue reveal enforcement; groundwork's silent
no-op; the anchor-store durability/fail-open nits.

**Documentation truth-up (cheap, high-trust-yield):** add `--actor` to `bench
judge` (or fix the README sentence); correct the trajectory schema-version doc;
state the disclosed detector FP/FN classes; note the flake-baseline operating
characteristic; align the "LLM-free contract" wording with the transitively-scoped
contract it actually is. Each is an honest limitation the code already lives with;
saying so is what keeps the instrument's central promise — "an unverified claim
about the instrument is a defect in the instrument" — true of the instrument's own
description.

---

## 8. Bottom line

verdi-bench delivers, in working and test-owned code, a genuinely differentiated
capability that no mainstream eval tool matches: a defensible, auditable A/B
verdict between agent stacks, with statistical and integrity machinery built to
survive a hostile reviewer. The engineering is disciplined and, rarely, honest
with itself — the prior audit's outer-seam findings are closed, and the
verification culture is intact.

This deeper pass shows the remaining gap is one layer beneath where the last
audit reached. It is no longer at the browser, the proxy, or the ledger read path
— those are fixed. It is in the trust boundaries the code draws around its own
authority: grading trusts a file the agent can reach, forensics chain-anchors
evidence it never committed, the cost ceiling trusts the arm's self-report, and
the citable card and one decision-rule knob drift from the fenced numbers they
claim to certify. These are seven specific, closable gaps in a strong instrument
— not a weak one. Close them, and verdi-bench's marketing and its mechanism would
finally say the same thing, which is the one bar an instrument built on
checkable claims most needs to clear.
