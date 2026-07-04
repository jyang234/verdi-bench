---
# MACHINE CONTRACT — PROPOSED (not yet graduated; AC enforcement begins when
# this file moves to docs/design/specs/ in the same commit as its first AC
# tests). Drafted 2026-07-04: the reviewer-safe surface every observability
# story since EVAL-13 has explicitly deferred — deferred precisely because
# its blinding gates must be designed in, not bolted on.
kind: "story"
ticket: "EVAL-18"   # synthetic key — source: EVAL-13-D003 deferred reviewer surface
parent: "EVAL-1"
title: "Reviewer surface: blinded capture-then-reveal in the browser, isolated from the operator tier by construction"
services: []
home: null          # inherited from EVAL-1 (verdi-bench)
inherited_decisions:
  - "EVAL-1-D001"   # instrument residence + name (RESOLVED: verdi-bench)
  - "EVAL-13-D003"  # operator view is openly unblinded WITH disclosure — this story is its counterpart
  - "EVAL-7-D003"   # IPW kappa estimator seam (calibration consumes these verdicts unchanged)
touchpoints:        # PLANNED symbols [judgment]
  - "harness/review/serve.py:make_review_server"
  - "harness/review/cli.py:register"
  - "harness/review/record.py:record_human_verdict"
  - "harness/review/record.py:reveal_comparison"
  - "harness/review/scrub.py:assert_identity_free"

graph_provenance: []

acceptance:
  - id: "AC-1"
    text: "The reviewer surface is its own verb and process (bench review serve), structurally incapable of leaking the operator tier: its handler serves only built blinded packets, the capture form, and the post-verdict reveal — it has no route for status, events, timeline, compare, trials, or artifacts, and its module imports none of harness.serve or harness.status (extended import contract). Every served packet page is re-scanned against the identity canary list before the bytes leave the process; a packet that would leak is refused with the reason, never served."
    vc: "Route enumeration: operator paths 404; the import contract names review-serve's isolation and a planted harness.serve import breaks it; a canary-poisoned packet file is refused with a named error while clean packets serve."
    touchpoints:
      - "harness/review/serve.py:make_review_server"
      - "harness/review/scrub.py:assert_identity_free"
    tests: []
  - id: "AC-2"
    text: "Capture-then-reveal survives the transport: the verdict form (winner 1|2|TIE|CANT_JUDGE, confidence, notes) and the two blinding-integrity questions submit as ONE ledgered human_verdict through the existing constructor — exactly one event, actor = the launch-bound reviewer; no page, response, or route exposes response_map, arm identities, or the judge's verdict before that event exists; the reveal affordance appears only afterwards and is its own explicit action producing exactly one ledgered reveal via reveal_comparison, which keeps refusing pre-verdict reveals at the record layer beneath the UI."
    vc: "One capture POST → one human_verdict with integrity answers and reviewer actor; pre-verdict pages contain no arm strings (canary scan) and no reveal affordance; the reveal POST → one reveal event; a hand-crafted pre-verdict reveal POST is refused with RevealError's message."
    touchpoints:
      - "harness/review/record.py:record_human_verdict"
      - "harness/review/record.py:reveal_comparison"
    tests: []
  - id: "AC-3"
    text: "Mutation posture: the reviewer's identity binds at launch through resolve_actor (refused loudly when unresolvable — never 'unknown'); the two capture/reveal endpoints are the only mutating routes and both flow through the existing record-layer functions (no new event kinds, one-event-per-operation preserved); every GET is side-effect-free (experiment bytes identical after arbitrary browsing); loopback bind by default."
    vc: "Launch without a resolvable reviewer exits with ActorResolutionError's message; non-enumerated POSTs are refused; a browse-everything pass leaves the directory byte-identical; REGISTERED_EVENTS is unchanged by the new modules."
    touchpoints:
      - "harness/review/cli.py:register"
    tests: []
  - id: "AC-4"
    text: "Queue ergonomics at leader parity, adapted to our verdict vocabulary: a pending-comparisons queue (built packets minus recorded verdicts) with progress (n of m), keyboard-first capture (1 / 2 / T for tie / C for CANT_JUDGE, field navigation, submit-and-advance), CANT_JUDGE always reachable, and integrity questions required before submit enables; served pages are self-contained (the needle property) and carry the reviewer-tier standing instruction — the inverse of the operator banner: do not open the operator view for experiments you review."
    vc: "Headless drive: the queue advances on submit with the hotkeys; a submit without integrity answers is refused client- and server-side; the needle scan passes; the reviewer banner text names the operator-view prohibition."
    touchpoints:
      - "harness/review/serve.py:make_review_server"
    tests: []
  - id: "AC-5"
    text: "Cross-surface isolation is testable, not aspirational: with an operator server and a reviewer server running over the same experiment, the reviewer surface never serves an unblinded byte (continuous canary scans across all its routes) and the operator surface gains no new route or affordance from this story — its EVAL-14 AC-8 posture tests pass unmodified. Packets served are the same built-packet bytes the CLI flow would show (D004): the surface adds transport, never content."
    vc: "Dual-server drive: every reviewer route passes the canary scan while operator routes still serve arm identities; bench serve's posture suite is untouched; served packet bytes equal the built packet file bytes."
    touchpoints:
      - "harness/review/serve.py:make_review_server"
    tests: []

