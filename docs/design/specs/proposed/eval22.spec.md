---
# MACHINE CONTRACT — PROPOSED (not yet graduated; AC enforcement begins when
# this file moves to docs/design/specs/ in the same commit as its first AC
# tests). Drafted 2026-07-04: the follow-up the anchors subsystem was built
# expecting — "external" today means a sibling file on the same disk, which
# an adversary who can rewrite the ledger controls too.
kind: "story"
ticket: "EVAL-22"   # synthetic key — source: 2026-07-04 anchoring directive (session)
parent: "EVAL-1"
title: "External anchoring at the lock ceremony: pre-registration witnessed outside the writer's control"
services: []
home: null          # inherited from EVAL-1 (verdi-bench)
inherited_decisions:
  - "EVAL-1-D001"   # instrument residence + name (RESOLVED: verdi-bench)
  - "EVAL-3-D008"   # anchor-plus-attestation-v1: the subsystem this story externalizes
  - "EVAL-17-D001"  # the lock ceremony is the authoring surface's one mutating act
touchpoints:        # PLANNED symbols [judgment]
  - "harness/ledger/anchors.py:anchor_head"
  - "harness/ledger/anchors.py:verify_against_anchor"
  - "harness/plan/lock.py:lock_experiment"
  - "harness/author/server.py:make_author_server"
  - "harness/analyze/fence.py:official_fence_report"

graph_provenance: []

acceptance:
  - id: "AC-1"
    text: "A designated deposit seam: anchor records can be deposited to an external target beyond the v1 sibling file — the target is configured operational state (run-config, never the sha-locked spec), the deposit hands the canonical anchor record to the target and returns a receipt {target, ref, deposited_ts}, and the seam is the ONLY place anchoring may touch a network or subprocess (the judge-provider precedent: everything else in the subsystem stays deterministic and injectable). A deposit still refuses a ledger whose own chain does not verify (AnchorIntegrityError carries)."
    vc: "A fake target captures the exact canonical record and returns a receipt; the deterministic core (anchor_head, verify_against_anchor) imports no network client and takes injected timestamps; depositing over a tampered ledger refuses before any target is invoked."
    touchpoints:
      - "harness/ledger/anchors.py:anchor_head"
    tests: []
  - id: "AC-2"
    text: "The lock is witnessed: both lock paths — bench plan and the authoring ceremony endpoint — deposit an anchor for the post-lock head as part of the ceremony, so the experiment_locked event (spec sha, seed, MDE acknowledgment) is covered by an external witness from the moment it exists. The deposit is OUTSIDE the hash chain: no new event kinds; receipts land per D002. A repo with no anchor target configured keeps today's behavior exactly, and says so rather than implying coverage."
    vc: "Locking with a configured fake target produces one deposit whose anchored height covers the experiment_locked line, via both lock paths; with no target configured, the lock succeeds and the ceremony/status output states 'no external anchor target configured'; REGISTERED_EVENTS is unchanged (unless D002 resolves otherwise, which is a ContractChange gate)."
    touchpoints:
      - "harness/plan/lock.py:lock_experiment"
      - "harness/author/server.py:make_author_server"
    tests: []
  - id: "AC-3"
    text: "Failure posture per D003: an external deposit failure at lock time follows the resolved posture (recommended: the lock itself proceeds — pre-registration is local truth and a flaky network must not block it — but the missing witness is disclosed loudly at the ceremony, on bench status, and as an official-fence item state, never silently absorbed). Whatever D003 resolves, the failure carries the target's own error text — no bare except, no sentinel."
    vc: "A target scripted to fail yields the resolved posture with the target's error named verbatim in the refusal/disclosure; the disclosure is visible on the ceremony output and the status surface."
    touchpoints:
      - "harness/plan/lock.py:lock_experiment"
    tests: []
  - id: "AC-4"
    text: "Verification closes the loop: verify_against_anchor consumes a fetched/retrieved external anchor store exactly as it consumes the sibling file (same record shape), and the property the witness buys is tested end-to-end — a wholesale-rewritten ledger with a re-dated lock, internally chain-consistent, PASSES verify-chain alone and FAILS against the witness's records."
    vc: "A fabricated back-dated ledger (self-consistent chain) verifies clean standalone and is refused against the anchor store with the height/hash mismatch named; an honestly-extended ledger verifies against the same store."
    touchpoints:
      - "harness/ledger/anchors.py:verify_against_anchor"
    tests: []
  - id: "AC-5"
    text: "Fence integration per D004: the official fence gains an 'externally anchored' item that is ok only when receipts cover the lock and the current head, unchecked (never failed) when no target is configured — the manifest-requiring-item precedent: absence of the optional apparatus is a disclosed measurement condition, not a violation."
    vc: "A receipts-covered experiment shows the item ok; no-target shows unchecked with the reason; configured-but-failed deposits show failed with the target error; official_ready reflects the resolved gating choice."
    touchpoints:
      - "harness/analyze/fence.py:official_fence_report"
    tests: []

