# 11 — Post-Phase-6 mop-up (reuse gaps)

**Status:** proposed. **Scheduled after Phase 6 lands** — nothing here blocks
a phase gate, and everything here is internal or additive (no approval-
register items; the one contract-adjacent step reuses already-approved A5
machinery).

**Basis:** reuse audit of `refactor/instrument-to-product` (2026-07-06)
against the master plan's §3 polymorphism table and §6 extensibility
targets. Verdict: 8 of 9 polymorphism rows landed as specced; infrastructure,
SDK facade, and webkit adoption are clean. Five confined gaps survived — all
of the same copy-paste class the refactor exists to eliminate, so they are
mopped up here rather than left as folklore.

## Gap register

Ordered by risk of silent divergence (the harbor/harbor_multiagent failure
mode), not by effort.

| # | Gap | Where | Class |
|---|---|---|---|
| G1 | Reference multi-agent image hand-rolls the proxy tunnel and does not extend `verdi-base` | `images/reference/multi-agent/agent.py:58-79`, `images/reference/multi-agent/Dockerfile` (`FROM python:3.12-alpine`) | surviving copy-paste |
| G2 | `TraceCollector` duplicates `MeteringProxy` sidecar-lifecycle scaffolding | `harness/hermetic/tracing.py` vs `metering.py`: `__enter__`/`__exit__`, `_await_ready`, `_remove_container`, `_container_logs`, `_READY_PROBE`, log-dir resolution, `teardown_managed` | parallel-copy lifecycle |
| G3 | Findings `Renderer` seam absent; dossier and card do not render over the `Section` model | `harness/analyze/findings/` (no seam); `harness/analyze/dossier.py:400` re-projects `list[dict]`; `harness/analyze/card.py` projects independently (imports only `asymmetry_line`) | partial §3 row 8 |
| G4 | A5 completeness meta-test guards only the ledger contract's source list | `tests/test_import_contracts.py:34-60` — no equivalent for `sdk-is-a-leaf` (or harbor) source lists; no planted-import case for an sdk-leaf breach | unguarded contract list |
| G5 | Drift trivia: (a) dead asset still embeds the verdict-JSON contract literal; (b) contamination probe bypasses the shared scorer envelope its docstring claims; (c) `engine == "fake"` literals outside the engine registry; (d) stale `images/multi-agent-reference/` dir containing only a `.pyc` | (a) `scripts/shakedown/assets/golden/rubric.md:15-22`; (b) `harness/contamination/probe.py:222-253` vs `harness/judge/envelope.py:4`; (c) `harness/run/api.py:90,121`; (d) `images/multi-agent-reference/` | D10-style trivia batch |

## G1 — reference multi-agent image onto `images/base`

The exact ~35-line CONNECT-tunnel copy that `images/base/verdi_agent.py`
(`post_json`, `:173-212`) was created to kill survives, byte-equivalent, in
the one reference image that predates the seam.

Steps:

1. Rebase `images/reference/multi-agent/Dockerfile` on `FROM verdi-base`
   (pattern: `images/official/generic-llm/Dockerfile`).
2. Delete the private `post_json`/tunnel imports from
   `images/reference/multi-agent/agent.py`; import
   `WORKSPACE, AgentLog, read_request, post_json` from `verdi_agent` instead.
   Multi-agent orchestration logic is the only thing the file keeps — that is
   the §6 target ("extend `images/base`, write agent logic only").
3. Remove the stale `images/multi-agent-reference/` directory (a `.pyc`-only
   leftover; the live image is `images/reference/multi-agent/`). Covered
   under G5(d) but executed with this step since it is the same image.
4. `bench images verify` passes for the rebuilt image; the live docker
   multi-agent e2e (`harbor_multiagent.py` path) is the behavior gate.

Constraining test: extend `tests/test_images_build_verify.py` with a
source-level assertion that **no file under `images/` outside `images/base/`
defines a `Proxy-Authorization`/`set_tunnel` sequence** — the same
grep-the-tree style as the seam tests, so the next hand-rolled tunnel fails
CI instead of code review.

## G2 — `ManagedSidecar` base for proxy + collector

The docker *command* machinery is genuinely single-sourced
(`DockerClient`, `HardenedCommand`, `network.py` constants, shared pinned
base image via `COLLECTOR_BASE_IMAGE = PROXY_BASE_IMAGE`). What duplicated is
the managed-lifecycle scaffolding around it — near-identical member-for-member
between `tracing.py` and `metering.py`. Two copies is survivable; the third
sidecar (a future artifact-syncer, log-shipper, …) must not make it three.

Steps:

1. New `harness/hermetic/sidecar.py`: `ManagedSidecar` base owning
   `__enter__`/`__exit__`, `_await_ready` (parameterized ready-probe string +
   port), `_remove_container`, `_container_logs`, log-dir/basename
   resolution, and a module-level `teardown_managed(name, client)` helper.
   Subclass hooks: `container_name`, `port`, `_stand_up()` (the
   `HardenedCommand` recipe), optional `_pre_teardown()` (D-09-1 raw-log
   deletion stays collector-only).
