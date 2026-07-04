---
# MACHINE CONTRACT — PROPOSED (not yet graduated; AC enforcement begins when
# this file moves to docs/design/specs/ in the same commit as its first AC
# tests). Drafted 2026-07-04: the EVAL-14 P2 list, promoted to a story now
# that the P0/P1 surface is real — every item here is ergonomics over
# existing seams; none adds telemetry, events, or trust surface.
kind: "story"
ticket: "EVAL-19"   # synthetic key — source: EVAL-14 P2 deferred polish
parent: "EVAL-1"
title: "Operator UI P2: static bundle export, typed filter grammar, saved views, and honest small-multiples polish"
services: []
home: null          # inherited from EVAL-1 (verdi-bench)
inherited_decisions:
  - "EVAL-1-D001"   # instrument residence + name (RESOLVED: verdi-bench)
  - "EVAL-13-D003"  # unblinded-operator disclosure carries to every new render, bundle included
  - "EVAL-13-D004"  # polling transport (unchanged; the bundle is the no-transport case)
  - "EVAL-14-D001"  # single-file no-build page (the grammar/views land inside it)
touchpoints:        # PLANNED symbols [judgment]
  - "harness/serve/bundle.py:write_bundle"
  - "harness/serve/cli.py:register"
  - "harness/serve/page.py:OPERATOR_PAGE"

graph_provenance: []

acceptance:
  - id: "AC-1"
    text: "Static bundle export (the Inspect view-bundle borrow): bench serve <dir> --bundle <out> writes a self-contained snapshot of the operator view — the page plus its data embedded inline (status, events, timeline, trial details, compare, fence) — that opens from the filesystem with no server, carries the unblinded-operator banner and ADVISORY/EXPLORATORY watermarks on every screen, contains no mutating affordance and no external reference (the needle property), and is byte-deterministic for a fixed (ledger, artifacts) input. Bundling appends no event and mutates nothing in the experiment directory."
    vc: "Two bundles over the same experiment are byte-identical; the bundle passes the needle scan and renders every screen headlessly from file:// with the banner present; the experiment dir digest is unchanged and no event lands."
    touchpoints:
      - "harness/serve/bundle.py:write_bundle"
    tests: []
  - id: "AC-2"
    text: "Typed filter grammar (the Langfuse two-tier borrow): a closed, documented grammar — field:value terms for the existing facets, negation (-field:value), * wildcards on id-like fields, and bare words as free text — compiles to exactly the same URL state the chips produce, both directions (chips render the grammar; grammar edits update the chips). Malformed input yields a named parse error displayed in place; it never silently applies a partial filter."
    vc: "Grammar strings and chip interactions produce identical URL states and row sets; each documented production round-trips; a malformed string shows the parse error and leaves the previous filter intact."
    touchpoints:
      - "harness/serve/page.py:OPERATOR_PAGE"
    tests: []
  - id: "AC-3"
    text: "Saved views: named local views (filter + sort + screen) stored in the browser's localStorage (D002 — the server stays structurally read-only), with rename and delete; the canonical shareable form remains the URL, and the UI says so — a saved view IS a stored URL fragment, restored by navigation, portable by copying the link."
    vc: "Headless drive: save/rename/delete round-trips across a reload; restoring a view reproduces the exact URL and row set; no request mutates the server or the experiment dir."
    touchpoints:
      - "harness/serve/page.py:OPERATOR_PAGE"
    tests: []
  - id: "AC-4"
    text: "Honest small multiples: the live screen's ETA derives client-side from trial-event completion timestamps, is labeled approximate, and is absent (not zero, not dash-dressed-as-data) below a minimum sample; per-arm cumulative-cost sparklines render as inline SVG from the same events with nulls as gaps (never zeros), following the dataviz mark discipline already used by the dossier."
    vc: "A two-trial experiment shows no ETA; a longer fixture shows the labeled estimate consistent with the timestamps; sparkline path points equal the per-arm cumulative costs with null-cost trials absent from the path, not zeroed."
    touchpoints:
      - "harness/serve/page.py:OPERATOR_PAGE"
    tests: []
  - id: "AC-5"
    text: "Tallies become navigation: compare-summary counts filter to their slice (click 'control 9' → control-won pairs), forensic flag chips deep-link to the trial's forensics tab, and every such affordance is URL-encoded so the filtered slice is shareable — the LangSmith/Braintrust header-tally idiom, on our separated-tier counts."
    vc: "Headless drive: each tally click filters to exactly the matching pairs with the state in the URL; a flag chip lands on the named trial's forensics tab; reload restores each slice."
    touchpoints:
      - "harness/serve/page.py:OPERATOR_PAGE"
    tests: []
  - id: "AC-6"
    text: "Posture under growth, again: bench serve remains GET-only with the EVAL-14 AC-8 suite passing unmodified; the bundle writer is added to the observability import-contract source lists; the page stays a single dependency-free file; no new ledger event kinds and no entrypoints."
    vc: "Existing posture suites pass untouched; the contract sections name the new module; REGISTERED_EVENTS and the entrypoint registry are unchanged."
    touchpoints:
      - "harness/serve/cli.py:register"
    tests: []

