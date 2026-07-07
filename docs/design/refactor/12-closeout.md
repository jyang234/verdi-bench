# 12 — Program closeout

**Status:** complete. Branch `refactor/instrument-to-product`, 184 commits over
`main@c35ae4a`, 2026-07-05 → 2026-07-06. Every phase implemented by
worktree-isolated agents, adversarially reviewed against its plan document,
findings fixed reproduce-first, and gated green before the next phase opened.

## Final gates (all run on the closing HEAD)

| Gate | Result | Baseline at c35ae4a |
|---|---|---|
| `make verify` | **1401 passed / 26 skipped** | 947 passed / 25 skipped |
| import contracts | **10 kept / 0 broken** | 8 |
| `make shakedown` (both `FORCE_COLOR` modes) | L1 4/4 · **L3 18/18**, exact reason strings | L3 env-sensitive to ANSI |
| `-m docker` (live daemon) | **12 passed / 4 env-skips** | 9 passed equivalent-scope |
| `-m browser` (real Chromium) | **33 passed** | 22 enumerated (10 silently unrun in CI) |
| Live real-key L6 (`harbor.py`) | **4/4** — managed proxy, executed holdouts | 4/4 with hand-rolled infra |
| Live real-key multi-agent | **5/5** (twice: post-3D and post-G1 rebase) | 5/5 with hand-rolled tunnel |

Golden discipline held for the whole program: `golden_ledger.ndjson` has
**one commit ever**; `golden_constructors.ndjson` has exactly **three**
(create + the two sanctioned regens: `rubric_sha256`, `spans_sha`), now 46
replay lines. No render fixture ever changed. No test was weakened anywhere —
every phase reviewer checked for it explicitly.

## What shipped, per phase

- **P0 safety nets** — byte-goldens for chain/constructors/anchor/renders;
  fixture extraction (16 cross-test imports → 0); browser marker (D6);
  shakedown in CI; defects D1–D10 fixed reproduce-first.
- **P1 write path** — `LedgerView` (98 hand-rolled join sites migrated at the
  worst offenders); `spec_to_yaml`/`TaskSpec`/`build_manifest`/`RunConfigFile`;
  lock preflight steps + author preview parity; stage APIs + CLI kit.
