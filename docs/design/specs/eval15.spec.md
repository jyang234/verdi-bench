---
# MACHINE CONTRACT — see template header for consumers and YAML style rules.
# Graduated 2026-07-04 in the same commit as the story's AC tests. This story
# IS the EVAL-14-D004 capture slice: the decision (raised and resolved under
# EVAL-14, 2026-07-04 session review) recorded five binding guardrails "to
# land as ACs when it graduates" — they are AC-1..AC-5 below, verbatim in
# intent.
kind: "story"
ticket: "EVAL-15"   # synthetic key — source: EVAL-14-D004 capture slice
parent: "EVAL-1"
title: "Trajectory v3: additive per-step detail — captured honestly, scrubbed, and confined to the operator tier"
services: []
home: null          # inherited from EVAL-1 (verdi-bench)
inherited_decisions:
  - "EVAL-1-D001"   # instrument residence + name (RESOLVED: verdi-bench)
  - "EVAL-14-D004"  # per-step content (RESOLVED: trajectory-v3-additive-detail — this story)
touchpoints:        # PLANNED symbols [judgment]
  - "harness/run/trajectory.py:TrajectoryStep"
  - "harness/adapters/claude_code.py:normalize_trajectory"
  - "harness/adapters/codex.py:normalize_trajectory"
  - "harness/analyze/timeline.py:_trajectory_for"
  - "harness/status/trial.py:trial_detail"

graph_provenance: []

acceptance:
  - id: "AC-1"
    text: "TrajectoryStep gains an additive nullable detail:str with kind-dependent semantics (message text, a file_edit's patch material, a tool_call/test_run's output); schema_version bumps to 3; empty-string means measured-empty and null means the platform did not expose it (the command/v2 precedent). A v2 artifact parses and sha-resolves exactly as before with detail null throughout, and no reader requires the field — the hash chain is untouched (trial events carry only trajectory_sha; old records stay valid forever)."
    vc: "A hand-built v2 artifact round-trips through parse/resolve as verified with every detail null; a v3 record round-trips detail including the empty string as distinct from null; the trial-event shape is unchanged."
    touchpoints:
      - "harness/run/trajectory.py:TrajectoryStep"
    tests:
      - "test_ac1_v3_additive_and_v2_reads_null"
  - id: "AC-2"
    text: "Adapters fill detail read-never-reconstructed and null-honest, asymmetrically by what each platform exposes: claude-code fills message text blocks, file-edit patch material rendered verbatim from tool inputs (Edit/MultiEdit old/new pairs; Write/NotebookEdit content), and tool outputs paired by the log's own tool_use id; codex fills a message's text, a patch's diff, and an exec's output only when the log carries them as strings. Any shape outside the closed table stays null — never guessed, never imputed."
    vc: "Fixture logs for both adapters yield exactly the expected detail per step kind; malformed inputs (non-string text, broken edit input, unknown tool_result id) yield null; codex events without content fields yield null."
    touchpoints:
      - "harness/adapters/claude_code.py:normalize_trajectory"
      - "harness/adapters/codex.py:normalize_trajectory"
    tests:
      - "test_ac2_adapter_detail_asymmetry_null_honest"
  - id: "AC-3"
    text: "Capture stays inside the redaction perimeter: detail rides the same persist-time scrub as every other string field — a secret canary planted inside detail never reaches the persisted artifact bytes, and a scrub that breaks the record's structure fails the trial closed (the EVAL-12 AC-2 property, extended over the new field)."
    vc: "A property test plants canaries in detail across step kinds and asserts the persisted bytes never contain them while the artifact still parses; the persisted record's detail carries the scrub mask, not the secret."
    touchpoints:
      - "harness/run/trajectory.py:TrajectoryStep"
    tests:
      - "test_ac3_detail_redaction_property"
  - id: "AC-4"
    text: "Step content never reaches a blinded surface: the forensic advisory review's packet input is the transcript, not the trajectory — structurally, by the build_forensic_packet signature — so an identity canary present only in trajectory detail never appears in any message a provider receives during a full scan-with-review, while the same canary remains readable on the operator tier (the persisted artifact)."
    vc: "An end-to-end scan over an experiment whose trajectory detail carries an arm-identity canary, with a recording provider, shows the review ran (messages captured) and no captured message contains the canary; the persisted trajectory does."
    touchpoints:
      - "harness/run/trajectory.py:TrajectoryStep"
    tests:
      - "test_ac4_blinded_review_never_sees_detail"
  - id: "AC-5"
    text: "Renderers that leave the operator tier exclude detail by contract: trial_timeline step rows carry no detail key (and therefore the dossier — whose only step source is trial_timeline — embeds none of its content); the operator drill-down (status trial_detail and /api/trial) is the only surface that serves it, and renders absent detail as not-captured, never as empty content."
    vc: "Timeline rows over a detail-bearing fixture contain no detail key; the rendered dossier HTML contains none of the planted detail strings while still rendering the steps; trial_detail and the /api/trial route return them verbatim."
    touchpoints:
      - "harness/analyze/timeline.py:_trajectory_for"
      - "harness/status/trial.py:trial_detail"
    tests:
      - "test_ac5_renderers_exclude_detail_drilldown_serves_it"

