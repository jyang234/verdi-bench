# Refactor master plan — from instrument to product

**Status:** proposed (audit complete, nothing implemented).
**Basis:** six parallel domain audits of `main` @ c35ae4a (2026-07-05), ~29k LOC:
schema/plan/author/cli · run/adapters/images · grade/judge/blind ·
ledger/forensics/contamination/process · analyze/serve/status/review/corpus ·
tests/shakedown/CI. Per-domain plans: [01](01-safety-nets.md) –
[08](08-shakedown-and-tests.md). Post-audit additions (2026-07-06):
[09](09-otlp-trace-capture.md) OTLP trace capture ·
[10](10-span-trajectory-normalization.md) span→trajectory normalization ·
[11](11-post-phase-6-mopup.md) post-Phase-6 reuse mop-up.

## 1. Why this refactor

verdi-bench's mission is to let a user **scientifically A/B-test AI-enablement
solutions** (models, skills, tools, harnesses, multi-agent workflows) on
test/control images, with maximal telemetry to inform design decisions. The
shakedown campaign proved the instrument end-to-end — and in doing so proved
that *everything a real experiment needs beyond the verbs is hand-rolled*:

- `scripts/shakedown/harbor.py` builds images with raw `docker build`, stands
  up networks + a metering proxy with 7 raw docker CLI steps (duplicated, and
  already diverging, in `harbor_multiagent.py`), writes `experiment.yaml` /
  `tasks.yaml` / `run.config.yaml` as untyped dicts, runs holdouts via
  `python -c` and injects `holdout_results.json` by hand, and drives every
  stage through a `bench` subprocess — because none of these exist as library
  API.
- Scripts must know engine internals: `harbor_multiagent.py:28-30` re-declares
  the `verdi-metered` network name with a comment saying it "MUST" match
  harbor's constant. The judge's verdict-JSON contract lives as a string
  literal inside a script rubric.
- The trial-image contract (request.json shape, agent-log format, proxy
  CONNECT auth, workspace layout, exit-code semantics, holdout fence) is
  spread across `harbor.py` internals, `docs/adapters.md`, a reference README,
  and two copy-pasted reference agents. Every image author re-implements ~35
  lines of proxy-tunnel code.

**Diagnosis in one sentence:** the *measurement core is sound* — pydantic
validation, the hash chain, blinding-by-construction, the pre-registration
fence, seeded stats, and eight import-linter contracts all held up under
adversarial audit — but the codebase has **no write path** (authoring,
images, infra, holdouts are user-space problems) and its modules accreted
per-story rather than per-concern (2,198-line `analyze/report.py`, 1,677-line
`serve/page.py`, 905-line `ledger/events.py`).

## 2. What is already good (build on, do not rebuild)

| Seam | Where | Quality |
|---|---|---|
| `CIMethod` protocol + registry | `harness/analyze/ci.py:66-190` | model to copy — new method is 1 file |
| `KappaEstimator` (one impl, two consumers) | `harness/review/kappa.py:26-232` | shared by EVAL-7/9/11 |
| `TaskSource` protocol + generic importer | `harness/corpus/public.py:37-178` | source-agnostic engine |
| `VarianceSource` protocol | `harness/plan/power.py:34-61` | genuine polymorphic injection |
| Adapter base + registry | `harness/adapters/base.py`, `__init__.py:10-33` | healthiest extension path (2 files for a new platform) |
| Engine protocol + factory seam | `harness/run/types.py:108-111`, `engines/__init__.py` | contract-enforced confinement |
| Grader plugin base + registry | `harness/grade/plugins/__init__.py:26-53` | clean contract (registration transport broken — D3) |
| Typed-constructor ledger funnel | `harness/ledger/events.py:82-96` + import contract | verified airtight |
| Status snapshot seam | `harness/status/aggregate.py` | one definition, all observers reuse |
| `http_guard`, `blind.core` | shared without violating isolation | correct neutral placement |

The refactor's job is to make the rest of the codebase look like this table.

## 3. Target architecture

