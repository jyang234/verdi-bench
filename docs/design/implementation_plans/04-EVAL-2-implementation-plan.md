# 04 — EVAL-2 Implementation Plan: Configurable, outcome-blind LLM judge layer

**Read with:** `00-EVAL-1-master-plan.md`, `Eval2.spec.md`, `Eval2.decisions.ndjson`. **Requires:** EVAL-3 (ledger, plan-time validation hooks), EVAL-4 (artifacts: diffs, prompts), EVAL-5 (holdout results). Stage 4 (`grade-judge`) of the six-stage pipeline; consumes per-trial artifacts, emits verdict events into the grades ledger.

## 1. Gate status

- **RESOLVED:** D001 judge configurable, **no vendor restriction in code**, mandatory disclosure instead; D002 judge sees **outcomes only** — task prompt + workspace diff + holdout results + rubric ("it judges the results; it never sees the contestants"); D003 both presentation orders, inconsistency downgrades to TIE; D004 human verdict final, judge advisory; D005 default judge = current pinned Gemini (exact version string pinned at generation time; cross-vendor to both arms — a config default, nothing in code depends on it); D007 process rubric = in v1 as EVAL-9 (out of scope here); D008 residence reparented to EVAL-1-D001 (RESOLVED: verdi-bench).
- **OPEN (gate formally blocked):** **D006** — kappa escalation threshold + minimum human-verdict sample per task class. Working assumption = recommendation: **0.6 / 20**, implemented strictly as config defaults in the `escalation` block so the resolution is a yaml default change, nothing more.

## 2. Objective

Any model can be configured as judge; the judge physically cannot learn which arm produced what it grades; every verdict is evidence-cited and provenance-stamped; position bias is mechanically cancelled; failures fail closed as first-class outcomes; and the judge's authority is *earned* per task class through measured kappa against the human — who alone closes comparisons.

## 3. Module layout & public symbols

```
harness/judge/packet.py       build_packet, validate_identity_free
harness/judge/client.py       judge_pair
harness/judge/schema.py       Verdict
harness/judge/calibrate.py    kappa_by_class
harness/analyze/confounds.py  judge_vendor_overlap
harness/ledger/events.py      append_verdict   (constructor extension)
```

Internal `[plan choice]`: `harness/judge/providers/` — thin per-provider clients (anthropic, openai, google) behind one `complete(model_id, messages, temperature) -> text` interface, versions pinned; deliberately not a heavyweight router dependency, keeping pins explicit and refusal/timeout semantics under our control. `validate_identity_free` delegates to `harness/blind/core.py` (master plan §7.4) so EVAL-7 shares the single blinding codepath.

## 4. Data contracts

**4.1 Judge config block** (validated by EVAL-3 at plan time):

```yaml
judge:
  model: <provider>/<fully-versioned-id>   # ANY provider [D001]; alias ids fail plan [AC-5]
  rubric: rubrics/code-task-v1.md
  orders: both        # "single" allowed only for smoke runs; flagged
  temperature: 0
  panel: null         # v2; schema stubbed only
  escalation: {kappa_threshold: 0.6, min_human_verdicts: 20}   # defaults pending D006
```

Alias detection `[plan choice]`: per-provider regexes requiring an explicit version/date segment (e.g. reject `gemini-pro`, `claude-sonnet`, `gpt-5`; accept date- or version-suffixed ids). Conservative: no version segment ⇒ reject at plan.

**4.2 Packet** [AC-2, D002]. `build_packet(trial_a, trial_b, task, rubric)` — the function signature **is** the allowlist: task prompt, final workspace diff per response, holdout results per response, rubric. Transcripts, telemetry, arm labels, agent/model names, and job paths are not parameters, so they are unreachable by construction. Response labels "Response 1/2" assigned randomly per call. `validate_identity_free(packet)` scans against the shared canary corpus (arm ids, agent names, model-id patterns, transcript markers); failing packets are **never sent**. Residual risk (code-style tells inside diffs) is disclosed, not hidden — that's what calibration is for.

**4.3 Verdict schema** [AC-4, AC-5] — exactly the spec's JSON: `winner ∈ {A, B, TIE, CANT_JUDGE}`, `reason`, `evidence: [{kind: diff|holdout, response, hunk|ref}]` (structurally required — evidence-free ⇒ schema-rejected and **re-recorded as `CANT_JUDGE(malformed)` event**, not an exception trace), `confidence`, `order_inconsistent`, `provenance: {judge_model, rubric_sha256, packet_sha256, call_ids[2], orders, temperature, ts}` — any missing provenance field fails schema. Human verdicts (EVAL-7) share this schema family so kappa is directly computable [constraint].

**4.4 Ledger.** `append_verdict` constructor; judge verdicts are **advisory** — ledger state machine keeps a comparison open until a `human_verdict` event exists [D004, AC-7].

## 5. Implementation sequence

**M1 — Schema + events.** `Verdict` model; `append_verdict`; malformed-verdict ⇒ CANT_JUDGE(malformed) event path. Tests: `test_ac4_evidence_required`, `test_ac4_malformed_becomes_cant_judge`, `test_ac5_verdict_provenance_complete`.

