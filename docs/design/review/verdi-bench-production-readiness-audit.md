# verdi-bench: production-readiness & competitive audit

**Audit date:** 2026-07-04 · **Commit:** `3f639f1` · **Auditor:** codebase audit (six
parallel subsystem passes + independent claim verification)

**Scope.** Code quality and production-readiness of every shipped subsystem,
measured against the capabilities the README and `docs/deep-dive.md` advertise;
plus a competitive comparison against the leading AI-evaluation tools. Every
finding below was read in source and, for the high-severity items, reproduced
or confirmed against the actual code — not inferred from docstrings.

---

## 1. Executive summary

verdi-bench is a genuinely unusual instrument, and mostly in a good way. It sets
out to answer *"is agent stack A really better than B, and can you defend it?"*
with a discipline almost no evaluation tool applies to itself: pre-registration,
a hash-chained ledger, paired-trial statistics with a coverage-validated
confidence-interval method, an identity-blind LLM judge governed by IPW-kappa
calibration, and integrity tiers (gaming forensics, contamination sentinel)
wired into the render fence. The engineering quality is high and, more
importantly, *honest*: fail-loud is real rather than aspirational, mocks sit at
true external boundaries, the trust model's limits are candidly documented, and
a large fraction of the trust claims are backed by an enforcing test or an
import-linter contract that actually fails when the guarantee is broken.

The instrument's own headline — "every trust claim is backed by an enforcing
test or structural contract, not by convention" — is *substantially* true. The
verification layer is one of the strongest this audit has seen: acceptance
criteria are mechanically bound to named tests at collection time, import
contracts are proven load-bearing by planting forbidden imports, and detectors
carry planted-violation fixtures that must flag alongside clean fixtures that
must not.

But "benchmark-grade" and "production-ready" are higher bars than "well
tested," and the audit found a consistent pattern: **the guarantees are
strongest where they are self-contained and weakest at the seams to the
outside world.** Three of the four highest-severity findings sit at exactly
those seams — the reader/verifier split in the ledger, the not-in-repo metering
proxy that the hermetic/cost claims rest on, and the CSRF-open lock ceremony.
None of these means the design is wrong; each means a specific advertised
capability currently outruns the code that delivers it.

