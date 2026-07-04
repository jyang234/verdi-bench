---
# MACHINE CONTRACT — see template header for consumers and YAML style rules.
# Graduated from specs/proposed/ 2026-07-04 in the same commit as the story's
# first AC tests, all four local decisions resolved (see
# eval17.decisions.ndjson). The second half of the original 2026-07-04
# directive ("configure the experiments in the UI") — deferred by EVAL-13/14
# until the mutation obligations could be designed rather than bolted on.
kind: "story"
ticket: "EVAL-17"   # synthetic key — source: 2026-07-04 UI directive, authoring half
parent: "EVAL-1"
title: "Authoring surface: browser pre-registration with the lock as a ceremony, not a button"
services: []
home: null          # inherited from EVAL-1 (verdi-bench)
inherited_decisions:
  - "EVAL-1-D001"   # instrument residence + name (RESOLVED: verdi-bench)
  - "EVAL-13-D003"  # the operator view stays openly unblinded (separate surface, carries)
touchpoints:        # PLANNED symbols [judgment]
  - "harness/author/server.py:make_author_server"
  - "harness/author/cli.py:register"
  - "harness/author/page.py:AUTHOR_PAGE"
  - "harness/plan/lock.py:lock_experiment"
  - "harness/schema/experiment.py:ExperimentSpec.from_yaml_text"

graph_provenance: []

acceptance:
  - id: "AC-1"
    text: "Validation and planning previews are pure reads: endpoints wrap ExperimentSpec.from_yaml_text (typed SpecError surfaced as named field errors), mde_check (the power_curve rendered as the with-N-reps-you-can-detect-X preview), derive_schedule (the deterministic interleave preview for the drafted seed), and load_task_dicts (duplicate/missing task ids refused loudly). No preview appends an event or mutates a locked experiment; identical draft bytes always preview the identical spec sha."
    vc: "Malformed drafts return the named SpecError per field; the power preview equals mde_check's output for the same inputs; the sha shown for a draft equals sha256 of its exact bytes; the ledger is byte-identical after any number of previews."
    touchpoints:
      - "harness/author/server.py:make_author_server"
    tests:
      - "test_ac1_previews_pure_reads"
  - id: "AC-2"
    text: "The lock is a ceremony producing exactly one event: a single mutating endpoint calls lock_experiment verbatim with the launch-bound actor and an explicit attested_by; the ceremony displays the draft's sha, MDE and flags before commitment, requires an explicit underpowered acknowledgment (riding inline on the lock event, PL-14) when the gate demands one, and every refusal (AlreadyLocked, Underpowered, missing rubric, task-commitment or chain errors) surfaces the typed error's own message. The sha displayed pre-lock equals the ledgered spec_sha256 — byte fidelity end to end (D002: the editable text pane is what locks; no serialization round-trip between preview and commit)."
    vc: "One lock POST appends exactly one experiment_locked event carrying actor/attestation/acknowledgment; the pre-lock sha equals the event's spec_sha256; each refusal class renders its typed message; a second lock attempt is refused with AlreadyLockedError's message."
    touchpoints:
      - "harness/plan/lock.py:lock_experiment"
    tests:
      - "test_ac2_lock_ceremony_one_event"
      - "test_ac2_page_ceremony_drive"
  - id: "AC-3"
    text: "Post-lock immutability is a UI fact, not just a backend refusal: a locked experiment renders read-only in the authoring surface (no edit or re-lock affordance; re-planning is creating a new draft directory), and draft writes are structurally confined to unlocked draft directories — an authoring write that would touch a locked experiment's pre-registered files is refused loudly."
    vc: "A locked experiment's authoring view carries no mutating affordances; a draft-write request naming a locked directory is refused with the reason; a new draft in a fresh directory proceeds."
    touchpoints:
      - "harness/author/server.py:make_author_server"
    tests:
      - "test_ac3_post_lock_readonly_refusals"
  - id: "AC-4"
    text: "Surface separation and posture: authoring is its own verb and server (bench author), never a mode of the operator observer — the EVAL-14 AC-8 GET-only posture of bench serve is untouched; the authoring server binds loopback by default, requires a resolvable actor at launch (refused loudly, never 'unknown'), serves self-contained pages (the needle property), and its mutating routes are the enumerated ceremony endpoints only — everything else is GET and side-effect-free."
    vc: "bench serve's posture tests still pass unmodified; bench author without a resolvable actor exits with the ActorResolutionError message; the authoring page passes the needle scan; non-enumerated POST routes are refused."
    touchpoints:
      - "harness/author/cli.py:register"
    tests:
      - "test_ac4_posture_actor_needles_routes"
  - id: "AC-5"
    text: "Task and rubric authoring feed the same commitment the CLI path locks: the tasks.yaml editor validates through load_task_dicts (dup/missing ids loud), the rubric file is created/edited beside the draft, and the ceremony's lock_experiment call commits task content and rubric hash exactly as bench plan does — a UI-authored lock and a CLI lock over the same bytes produce the same event payload (timestamps and actor aside)."
    vc: "Locking the same draft via the ceremony and via bench plan yields payload-identical experiment_locked events modulo provenance; invalid tasks.yaml is refused with load_task_dicts' message; a missing rubric refuses with the lock's own RubricCommitmentError."
    touchpoints:
      - "harness/author/page.py:AUTHOR_PAGE"
    tests:
      - "test_ac5_tasks_rubric_commitment_parity"

