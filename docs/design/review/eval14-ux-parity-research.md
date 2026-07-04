# EVAL-14 UX-parity research — eval-platform interaction idioms (2026-07-04)

Condensed research record behind the EVAL-14 wireframes and proposed spec
(`docs/design/specs/proposed/eval14.spec.md`). Sourced from official docs,
open docs repositories, and 2025–2026 changelogs of: Braintrust, LangSmith,
W&B Weave, Langfuse, Arize Phoenix, Inspect AI, METR Vivaria. Direct site
fetches were proxy-blocked for several vendors; claims trace to each
product's official docs repo (raw.githubusercontent.com mirrors of
langchain-ai/docs, wandb/docs, langfuse/langfuse-docs,
UKGovernmentBEIS/inspect_ai, Arize-ai/phoenix, METR/vivaria) or to search
snippets quoting official pages (Braintrust, which has no public docs repo —
those claims are slightly less complete). Nothing below is invented; the
full per-product report with URLs lives in the session research transcript
and the load-bearing findings are restated here.

## The recurring idioms ("table stakes"), condensed

1. **Experiment lists carry score aggregates in column headers** — and the
   headers are interactive (Braintrust header stats filter by
   improvements/regressions; LangSmith headers show "N improved / M
   regressed" tallies that are clickable filters).
2. **"Select N → Compare"** is the universal entry to comparison
   (LangSmith, Weave, Langfuse, Phoenix); Braintrust auto-compares against
   a baseline.
3. **Explicit baseline designation converged in 2025**: LangSmith "set as
   source experiment", Weave "Make baseline" (pinned leftmost), Phoenix
   baseline runs (07-2025), Langfuse baselines (11-2025). Green=improved /
   red=regressed everywhere.
4. **Filter-to-regressions is the core comparison workflow** in every
   product (Langfuse: delta/threshold filters to "build your regression
   worklist"; Phoenix: value-flip correct→incorrect filters).
5. **Row-aligned example-keyed comparison** with per-example paging and
   repetition grouping (Braintrust "Trials" column, LangSmith "Repetition
   Summary", Inspect sort-by-sample-across-epochs).
6. **Density modes + a two-way diff mode** (LangSmith Compact/Full/Diff;
   Weave Summary/Side-by-side/Unified with a "Diff only" changed-rows
   toggle). Text diffing is restricted to exactly two candidates.
7. **Master-detail: side panel first, full page on demand** — click a row,
   get a right panel with the trace in context; explicit
   fullscreen/new-page escape (Braintrust names both buttons).
8. **Trace view = left tree + right tabbed detail pane**, with a 2025-era
   second temporal projection everywhere (Langfuse tree/timeline toggle +
   waterfall; Phoenix timeline view; Weave flame graph and Timeline/Peers/
   Siblings/Stack scrubbers; LangSmith waterfall).
9. **Find-in-trace** (Braintrust Find + span-type filter; Langfuse
   observation search; Weave op search) — mandatory once agent traces got
   long.
10. **Saved views + URL-serialized state** — filters/sort/columns persist
    as named views; the full query serializes into the URL so a link
    reproduces the exact slice (Langfuse explicitly; Braintrust saved
    custom views record in the URL; Weave Saved Views).
11. **Two-tier filtering**: point-and-click chips atop a typed grammar for
    power users (Braintrust BTQL/SQL; LangSmith operator DSL with
    Trace-vs-Tree scoping; Langfuse typed grammar with wildcards, negation,
    boolean groups).
12. **Review-queue ergonomics with hotkey scoring**: Braintrust `r` +
    auto-advance; Langfuse `←→` items / `↑↓` fields / `1–9` categorical /
    `Cmd+Enter` complete / `?` cheatsheet; LangSmith pairwise A/B/E keys;
    Phoenix vim-style `j`/`k` + `e`/`n` (v9, 05-2025). `j`/`k` became the
    de-facto navigation pair in 2025.
13. **Live behavior is mostly incremental refresh, not push. Inspect owns
    live tail**: per-sample live following while an eval runs, incremental
    metrics, shared live viewing of remote logs, and deliberate
    loading-state hygiene (virtualized lists, clear-before-paint).
14. **The 2025 agent-era layer**: session/conversation grouping above
    traces, graph renderings of agent structure, specialized tool-call/
    approval renderers, AI-assistant-generated custom render views
    (Braintrust Loop).
15. **Empty/loading/error states are rarely documented** — exceptions worth
    copying: Braintrust's empty state with a "Create default view" CTA,
    Inspect's loading-state discipline, Phoenix's explicit error-handling
    release item.

## Architectural kinship

The Inspect AI viewer is verdi-bench's closest relative: log-file-based, a
local `inspect view` server, auto-updating history over a log directory,
live-follow of running samples, and `inspect view bundle` producing a
static self-contained viewer for hosting/archiving — the same posture as
our dossier. Vivaria is the frontier of *intervention* UX (approve/rate the
agent's next action, rewind-and-rerun) — explicitly out of scope for our
read-only observer, noted for the eventual mutation story.

## What EVAL-14 takes, and what it refuses

Adopted (mapped to ACs in the proposed spec): live experiments table with
interactive aggregates (AC-1), side-panel-then-page master-detail (AC-4),
step timeline + find-in-steps (AC-2 data, screen 4), filter-to-
disagreements paired compare with two-way diff (AC-6), URL-serialized view
state (AC-3), feed tail ergonomics (AC-5), keyboard-first navigation
(AC-4), fence-checklist findings (AC-7). Deferred to P2: typed filter
grammar; static bundle export (`bench serve --bundle`, the Inspect idea).

Refused on purpose (constraints in the proposed spec): annotation queues in
the operator surface (EVAL-7 blinding is packet-and-reveal gated),
editable history (append-only ledger), auto-declared winners (the
pre-registered fence decides; everything else is watermarked EXPLORATORY),
and mutable baseline toggles in compare (the baseline is arm A by lock
order — pre-registration, not preference).