- **P2 SDK** — `Experiment` builder + `ExperimentWorkspace` facade;
  single-sourced starter + judge-rubric templates (A8's single source);
  `sdk-is-a-leaf` contract; hermetic shakedown scripts on the SDK; the
  north-star as a committed passing test.
- **P3 images & infra** — `harness/hermetic` (DockerClient, HardenedCommand,
  managed `MeteringProxy`); polymorphic holdouts (A2) + `local-exec` runner +
  in-image `run_holdouts`; `images/base` + `verdi_agent.py` + official
  images + `bench images build/verify/list`; typed `request.json` (A1);
  EnvironmentSpec (A3); harbor scripts converted, `assets/harbor` deleted.
- **P4 polymorphism** — `EngineBase` ABC + registry + A10 fake parity;
  provider `Completion` + registry; `JudgingSession`; Detector/channel
  registries + scorer envelope; entrypoint-fixture migration; `MetricDef`;
  `IMPORTERS`; typed `ResultCard`; rubric union-dims (P4-RUBRIC).
- **P5 decomposition** — `report.py` → `findings/` package (139-line facade,
  unified `FENCE_CHECKS` shared `is`-identical by render + observer);
  declarative `EventSpec` registry (A7); grade split + runner ABC; forensics
  assembler split; webkit + per-surface lintable JS (P5-JS); `run_trial`
  capture pipeline + `SpendTracker`.
- **OTLP (09/10)** — hermetic trace collector + `TraceCollector.managed`;
  engine span ladder (`span_log_missing`/`spans_corrupt`, A12); `spans_sha`
  (A13); `[otlp]` extra (A14); the `otlp` adapter's whitelist projection with
  goldens-first mapping fixtures and the adversarial identity property.
- **P6 docs** — usage-guide SDK path; normative otlp mapping in adapters.md;
  images.md spans section; shakedown/deep-dive/README truth pass; last
  retired-id and fixture cleanups.
- **P7 mop-up (plan 11)** — G1 reference image on `verdi-base` (+ plant-proven
  tunnel sweep); G2 `ManagedSidecar` hoist; G4 machine-forced contract lists
  (immediately caught a real `http_guard` gap); G5 trivia; G3 `Renderer`
  registry (md/html/dossier) with the card stopped at the plan's own wall.

## §6 extensibility — final

| Task | Shipped cost |
|---|---|
| A/B from Python | 1 import, ~8 fluent statements (`tests/test_sdk_northstar.py`) |
| New provider | 1 file + `_PROVIDER_REGISTRY` entry, typed `Completion` |
| New engine | 1 file + `ENGINES` entry, auto-contract-tested, `docs/engines.md` normative |
| New holdout kind | 1 subclass + union member |
| New detector | 1 registry row + fixture pair (meta-test-enforced) |
| New metric | enum value + `MetricDef` (power entry only for exotic null families) |
| New findings format | 1 `Renderer` registration (md/html/dossier live; card is a distinct versioned artifact by design) |
| New importer | source class + `IMPORTERS` entry |
| New test image | extend `images/base`, write agent logic, `bench images verify` |
| Metering/tracing infra | `MeteringProxy.managed` / `TraceCollector.managed` context managers |

## Deviations register (accepted; where recorded)

north-star `arm(image=)` is task-level in reality (seam raises loudly);
`MeteringProxy` imported from `hermetic`, not re-exported by sdk; metric =
2 touchpoints (beats the plan's honest 3); `events.py` +6% LOC for
policy-as-data (A7 golden-proven); `extract.py` 1182 LOC with an in-module
size rationale; scripts 1081→796 LOC vs the aspirational ≈300 (qualitative
goals all hit; tripwires' 303 lines *are* its 18 vectors); tripwires still
uses `dump_yaml` for deliberately-invalid specs; A9 read side stays lenient
(lint verb instead); A8 packet-framing not taken (template single-source
instead); G5b probe keeps its own multi-call sequence (envelope docstring
corrected — byte-neutral adoption was impossible); G3 card stopped at the
sanctioned wall; F2 `verdi.agent` validated where emitted.

## Open items (deliberate, for the operator)

1. **A8** packet-framing move — deferred with its own approval path.
2. **`VerdiRefusal` base** (P1 F2) — exception-hierarchy reparenting needs
   sign-off; refusal mapping is per-verb explicit enumeration today.
3. **Scheduler split** (04 §5) — optional, not taken; both files under gate.
4. **CI proxy wiring** (P0-PROXY-CI) — wire `VERDI_REQUIRE_PROXY` once CI
   provisions the now-shipped managed proxy; update the `ci.yml` comment.
5. **>500-LOC without in-module notes** — `events.py` 963, `sections.py` 636,
   `dossier.py` ~520, `card.py` ~517 (marginal; `extract.py` carries a note).
6. **`holdout_results.json`** still declared in 3 places
   (`grade/runners.py`, `run/workspace.py`, `contamination/scan.py`) —
   the cross-subsystem copies were a sanctioned layering call.
7. **Operational:** concurrent verdi runs on one docker daemon collide on the
   canonical sidecar names/networks (observed twice during this program as
   test-vs-script contention). Single-operator assumption; a per-run name
   suffix would lift it if ever needed.

## Addendum — open items dispositioned (2026-07-06, refactor 13)

All seven items above are resolved; see [13-open-items.md](13-open-items.md)
and decisions.ndjson (OI-1/A8-EXEC, OI-2, OI-3, OI-7, P0-PROXY-CI closed):

1. **A8 — DONE (OI-C):** verdict-JSON contract is harness-owned packet
   framing (fingerprint v1→v2); rubric template slims to judgment criteria;
   golden investigation proved the ledger golden's packet shas were synthetic
   — zero fixture bytes moved; live L2 real-judge run 5/5 under framing v2.
2. **`VerdiRefusal` — DONE (OI-B):** `harness/errors.py` base; ~48 refusal
   types reparented; pydantic wrapped at the spec boundary message-verbatim;
   `refusal_exit()` bare form is the uniform net, narrow ladders preserved;
   AST completeness meta-test enforces the property.
3. **Scheduler split — WON'T-DO** (recorded).
4. **CI proxy wiring — CLOSED (OI-A):** managed path is CI-covered by its own
   live e2e; the reference-Squid gate is operator-run by design.
5. **Size notes — DONE (OI-A):** all four modules carry in-module rationales.
6. **Holdout filename — DONE (OI-A):** three-way equality meta-test.
7. **Sidecar collisions — DEFERRED** (recorded; single-operator posture).

Post-pass gates: 1414 passed / 26 skipped / 10 contracts; shakedown 4/4 +
18/18 both color modes; live L2 official 5/5 (real Anthropic judge, framing
v2). Review model for this pass: every diff reviewed directly by the
orchestrator; two orchestrator-found nits fixed at merge (a stale
constant-name comment; nothing in OI-B/OI-C).

## Addendum 2 — test-pruning pass (2026-07-06, refactor 14)

The suite passed its own audit. Against a pre-audit estimate of ~40–70
prunable functions, the evidence bar yielded: 7 tests REWRITTEN (4 LedgerView
oracle-equivalence tests re-aimed at direct semantics with plant-proven
discriminators; 2 importer byte-pins re-aimed at registry derivation with a
hardcoded key-set anchor; 1 facade-parity tautology halved), ~190 lines of
dead oracle code deleted (verbatim ports of P1-removed production), zero
whole-test deletions, net −4 collected items. The vacuity hunt found no
structurally-unfailable test and no coverage gap; the duplicate-pair audit
found the canonical engine-vs-builder argv duplicate does not exist, and a
suite-wide plant proved the query-parity pair is the sole guard of
``latest()`` last-wins — kept as load-bearing. Verdict: the tests are
needed; the suite's redundancy is layered by design, not accreted.
Post-pass gates: 1410 passed / 26 skipped / 10 contracts; shakedown 4/4 +
18/18; docker/browser collections unchanged.