```
                    ┌────────────────────────────────────────────┐
 user surfaces      │  bench CLI (thin shells)  ·  author/serve/ │
                    │  review UIs  ·  scripts/shakedown (thin)   │
                    ├────────────────────────────────────────────┤
 facade (NEW)       │  harness/sdk  — builders, ExperimentWork-  │
                    │  space, stage APIs re-exported, templates  │
                    ├────────────────────────────────────────────┤
 subsystems         │  schema plan run grade judge blind analyze │
                    │  review process corpus contamination       │
                    │  forensics status serve ledger author      │
                    │  + NEW: images (specs/build/verify)        │
                    │  + NEW: hermetic (docker cmds, networks,   │
                    │         metering proxy lifecycle)          │
                    │  + NEW: webkit (shared read-only HTTP/page │
                    │         kit — tier-neutral)                │
                    ├────────────────────────────────────────────┤
 contracts core     │  ledger/chain + events (byte-pinned) ·     │
 (frozen, golden-   │  spec sha-lock · trajectory/flight-        │
  tested)           │  recorder canonical bytes · fence + CANT   │
                    │  vocabularies · seeds namespaces           │
                    └────────────────────────────────────────────┘
 repo images/       │  base/ (compat baked in + verdi_agent.py)  │
                    │  official/{anthropic-claude-code, openai-  │
                    │  codex, generic-llm}/ · reference/multi-agent
```

Import direction is strictly downward. `harness.sdk` is a **leaf consumer**:
it may import any subsystem; no subsystem may import it (proposed as a new
import-linter contract). `harness.review` continues to import neither
serve/status/author *nor* sdk/webkit-page-parts that would breach blinding.

### The north-star UX

Today's `scripts/shakedown/harbor.py` is 135 lines plus a 110-line agent, a
106-line proxy, and a Dockerfile. The same experiment after this refactor:

```python
from harness.sdk import Experiment, Task, AssertionHoldout, MeteringProxy, official_image

exp = (Experiment("mini-ab", seed=1234, cost_ceiling_usd=10.0)
       .arm("control",   model="anthropic/claude-haiku-4-5-20251001", image=official_image("generic-llm"))
       .arm("treatment", model="openai/gpt-4.1-mini-2025-04-14",      image=official_image("generic-llm"))
       .judge("fake/deterministic-2026-01-01")          # rubric defaults to the library template
       .task(Task("t_add", prompt="Write solution.py defining add(a, b)...",
                  holdout=AssertionHoldout("from solution import add; assert add(2,3)==5")))
       .task(Task("t_pal", prompt="Write solution.py defining is_palindrome(s)...",
                  holdout=AssertionHoldout("from solution import is_palindrome as p; assert p('racecar')"))))

ws = exp.write("_run/harbor")                            # experiment.yaml, tasks.yaml, rubric.md, holdouts/
with MeteringProxy.managed(ws, allow=["api.anthropic.com", "api.openai.com"],
                           keys_by_arm={"control": ["ANTHROPIC_API_KEY"],
                                        "treatment": ["OPENAI_API_KEY"]}):
    ws.plan(actor="shakedown")
    ws.run(engine="harbor")
    ws.grade()                                           # executes AssertionHoldouts — no injection
    ws.judge()
    findings = ws.analyze(exploratory=True)
assert ws.verify_chain().ok
```

Everything above maps onto existing, tested subsystem behavior — the SDK adds
no second implementation of anything (single-responsibility directive: the
facade composes, subsystems own).

### Where polymorphism applies (and where it deliberately does not)

| Concern | Today | Target |
|---|---|---|
| Engines | Protocol + if/elif factory; implicit obligations folklore | ABC with template-method post-run ladder (digest check, native-log read, proxy scan shared); dict registry ([04](04-run-engine.md)) |
| Judge/LLM providers | Protocol declares 1 of 4 real obligations; `last_usage` side-channel; if/elif dispatch | `complete() -> Completion(text, usage)`; dict registry; lazy vendor imports kept ([05](05-grading-judging.md)) |
| Holdouts | no concept — amorphous JSON + out-of-repo images | `Holdout` hierarchy (assertion/pytest/command) with polymorphic `materialize()`/`execute()` ([05](05-grading-judging.md)) |
| Gaming detectors | pure functions in a hardcoded tuple, id declared 3× | `Detector` dataclass registry + fixture-completeness meta-test ([06](06-ledger-telemetry.md)) |
| Contamination channels | inline code in one loop | channel functions with declared evidence labels ([06](06-ledger-telemetry.md)) |
| Ledger events | 31 near-identical hand constructors | declarative `EventSpec` table, byte-identical output (golden-gated) ([06](06-ledger-telemetry.md)) |
| Primary metrics | if/elif on strings at 9 sites across 4 files | `MetricDef` registry ([07](07-analysis-surfaces.md)) |
| Findings renderers | md/html/dossier/card each re-project privately | typed `Section` model + `Renderer` seam ([07](07-analysis-surfaces.md)) |
| Benchmark importers | if/elif on `--benchmark` | `IMPORTERS` registry ([07](07-analysis-surfaces.md)) |
| Adapters, CI methods, kappa, variance, task sources | already polymorphic | unchanged |