constraints:
  - text: "One mutating ledger operation in the whole story — the lock — and it flows through lock_experiment verbatim: no new event kinds, no reimplemented validation, the one-event-per-operation property extended to the ceremony endpoint. Draft file writes are pre-registration workspace, not evidence: they exist only in unlocked draft directories."
    enforced_by: "AC-2/AC-3 tests on graduation"
  - text: "Stage execution stays out (D003): the authoring surface neither runs, grades, judges, nor analyzes — long-lived execution belongs to the CLI and the observer already watches it. A run button is a different story with process-management obligations this spec deliberately refuses."
    enforced_by: "AC-4 route-enumeration tests on graduation"
  - text: "Byte fidelity is the contract: what the user saw hashed is what locked. The structured wizard only ever emits into the editable text pane; the pane's exact bytes are validated, previewed, and locked [D002]."
    enforced_by: "AC-1/AC-2 sha-equality tests on graduation"

decisions:
  - "EVAL-17-D001"  # surface (RESOLVED: new-author-verb)
  - "EVAL-17-D002"  # text fidelity (RESOLVED: wizard-emits-editable-text)
  - "EVAL-17-D003"  # stage execution (RESOLVED: excluded-v1)
  - "EVAL-17-D004"  # draft residence (RESOLVED: plain-directories)
open_decisions: []

policy_proposals: []
predicted_reach: null
expected_verify: "On graduation: preview purity (ledger byte-identity), the one-event ceremony with payload parity against bench plan, post-lock read-only enforcement, actor/needle/loopback posture, and typed-refusal rendering for every named error class."
---

# EVAL-17 — Authoring surface (proposed)

## Problem & context

The original directive was observe **and configure**. Observation shipped
(EVAL-13/14/15) as a structurally read-only surface. Configuration is a
different animal: it mutates — so it was deferred until the obligations
(actor identity, one-event-per-operation, byte-hashed pre-registration,
the no-amend lock) could shape the design instead of being patched in.
Every seam it needs already exists as a pure function: from_yaml_text,
mde_check's power_curve, derive_schedule, load_task_dicts, lock_experiment.

## Goal

An operator drafts an experiment in the browser — arms, corpus ref,
repetitions, metric, decision rule, judge, seed, ceiling, tasks, rubric —
sees the power curve and the derived interleave for those exact bytes, and
locks it in an explicit attested ceremony that appends the same single
genesis event `bench plan` would. After that, the surface tells the truth
the instrument enforces: locked means immutable; re-plan means new draft.

## Design

New `harness/author` subsystem (D001): a loopback server with GET previews
(validate / power / schedule / sha) and enumerated ceremony endpoints
(draft-write into unlocked dirs, lock). The wizard generates YAML into an
editable text pane once; the pane is canonical (D002) — previews and the
ceremony hash its exact bytes, so the spec_sha256 shown is the spec_sha256
ledgered. Actor binds at launch (`bench author --actor …` semantics via
resolve_actor); attested_by is an explicit ceremony field; the underpowered
path renders the MDE numbers and requires the acknowledgment that rides
inline on the lock event. Import contracts extend to harness.author
(ledger-writes-only-via-events source list; observability-llm-free
companion treatment).

## Out of scope

Running stages from the browser (D003); editing anything post-lock; corpus
mining/curation UI (EVAL-8's signed approval flow is its own future
surface); auth/multi-user; remote hosting.