2. `MeteringProxy` and `TraceCollector` become subclasses; deliberate
   divergences stay in the subclass (collector: `METERED_NETWORK`-only, no
   egress connect; proxy: dual-network + CONNECT allowlist).
3. Behavior gates: `tests/test_eval4_hermetic.py`, `tests/test_otlp_collector.py`,
   and the live docker e2es pass unchanged. No new behavior — this is a
   hoist, so no new golden is needed; the existing tests are the pin.

## G3 — finish §3 row 8: `Renderer` seam; dossier + card over `Section`

What exists: the typed `Section` model (`findings/sections.py:30`), canonical
`official_sections`/`exploratory_sections` builders, md + html renderers over
the model, and dossier reuse of the shared *line-builders* (content
duplication already gone). What is missing is the seam itself and the last
two consumers.

Steps, in byte-safety order:

1. Declare the seam: `Renderer` protocol in `findings/` —
   `render(sections: Sequence[Section], ctx: RenderContext) -> str` — with a
   dict registry keyed by format id (`md`, `html`, `dossier`, `card`),
   following the `CIMethod` model the master plan §2 designates as the one
   to copy. `render_md`/`render_html` register as-is (pure renaming).
2. `dossier.py:_disclosure_sections` returns `list[Section]` instead of its
   private `{"title","body"}` dicts; the dossier's layer-wrapping becomes a
   `Renderer` over that sequence. The line-builder reuse it already has is
   untouched.
3. Card is the judgment call: `card.py` projects from `compute_findings`
   output, not from findings prose, and `CARD_SCHEMA_VERSION` + the
   comparability key are §8 invariants. Bring the card's *rendered md/html*
   under the `Renderer` seam, but do **not** force the card's data model
   through `Section` — that would be seam-for-seam's-sake against a versioned
   contract. If during implementation this split proves unnatural, stop and
   ask rather than bending either model.
4. Byte gates: `tests/test_golden_renders.py`, `tests/test_findings_html.py`,
   and the dossier/card fixtures must be byte-identical before/after. This
   is a Phase-5-style decomposition: the goldens are the license to move.

## G4 — extend the A5 completeness meta-test

`tests/test_import_contracts.py` mechanically forces source-list completeness
only for `ledger-writes-only-via-events`. The `sdk-is-a-leaf` list is
complete *today* by hand; nothing fails when the next `harness/` package is
born outside it.

Steps:

1. Generalize `test_ledger_contract_source_list_covers_every_harness_package`
   to parametrize over the contracts whose source lists must enumerate every
   harness package (`ledger-writes-only-via-events`, `sdk-is-a-leaf`,
   `harbor-confined-to-seam`), asserting each list ⊇ discovered packages
   minus that contract's own target.
2. Add a planted-import case to `_CASES` (`:17-21`): a temporary
   `import harness.sdk` inside a subsystem module must turn the contract red
   — proving the contract bites, not just exists.

No `.importlinter` edits expected (the lists are currently complete); this is
test-only, so A5's "the meta-test forces it in the same commit" becomes true
as written.

## G5 — drift trivia batch

Single commit, D10 pattern:

- (a) Delete `scripts/shakedown/assets/golden/rubric.md` (dead since the
  scripts moved to the SDK rubric template; nothing reads it — only
  `assets/golden/tasks.yaml` is consumed, by `tests/test_schema_tasks.py:94`).
  The verdict-JSON contract then exists in exactly one place, closing the A8
  single-source claim.
- (b) Route the contamination probe's multi-call sequence
  (`probe.py:222-253`) through `harness/judge/envelope.py`, or — if the
  probe's raw `prov.complete` loop is deliberate (it wants unenveloped
  completions for contamination evidence) — fix the envelope docstring
  (`envelope.py:4`) to stop claiming the probe as a consumer, and say why
  in the probe. Either way the advertised reach matches reality.
  **Recommendation:** adopt the envelope; the probe re-implements its
  fail-closed sequencing for no visible reason. Reproduce-first if adoption
  changes any emitted evidence bytes.
- (c) Replace the two `engine == "fake"` literals (`run/api.py:90,121`) with
  a capability flag on the engine registry entry / `EngineBase` (e.g.
  `manages_real_infra`), so infra gating derives from the registry instead of
  a string that a new offline engine would have to know to imitate.
- (d) `images/multi-agent-reference/` removal — executed with G1.

## Sequencing and gate

G1 ∥ G2 ∥ G4 ∥ G5 are independently mergeable; G3 lands last (largest blast
radius, wants the byte-goldens green and quiet). Every step ends with
`make verify` green, per the standing directive.

**Gate:** the master plan's §2 "already good" table gains no counterexamples —
concretely: zero tunnel code outside `images/base` (G1 test), one sidecar
lifecycle (G2), every findings surface behind the `Renderer` registry with
goldens byte-identical (G3), every all-packages contract list machine-forced
(G4), and grep finds no verdict-JSON literal, envelope bypass, or
`== "fake"` infra gate (G5).