constraints:
  - text: "The bundle is an archive of the operator tier, and says so: banner, watermarks, and the chain verdict at bundle time are embedded — it is a snapshot with provenance, not a live view, and its determinism makes two archives of the same ledger comparable byte-for-byte [the dossier's self-containment lineage]."
    enforced_by: "AC-1 tests on graduation"
  - text: "The grammar is closed and documented in the page itself (a ? affordance lists the productions); anything outside it is a parse error, never a guess — the fail-loud posture applied to query strings."
    enforced_by: "AC-2 tests on graduation"
  - text: "Views never move trust server-side: localStorage only, URL as the portable truth; a future team surface would revisit this as part of the platform story, not silently here [D002]."
    enforced_by: "AC-3 tests on graduation"

decisions: []
open_decisions:
  - "EVAL-19-D001"  # bundle surface: bench serve --bundle flag (recommended) vs a new verb
  - "EVAL-19-D002"  # saved-view storage: browser localStorage, URL stays canonical (recommended) vs server-side files
  - "EVAL-19-D003"  # grammar scope v1: facet fields + negation + id wildcards + free text (recommended) vs comparison operators on numerics too

policy_proposals: []
predicted_reach: null
expected_verify: "On graduation: byte-determinism and needle/banner tests for the bundle, grammar↔chips round-trip and parse-error tests, saved-view drives, null-honest ETA/sparkline fixtures, tally-navigation drives, and the unchanged posture suites."
---

# EVAL-19 — Operator UI P2 (proposed)

## Problem & context

EVAL-14 shipped the operator surface at parity on structure; the parity
research names the remaining leader idioms worth having — Inspect's static
bundle, Langfuse's typed grammar over chip state, saved views, and
tallies-as-filters — plus two honesty-sensitive small multiples (ETA,
cost sparklines) the wireframes deferred. All of it is presentation over
seams that already exist.

## Goal

The operator surface archives like the dossier (one deterministic
self-contained snapshot), filters like a power tool (grammar and chips as
two views of one URL state), and remembers like a workbench (named local
views) — without a single new event kind, mutating route, or dependency.

## Design

`write_bundle` renders the page with data embedded inline (the page already
separates data acquisition behind one helper; embedded mode replaces the
fetch layer), stamped with the bundle-time chain verdict and watermarks —
deterministic because every input is file state. The grammar is a closed
parser inside the single-file page compiling to the existing URLSearchParams
state; chips and grammar are projections of the same object. Saved views
wrap URL fragments in localStorage (D002). ETA and sparklines follow the
dataviz discipline: labeled approximations, nulls as gaps, inline SVG.

## Out of scope

SSE push (EVAL-13-D004 stands); server-side view storage or any team
feature (platform story); bundling the reviewer or authoring surfaces;
chart theming beyond the established tokens.