constraints:
  - text: "Determinism is untouched outside the seam: anchor_head keeps injected timestamps and pure file reads; only the deposit target may perform I/O beyond the anchor store, and the target is operational config (run-config precedent, RN-13) — never spec bytes, never ledger content."
    enforced_by: "AC-1 tests on graduation"
  - text: "Anchoring upgrades tamper-EVIDENCE, and the spec says so: a witness outside the writer's control makes wholesale rewrite detectable; it does not make anything tamper-proof, and an adversary controlling both the disk and the anchor target is out of scope — documented, not papered over (the aggregator-host residual-limit precedent)."
    enforced_by: "spec text + AC-4 property test on graduation"
  - text: "No new blind or scrub surface: anchor records and receipts carry hashes, heights, timestamps, and target refs only — never spec content, arm identities, or task material."
    enforced_by: "AC-1 record-shape test on graduation"

decisions: []
open_decisions:
  - "EVAL-22-D001"  # v1 external target: configured-command seam (recommended) vs built-in git-notes client vs HTTPS timestamping client
  - "EVAL-22-D002"  # receipt residence: sidecar receipts file (recommended) vs ledgered anchor_deposited event (ContractChange)
  - "EVAL-22-D003"  # deposit failure at lock: lock-then-disclose-loudly (recommended) vs refuse the lock
  - "EVAL-22-D004"  # fence: 'externally anchored' item, unchecked without a target (recommended) vs stay out of the fence

policy_proposals: []
predicted_reach: null
expected_verify: "On graduation: fake-target deposit capture with refusal-on-tamper, both-lock-paths witnessing, the resolved failure posture with verbatim target errors, the back-dated-lock detection property, and the fence item's three states."
---

# EVAL-22 — External anchoring at the lock ceremony (proposed)

## Problem & context

The chain makes partial edits detectable; it cannot detect a wholesale
rewrite, because a writer who controls the file can recompute every
hash (the head-line opacity boundary, found live during EVAL-14
testing). `bench anchor` exists for this, but its v1 "external" store
is a sibling file on the same disk — controlled by exactly the party
the witness is supposed to check. Meanwhile the instrument's central
credibility claim is pre-registration: that the design was frozen
before data arrived. Today that claim is one the ledger makes about
itself.

## Goal

The lock ceremony deposits the post-lock head to a witness outside the
writer's control, receipts are retrievable, and verification against
the witness turns "pre-registered" from self-attestation into a claim
a third party can check: a fabricated ledger with a back-dated lock
fails against the witness even though its own chain verifies.

## Design

The existing `anchor_head` already refuses tampered history and takes
its destination as a parameter — the story externalizes the
destination behind a deposit seam (D001; a configured command is the
recommended v1: one generic exec target covers git push, curl to a
timestamping service, or an operator's own script, without the
instrument adopting a vendor). Both lock paths call the same deposit;
receipts land per D002 (sidecar recommended — the ledger cannot
usefully witness its own witness, and a ledgered receipt would be a
chain contract change). Failure posture per D003; fence disclosure per
D004. Anchor cadence beyond the lock (per-run, per-analyze heads) is a
cheap follow-on once the seam exists.

## Out of scope

Multi-party/notary schemes and signed attestations; anchoring the
review or authoring drafts (unlocked workspaces are not evidence);
retrofitting witnesses onto pre-existing experiments (their locks were
never witnessed — honesty over reconstruction); any change to chain
serialization.
