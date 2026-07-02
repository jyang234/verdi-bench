# 06 — EVAL-7 Implementation Plan: Human review packet — blinded evidence, measured integrity, verdict capture, kappa feed

**Read with:** `00-EVAL-1-master-plan.md`, `Eval7.spec.md`, `Eval7.decisions.ndjson`. **Requires:** EVAL-2 (verdict schema family, canary/blinding core, kappa consumer), EVAL-5/EVAL-4 (holdout results, diffs, prompts), EVAL-6 (findings renderer to carry the integrity rate), EVAL-3 (ledger state machine).

## 1. Gate status

- **RESOLVED:** D001 review surface = static HTML packet (rich diffs) + CLI verdict capture; D002 sampling = mandatory disagreement set + seed-derived **20% random floor of agreements** (unbiased-direction kappa with bounded workload).
- **OPEN (gate formally blocked):** **D003** — kappa estimator correction: inverse-probability-weighted over the reviewed set (floor items reweighted 1/0.2), with floor-only kappa as a sensitivity analysis, replacing the raw pooled estimator. Working assumption = recommendation. Implement behind a `KappaEstimator` seam (§M5) with `ipw` and `raw_pooled` both available; report defaults to IPW + floor-only sensitivity `[assumption]`. Note: EVAL-9 AC-5 explicitly inherits this resolution — resolving D003 unblocks the estimator choice for both stories.

## 2. Objective

The human is the final judge — but a human anchored by the machine's opinion, reviewing an unblinded packet, or sampled only on disagreements produces verdicts that can't calibrate anything. Sufficient blinded evidence, bounded workload, verdict captured strictly before any reveal, and a blind that is **measured, never asserted**.

## 3. Module layout & public symbols

```
harness/review/packet.py    build_review_packet
harness/review/scrub.py     blind_scrub          # thin wrapper over harness/blind/core.py
harness/review/sample.py    select_for_review
harness/cli.py              cmd_review_record
```

`blind_scrub` **must** delegate to the same core the judge packets use — the spec constraint is "one scrub implementation, a single blinding codepath to test" (master plan §7.4).

## 4. Data contracts

**4.1 Sampling** [D002, AC-2]. Mandatory set = every disagreement: deterministic-vs-judge conflicts, `order_inconsistent`, `CANT_JUDGE`. Floor = seed-derived 20% of *agreements* (namespaced sub-seed from the locked plan seed). Kappa computed **only** over the human-reviewed set; each reviewed item records its stratum (`mandatory` | `floor`) for the D003 estimator.

**4.2 Packet** [D001, AC-1, AC-3]. Self-contained static HTML bundle per experiment: side-by-side diffs, holdout results, task prompt; ordered disagreements-first (without revealing *why* an item is in the mandatory set beyond ordering `[plan choice: don't label items "disagreement" — that leaks the judge's state]`). Deliberately excludes judge verdicts and arm identities — revealing the judge's opinion before the human's is a partial unblind [derived from EVAL-2-D002/blinding-measured]. Offline: no external requests; inline CSS/JS only; diffs pre-rendered server-side (`difflib` + inline-styled pygments `[plan choice]`).

**4.3 Events added.** `human_verdict` — schema mirrors `judge_verdict` (same family ⇒ kappa directly computable) **plus blinding-integrity fields**: `{arm_recognized: bool, arm_guess: str|null}` [AC-4]; `reveal` — `{verdict_event_id, revealed: {judge_verdict_id, arm_identities}}`, appendable only after the referenced verdict exists. Only `human_verdict` events close comparisons [AC-5, EVAL-2-D004].

## 5. Implementation sequence

**M1 — Sampling.** `select_for_review(ledger, seed)`: mandatory set exactly as defined; reproducible floor; strata recorded. Tests: `test_ac2_mandatory_set`, `test_ac2_random_floor_seeded`, `test_ac2_kappa_reviewed_only` (unreviewed comparisons excluded from kappa inputs).