constraints:
  - text: "Blinding is enforced at every layer that already enforces it, plus the transport: packet build scrubs (existing), the reviewer server re-scans before serving (belt-and-suspenders, the packet-validator precedent), and the record layer keeps its capture-before-reveal gates — the UI adds no bypass and holds no secret: response_map never reaches its process's responses."
    enforced_by: "AC-1/AC-2/AC-5 canary and refusal tests on graduation"
  - text: "The surface cannot police memory — it polices exposure: who reviewed is ledgered (actor on the verdict), what they saw is exactly the built packet, and the integrity questions remain the honest instrument for residual recognition [EVAL-7 §4.3]. The operator-view prohibition is a stated norm on every page, like the operator banner it mirrors."
    enforced_by: "AC-4 banner test on graduation"
  - text: "No new event kinds and no verdict-schema changes: EVAL-7's kappa calibration and EVAL-9's reveal-keyed process scoring consume these verdicts and reveals unchanged."
    enforced_by: "AC-2/AC-3 tests on graduation"

decisions: []
open_decisions:
  - "EVAL-18-D001"  # surface: separate verb + process with import-contract isolation (recommended) vs role-gated routes on one server
  - "EVAL-18-D002"  # reviewer identity: launch-bound via resolve_actor (recommended) vs per-request field
  - "EVAL-18-D003"  # hotkeys: 1/2/T/C + enter-advance mapped to our verdict vocabulary (recommended) vs the LangSmith A/B/E convention
  - "EVAL-18-D004"  # packet transport: serve the built packet bytes verbatim (recommended) vs re-render live per request

policy_proposals: []
predicted_reach: null
expected_verify: "On graduation: route/import isolation with a planted-import break, the one-event capture and reveal flows with record-layer refusals, canary scans across every reviewer route under a dual-server drive, actor/loopback/needle posture, and the keyboard queue drive."
---

# EVAL-18 — Reviewer surface (proposed)

## Problem & context

Human verdicts are the instrument's calibration authority (EVAL-7), and
today they are captured by CLI against packet files — correct, blinded, and
slow enough that review debt accumulates. The leaders' annotation queues
show the ergonomics reviewers actually sustain (hotkey scoring,
auto-advance, progress); we refused those queues in the *operator* surface
because they sat next to unblinded traces. This story builds the queue
where it belongs: a separate surface that can only ever see what a blinded
reviewer may see.

## Goal

A reviewer opens one loopback URL, works a queue of blinded comparisons
with their keyboard, submits verdict + integrity answers as the one
ledgered event the CLI would have written, and — only after — may perform
the explicit, ledgered reveal. The operator tier stays a different process,
a different verb, and a different import graph.

## Design

`bench review serve --reviewer <name>` (identity via resolve_actor, D002)
hosts: the queue (built packets minus recorded verdicts), the packet page
(built bytes verbatim, D004, re-scanned against identity canaries before
serving), the capture form (winner/confidence/notes + the two integrity
questions, one POST → record_human_verdict), and the post-verdict reveal
(one POST → reveal_comparison). Isolation is structural: no operator
routes, no harness.serve/harness.status imports (contract-enforced), no
response_map in the process's output surface. Ergonomics: 1/2/T/C hotkeys,
submit-and-advance, n-of-m progress (D003) — the Langfuse/LangSmith queue
patterns from the parity research, adapted to our verdict vocabulary.

## Out of scope

Assignment/reservation across multiple reviewers (single-reviewer loopback
v1; the verdict's actor field already distinguishes reviewers); EVAL-9
process-score capture (its own openly-unblinded flow); remote hosting and
auth; any change to sampling strata or kappa machinery.
