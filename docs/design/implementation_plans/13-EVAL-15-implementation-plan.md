# EVAL-15 implementation plan — trajectory v3 per-step detail

The EVAL-14-D004 capture slice. Spec: `docs/design/specs/eval15.spec.md`;
the five ACs are the decision's recorded guardrails.

## M1 — contract (AC-1)

`harness/run/trajectory.py`: additive nullable `detail: str` on
TrajectoryStep (`""` measured-empty vs null unmeasured, the `command`/v2
precedent), `TRAJECTORY_SCHEMA_VERSION` 2 → 3. v2 artifacts parse and
sha-resolve unchanged (versions are record-carried defaults, not pins); the
trial-event format is untouched. The EVAL-12 deliberate version tripwire
(`test_canonical_bytes_deterministic`'s literal pin) advanced 2 → 3 citing
the D004 approval.

## M2 — adapters (AC-2)

- claude-code: message text blocks verbatim; file-edit patch material via
  `_edit_detail` (Edit/MultiEdit old/new pairs labeled and joined;
  Write/NotebookEdit content); tool outputs paired by the log's own
  `tool_use` id → `tool_result` join (`_result_text`: bare string or joined
  text blocks). Every unrecognized shape → null.
- codex: `text` / `diff` / `output` per event type, strings only, else null
  — the same disclosed asymmetry as its null cost.

## M3 — perimeters (AC-3, AC-4, AC-5)

- Redaction: nothing to add — `persist_trajectory` scrubs the serialized
  record, so detail inherits the perimeter; the property test plants
  canaries in detail across kinds and asserts the artifact never carries
  them while still parsing.
- Blinded review: nothing to add — `build_forensic_packet`'s input is the
  transcript by signature; the end-to-end test runs a full scan-with-review
  over an identity-canary-in-detail experiment with a recording provider
  and asserts no provider message carries it (while the operator-tier
  artifact does).
- Renderers: `trial_timeline` excludes detail
  (`model_dump(exclude={"detail"})`), which keeps the dossier clean by
  construction; `status.trial` / `/api/trial` remain the only serving
  surface, and the operator page renders detail under the step line with
  the not-captured placeholder for null.

## Out of scope

Detector upgrades consuming detail (later forensics story), Harbor
transcript capture, render-side truncation polish.