**M2 — Scrub.** `blind_scrub` wrapping `harness/blind/core.py`; scrub failure ⇒ **packet generation blocked** (fail closed, consistent with EVAL-2's never-send). Test: `test_ac1_scrub_canaries` (canary identity strings seeded through fixture artifacts never appear in the rendered packet).

**M3 — Packet build.** Static HTML bundle per §4.2. Tests: `test_ac3_html_selfcontained` (opens offline; parse for external `http(s)`/protocol-relative refs ⇒ none), `test_ac3_no_judge_or_arm_content` (no judge-verdict fields, no arm identifiers in the HTML).

**M4 — Capture-then-reveal.** `bench review record`: takes the verdict + the two integrity questions ("could you identify the arm? guess?") **strictly before any unblinding**; only then does `bench review reveal` disclose judge verdict + arm identity, as a ledgered `reveal` event referencing the verdict event id. The ordering is enforced by the tool, not by discipline: reveal refuses if no verdict+integrity event exists for the comparison [AC-4]. This reveal event is also the unlock EVAL-9's human process scoring keys off (EVAL-9 AC-3) — keep its shape stable. Tests: `test_ac4_verdict_event_schema`, `test_ac4_integrity_pre_unblind`, `test_ac4_reveal_after_verdict`.

**M5 — Kappa feed + estimator seam.** Human verdicts feed `kappa_by_class` (EVAL-2 M6 consumer); comparisons without human verdicts remain open in ledger state. `KappaEstimator` seam [D003]: `ipw` (reviewed items weighted by 1/inclusion-probability — floor items 1/0.2, mandatory 1.0) as default, `floor_only` as sensitivity, `raw_pooled` retained for comparison; report both headline + sensitivity `[assumption: D003 rec]`. Tests: `test_ac5_kappa_feed` (fixture human verdicts change `kappa_by_class` outputs; open-state assertion), plus estimator fixture recovering hand-checked IPW values (shared with EVAL-9's weighted-kappa fixtures where possible).

**M6 — Integrity as data.** Blinding-integrity rate = share of reviews with `arm_recognized=true`, plus guess accuracy among those; computed per experiment and **rides every findings render** — wire into EVAL-6's renderer: a findings render missing the integrity rate fails validation [AC-6]. A high rate doesn't invalidate results; it bounds how much the blind can be credited. Test: `test_ac6_integrity_rate_reported`.

## 6. Test plan summary

| AC | Tests |
|---|---|
| AC-1 | scrub_canaries (shared blinding core) |
| AC-2 | mandatory_set, random_floor_seeded, kappa_reviewed_only |
| AC-3 | html_selfcontained, no_judge_or_arm_content |
| AC-4 | verdict_event_schema, integrity_pre_unblind, reveal_after_verdict |
| AC-5 | kappa_feed (+ IPW fixture) |
| AC-6 | integrity_rate_reported (render-validation failure without it) |

## 7. Constraints checklist at merge

- Verdict + integrity captured before any unblinding; reveal path unreachable earlier ✓ (M4)
- One scrub implementation shared with judge packets ✓ (M2)
- Blinding measured, never asserted: integrity rate part of every finding ✓ (M6)

## 8. Definition of done

A fixture experiment produces a browsable offline packet; recording a verdict via CLI closes the comparison, updates kappa inputs, and unlocks the reveal; integrity rate appears in findings and its absence fails render validation. Formal closure additionally requires D003 RESOLVED (seam makes it a default swap).

## 9. Risks / watch items

- Diff rendering is the likeliest identity-leak surface (paths, comments, style); the canary corpus should include realistic path/name patterns, not just literal arm ids.
- Don't let packet ordering or styling encode judge state (see M3 note) — a reviewer inferring "these are the disagreements" is a partial anchor.
- Out of scope, resist creep: multi-reviewer workflows / inter-rater reliability, web-served packets, annotation beyond the verdict schema.