constraints:
  - text: "detail is capture-only evidence for the operator tier and (future) deterministic detectors: it must never enter judge packets (unreachable by the packet signature), blinded review packets (transcript-only input), or the dossier/timeline renders — the leak surface of an archivable artifact does not grow with this field."
    enforced_by: "test:test_ac4_blinded_review_never_sees_detail"
  - text: "Read, never reconstructed: an adapter fills detail only from fields the native log actually carries; no diffing, no inference, no cross-event synthesis beyond the log's own tool_use/tool_result join. Unrecognized shapes are null [EVAL-4-D004]."
    enforced_by: "test:test_ac2_adapter_detail_asymmetry_null_honest"
  - text: "No truncation at capture: the artifact stores what the log carried; any shortening is render-side. A capped capture would be silent data loss."
    enforced_by: "test:test_ac3_detail_redaction_property"

decisions: []
open_decisions: []

policy_proposals: []
predicted_reach: null
expected_verify: "AC suite green: v2 round-trip, both adapters' asymmetry tables, the planted-canary redaction property, the end-to-end blinded-surface exclusion with a recording provider, and the renderer-exclusion/drill-down contrast."
---

# EVAL-15 — Trajectory v3: per-step detail (the EVAL-14-D004 capture slice)

## Problem & context

The trial drill-down (EVAL-14) renders step *shapes* — kinds, commands,
files, timing — but the content that explains a step (what the agent said,
what a patch changed, what a tool returned) is dropped at normalization even
though the redacted native logs carry it. The same content is the natural
substrate for sharper forensic detectors. EVAL-14-D004 resolved to capture
it as a versioned additive field with five binding guardrails; this story is
that slice.

## Goal

`detail` on every step where the platform honestly exposes it — scrubbed at
persist like everything else, confined to the operator tier, invisible to
every blinded and archivable surface, and absent-honest everywhere else.

## Design

Schema: `detail: Optional[str]` on TrajectoryStep, `schema_version` 3,
the `command`/v2 additive precedent end to end (`""` measured-empty vs null
unmeasured; v2 records read back null; no reader may require it; the ledger
event format is untouched). Adapters: claude-code pairs tool outputs by the
log's own `tool_use` id and renders edit-tool inputs verbatim
(`_edit_detail`); codex reads `text`/`diff`/`output` where present — content
asymmetry disclosed exactly like its cost asymmetry. Perimeters: persist-time
scrub covers the field automatically (it scrubs the serialized record);
`trial_timeline` excludes it (`model_dump(exclude={"detail"})`), which keeps
the dossier clean by construction; the blinded forensic review never sees it
because its packet input is the transcript. The operator page renders detail
under the step line and keeps the "not captured in this record version"
placeholder for null.

## Out of scope

Detector upgrades that consume detail (a later forensics story, with its own
planted-violation fixtures); transcript capture for Harbor; any UI beyond
lighting up the existing drill-down.
