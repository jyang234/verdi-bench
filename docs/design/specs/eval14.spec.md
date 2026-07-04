---
# MACHINE CONTRACT — see template header for consumers and YAML style rules.
# Graduated from specs/proposed/ 2026-07-04 in the same commit as the story's
# first AC tests, all four local decisions resolved (see
# eval14.decisions.ndjson). Drafted from the operator-UI wireframe record
# (session artifact) after the 2026-07-04 directives: workspace home, local
# single operator, interaction parity with the leading eval platforms.
kind: "story"
ticket: "EVAL-14"   # synthetic key — source: 2026-07-04 UI-parity directive (session)
parent: "EVAL-1"
title: "Operator UI v2: workspace home, trial drill-down, and leader-parity interaction ergonomics"
services: []
home: null          # inherited from EVAL-1 (verdi-bench)
inherited_decisions:
  - "EVAL-1-D001"   # instrument residence + name (RESOLVED: verdi-bench)
  - "EVAL-13-D003"  # unblinded operator view with standing disclosure (carries to every screen)
  - "EVAL-13-D004"  # client polling transport (carries; tail cursor reused)
touchpoints:        # PLANNED symbols [judgment]
  - "harness/serve/server.py:make_server"
  - "harness/serve/workspace.py:scan_workspace"
  - "harness/serve/trial.py:trial_detail"
  - "harness/serve/compare.py:paired_comparisons"
  - "harness/serve/page.py:OPERATOR_PAGE"

graph_provenance: []

acceptance:
  - id: "AC-1"
    text: "Workspace home: bench serve --root <dir> scans one level for directories containing ledger.ndjson and serves /api/experiments — per-experiment status summaries (state, cells, spend, grade/judge progress, fence state, last event ts, heartbeat liveness). A broken-chain experiment renders withheld (chain.ok=false, sections null), never zeros; scan tolerates non-experiment directories silently."
    vc: "A root fixture with running/finished/tampered/empty experiment dirs yields exactly the expected summaries; the tampered one is withheld; a plain subdirectory is not listed."
    touchpoints:
      - "harness/serve/workspace.py:scan_workspace"
    tests:
      - "test_ac1_workspace_scan_summaries"
  - id: "AC-2"
    text: "Trial drill-down: /api/trial/<trial_id> aggregates, for one trial, the ledgered record, grade/cant_grade events with per-assertion detail, judge verdicts whose comparison includes it, forensic flags naming it, egress attempts, and the sha-verified trajectory with its status (verified|absent|missing_artifact|sha_mismatch|corrupt) — nulls stay null end-to-end. Unknown trial id is 404 with the id named."
    vc: "A fixture trial with grade, verdict, flag, and verified trajectory returns every section with exact values; a null-telemetry trial returns nulls (never zeros); an unknown id 404s."
    touchpoints:
      - "harness/serve/trial.py:trial_detail"
    tests:
      - "test_ac2_trial_detail_aggregates"
  - id: "AC-3"
    text: "Deep links and routing: every view (home, experiment, trials with active filters, trial, compare with its toggles, findings) has a hash route that round-trips — load the URL, get the view with the same filter state; filters are URL-encoded, reload-safe, and shareable."
    vc: "Driving the page headlessly: navigating to each route renders the view; setting facets rewrites the URL; reloading the rewritten URL restores the facet state."
    touchpoints:
      - "harness/serve/page.py:OPERATOR_PAGE"
    tests:
      - "test_ac3_hash_routes_round_trip"
  - id: "AC-4"
    text: "Trials table + detail panel: faceted filters (arm, task, outcome, graded-state, flagged) computed from ledger events plus free text; selecting a row opens a side panel preview keeping table context; enter promotes to the full trial route; j/k/enter/esc keyboard conventions work on every list view."
    vc: "Facet combinations select exactly the matching trials against a scripted ledger; keyboard navigation drives selection and panel/page promotion headlessly."
    touchpoints:
      - "harness/serve/page.py:OPERATOR_PAGE"
    tests:
      - "test_ac4_facets_panel_keyboard"
  - id: "AC-5"
    text: "Feed ergonomics on the incremental tail: kind facets, follow-newest with pause-on-hover, and an N-new-events pill when scrolled away; polling continues to use the EVAL-13 byte cursor (no full-ledger re-reads in the page's poll loop)."
    vc: "Headless drive: appended events surface without a full refetch (offset advances monotonically); pausing holds the viewport; the pill count matches appended events while scrolled."
    touchpoints:
      - "harness/serve/page.py:OPERATOR_PAGE"
    tests:
      - "test_ac5_feed_tail_ergonomics"
  - id: "AC-6"
    text: "Paired compare: /api/compare pairs each task/repetition across the two locked arms — per-response workspace diff (the review-packet diff artifact), holdout outcomes, and advisory judge verdicts kept as separate lines (deterministic and advisory tiers never blended into one score); an only-disagreements filter; and the summary header watermarked EXPLORATORY unless the official fence (same code path as bench analyze) passes for this ledger."
    vc: "A fixture with mixed agreements renders pairs with diffs and separate tier lines; the disagreements filter selects exactly the differing pairs; a fence-failing ledger shows the EXPLORATORY watermark and an official-fence-passing one does not."
    touchpoints:
      - "harness/serve/compare.py:paired_comparisons"
    tests:
      - "test_ac6_compare_pairs_diff_and_watermark"
  - id: "AC-7"
    text: "Findings screen: the official-fence requirements render as a named checklist with per-item state (chain, selfcheck currency, calibration, contamination asymmetry), and existing render artifacts (findings.json, dossier HTML) are listed with the ledger head they were rendered against and served read-only; nothing is re-rendered by the UI."
    vc: "Fence states from a scripted ledger map one-to-one to checklist items; the served dossier bytes equal the artifact on disk; no findings_rendered event is appended by serving."
    touchpoints:
      - "harness/serve/server.py:make_server"
    tests:
      - "test_ac7_fence_checklist_and_artifacts"
  - id: "AC-8"
    text: "Posture preserved under growth: every screen carries the unblinded-operator disclosure; the server remains GET-only with no mutating endpoint; served pages remain self-contained (no external URI schemes or fetched assets); any new serve/status modules are added to the EVAL-13 import-contract source lists; no new ledger event kind is introduced."
    vc: "The EVAL-13 posture tests extend to every route: needle scan on all served HTML, non-GET refusal on all routes, experiment-dir byte-identity after arbitrary browsing, contract source-list membership asserted."
    touchpoints:
      - "harness/serve/server.py:make_server"
    tests:
      - "test_ac8_posture_all_routes"