Deliberately **not** made polymorphic: verb registration and adapter/engine
registries stay explicit lists (auditability posture), the fake/real judge
split stays a provider, and the arm-blind fake engine stays arm-blind (that is
a designed property, `docs/design/shakedown.md:99-104`).

## 4. Phases and gates

Every phase ends with `make verify` green, and no phase changes contract
bytes (proven by the Phase-0 goldens). Phases are ordered so user-visible
value (SDK, images) lands early and risky decomposition lands late, behind
the safety nets.

| Phase | Content | Domain plans | Gate |
|---|---|---|---|
| **0 — Safety nets & defects** | golden ledger/serialization guards, render byte-fixtures, test-fixture extraction, CI marker fixes, the 8 defect fixes (reproduce-first) | [01](01-safety-nets.md) | goldens committed & passing; zero cross-test-file imports; defects fixed or explicitly deferred with owner |
| **1 — Write path & stage APIs** | spec/task/run-config serializers + builders, stage verb bodies extracted to library functions, `LedgerView`, CLI kit, lock-preflight decomposition | [02](02-experiment-sdk.md), [06](06-ledger-telemetry.md) §LedgerView | an experiment can be authored, locked, run, graded, judged, analyzed from Python with no subprocess and no hand-written YAML |
| **2 — SDK facade + hermetic shakedown conversion** | `harness/sdk` package, `ExperimentWorkspace`, starter/rubric templates single-sourced; `golden.py`/`tripwires.py`/`official.py` rewritten on it | [02](02-experiment-sdk.md), [08](08-shakedown-and-tests.md) | `make shakedown` passes on the SDK-based scripts; scripts contain vectors + assertions only |
| **3 — Images, environments, infra** | `harness/hermetic` (docker cmd builder, networks, `MeteringProxy`), `harness/images` (+ `images/base`, `images/official/*`, `verdi_agent.py`, `bench images build/verify/list`), holdout hierarchy + executing runner + in-image grader entrypoint; OTLP trace collector (`TraceCollector`) + span→trajectory adapter | [03](03-images-and-environments.md), [04](04-run-engine.md) §hermetic, [05](05-grading-judging.md) §holdouts, [09](09-otlp-trace-capture.md), [10](10-span-trajectory-normalization.md) | `harbor.py`/`harbor_multiagent.py` contain zero raw docker calls; a new stack image = extend base + `bench images verify` |
| **4 — Polymorphism hardening** | Engine ABC + registry, provider `Completion`, `JudgingSession`, Detector/channel registries, scorer envelope, MetricDef, importer registry | [04](04-run-engine.md), [05](05-grading-judging.md), [06](06-ledger-telemetry.md), [07](07-analysis-surfaces.md) | each walkthrough cost hits its target (§6); no behavior change outside flagged decisions |
| **5 — God-module decomposition** | `analyze/report.py` → findings package + unified fence; `serve` webkit + page-kit; `ledger/events.py` declarative registry; `run_trial` capture pipeline; `grade/container.py` split; `run_forensics` phases | [04](04-run-engine.md), [05](05-grading-judging.md), [06](06-ledger-telemetry.md), [07](07-analysis-surfaces.md) | byte-diff fixtures unchanged; no module > ~500 LOC without a documented reason |
| **6 — Docs, examples, end-state** | usage-guide/README on the SDK path, `docs/engines.md` engine contract, image-authoring guide, shakedown docs refresh | [03](03-images-and-environments.md), [08](08-shakedown-and-tests.md) | docs walkthrough reproduces from an empty dir; example-spec exists in exactly one place |
| **7 — Reuse mop-up** (post-6) | reference multi-agent image onto `images/base`, `ManagedSidecar` hoist, findings `Renderer` seam (dossier/card), A5 completeness meta-test extension, drift trivia batch | [11](11-post-phase-6-mopup.md) | render goldens byte-identical; zero tunnel code outside `images/base`; every all-packages contract list machine-forced |

Rough sizing (uninterrupted, one engineer + review): Phase 0 ≈ 1 wk · 1 ≈ 1.5 wk
· 2 ≈ 1 wk · 3 ≈ 2 wk · 4 ≈ 1.5 wk · 5 ≈ 2–3 wk · 6 ≈ 0.5 wk · 7 ≈ 0.5–1 wk.
Phases 3–5 have internal parallelism (per-domain plans are independently
mergeable workstreams); Phase 7's G1/G2/G4/G5 are likewise independent, G3 last.

