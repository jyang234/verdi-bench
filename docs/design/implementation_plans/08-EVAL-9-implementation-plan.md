# 08 — EVAL-9 Implementation Plan: Transcript process rubric — openly-unblinded diagnostic scoring with firewall mechanisms

**Read with:** `00-EVAL-1-master-plan.md`, `eval9.spec.md`, `eval9.decisions.ndjson`. **Requires:** EVAL-2 (judge client, provider layer, vendor-overlap machinery), EVAL-7 (reveal event, reviewed sample, kappa/IPW machinery), EVAL-4 (upstream redaction), EVAL-6 (exploratory rendering, correlation reporting), EVAL-3 (closed metric vocabulary). Origin: EVAL-2-D007 resolved `include-in-v1-as-EVAL-9` — a dedicated child story precisely to preserve EVAL-2's outcome-blind construct.

## 1. Gate status — the most provisional plan in the set

**All four local decisions are OPEN** (raised 2026-07-02T17:51:31Z, unresolved). The gate is blocked until they resolve. The plan below assumes every recommendation and isolates each behind an explicit parameter so any flip is contained:

| Decision | Recommendation (assumed) | Where parameterized |
|---|---|---|
| D001 scoring shape | **per-trial absolute** on anchored ordinal scales (comparative A/B would double identity exposure and import preference framing into a diagnostic) | Scoring core is per-trial; a comparative mode would be a new packet+schema, so flag loudly if D001 flips |
| D002 scorers | **judge on all trials + human calibrated** on the EVAL-7 sample (human-only forfeits scale) | Scorer set is config; human-only = disable judge path |
| D003 v1 dimensions | **the proposed five**: planning quality, exploration efficiency, error recovery, instruction adherence, destructive-action caution | Dimensions live in the versioned rubric file, not code |
| D004 transcript policy | **full-or-CANT_SCORE** — no silent truncation | Policy enum in `score_trial_process`; `recorded-truncation` would add a policy branch |

Inherited EVAL-1-D001 RESOLVED. **Soft dependency:** AC-5 requires the estimator to match the **EVAL-7-D003** resolution (IPW + floor sensitivity) — itself still open; resolving that one item unblocks both stories' estimator choice.

## 2. Objective

Outcome metrics say *whether*; adoption decisions hinge on *how*. Transcripts answer that but identify their stack within lines — this layer **cannot** be blinded, and pretending otherwise would poison the outcome-blind construct next door. So: a separate, openly-unblinded diagnostic tier whose bias is contained by mechanism, not denied — disclosed scorer identity, verdict-before-process sequencing, hard ineligibility as official primaries, and judge process scores that earn weight through per-dimension calibration against the human's.

## 3. Module layout & public symbols

```
harness/process/rubric.py    ProcessRubric
harness/process/score.py     score_trial_process
harness/process/packet.py    build_process_packet
harness/cli.py               cmd_process_record
```

Owns `harness/process/`; reuses EVAL-2's provider client and EVAL-7's reveal/kappa machinery — import, don't fork.

## 4. Data contracts

**4.1 `ProcessRubric`** [AC-1]: versioned YAML — `{rubric_version, dimensions: [{id, name, scale: 1..5, anchors: {1: "...", ..., 5: "..."}, telemetry_correlates: [tokens|tool_calls|wall_time|retries|timeouts]}]}` with the five assumed dimensions [D003]. Changing any anchor bumps the version; the version is stamped into **every** process-score event; fixtures score against a pinned rubric.

**4.2 `process_score` event** [AC-2]: `{trial_id, rubric_version, scores: {dim_id: 1..5 | CANT_SCORE(reason)}, provenance: {unblinded: true, scorer: {kind: judge|human, id}, judge_vendor_overlap: bool, ts, ...}}`. A process score **without** unblinded provenance is unrepresentable (schema-required fields). CANT_SCORE carries token counts when the reason is context overflow [AC-4].

**4.3 Metric firewall** [AC-6]: process dimension ids are *not* members of the closed `PrimaryMetric` enum (EVAL-3-D006; the enum lives in `harness/schema/metrics.py`) — registering one as `primary_metric` fails schema by construction. Renders: EXPLORATORY-labeled secondaries **plus** the unblinded disclosure block; findings fixtures including process scores fail validation when the disclosure is absent [AC-2].

## 5. Implementation sequence

**M1 — Rubric.** `ProcessRubric` loader + version stamping + anchored ordinal schema. Tests: `test_ac1_rubric_versioned`, `test_ac1_ordinal_schema`.

