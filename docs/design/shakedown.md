# The verdi-bench shakedown

A **shakedown** proves the instrument's fences *fire* — not merely that the
pipeline runs. verdi-bench's advertised capabilities are almost all *negative*
guarantees ("you cannot p-hack it", "you cannot quietly edit history", "gaming
is detected"). You only prove such a guarantee by **driving something at it that
must be refused, and confirming it is.**

So the shakedown is a **known-answer validation suite**, in the spirit of NIST
crypto validation: *positive controls* the instrument must **recover**, and
*negative controls* every fence must **refuse or flag**. Every claim in the deep
dive's §3 trust table (`docs/deep-dive.md`) maps to at least one live vector with
a known expected disposition. It is layered because a single experiment cannot be
both a valid official finding and riddled with tampering.

The harness lives in [`scripts/shakedown/`](../../scripts/shakedown/); the
hermetic core is a repeatable gate:

```bash
make shakedown        # L1 golden path + L3 tripwire matrix (no keys, no Docker)
```

## The seven layers

| Layer | Proves | How |
|---|---|---|
| **L0** self-integrity | the instrument's own tests + structural contracts | `make verify` — 922 tests, 8 import contracts, AC coverage enforced |
| **L1** golden path | the full pipeline recovers a known effect | `scripts/shakedown/golden.py` (fake engine, fake judge) |
| **L2** official + real judge | the pre-registration fence can be *earned* | `scripts/shakedown/official.py` (real Anthropic judge) |
| **L3** tripwire matrix | every fence fires on an attack | `scripts/shakedown/tripwires.py` (18 vectors) |
| **L4** real containers | hermeticity / digest-pin / redaction / grade | `VERDI_REQUIRE_DOCKER=1 pytest -m docker` |
| **L5** operator UIs | operator/reviewer/author surfaces | `VERDI_REQUIRE_BROWSER=1 pytest` UI drive tests |
| **L6** real-agent harbor | real LLMs in hermetic containers, egress metered | `scripts/shakedown/harbor.py` |

L0, L1, L3 are hermetic. L2/L6 need provider keys (`.env`); L4/L6 need Docker;
L5 needs a host node + Playwright + Chromium.

Since the Phase-2 SDK (refactor 02/08) and the Phase-3D real-container conversion,
every script — `golden.py`, `tripwires.py`, the real-judge `official.py`, and the
real-agent `harbor.py` / `harbor_multiagent.py` — authors + drives experiments
in-process through `harness.sdk`: no hand-built spec dicts, no `bench` subprocess
for the pipeline, ledger reads through `LedgerView`. L6 builds its trial images
through `harness.images` (the official `generic-llm` / reference multi-agent
images) and meters egress through the managed metering proxy (`run.config`
`proxy.managed`) — zero raw `docker` calls. The one console-script survivor is the
pre-registration refusal matrix (L3 #1–7), whose point is the installed
`bench plan` exit-code mapping.

## L3 — the tripwire matrix (18 vectors)

Each row is an adversarial input; the fence must produce the exact disposition.

### Pre-registration — you cannot lock a p-hackable spec
| # | Vector | Expected |
|---|---|---|
| 1 | missing `cost_ceiling` | refuse: "must declare a cost_ceiling"; nothing locked |
| 2 | `primary_metric` = a process dimension | refuse: "composite and unknown metrics are banned" |
| 3 | alias judge model (`…/claude-sonnet-5`) | refuse: "alias ids are rejected at plan time" |
| 4 | `decision_rule: "… == 0"` | refuse: "equality on a bootstrap float is never decidable" |
| 5 | duplicate arm names | refuse: "arm names must be unique" |
| 6 | extra top-level key | refuse: "Extra inputs are not permitted" |
| 7 | a single arm | refuse: "at least 2 items" |

### Ledger integrity — you cannot quietly edit history
| # | Vector | Expected |
|---|---|---|
| 8 | flip one byte of a non-head ledger line | `verify-chain` → "CHAIN BROKEN: broken link at line N" |
| 9 | `anchor` a tampered ledger | refuse; no anchor file written |

### Analyze fence — official findings are gated
| # | Vector | Expected |
|---|---|---|
| 10 | `analyze --official` before selfcheck | `cant_analyze: selfcheck_required` |
| 11 | quarantine a trial after a *passing* official render | official PASSES, then refuses `selfcheck_required` (quarantine is data-bearing) |
| 12 | `analyze --official` citing a different corpus | `cant_analyze: corpus_mismatch` |

### Cost · insulation · statistics
| # | Vector | Expected |
|---|---|---|
| 13 | a tiny `cost_ceiling` | `run_stopped_cost_ceiling`; new trials refused |
| 14 | a holdout canary in the agent-visible prompt | `trial_infra_failed(holdout_leak)`; zero trials |
| 15 | A/A (identical arms, identical grades) | Δ 0.0000; decision not met |

### Gaming & training-contamination
| # | Vector | Expected |
|---|---|---|
| 16 | a trajectory `file_edit` under the holdouts dir | flag `holdout_tamper` (rendered beside the comparison) |
| 17 | a clean trajectory | zero flags (with coverage > 0 — a real clean pass) |
| 18 | one arm's workspace overlaps the task oracle | `cant_analyze: asymmetric_contamination` (overlap channel) |

## Capability coverage (deep-dive §3 → live proof)

| Trust claim | Proven by |
|---|---|
| The question was fixed before the data | L3 #1–7 · L2 fence |
| No operation happened off the record | L0 one-event sweep · L1/L2 event counts |
| The ledger shown is the ledger written | L3 #8–9 |
| Arms never saw the graders' answers | L3 #14 · L4 request-mount `:ro` |
| Grades are mechanical | L0 no-LLM contracts · L1/L6 real grades |
| The judge can't favor a brand | L2 blind real judge · L6 identity-leak caught |
| Judge weight is earned | L2 order-consistency + calibration |
| Secrets don't leak into artifacts | L4 key redaction |
| The stats mean what they say | L1/L2 CI+MDE · L3 #10 selfcheck · #15 A/A |
| Gaming is detected, not narrated | L3 #16–17 |
| Nothing suppresses evidence | L3 #16 renders beside the comparison |
| Training-set contamination is caught | L3 #18 |

## Honest caveats (disclosed, not defects)

- **The fake engine is arm-blind** (`FakeEngine.run` reads only
  `task.fake_behavior`, never the arm). In L1/L3 the treatment-beats-control
  asymmetry and single-arm gaming flag are produced by an operator-scripted
  per-arm `holdout_results.json`/workspace injection between `run` and
  `grade` — exactly how the shipped e2e tests do it. *Organic* asymmetry (two
  genuinely different models) is proven in **L6**.
- **Three L4 tests need a Linux `gcc`** to compile a `FROM scratch` binary
  (kill-on-timeout, plugin net-isolation, the egress emitter) — unavailable on a
  macOS host. Their mechanisms are unit-tested in L0; kill-on-timeout can be
  demonstrated live via `DockerCliRunner().run_container([...], timeout_s=2)`.
- **The reference `deploy/metering-proxy/` Squid config rejects harbor's
  credential.** Harbor injects the trial id as a basic-auth *username with an
  empty password* (`_with_trial_auth`), which Squid 6 refuses in core — the
  "validate against your Squid version" caveat that now lives only in
  `deploy/metering-proxy/README.md`, where the external Squid path lives on. The
  **shipped** metering path is the managed proxy: the stdlib CONNECT proxy
  `harness/hermetic/_proxy_container.py`, stood up and torn down by
  `MeteringProxy` (`run.config` `proxy.managed`, which L6 uses), which accepts
  the username-only credential and emits the `{"trial","host","decision"}` JSONL
  `_scan_proxy_log` parses — genuine per-trial egress attribution, no external
  Squid required.

## Executed baseline — 2026-07-05 (main @31b5be9)

Full-fidelity run, PASS across all seven layers, against the **unmodified**
instrument (only `.gitignore` changed):

- **L0** 922 passed · 8/8 contracts kept · AC coverage enforced.
- **L1** Δ +0.60 (CI [0.20, 1.00]), decision MET; capture-then-reveal gated.
- **L2** official fence PASSED unwatermarked (selfcheck coverage 0.945,
  calibration full-run-validated); real haiku-4.5 judge — 12 substantive + 12
  TIE, `order_inconsistent=0`, identity-blind; dossier byte-deterministic +
  self-contained; card tier ADVISORY.
- **L3** 18/18 fences fired with the exact reason strings above.
- **L4** 5 passed (real harbor trial, digest-pin, request-mount, key redaction,
  nonce-fenced grade, materialized SWE-bench grade); kill-on-timeout live; 3
  host-gcc-limited.
- **L5** 21 passed under fail-closed browser (real Chromium).
- **L6** gpt-4.1-mini vs haiku-4.5 solved tasks in digest-pinned hermetic
  containers; egress metered per-trial per-arm; 4/4 holdouts passed; the blind
  judge caught an agent identity-leak (fail-closed).