## 5. Approval register — changes that touch versioned contracts

Per CLAUDE.md, these ship **only** with explicit human approval and a
migration story. Everything else in this plan is additive or internal.

| # | Change | Contract touched | Migration story | Plan |
|---|---|---|---|---|
| A1 | `schema_version` field added to `/verdi/request.json` | de-facto trial-image contract | additive key; existing images `json.loads` + pick keys; docker test extended | [03](03-images-and-environments.md) |
| A2 | Versioned `holdout.json` schema (`kind` discriminator) | new contract (today amorphous) | v1 = today's bytes accepted forever; `kind` absent ⇒ opaque/out-of-repo grading unchanged | [05](05-grading-judging.md) |
| A3 | Additive `tasks.yaml` fields (`holdout:` inline, environment keys) | task-content sha feeds the lock | additive + optional; sha covers raw bytes so *existing* locked files are untouched; builders emit them pre-lock only | [03](03-images-and-environments.md), [05](05-grading-judging.md) |
| A4 | `plugins` → `plugin_ids` fingerprint fix | control-reuse fingerprint (versioned, `FINGERPRINT_VERSION`) | reproduce-first test; bump `FINGERPRINT_VERSION`; old bundles refuse cleanly (designed lever) | [01](01-safety-nets.md) D2 |
| A5 | `.importlinter` source-list additions for `sdk`/`images`/`hermetic`/`webkit` + new "sdk is a leaf" contract | enforced contract file | mechanical; the completeness meta-test (`tests/test_import_contracts.py:40-60`) forces it in the same commit | [02](02-experiment-sdk.md) |
| A6 | Re-document "only harbor talks to Docker" as "only `hermetic` talks to Docker" | documented intent (`harbor.py:3`), already false via `grade/container.py` | docstring + deep-dive edit; AST seam test unchanged (still bans naming harbor) | [04](04-run-engine.md) |
| A7 | `ledger/events.py` physical reorganization | named write seam | byte-identity proven by golden constructor replay; module path preserved via package re-export | [06](06-ledger-telemetry.md) |
| A8 | Verdict-JSON format instruction moves from user rubrics into harness packet framing | `packet_sha256` framing fingerprint; rubric pinning | opt-in flag or major framing-version bump; decision explicitly deferred to human | [05](05-grading-judging.md) |
| A9 | Read-side strict `tasks.yaml` validation | lenient loader feeding the lock hash | ship as `bench tasks validate` lint first; strictness later if ever | [02](02-experiment-sdk.md) |
| A10 | Fake-engine fail-closed parity for configured-but-missing proxy log | engine behavior asymmetry | behavior change to fake engine only when a proxy is *configured*; tests updated deliberately | [04](04-run-engine.md) |
| A11 | `otlp` config on `TrialRequest`/`RunConfigFile`; OTel env-var injection (incl. `NO_PROXY`) | engine env surface (request.json untouched) | additive, `None`-defaulted; no behavior unless configured; `NO_PROXY` pinned by contract test | [09](09-otlp-trace-capture.md) |
| A12 | `span_log_missing`/`spans_corrupt` join the `failure_reason` vocabulary (after `proxy_log_missing`); fake-engine parity | closed `failure_reason` vocabulary + downgrade ladder | fires only when a collector is configured (opt-in = required); A10-pattern parity tests | [09](09-otlp-trace-capture.md), [10](10-span-trajectory-normalization.md) |
| A13 | Additive `spans_sha` on `TrialRecord` + hoisted on the `trial` event (`omit_if_none`) | trial event payload key set | third instance of the sha-hoist pattern; existing event bytes unchanged when absent; constructor-replay golden extended same commit | [09](09-otlp-trace-capture.md) |
| A14 | `opentelemetry-proto` as optional extra `verdi-bench[otlp]`, lazy import, fail-loud | dependency surface | core install unchanged; protobuf decode refuses loudly without the extra | [09](09-otlp-trace-capture.md) |

## 6. Measured extensibility targets

From the audit walkthroughs (files a developer touches, excluding tests):