**M2 — Judge process scoring (isolated).** `build_process_packet(trial)` — allowlist inputs: **post-redaction full transcript** + rubric + (for juxtaposition, human packets only, see M5) telemetry. The judge process call is a **separate model call sharing no context with outcome verdicts** — by construction: the packet builder's signature cannot accept verdict content, property-tested the same way EVAL-2's packet builder is [AC-3]. `score_trial_process` (judge path): full transcript fits judge context or **fail closed to CANT_SCORE(reason)** with token counts recorded — no silent truncation [D004, AC-4]; token counting via the provider's tokenizer/count endpoint `[plan choice]` with a conservative margin. Redaction is **upstream** (EVAL-4 artifact capture): redaction canaries must never reach the judge payload — reuse the shared canary corpus. Tests: `test_ac3_judge_call_isolated` (packet-builder property test: no outcome-verdict content representable), `test_ac4_full_or_cant_score`, `test_ac4_redaction_upstream`.

**M3 — Sequencing firewall (human path).** `cmd_process_record` (`bench process record`): human process scoring is reachable **only after the EVAL-7 reveal event** for that comparison exists — the CLI refuses before a referenced reveal event is ledgered [AC-3]. Direction of the firewall: trajectory impressions must not contaminate outcome verdicts, so process comes strictly after verdict+reveal. Test: `test_ac3_human_post_reveal_only`.

**M4 — Calibration.** Human process scores captured on the EVAL-7 reviewed sample [D002]; per-dimension judge-human agreement via **quadratic-weighted kappa** (ordinal scales), with the same IPW correction and gate mechanics as outcome kappa — estimator must match the EVAL-7-D003 resolution (import EVAL-7's `KappaEstimator` seam; do not reimplement) [AC-5]. Dimension gates escalate independently (a dimension below threshold is flagged without dragging the others). Tests: `test_ac5_weighted_kappa` (hand-checked fixture values), `test_ac5_per_dimension_gates`.

**M5 — Telemetry juxtaposition.** The process packet (human-facing form) juxtaposes deterministic telemetry correlates — tokens, tool calls, wall time, retries, timeouts — beside each scored dimension, anchoring human scoring in data [AC-7]. `analyze` gains a score-vs-telemetry correlation table (Spearman per dimension against its declared correlates `[plan choice]`); a dimension **uncorrelated with its own stated correlates is measuring style, not process** — flag it in process-score reports. Tests: `test_ac7_telemetry_juxtaposed`, `test_ac7_correlation_reported`.

**M6 — Firewalled rendering.** Wire into EVAL-6's renderer: process dimensions render only as EXPLORATORY secondaries carrying the disclosure block; the primary-ineligibility schema test; official-path exclusion. Tests: `test_ac2_unblinded_provenance`, `test_ac2_disclosure_required`, `test_ac6_primary_ineligible`, `test_ac6_exploratory_rendering`.

## 6. Test plan summary

| AC | Tests |
|---|---|
| AC-1 | rubric_versioned, ordinal_schema |
| AC-2 | unblinded_provenance, disclosure_required |
| AC-3 | human_post_reveal_only, judge_call_isolated |
| AC-4 | full_or_cant_score (token counts recorded), redaction_upstream (canaries) |
| AC-5 | weighted_kappa (hand-checked), per_dimension_gates |
| AC-6 | primary_ineligible (schema), exploratory_rendering (label + disclosure) |
| AC-7 | telemetry_juxtaposed, correlation_reported (uncorrelated flagged) |

## 7. Constraints checklist at merge

- Process scores never primary metrics, never rendered without the unblinded disclosure ✓ (M6; permanent by constraint)
- Human process scoring cannot precede verdict + reveal for that comparison ✓ (M3)
- Transcripts pass EVAL-4 redaction before **any** scorer — model or human — sees them ✓ (M2 canary test)
- Judge process scoring and judge outcome verdicts never share a model call or context ✓ (M2 property test)

## 8. Definition of done

Process scores flow for a fixture experiment: judge-scored trials, post-reveal human capture, per-dimension weighted kappa, disclosure-bearing exploratory renders, correlation table flagging style-only dimensions. **Formal closure requires D001–D004 RESOLVED** (and, transitively, EVAL-7-D003 for the estimator); until then this story must not be marked built even if the code exists.

## 9. Risks / watch items

- The whole story's legitimacy rests on the firewalls being *mechanical* — if any of the three (sequencing, metric, disclosure) is enforced by convention rather than schema/CLI refusal, stop and fix it before proceeding.
- Long transcripts will hit judge context often at first; expect a meaningful CANT_SCORE rate and report it rather than reaching for truncation — that pressure is what D004 exists to resist.
- Out of scope, resist creep: process scores as primaries (permanently), transcript summarization, cross-experiment process trend analysis (v2, enabled by the ledger).
