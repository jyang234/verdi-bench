# EVAL-16 implementation plan — step-content forensics

Spec: `docs/design/specs/eval16.spec.md`; decisions D001–D003 resolved in
`eval16.decisions.ndjson` (mechanical matching, additive coverage keys,
exact literals).

## M1 — vocabulary v2 (AC-1)

`FORENSICS_VOCABULARY_VERSION` 1 → 2; three ids join the closed
`DETECTOR_IDS` (`transient_holdout_tamper`, `transient_hardcoded_output`,
`transient_test_skip`), grouped as `DETAIL_DETECTOR_IDS`. The EVAL-11
closed-enum and version pins advanced with citations — the human-gated
bump those pins exist to force.

## M2 — detectors (AC-2..AC-4, AC-6)

Pure functions over the existing `TrialEvidence` (one additive field:
`holdout_relpaths`, only multi-segment relpaths are content-matchable —
the scan filters). `detail_evaluable` defines honest coverage: a verified
trajectory with ≥1 non-null detail; anything else is not_evaluable, never
a clean pass. Matching is the existing closed machinery (`_contains`
token-honest literals, `_under` prefix paths, `_SKIP_MARKERS`); the
detectors scan step `detail` + `command`, so an edit-then-revert with a
clean end state still flags with the step index in evidence. Verified-only
holds structurally: evidence carries the resolver's record, which is None
for anything but `verified`.

## M3 — coverage + disclosure (AC-5)

`run_forensics` rolls up per-arm detail coverage
(`trials / detail_evaluable / steps_total / steps_with_detail`) plus
per-trial `detail_gaps` as additive keys inside the report's coverage
block (one scan, one event, old ledgers never refused);
`_forensics_lines` renders per-arm coverage lines and an explicit
ASYMMETRIC sentence when the arms were not equally inspectable — a
disclosed measurement condition, the telemetry-asymmetry precedent.

## M4 — tests

Planted/clean pairs per detector (including the end-state-blind
edit-then-revert case, with regression asserts that the v1 tier keeps its
own coverage), the mixed-arm scan proving coverage counts, gap reasons,
an end-to-end transient catch, and the rendered asymmetry disclosure;
sha-mismatch → gap-not-evidence, double-scan determinism, and a fence
vocabulary check that no forensic item gates anything [EVAL-11 D004].