| Task | Today | Target |
|---|---|---|
| Author + lock + run an experiment from Python | impossible past lock (7 steps, 3 hand-serialized files, then subprocess) | 1 import, ~10 lines |
| New judge/LLM provider | 2 files, 4 obligations of which 1 is typed | 1 file + registry entry, all obligations typed |
| New engine | ~5 files + reverse-engineering harbor.py folklore | 1 file + registry entry against a documented ABC |
| New holdout kind | impossible in-library (bespoke grader image) | 1 subclass |
| New gaming detector | 2 files, id declared 3×, fixtures unenforced | 1 registry entry + fixture pair (enforced by meta-test) |
| New primary metric | 3–4 files, ~9 string-dispatch edit sites | enum value + 1 `MetricDef` + power entry |
| New findings output format | fork inside a 2,198-line module | 1 `Renderer` over the sections model |
| New benchmark importer | source class + 3 hand-synced strings | source class + registry entry |
| New test image | consult 4 docs + 2 reference agents, hand-roll ~35 lines of proxy code | extend `images/base`, write agent logic only, `bench images verify` |
| Stand up metering infra | 7 raw docker steps, engine-internal constants | `MeteringProxy.managed(...)` context manager |

## 7. Defect register (fix in Phase 0, reproduce-first per CLAUDE.md)

| # | Defect | Where | Severity |
|---|---|---|---|
| D1 | Verb registration swallows *transitive* `ModuleNotFoundError`, silently dropping CLI verbs | `harness/cli.py:192-194` | high (fail-open) |
| D2 | Control-reuse fingerprint reads task key `plugins`; grading + docs use `plugin_ids` — grader drift can never fire for doc-conformant tasks | `harness/run/reuse.py:77` vs `harness/grade/cli.py:39` | high (silent under-coverage; approval A4) |
| D3 | In-container plugin entrypoint imports no plugin module ⇒ `UnknownPluginError` for any real containerized plugin run | `harness/grade/run_plugin.py:34-36` | high (latent break) |
| D4 | Retired model id as default parameter (the exact class EVAL-24-D002 banned) | `harness/process/score.py:198` | medium |
| D5 | Secret scan omits holdout results that the identity scan covers — hand-maintained blob list drift | `harness/judge/packet.py:216-221` | medium |
| D6 | CI browser job enumerates 4 file paths; `test_serve_legibility.py` browser tests silently never run in CI | `.github/workflows/ci.yml:74-78` | medium |
| D7 | `Optional` used but never imported (masked by `from __future__ import annotations`) | `harness/analyze/nullsim.py:95` | low |
| D8 | Observer fence (`fence.py`) lacks the correction-consistency check the render fence enforces ⇒ `official_ready` can lie | `harness/analyze/fence.py` vs `report.py:1809-1824` | medium |
| D9 | "all shipped events registered" meta-test pins 14 of 31 event types | `tests/test_eval3_events.py:34-52` | low (drifted guard) |
| D10 | Trivia batch: stale `scratchpad/` docstring (`harbor_multiagent.py:10`), stray f-string (`report.py:1908`), fence comment numbering (`report.py:1780/1786`), author template + fixtures naming a retired model id | various | low |

## 8. Invariants (summary — full lists in each domain plan)

Never changed by this refactor, each pinned by a Phase-0 golden or an
existing AC test: chain canonicalization bytes and event envelope; all 31
registered event names and payload key sets; spec sha = raw file bytes;
task-content commitment recipe and sort order; seed sub-namespaces (every
`sub_seed(...)` string literal); trajectory/flight-recorder canonical bytes
and closed role vocabulary; holdout fence tags + nonce discipline;
`response_map`/reveal shapes; watermark bytes; `CantX` reason vocabularies;
`CARD_SCHEMA_VERSION` and comparability key; canary namespace; overlap
detector constants; the one-event-per-operation property; all eight
import-linter contracts (extended, never weakened); fail-closed CI switches.

## 9. How to consume this plan

Each domain plan is written to be independently actionable: motivation with
audit citations, target design (concrete classes/modules), migration steps,
constraining tests, and its own approval items. Recommended review order:
[01](01-safety-nets.md) (gates everything) → [02](02-experiment-sdk.md) +
[03](03-images-and-environments.md) (the product) → the rest by interest.
[09](09-otlp-trace-capture.md)/[10](10-span-trajectory-normalization.md)
(OTLP telemetry) depend on [04](04-run-engine.md) §1 hermetic and slot into
Phase 3; their decisions are recorded in `decisions.ndjson` (A11–A14,
D-09-1, D-10-1, D-10-2 — all accepted 2026-07-06).
[11](11-post-phase-6-mopup.md) collects the reuse gaps found by the
2026-07-06 branch audit; it runs after Phase 6 and adds no approval items.
Decisions the human must make before the affected phase are marked
**DECISION** inline and collected in each plan's header.
