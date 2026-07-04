---
# MACHINE CONTRACT — PROPOSED (not yet graduated; AC enforcement begins when
# this file moves to docs/design/specs/ in the same commit as its first AC
# tests). Drafted 2026-07-04: the standing-archive follow-on to EVAL-19's
# bundle — regeneration as a harness act instead of an operator's memory.
kind: "story"
ticket: "EVAL-23"   # synthetic key — source: 2026-07-04 anchoring directive (session)
parent: "EVAL-1"
title: "Standing bundle archive: the operator view regenerated on completion, with provenance that makes drift visible"
services: []
home: null          # inherited from EVAL-1 (verdi-bench)
inherited_decisions:
  - "EVAL-1-D001"   # instrument residence + name (RESOLVED: verdi-bench)
  - "EVAL-13-D003"  # unblinded-operator disclosure carries to every render, archives included
  - "EVAL-19-D001"  # the bundle lives on the serve verb; this story schedules it
touchpoints:        # PLANNED symbols [judgment]
  - "harness/serve/bundle.py:write_bundle"
  - "harness/serve/bundle.py:collect_bundle_data"
  - "harness/run/cli.py:register"
  - "harness/serve/cli.py:register"

graph_provenance: []

acceptance:
  - id: "AC-1"
    text: "Archive as a harness act: a completion hook (per D001 — recommended: an opt-in --archive flag on bench run plus the existing explicit --bundle path for ad-hoc use) regenerates the bundle when a run reaches a terminal state, writing to the resolved archive path. The archive write appends no event, adds no server route, and a failing archive write is disclosed loudly without altering the run's own outcome or events."
    vc: "A completed fixture run with the flag produces the archive file and zero extra ledger events; a scripted write failure surfaces the OS error on the CLI while the run's recorded outcome and ledger are byte-identical to a no-flag run."
    touchpoints:
      - "harness/run/cli.py:register"
    tests: []
  - id: "AC-2"
    text: "Provenance travels with the archive (D002): the bundle embeds a provenance block {ledger_sha256, ledger_height, bundle_format} rendered on the page — pure file state, so byte-determinism is preserved. Regeneration over an unchanged (ledger, artifacts) input is byte-idempotent; a stored archive that mismatches a fresh render is explainable from provenance alone: ledger grew (heights differ) or the render version changed (formats differ) — never a silent mystery."
    vc: "Two regenerations byte-match; appending one event then regenerating changes the archive with the height delta visible in the embedded provenance; the drift check names which provenance field moved."
    touchpoints:
      - "harness/serve/bundle.py:write_bundle"
    tests: []
  - id: "AC-3"
    text: "Retention per D003 (recommended: content-addressed archive names bundle.<height>.<head8>.html so successive archives coexist and re-archiving an unchanged ledger is a no-op on disk), with a stable latest pointer for humans; nothing is deleted by the harness — retention pruning is the operator's act, not the instrument's."
    vc: "Successive archives over a growing ledger coexist under distinct content-addressed names; re-archiving unchanged state writes no new file; the latest pointer resolves to the newest archive."
    touchpoints:
      - "harness/serve/bundle.py:write_bundle"
    tests: []
  - id: "AC-4"
    text: "Composition with the external witness (EVAL-22, when present): the archive's sha256 is recorded beside the anchor receipts for the same head, so a witnessed head has a matching frozen human-readable view; absent EVAL-22, the sha lives in the archive's provenance sidecar only. Never a ledger event in either case."
    vc: "With a fake anchor target configured, archiving records the bundle sha next to the head's receipt; without one, the sidecar carries it; REGISTERED_EVENTS is unchanged."
    touchpoints:
      - "harness/serve/bundle.py:collect_bundle_data"
    tests: []
  - id: "AC-5"
    text: "Posture under growth: the archive carries the unblinded-operator banner and the static-bundle disclosure unchanged (EVAL-13-D003 lineage); the needle property holds over every archive; bench serve stays GET-only with the EVAL-14 AC-8 suite passing unmodified; headless/cron regeneration works without a display or a server."
    vc: "Archive needle scan passes and both disclosures render; the operator posture suites pass untouched; a cron-style headless invocation produces the same bytes as the CLI."
    touchpoints:
      - "harness/serve/cli.py:register"
    tests: []

constraints:
  - text: "The archive is evidence presentation, never evidence: it appends no events, participates in no chain, and its loss or deletion invalidates nothing — the ledger and artifacts remain the record; the archive is the record made openable."
    enforced_by: "AC-1/AC-4 no-event tests on graduation"
  - text: "Byte-determinism is the drift alarm and must survive the feature: any addition to the bundle (provenance block included) stays a pure function of (ledger bytes, artifacts, page version) — no wall clock, no environment leakage into the bytes."
    enforced_by: "AC-2 idempotence tests on graduation"
  - text: "Archives are operator-tier: they carry arm identities by design and say so; nothing here creates a reviewer-safe archive (that would be a blinding story, designed like EVAL-18, not bolted on here)."
    enforced_by: "AC-5 disclosure tests on graduation"

decisions: []
open_decisions:
  - "EVAL-23-D001"  # trigger: opt-in --archive on bench run + explicit verb (recommended) vs always-on vs post-analyze hook
  - "EVAL-23-D002"  # provenance residence: embedded block rendered on the page (recommended) vs sidecar-only
  - "EVAL-23-D003"  # retention: content-addressed names + latest pointer (recommended) vs single overwrite

policy_proposals: []
predicted_reach: null
expected_verify: "On graduation: hook produces archive with zero events and disclosed write failures, byte-idempotence with provenance-explained drift, content-addressed retention with a latest pointer, witness composition with and without EVAL-22, and the unchanged operator posture suites."
---

# EVAL-23 — Standing bundle archive (proposed)

## Problem & context

EVAL-19's bundle made the operator view archivable; whether an archive
exists still depends on someone running the flag. The live observer is
ephemeral by design — loopback, one process, reclaimed containers — so
the moment the question "why did we ship arm B?" arrives months later,
the investigation surface exists only if discipline held. The
instrument's own posture (guarantees by construction, not by memory)
argues the archive should be a harness act.

## Goal

Every completed experiment leaves a self-contained, deterministic,
provenance-stamped archive of its operator view beside its ledger —
regenerated idempotently, drift always explainable from the embedded
provenance, composing with the external witness when one is configured
(EVAL-22) so a witnessed head has a frozen human-readable counterpart.

## Design

`write_bundle` already guarantees byte-determinism over (ledger,
artifacts); the story adds a provenance block (D002) so a mismatch
between a stored archive and a fresh render is diagnosable without
guesswork, a completion hook on `bench run` (D001) so regeneration
happens where completion is observed, and content-addressed retention
(D003) so archives of successive heads coexist and unchanged state
writes nothing. The EVAL-22 composition is deliberately thin: the
archive sha rides beside the head's receipt — the witness pins the
ledger, the sha pins the view of it.

## Out of scope

Reviewer-safe or blinded archives; archiving workspaces or raw
artifacts (the bundle embeds their rendered payloads, not their
bytes); retention pruning policy; remote upload of archives (the
operator's storage is their own — only the sha composes with EVAL-22's
deposit).
