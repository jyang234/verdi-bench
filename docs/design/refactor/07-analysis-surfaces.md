# 07 — Analyze, operator/reviewer surfaces, corpus (Phases 4–5)

**DECISIONS required:** A5-adjacent approval for the `webkit` package (an
`.importlinter` edit); whether serve/review/author JS moves from Python
strings to inlined package-data files (§4, recommended).

## 1. `analyze/report.py` → a findings package (Phase 5; the big one)

2,198 lines fusing six concerns (refusal taxonomy · findings models ·
ledger extraction · stats/section computation · the official fence ·
md+html rendering). Its underscore-privates are a fiction: `dossier.py`
imports **13** of them, `selfcheck.py` 5, `fence.py` 3, `cli.py` 1 — it is
the subsystem's shared library with no declared API. The fence exists
twice (render-time checks at `report.py:1653-1824`; an observer checklist
re-authored in `fence.py:41-209`) and has already drifted (D8: the
correction-consistency check exists only render-side). The two markdown
renderers hand-maintain the same ~14-section ordering divergently
(`:1883-1956` vs `:1962-2010`), and `render_html` is escaped markdown in
`<p>` tags (`:2164-2198`).

Target layout (report.py stays as a re-exporting facade until importers
migrate):

```
harness/analyze/findings/
  model.py        # FindingsDocument + typed Decision / ComparisonStats /
                  # EffectBlock (today raw dicts read by .get in three artifacts)
  extract.py      # ledger→series layer (the code dossier/selfcheck/card
                  # already import privately), on LedgerView
  sections.py     # Section objects: title, lines() -> list[str]  (today's exact
                  # markdown strings — wording is AC-pinned), data() -> typed
  fence.py        # ONE ordered list of FenceCheck objects: id, typed
                  # AnalyzeError, evaluate() -> (state, detail)
  render_md.py    # official/exploratory framing + watermark policy over the
                  # single canonical section sequence
  render_html.py  # a real HTML renderer over the same sections
```

- **Fence unification is the safety payoff:** `render_markdown` raises on
  the first failing check; `official_fence_report` itemizes the *same
  list*; the compare screen's `official_ready` consumes it — the D8 drift
  class becomes unrepresentable, and the dossier stops calling
  `render_markdown` and discarding a full render just for its side effects
  (`dossier.py:464-472`).
- **Section model ends comment-enforced parity:** dossier's
  `_disclosure_sections` (`dossier.py:401-453`) and the card's re-derived
  disclosure block (`card.py:376-388`, which literally comments a plea for
  parity) consume the same objects. A new output format becomes one
  `Renderer` over the model instead of a fork of a 2,198-line module.
- Gates: the Phase-0 byte-fixtures (findings md ×2, dossier html, card
  json) must be byte-identical after every extraction step;
  `CantAnalyzeReason` values, the AN-3 one-event envelope, watermark bytes,
  and `FindingsDocument` serialization (covered by `findings_sha256`) are
  frozen; exception messages partially asserted by tests are preserved.
- Also here: replace the index-coupled parallel lists
  (`comparisons`/`deltas_by_pair`, `report.py:1099-1191` →
  `_apply_holm`) with a small paired dataclass.

## 2. `MetricDef` registry (Phase 4)

`primary_metric` is dispatched by string comparison at ~9 sites across 4
files (`report.py:477,503,581,591,1012,1079`, `_METRIC_TELEMETRY_FIELD`
`:45-48`, `selfcheck.py:58`, plus plan-time power). One registry:

```python
@dataclass(frozen=True)
class MetricDef:
    id: str                              # the PrimaryMetric enum value
    telemetry_field: str | None
    claim_tag: str
    null_model: str
    pairwise_only: bool
    extract: Callable[[LedgerView, ...], PerTaskSeries]
METRICS: dict[str, MetricDef]
```

A new metric = enum value (schema — pre-registered vocabulary, additive,
locked-spec-compatible) + one `MetricDef` + a power-sim entry, instead of a
hunt across if/elif chains where a missed site surfaces as a runtime
`AnalyzeError` only when that section renders. Pinned by the per-metric
null-model/claim-tag AC tests and the closed-enum tests.

## 3. Corpus: importer registry + thin CLI (Phase 4; lowest risk)