**M2 — Packet builder + blinding.** Allowlist constructor; random labels; canary validation via `harness/blind/core.py`; hypothesis property test seeding identity strings into every **non**-allowlisted artifact field and asserting they never appear in packets; failing packet never sent. Tests: `test_ac2_packet_identity_free`, `test_ac2_packet_allowlist_only`.

**M3 — Client + order debiasing.** `judge_pair`: two calls per comparison, orders swapped, temperature 0, strict structured-output parse. Agreement ⇒ one verdict carrying both call ids; disagreement ⇒ `TIE` + `order_inconsistent=true` [D003, AC-3]. Order-inconsistency **rate** computed and reported as a judge-quality metric. No vendor allow/deny list anywhere — an arm-vendor judge config validates, runs, and produces verdicts [AC-1]. Fail-closed: timeout, refusal, provider error, unparseable output each ⇒ exactly one `CANT_JUDGE(reason)`; an attempted comparison without a verdict event is unrepresentable [AC-8]. Tests: `test_ac1_judge_vendor_unrestricted`, `test_ac3_order_debias_inconsistent_ties`, `test_ac3_both_orders_invoked`, `test_ac8_fail_closed_timeout`, `test_ac8_fail_closed_refusal` (mocked provider faults).

**M4 — Plan-time validation.** Wire alias rejection and judge-block validation into EVAL-3's `ExperimentSpec` (the shape stub left there). Test: `test_ac5_alias_model_id_rejected`.

**M5 — Vendor-overlap disclosure.** `judge_vendor_overlap` in `harness/analyze/confounds.py`: derive vendor from model-id prefix for judge and every arm; overlap ⇒ confound flag registered on the experiment, rendered later in the report header (EVAL-6) and review-packet header (EVAL-7) — register the flag now; renderers consume it when they land. Same-vendor is *legal and always disclosed* [D001 constraint]. Tests: `test_ac6_vendor_overlap_flagged`, `test_ac6_cross_vendor_clean`.

**M6 — Calibration.** `kappa_by_class`: per task class, once `min_human_verdicts` reached, Cohen's kappa (hand-rolled + fixture-verified `[plan choice]` — avoids an sklearn dep) between judge and human verdicts; below-threshold classes flagged for panel escalation (v1 = flag only, panel is v2). Only `human_verdict` events close comparisons — assert ledger state. "Insufficient human verdicts" rendered per class until threshold. Tests: `test_ac7_kappa_computation`, `test_ac7_human_verdict_closes`, `test_ac7_escalation_table`.

**M7 — Emergent capability check.** Because the judge is pure configuration, verify (fixture-level, no new machinery) that an experiment can declare multiple judges over identical packets — verdict deltas between judges measure judge bias directly. Don't build orchestration for it; just ensure nothing prevents it.

## 6. Test plan summary

| AC | Tests | Type |
|---|---|---|
| AC-1 | judge_vendor_unrestricted | config + grep-no-denylist |
| AC-2 | packet_identity_free, packet_allowlist_only | hypothesis canary property |
| AC-3 | order_debias_inconsistent_ties, both_orders_invoked | mocked cross-order |
| AC-4 | evidence_required, malformed_becomes_cant_judge | schema |
| AC-5 | verdict_provenance_complete, alias_model_id_rejected | schema + plan stage |
| AC-6 | vendor_overlap_flagged, cross_vendor_clean | confounds |
| AC-7 | kappa_computation, human_verdict_closes, escalation_table | fixture ledgers |
| AC-8 | fail_closed_timeout, fail_closed_refusal (+ provider-error, parse) | fault injection |

## 7. Constraints checklist at merge

- Judge never receives arm identity; packet builder is the **sole** path to the judge ✓ (M2 + no other call site — import-lint `judge/client` reachable only via packet types `[plan choice]`)
- Judge verdicts advisory; only human verdicts close ✓ (M6)
- Fully versioned judge ids; aliases rejected at plan ✓ (M4)
- Same-vendor legal, always disclosed ✓ (M5)
- Verdict ledger append-only, hash-chained, one schema family with human verdicts ✓ (EVAL-3 substrate + M1)

## 8. Definition of done

Judge layer callable standalone (packets in, verdict events out; no other stage imports judge internals except through the grades ledger); canary property suite green — identity isolation **proven, not asserted**; reports render verdict distribution, order-inconsistency rate, vendor-overlap flag, kappa table / insufficient-verdicts per class, escalation candidates. Formal closure additionally requires D006 RESOLVED (then set the config defaults accordingly).

## 9. Risks / watch items

- Structured-output reliability varies by provider; parse strictly and let CANT_JUDGE(parse) absorb the failures — do not add lenient parsing that launders malformed verdicts into data.
- Rubric file is provenance (`rubric_sha256`): treat rubric edits like schema changes.
- Out of scope, resist creep: panel aggregation (stub only), transcript/process scoring (EVAL-9), judge fine-tuning/few-shot calibration.