**Verdict.** Architecturally sound and unusually credible as an *advisory*
instrument, which is exactly the tier it stamps on its output. It is **not yet
production-ready for the trust level its top-line marketing implies** ("you
cannot p-hack it", "the graders cannot hallucinate", "hermetic containers")
until the seam-level gaps below are closed. The gap between the delivered
*tamper-evident, advisory* tier and the claimed *tamper-proof, hermetic* tier
is the single theme a reader should carry away — and, to the project's credit,
the code's own docstrings already admit most of it even where the README does
not.

### Severity tally

| Severity | Count | Theme |
|---|---|---|
| **High** | 5 | ledger read-path DoS; CSRF lock forgery; judge crash voids a headline invariant; metering-proxy trust chain (unshipped, spoofable, fail-open); browser-tier ACs unverified in CI |
| **Medium** | ~18 | TOCTOU/race windows on the lock; multi-arm multiple-comparison inflation; symlink leak into the blind packet; unsandboxed grader plugins; several fail-open/torn-state paths |
| **Low / Info** | ~30 | doc drift, non-atomic secondary writes, disclosure gaps, hardening nits |

---

## 2. Methodology

Six independent audit passes read every module and its tests in one subsystem
cluster each: (1) plan/schema/ledger/anchors, (2) run/adapters, (3)
grade/judge/blind/process, (4) analyze/review/forensics/contamination, (5)
corpus/serve/author/status/CLI, (6) the verification layer itself
(tests/tooling/docs). Baseline was established first: `make verify` is **green**
at `3f639f1` — 725 fast tests pass (3 Docker-conditional skips), and all **7
import-linter contracts** are kept. Repo size: ~21.4k lines of harness across
132 modules, ~16.5k lines of tests across 82 files (a ~0.77:1 test-to-code
ratio, and higher by test *count* — 717 test functions, 352 of them
AC-named).

High-severity findings were then re-confirmed directly against source by the
lead auditor (e.g. the `splitlines()`/`split(b"\n")` divergence, the absent
`CantJudgeReason` member, the missing proxy component, the absence of any
Origin/CSRF check).

---

## 3. What is genuinely well built

This is not a code-quality-theatre project; the substance is real. Worth
stating plainly before the gaps, because it is what would make someone reach
for this tool:

- **The statistics are serious and correct where it counts.** The paired
  bootstrap, BCa interval (jackknife acceleration and Acklam PPF verified by
  hand), Cliff's delta, and IPW-corrected Cohen's/weighted kappa were all
  checked against their formulas and against hand-computed values. The
  confidence-interval *method* is chosen by empirical coverage at the realized N
  under a recentered null (`nullsim.py`), and the A/A selfcheck validates the
  deployed method from the same random stream the render uses — a level of
  statistical self-scrutiny essentially no mainstream eval tool ships.

- **The pre-registration fence is mechanically real.** The official render runs
  all six checks (locked spec, corpus identity, rubric-hash agreement, passing
  current selfcheck, no asymmetric contamination, head-hash binding), the HTML
  dossier delegates to the same path rather than re-implementing it, and the
  asymmetry check is recomputed from the *ledgered* probe with the findings
  field only a widening fallback. The audit could not construct a fence bypass.

- **Blinding is by construction, not by hope.** The judge packet builder's
  function signature *is* the allowlist (a property test pins the exact
  parameter set), holdout detail is stripped to `{id, result}`, per-experiment
  canaries are derived from the locked spec including aux model ids and fail
  *loudly* on an unreadable aux entry, and the finished packet plus every HTTP
  response is re-scanned fail-closed. Capture-then-reveal verifies the chain
  *before* the reveal gate, so a forged human verdict cannot unblind.

- **Evidence protection in grading is strong.** Fresh-copy grading removes any
  agent-planted `holdout_results.json`, copies symlinks as links (no follow),
  and separates terminal from transient failures — with a planted-violation
  test proving an agent's forged all-pass file is ignored and the real
  container's FAIL wins.

- **The ledger append path is careful.** Single-`os.write` under an exclusive
  `flock`, `fsync`, an explicit refusal to append onto a truncated final line,
  and O(1) tail reads. Tamper/deletion/reorder/atomicity/concurrency are all
  genuinely tested, and the "one typed event per operation" property is swept
  mechanically across a registry of all 16 stage entrypoints.

- **The verification layer polices itself.** The AC-coverage hook is
  meta-tested against planted violations and proven to abort real collection;
  import contracts are proven load-bearing by planting forbidden imports and
  running the real linter; the README verb/contract checker plants a phantom to
  prove the checker works. There is essentially zero skip/xfail debt.

- **Honest-null semantics are structural.** Unmeasurable telemetry, trajectory
  fields, and forensic metrics are `Optional` end to end and validated so a
  silently-imputed zero is unrepresentable. "Not measured" never becomes "0".

The recurring virtue is intellectual honesty: many known limitations are
already admitted in-code (the ledger's `D002` "tamper-evident, not
tamper-proof" note; the "full real-proxy egress e2e is intentionally out"
comment). The audit's job was largely to find where the *outward-facing* docs
have not caught up to what the code admits about itself.

---

## 4. Claim-by-claim assessment

Each row is the audit's verdict on an advertised capability. "Delivered" means
the mechanism exists and is test-owned; "Partial" means it works but a real gap
undercuts the strength of the claim; "Overstated" means the marketing language
is stronger than the delivered tier.

| Advertised claim | Verdict | Basis |
|---|---|---|
| Spec sha-locked before trials; official render refuses unregistered questions | **Delivered (fence) / Overstated (as "cannot p-hack")** | Fence is mechanical and unbypassable in-process; but `rm ledger.ndjson && bench plan` re-registers cleanly, and consumer stages re-read the spec after the lock check (TOCTOU). The delivered tier is *tamper-evident*, not *tamper-proof* — as the code's own D002 note says. |
| One typed, provenance-stamped event per operation; hash-chained; verify-chain + anchors detect tampering | **Delivered, with one high-sev read-path defect** | Constructor-only writes enforced by contract; one-event property swept over all entrypoints; tamper genuinely detected. **But** the reader splits lines differently from the verifier (F-H1) — a chain-valid ledger can be made permanently unreadable. |
| Hermetic per-trial containers; insulated arms; egress confined to a metering proxy with per-trial attribution | **Partial → Overstated** | Digest-pinning, request-mount, key redaction, and insulation-by-construction are real and tested. **But** the metering proxy is not in the repo, its per-trial attribution is agent-spoofable, and it fails *open* when its log is missing (F-H4). "Internal" network still reaches the host gateway. |
| Deterministic grading imports no LLM client; grade container is network-less | **Delivered (holdouts) / Partial (plugins)** | Import contract is real and load-bearing; holdout grading is containerized. **But** grader *plugins* run unsandboxed in the host process against the agent-controlled workspace (F-M). |
| Identity-blind advisory judge; order-debiased; advisory-only; calibrated | **Delivered, with one high-sev crash + one leak** | Blinding, order-debiasing, and kappa math are trustworthy. **But** an OpenAI `context_overflow` crashes `judge_pair` and writes *no* verdict event, voiding the "one verdict per comparison" invariant (F-H3); and a workspace symlink can leak host-file contents into the blind packet (F-M). |
| Gaming looked for via planted-violation-owned detectors; flags advisory, quarantine ledgered | **Delivered, with disclosed-precision caveats** | Detectors, planted/clean fixtures, and the advisory tier are real; disposition (flags never auto-fail) is exactly as advertised. **But** in production the "clean fixture must not flag" guarantee is weaker than the tests imply (no pristine baseline exists at runtime), and the mechanical detectors are more evadable than a reader would assume (F-M/L). |
| Contamination sentinel; asymmetric flagged contamination refuses official render | **Delivered** | The asymmetry fence is recomputed from the ledgered probe and is unbypassable; honest tri-state dating; hash-only canaries. One gap: the contamination CLI skips the lock gate other stages enforce (F-M). |
| Paired bootstrap, coverage-validated CI, MDE always reported, A/A selfcheck gates official | **Delivered** | See §3. The one real statistical gap is multi-arm multiple-comparison inflation with no correction or disclosure (F-M2). |
| Docs match the binary; AC coverage recomputed at collection | **Partial** | README verb coverage is machine-checked and the AC machinery is excellent — **but** AC binding is *presence*-based (a named test satisfies it even if it asserts nothing or is skipped), 10 browser-driven ACs never execute in CI, and CLAUDE.md / deep-dive carry stale counts and a wrong "scaffolded" list (F-H5, F-M). |
| Secrets don't leak into artifacts | **Delivered at the capture boundary** | Whole-workspace redaction, PEM-body scrubbing, non-UTF-8 handling, fail-loud on unreadable files, redaction of key literals — careful and well tested. Residual gaps are narrow (`.tar` not scanned; judge re-scan checks identity but not secrets). |

---

## 5. Findings (prioritized, with evidence and remediation)

Finding IDs are grouped by severity. File:line references are to `3f639f1`.

### High

**F-H1 — Reader/verifier line-splitting divergence: a single chain-valid event
can permanently brick an experiment.**
`harness/ledger/chain.py` canonicalizes with `ensure_ascii=False` and
`verify_chain` splits on `b"\n"` (correct), but `harness/ledger/query.py:83,152`
(`iter_events`, `tail_events`) split with `str.splitlines()`, which *also*
breaks on U+0085 (NEL), U+2028 (LS), and U+2029 (PS) — all legal, unescaped,
inside a JSON string. A legitimate event whose payload string contains one of
these characters (infra-failure reasons, trial records, and judge reasons all
carry agent/tool output) verifies as a clean chain but makes every read gate
raise `JSONDecodeError`. Because `assert_lock → find_events → iter_events` is the
first thing every stage calls, one such event renders run/grade/judge/analyze
permanently inoperable on a ledger that `verify-chain` calls OK. A hostile
agent-under-test can trigger it deliberately — a poison-event denial of service
on the instrument. *Confirmed in source.*
**Remediation:** split readers on `b"\n"` exactly as the verifier does; add a
Hypothesis round-trip property test over arbitrary-unicode payload strings (the
chain currently has no property test, despite the repo's `hypothesis`
dependency).

**F-H2 — CSRF on the authoring surface forges the lock ceremony.**
`harness/author/server.py` has no Origin, Host, or CSRF-token check on its
mutating POSTs, and `_body()` parses JSON regardless of Content-Type. Loopback
binding is not a defense: any web page the operator visits can fire a no-cors
`POST http://127.0.0.1:8390/api/lock` (fixed default port) and record a **forged
`experiment_locked` genesis event** — the chain-anchored pre-registration the
whole instrument's credibility rests on — under the launch-bound actor with
attacker-chosen `attested_by` and `acknowledge_underpowered`. *Confirmed: no
Origin/CSRF/Referer check exists in `harness/author/` or `harness/serve/`.*
**Remediation:** reject POSTs whose `Origin` is absent or foreign; validate
`Host` (also closes the DNS-rebinding read of unblinded operator data);
require `Content-Type: application/json`; or embed a per-launch token in the
served page and require it on the ceremony endpoints. (The read-only serve
observer has no mutating routes and so no CSRF surface — that claim holds.)

**F-H3 — `context_overflow` crashes the judge and writes no verdict event,
voiding AC-8.**
In `harness/judge/client.py:200-205,216`, a `ProviderError` is mapped to
`CantJudgeReason(provider_failure_reason(e))`. For `ProviderContextOverflow`
(raised on OpenAI `context_length_exceeded`), `provider_failure_reason` returns
`"context_overflow"` — but `CantJudgeReason` (`harness/judge/schema.py:61-67`)
has **no such member**. Constructing the enum raises `ValueError` *inside* the
`except`, escaping `judge_pair` with no `judge_verdict` written. This is
reachable in production (an OpenAI judge plus a large workspace diff) and breaks
the headline "an attempted comparison without a verdict event is
unrepresentable [AC-8]". The process tier handles the identical case correctly
(`score.py` catches `ProviderContextOverflow` before `ProviderError`); the judge
client simply drifted. *Confirmed: enum lacks the member.*
**Remediation:** add `CONTEXT_OVERFLOW = "context_overflow"` (additive — the
reason is stored as a free-form string) or catch `ProviderContextOverflow`
explicitly; add a `judge_pair` test asserting exactly one `CANT_JUDGE` event on
overflow.

**F-H4 — The metering proxy — linchpin of egress, attribution, and cost for
non-self-reporting arms — is not in the repo, is agent-spoofable, and fails
open.** Three compounding facts:
- *Unshipped:* `harness/run/egress.py` only produces a `ProxyConfig`; its own
  docstring says "the existing Squid/devcontainer proxy architecture drops in."
  Nothing in the codebase creates, configures, or attaches a proxy, and the JSONL
  attribution schema the engine parses is produced only by the FakeEngine and
  test fixtures. The real-proxy egress e2e is explicitly out of scope
  (`test_e2e_harbor.py`). So "egress confined to a metering proxy with per-trial
  JSONL attribution" is, end to end, unverified against any real component.
- *Fail-open:* `harbor.py:378-381` — if a configured proxy log is missing, the
  scan returns "no attempts, no violation, no cost." A null-telemetry arm then
  spends *invisibly* against the pre-registered ceiling, and egress violations
  vanish. This directly contradicts the repo's fail-loud directive.
- *Spoofable:* the trial id is delivered as HTTP-proxy userinfo, which the agent
  controls; lines with a wrong/absent trial id are attributed to no one, so a
  misbehaving arm dodges the cost guard and sheds violation evidence unless the
  external proxy authenticates trial ids (nothing in-repo requires this).
*Confirmed: no proxy component exists.*
**Remediation:** ship or vendor the proxy configuration and add one
docker-marked e2e with a real proxy container asserting deny-logging and
attribution; make a configured-but-missing log a ledgered flag or
`infra_failed`; require the proxy to reject unknown credentials and treat
unattributed in-window lines as the trial's (or a run-level violation).

**F-H5 — Ten acceptance criteria are verified by browser tests that never run in
CI.** `tests/fixtures/browser.py` hardcodes `/opt/node22/...` and
`/opt/pw-browsers/chromium`; `browser_available()` is False on GitHub-hosted
runners, so every `drive()` test skips. Ten AC-owning tests
(`test_eval14_page_drive`, `test_eval17_author`, `test_eval18_review_serve`,
`test_eval19_operator_p2`) depend on it, and because AC coverage is a *static
AST presence* check, they satisfy the AC gate while never executing in CI. The
Docker tier got this exactly right (a `VERDI_REQUIRE_DOCKER` fail-closed
fixture, a dedicated job, a guard test); the browser tier has no analog.
**Remediation:** provision node + Playwright in a CI job with a
`VERDI_REQUIRE_BROWSER` fail-closed switch and env-configurable paths.

### Medium (grouped)

**Lock integrity at the seams.**
- *Consumer-side TOCTOU:* every downstream stage does `assert_lock(spec_path)`
  and then a *second* independent `ExperimentSpec.from_yaml(spec_path)`
  (`run/cli.py:65-66`, `grade/cli.py:153-154`, `judge/cli.py:47-48`,
  `analyze/cli.py:48-49,112-113`, `review/cli.py:51-52`, `process/cli.py:67-68`,
  `forensics/scan.py:109-110`). A spec swapped between the sha check and the
  re-read runs under an unlocked spec while the ledger attests the locked one —
  the exact gap the lock advertises closing. *Fix:* have `assert_lock` read the
  bytes once and return `(event, spec)`; delete the second read.
- *Contamination skips the gate entirely:* `contamination/cli.py:66` loads the
  spec with no `assert_lock`/`assert_chain` — the only ledgered stage that
  skips the lock gate.
- *Double-lock race:* the `AlreadyLockedError` check runs outside the append
  `flock`, so two concurrent `bench plan` invocations both append
  `experiment_locked`; `assert_lock` keys `locks[0]` and never refuses `>1`
  lock. *Fix:* re-check under the append lock and refuse `len(locks) > 1`.

**Statistical.**
- *Multi-arm multiple comparisons:* with `>2` arms, every `arms[0] vs arms[k]`
  pair renders in the *official* report with its own 95% CI and its own
  "rule MET" decision — no family-wise correction and no disclosure anywhere
  (`report.py:809-891,1354-1373`). For a tool that pre-registers *one* decision
  rule, k−1 simultaneous 95% tests silently inflate the false-positive rate.
  *Fix:* restrict official decision status to the primary pair (label the rest
  exploratory) or apply and disclose Holm/Bonferroni when `len(arms) > 2`.
- *CI-method label can misstate the interval:* BCa silently returns a percentile
  interval at small N / non-finite z0, and `cluster_robust_t` falls back on
  degenerate SEs, but the render still prints the requested method name. The
  numbers stay coverage-consistent (nullsim measures the method *with* its
  fallback), but the label is wrong at the edge. *Fix:* surface the realized
  method / a `fallback: true` field.

**Isolation weaker than advertised.**
- *Symlink leak into the blind judge packet:* `judge/assemble.py`'s
  `_read_workspace_diff` walks `rglob("*")` and `read_text()`s files *following
  symlinks*, so an agent-planted symlink pointing outside the workspace ships
  host-file contents into the (identity-only-rescanned) judge packet.
  *Reproduced by the auditor.* The grade container hardens the same threat with
  `copytree(symlinks=True)`; the judge assembler is the weaker sibling. *Fix:*
  skip or resolve-and-confine symlinks.
- *Unsandboxed grader plugins:* `grade/deterministic.py` runs
  `get_plugin(id).grade(workspace, task)` in-process in the host, with full
  network and filesystem access, against the agent-controlled workspace — none
  of the `--network none` / fresh-copy protection the holdouts get. Only
  registry-declared plugins resolve, but a registered plugin that shells out
  over malicious workspace files is a host-side code-execution surface. *Fix:*
  run plugins under the container discipline, or document them as trusted
  host-side code and note the asymmetry in the seam docstring.
- *"Internal" network reaches the host:* `docker network create --internal`
  blocks external routing but the host is still reachable at the bridge gateway
  IP (any 0.0.0.0-bound service, notably the `serve` observability server);
  all trials share one network with only the serial loop preventing cross-arm
  contact; no `--cap-drop`/`--security-opt no-new-privileges`/`--pids-limit`/
  read-only rootfs. *Fix:* a documented DOCKER-USER/host-gateway block; consider
  per-trial networks.
- *All provider keys in every container:* every arm's container receives every
  provider key, so arm A can spend on arm B's account — a least-privilege
  breach of "insulated arms" that also confounds per-provider cost. *Fix:*
  per-arm key lists.

**Fail-open / torn-state paths.**
- *Timeout kill swallows all errors:* `harbor.py:121-130` `_kill` does
  `except: pass` on `docker kill`/`wait`, then redaction runs over a workspace a
  possibly-still-live container may write into — the exact post-scrub secret
  hazard the code's own RN-10 comment exists to prevent. *Fix:* verify the kill
  succeeded; on failure `infra_failed(kill_failed)` before trusting redaction.
- *Spend lost on the exception path:* a `RedactionError`/`TrajectoryCorruptError`
  after the engine ran ledgers `trial_infra_failed` with **no cost**, so
  repeated post-spend failures burn budget invisibly to the ceiling (and the
  loss survives resume). *Fix:* carry a cost field on `trial_infra_failed`.
- *Dead proxy yields valid-looking "completed" trials:* with `proxy_url` set but
  the proxy down, every API call fails, the agent exits nonzero (recorded as
  `completed` by design), telemetry is null, and per F-H4 there is no violation
  and no cost — a whole run can burn wall-clock producing garbage. *Fix:* a
  proxy liveness preflight.
- *Corpus admission torn state:* `admit_task` appends `task_admitted` and then
  writes the embedded copy and saves the manifest *outside* the try; a late
  failure leaves the ledger advanced, the manifest `pending-curation`, and a
  raw traceback — and a re-run appends a *second* `task_admitted` (no
  already-admitted refusal). `admit --candidate-json` also never verifies the
  candidate *content* against the approved sha, so a stale/tampered file yields
  a ledgered admission and a canary embedded in unreviewed bytes. *Fix:*
  validate destinations before ledgering; refuse an already-admitted candidate;
  recompute and check the candidate content sha.

**Serve fail-closed inconsistency.** Only `/api/status` verifies the chain and
withholds on tamper; `/api/events`, `/timeline`, `/trial`, `/compare`, and the
exported bundle render unverified ledger content, so a tampered ledger shows a
"chain BROKEN" chip yet still renders the trial list, feed, drill-down, compare,
and bundle. *Fix:* gate all ledger-reading serve routes on `verify()` (cacheable
by size+mtime), or document that only status withholds.

**Forensics precision caveats.** The "clean fixture must not flag" guarantee
holds in tests only because they supply a pristine baseline that production never
has (`scan.py` hardcodes `pristine_files={}`), so a pre-existing skip marker or
holdout literal in a legitimately-edited file will flag at runtime. Skip-marker
detection omits `xfail` despite the docstring; `transient_holdout_tamper` matches
path substrings in prose narration; and the mechanical detectors are
rename-/reformat-evadable in ways the module docstrings do not fully state.
These are all *advisory-only* (no flag gates the fence in v1, by explicit
design), which bounds the impact — but the disclosure should match the
mechanism. *Fix:* assemble pristine content from the corpus task seed, or stamp
low-confidence attribution; add `xfail`; add one honest sentence per detector
module naming the evasion class it does not catch.

**Idempotency asymmetry.** Transient `CANT_JUDGE`/`CANT_SCORE` (e.g. a provider
timeout) are treated as permanent on re-run — there is no `--retry-terminal`
analog for judge/process as there is for grade — so a network hiccup silently
drops a comparison from calibration. *Fix:* exclude transient reasons from the
skip set, or state "terminal on first attempt" in the CLI docstrings.

### Low / Info (representative)

Doc and provenance drift: CLAUDE.md still calls analyze/review/process/corpus
"scaffolded" (all four are fully built — this misinforms every agent bound by
the file) and omits five subsystems; the README Status table stops at EVAL-12
though EVAL-13–21 ship; `deep-dive.md` says "three of the five" and "550+ tests"
(actual: 7 contracts, 725 tests); `docs/adapters.md` documents trajectory
"schema v2" while the code is at v3. `attested_by` defaults to `"unknown"` /
`"cli-user"` — the exact sentinel `actor.py` exists to ban — and the
`anchor-plus-attestation-v1` method string implies a cryptographic attestation
that does not exist (contrast the *real* Ed25519 curation signatures).
Non-adversarial nits: `.tar` bodies not scanned by redaction; NaN/Infinity
accepted into the "canonical" line (non-RFC-8259, external verifiers reject);
non-atomic in-place secondary writes (mitigated by not-yet-ledgered or read-back
checks); a stale `TODO(EVAL-6)` in `plan/power.py`; docker CLI stdout/stderr
discarded on daemon errors; the ledger import contract's source list is
hand-maintained and fails open for a new subsystem (harbor has an AST backstop;
the ledger does not).

---

## 6. Competitive positioning

verdi-bench does not compete on scale, and shouldn't be read as trying to. The
useful question is: *given an established tool already exists, why would someone
reach for this one?* The honest answer is a specific posture, not a feature
count.

### The landscape (mid-2026)

| Tool | Category | Where it's strong |
|---|---|---|
| **Inspect AI** (UK AISI) | OSS agent-eval framework | Closest philosophical peer. Mature sandboxing toolkit across Docker/Kubernetes/Proxmox on three isolation axes (tooling/host/network), a large eval registry (`inspect_evals`), log viewer, tool-approval gating. Adopted by CAISI, METR, Apollo. |
| **LangSmith** (LangChain) | Hosted observability + evals | Production tracing, annotation queues, deep LangChain/LangGraph integration, team collaboration at scale. |
| **Braintrust** | Hosted evals + observability | Most complete commercial platform; native CI/CD quality gates (GitHub Action posts score diffs and blocks merges), playgrounds, generous free tier. |
| **promptfoo** | OSS CLI evals + red-teaming | Purpose-built prompt security/red-teaming; MIT, model-agnostic (acquired by OpenAI, March 2026, core to stay open). |
| **DeepEval / Langfuse / Phoenix / W&B Weave** | OSS eval + observability | pytest-style assertions, OTel tracing, dashboards. |
| **lm-evaluation-harness / HELM / SWE-bench harnesses** | Academic benchmark batteries | Breadth of standardized tasks and leaderboards. |

### Where verdi-bench is genuinely ahead

These are capabilities *no* mainstream tool in the table ships as a first-class,
test-enforced property:

1. **Pre-registration and an anti-p-hacking fence.** The sha-locked spec plus
   the official render that *refuses* unregistered questions is unique. Every
   other tool lets you run first and pick the favorable metric afterward.
2. **A tamper-evident, hash-chained, externally-anchorable ledger.** Competitors
   store results in a hosted database you are asked to trust; verdi-bench makes
   silent history-editing *detectable* against state an attacker doesn't hold.
3. **Statistical seriousness at small N.** Paired trials with seeded interleave,
   a paired bootstrap, a coverage-validated CI method, MDE always reported, and
   an A/A selfcheck gate. The commercial tools show score deltas; they do not
   report power, MDE, a pre-registered decision rule, or validate their own
   interval coverage.
4. **LLM-judge governance rather than LLM-judge-as-truth.** Identity-blind with
   canary verification, order-debiased, explicitly *advisory*, and calibrated
   against blinded humans with IPW-corrected kappa — with its one designed
   dependence disclosed in every render. Most tools report an LLM-judge score as
   a headline number with none of this scaffolding.
5. **Integrity tiers wired into the verdict.** Gaming forensics and a
   contamination sentinel, with the asymmetric-contamination fence actually
   refusing an official render. This is a threat model most eval tooling doesn't
   even name.
6. **A verification culture applied to the instrument itself.** AC-to-test
   binding at collection, load-bearing import contracts, planted-violation
   fixtures.

The one-sentence version: **verdi-bench is the only tool here built to produce a
*defensible A/B decision* — one you could put in a procurement memo, a migration
sign-off, or a published claim and expect to survive a hostile reviewer.**

### Where it falls behind (and why that's often fine)

- **Scale and throughput.** Serial local execution, no fleet scheduler, no
  Kubernetes path (Inspect has one). This is a deliberate trade — "rigor costs
  wall-clock" — but it rules out large batteries and high-volume iteration.
- **Breadth.** Two native adapters plus a generic log format, versus Inspect's
  provider matrix; no bundled benchmark library (deliberate) versus
  `inspect_evals` / lm-eval-harness. You bring your corpus.
- **Observability and monitoring.** No production tracing, no OpenTelemetry, no
  online monitoring or alerting. It is an experiment instrument, not an
  ops-time observability platform — a different category from LangSmith/Langfuse.
- **UX, collaboration, ecosystem.** A CLI plus minimal local web views versus
  polished hosted dashboards, annotation queues, and team workflows; a single
  repo with no PyPI package, plugin ecosystem, or community — and a
  correspondingly high bus factor.
- **Security/red-teaming.** No prompt-injection or vulnerability probing
  (promptfoo's specialty).
- **Sandbox maturity.** Per F-H4/F-M, the hermetic story currently rests on an
  unshipped external proxy and lacks the host-isolation hardening Inspect's
  sandboxing toolkit provides out of the box. This is the area where a
  *stronger* competitor is also *ahead on verdi-bench's own turf*, and it is the
  most important gap to close to make the "hermetic" claim real.

### When to reach for which

- **Reach for verdi-bench** when the deliverable is a defensible decision
  between two agent stacks — procurement, a migration/rollback call, an
  audit/compliance context, a publishable comparison — especially with small N,
  adversarial-integrity concerns (gaming, contamination), and a need to hand a
  skeptic an auditable trail.
- **Reach for Braintrust/LangSmith** for day-to-day prompt iteration, production
  monitoring, and team collaboration at scale.
- **Reach for Inspect AI** for large agentic evaluations needing mature,
  scalable sandboxing and a ready task library.
- **Reach for promptfoo** for security red-teaming.

verdi-bench is a scalpel in a landscape of dashboards. That's its value and its
limit.

---

## 7. Remediation roadmap

Ordered by "credibility per unit effort" — the earliest items close the widest
gap between advertised and delivered trust.

**Before advertising the tamper-proof/hermetic tier (ship-blocking):**
1. F-H1 — fix the ledger reader/verifier split; add a chain property test.
   *(Small, high-impact: a correctness + availability defect in the core
   guarantee.)*
2. F-H2 — add Origin/Host/Content-Type (or per-launch token) checks to the
   author ceremony endpoints. *(Small; closes a chain-anchored forgery.)*
3. F-H3 — add the `CONTEXT_OVERFLOW` reason or catch the overflow explicitly;
   test it. *(Trivial; restores AC-8 for OpenAI judges.)*
4. F-H4 — make the metering-proxy contract real: ship the proxy config, make a
   missing log fail *loud*, require credential authentication, and add one
   real-proxy docker e2e. *(Largest item; without it the hermetic/cost/egress
   claims should be softened in the README to "requires an external metering
   proxy; per-trial attribution and cost enforcement depend on it.")*
5. F-H5 — add a fail-closed browser CI job so the 10 UI ACs actually execute.

**Next (harden the seams):** consumer-side lock TOCTOU and the double-lock race;
the contamination lock gate; multi-arm multiple-comparison correction/disclosure;
the symlink leak into the judge packet; grader-plugin sandboxing (or explicit
documentation); the timeout-kill and exception-path fail-open windows; the
serve fail-closed inconsistency; the corpus admission torn state and content-sha
check.

**Documentation truth-up (cheap, high-trust-yield):** reconcile CLAUDE.md
(remove "scaffolded", add the five missing subsystems), the README Status table,
and the deep-dive's contract/test counts; extend the consistency test to cover
the deep-dive; correct the trajectory schema-version doc; downgrade the
`attested_by` sentinels and the `attestation-v1` method string to match what is
actually delivered.

**Disclosure alignment:** state the detector evasion classes, the "clean
fixture" production caveat, the spend-ceiling-is-a-stopping-rule semantics, and
the transient-CANT terminal behavior — each is an honest limitation the code
already lives with; saying so in the docs is what keeps the instrument's
central promise ("an unverified claim about the instrument is a defect in the
instrument") true of the instrument's *own* description.

---

## 8. Bottom line

verdi-bench delivers, in working and test-owned code, a genuinely
differentiated capability: a defensible, auditable A/B verdict between agent
stacks, with statistical and integrity machinery no mainstream eval tool
matches. The engineering is disciplined and, rarely, honest with itself. The
gap between what it *is* and what its top-line language *claims* is real but
narrow and specific: it is an advisory, tamper-*evident* instrument whose
strongest guarantees hold inside its own process and weaken at the seams to
containers, browsers, and the outside proxy. Close the five high-severity
seam findings and truth-up the docs, and the marketing and the mechanism would
finally say the same thing — which, for an instrument whose entire pitch is that
its claims are checkable, is the one bar it most needs to clear.