- `IMPORTERS: dict[str, ImporterSpec]` in `corpus/benchmarks.py`; the
  `--benchmark` flag validates against `IMPORTERS.keys()` and derives its
  help/error text (today the valid set is hand-synced in three strings,
  `corpus/cli.py:41-43,65-78`). A new importer = a `TaskSource` class +
  a registry entry — the generic idempotent engine
  (`public.py:74-178`) already does the rest.
- Move `calibrate`'s inline stats (`cli.py:282-298`) into a corpus
  function and `admit`'s two-phase persistence orchestration
  (`cli.py:355-401`) beside `admit_task` — the two genuine thin-CLI
  violations; the other seven commands are already thin.

## 4. `harness/webkit` — shared read-only HTTP/page kit (Phase 5; approval)

serve, review, and author triplicate ~90 lines each of HTTP plumbing
(`_send`/`_json`/`_body`/`_method_not_allowed`/quiet logging/the
`type("Bound…Handler", …)` factory) and the mechanical page layer (CSS
token block, `h()` DOM builder, `j()` fetch wrapper, `window.__vb` test
seam) — copy-maintained because review may not import serve/status/author
(`.importlinter:116-127`, strict + transitive).

- **Placement is the whole design:** a new tier-neutral `harness/webkit`
  importing none of serve/status/author/review (same pattern as the
  existing `http_guard`, which proved the neutral-package approach). The
  `.importlinter` edit adding it to source lists is contract-file surgery →
  human approval (A5 mechanics).
- Layer 1 (`JsonRouteHandler`): route-table dispatch replacing if/elif
  chains, the `_NotFound/_Refused/_ChainBroken`→status mapping, host/CSRF
  guard invocation, `bind_handler()` replacing the three `type(...)`
  factories (and the author server's dynamic-subclass state injection,
  `author/server.py:392-396`). Each surface keeps its own routes,
  semantics, and banner text — blinded vs unblinded wording is product
  content and never shared.
- Layer 2 (page kit): the token CSS + mechanical JS chunks composed into
  each page string at import time, still emitting one self-contained
  document per surface. Preserve verbatim: the `const BUNDLE = null;`
  marker and needle-inerting (`bundle.py:37-51,117-124`), the
  no-external-URI needle scans, per-surface banners.
- **DECISION (recommended):** move the three SPAs' JS from escaped Python
  strings (1,470 lines in `serve/page.py` alone, non-ASCII as `\\u00b7`,
  business logic mirrored from Python by comment — `pairTallies`
  `page.py:772-775`, the comparison-id format `:801-802`, the route mirror
  `:273-295`) into real `.js` files under package data, inlined at
  serve/bundle time. Runtime output is byte-equivalent; the win is
  lintable/diffable JS. The Python↔JS logic mirrors additionally get
  parity tests (or the server starts shipping the derived values in its
  payloads, removing the mirrors — preferred where payload shape allows an
  additive field).

## 5. Typed `ResultCard` (Phase 4)

Pydantic model mirroring today's dict exactly; `serialize_card` reproduces
the current `json.dumps(sort_keys, separators=(",",":"))` bytes
(golden-pinned in Phase 0); renders consume the model; comparability
refusal on `(battery_sha, basis, metric)` and `CARD_SCHEMA_VERSION`
untouched. Any field change is a schema-version bump + comparability story
— out of scope here.

## 6. Invariants

Fence semantics + `CantAnalyzeReason` closed set; single
`findings_rendered` event, event-before-files ordering, card F-H5
freshness rule; watermark bytes on every layer; dossier zero-JS,
self-contained, byte-deterministic; `VERDICT_TEMPLATES` closed registry +
inventory test; bundle marker/inerting; reviewer isolation (strict,
transitive) and observability LLM-free contracts; every `sub_seed`
namespace literal (`"paired_bootstrap"`, `"holm_p"`, `"nullsim_*"`,
`"selfcheck_validate"`, `"review_floor"`, `"kappa_ci"`, …); selfcheck's
deliberate bare-`spec.seed` selection equivalence
(`selfcheck.py:79-88` — fenced at `report.py:1750-1761`); serve stays
GET-only with its route/artifact allowlists.