constraints:
  - text: "Parity is ergonomics, never trust model: no annotation/scoring affordances in the operator UI (blinded review is its own surface with capture-then-reveal gates), no editable history (the ledger is append-only; corrections are new ledgered events), no auto-declared winners (verdicts exist only through the pre-registered rule behind the fence; everything else is watermarked EXPLORATORY on every layer)."
    enforced_by: "AC-6 and AC-8 tests on graduation"
  - text: "Null honesty carries to every new surface: unmeasured telemetry renders as 'not measured', never zero, in tables, panels, tooltips, and step strips [EVAL-4-D004 inherited]."
    enforced_by: "AC-2 tests on graduation"
  - text: "The UI renders what the seams return and never re-derives statistics client-side; fence and watermark vocabulary comes from the same code paths bench analyze uses."
    enforced_by: "AC-6/AC-7 tests on graduation"

decisions:
  - "EVAL-14-D004"  # per-step content (RESOLVED: trajectory-v3-additive-detail — capture-side slice behind P0, five guardrail ACs recorded in eval14.decisions.ndjson)
  - "EVAL-14-D001"  # frontend form (RESOLVED: single-file-no-build)
  - "EVAL-14-D002"  # trial detail default (RESOLVED: side-panel-then-page)
  - "EVAL-14-D003"  # workspace discovery (RESOLVED: ledger-scan)
open_decisions: []

policy_proposals: []
predicted_reach: null
expected_verify: "On graduation: AC suite green including headless page drives (routing, facets, keyboard, tail ergonomics), posture needle-scans on every route, and the fence-parity watermark test."
---

# EVAL-14 — Operator UI v2 (proposed)

## Problem & context

EVAL-13 shipped the substrate (heartbeat, tail cursor, status seam, a minimal
operator page) and the 2026-07-04 review set the bar: multiple experiments
under one workspace home, local single operator for v1, and interaction
ergonomics at parity with the leading eval platforms (Braintrust, LangSmith,
W&B Weave, Langfuse, Inspect AI viewer). The wireframe record (session
artifact, 2026-07-04) fixes six screens and a parity checklist; this spec is
its buildable form.

## Goal

An operator can open one URL, see every experiment live, drill from a running
row to a single trial's sha-verified step timeline in two keystrokes, compare
arms diff-first per task, and read the fence state as a checklist — with the
ergonomics of the best commercial tools and none of their trust-model
shortcuts (nothing mutable, nothing unwatermarked, nothing unblinded without
saying so).

## Design (mirrors the wireframe record)

Screens: (1) workspace home — live experiments table with attention badges,
withheld-on-tamper rows; (2) experiment live view — pipeline stage rail,
in-flight card with attempt lineage, spend vs pre-registered ceiling, feed
with follow/pause/new-pill; (3) trials — faceted table with side-panel
preview; (4) trial detail — step strip on relative_ts, tier tabs
(trajectory/grade/forensics/egress/raw); (5) compare — paired per-task diffs,
separate deterministic/advisory summary lines, EXPLORATORY watermark unless
the official fence passes; (6) findings — fence checklist + hosted dossier.

Deliberately not borrowed (constraints above): annotation queues, editable
history, live token streaming, auto-declared winners.

## Build order

P0: AC-1..AC-5 (workspace + drill-down + routing/feed ergonomics).
P1: AC-6..AC-7 (compare + findings). AC-8 holds throughout.
P2 polish (ETA estimate, saved views, sparklines) rides later stories.

Trajectory v3 (D004, resolved): the per-step `detail` capture is a separate
capture-side slice — schema_version 3, adapters, and the five guardrail ACs
from eval14.decisions.ndjson — sequenced behind P0. Trial detail (AC-2)
ships rendering the current step schema plus the whole-trial workspace diff;
`detail` lights up where present, and absent detail renders "not captured in
this record version" (old records never backfill). Per-step patch
affordances are claude-code-mostly by data availability; codex steps show
commands/timings.

## Out of scope

Reviewer-safe blinded views and review capture (own story, blinding gates
built in); any mutating endpoint (plan/run from the browser — needs actor
plumbing and one-event obligations); auth/multi-user (platform work when
demand exists); step-level live streaming (engine-seam story).
