# Design: the benchmark result card (comparability & legibility layer)

Status: **approved for build** (human decisions recorded below).
Concern: make a verdi run's result *comparable* and *citable* against the field
without a hosted service and without abandoning verdi's paired-A/B identity — the
one remaining codeable gap on the "belongs in the same conversation as public
benchmarks" axis.

## The problem

verdi produces a defensible *private* A/B. A public benchmark's social currency
is a *comparable, citable number*. Today two people's verdi runs are not
comparable to each other, and a run doesn't emit a single artifact you can cite.
This layer closes that — comparability made **verifiable**, not asserted.

## Human decisions (recorded)

1. **Center of gravity — co-equal.** The card presents the per-arm *absolute
   score* (the leaderboard's language) and the *paired delta + CI* (verdi's
   rigor) as co-equal top-line facts, both under the honesty stamps.
2. **Ledger posture — read-only projection.** `bench card` appends nothing. The
   card is a deterministic projection of already-ledgered facts, re-derivable and
   thus self-verifying. No event-schema change.
3. **Process — design doc + tested slices** (this document), PRA-style named
   tests, `make verify` each slice.
4. **First slice — card + compare verifier.** The emitter and the comparability
   check ship together; a card you cannot compare isn't done.

## Core idea: comparability is verifiable, not claimed

Two cards are comparable **iff they ran the same tasks**. verdi already commits a
tamper-evident fingerprint of the task set — `task_shas_sha256` inside the
`experiment_locked` event (`harness/corpus/commit.py:compute_commitment`). The
card carries a **`battery_sha`** derived from that, so "same tasks?" is a
one-glance, machine-checkable fact, and comparing across different task sets is a
**loud refusal**, never a silent mismatch.

### `battery_sha` semantics (the one subtlety, handled honestly)

- With `--corpus <manifest>` (the public-benchmark path): `battery_sha` is
  computed over the corpus's *intrinsic* per-task shas
  (`content_sha({task_id: manifest.task(task_id).sha})`) for the task ids the
  experiment ran. For the SWE-bench importer these shas are **image-insensitive**
  (problem + tests + repo + commit, not the mirror/digest), so two runs of the
  same subset are comparable across image mirrors. `battery_basis = "corpus"`.
- Without `--corpus`: `battery_sha` falls back to the lock's `task_shas_sha256`
  (over `tasks.yaml`, so image-*sensitive*), and the card sets
  `battery_basis = "lock_commitment"` — comparability is still exact, just
  narrower, and the card says so.

Two cards are comparable only when `battery_sha`, `battery_basis`, and
`primary_metric` all match. The `compare` verifier refuses otherwise.

## The card (schema v1, canonical JSON)

A versioned public contract; byte-deterministic for a fixed `(ledger, seed,
corpus)` like the dossier. Fields:

```jsonc
{
  "schema_version": 1,
  "instrument": { "version": "...", "git_sha": "...", "tier": "ADVISORY" },
  "battery": {
    "battery_sha": "<hex>",            // the comparability key
    "battery_basis": "corpus|lock_commitment",
    "corpus_id": "swe-bench", "semver": "1.0.0",
    "dataset": { "name": "swe-bench", "version": "2.0" },   // when known
    "n_tasks": 50
  },
  "primary_metric": "holdout_pass_rate",
  "decision_rule": "delta_holdout_pass_rate > 0",
  "arms": [                              // the leaderboard's language, per arm
    { "name": "control",   "model": "...", "aux_models": [...],
      "absolute_score": 0.62, "n": 50 },
    { "name": "treatment", "model": "...", "aux_models": [...],
      "absolute_score": 0.58, "n": 50 }
  ],
  "comparison": {                        // verdi's rigor, co-equal
    "arm_a": "control", "arm_b": "treatment",
    "delta": -0.04, "ci_low": -0.11, "ci_high": 0.02,
    "ci_method": "percentile", "ci_level": 0.95,
    "mde": 0.15, "official_decision": true, "decides_positive": false,
    "detected": false
  },
  "provenance": {
    "spec_sha256": "...", "lock_commitment_sha": "...",
    "ledger_head": "...", "mode": "official|exploratory",
    "selfcheck": "passed|absent", "rubric_committed": true
  },
  "disclosures": {                        // honesty block, non-suppressing
    "confounds": [...], "contamination": {...},
    "forensic_quarantines": [...], "excluded_metrics": [...]
  }
}
```

Notes that keep it honest:
- `absolute_score` is a **point estimate** (mean of the arm's per-task primary
  metric) + `n` — exactly the convention public leaderboards use (e.g. SWE-bench
  "% Resolved" is a bare number). The *uncertainty* that governs the decision is
  the paired `delta` CI, which leaderboards lack — that's verdi's addition.
- Every number already exists in the ledger/findings; the card computes **no new
  statistic** beyond the per-arm mean. Per-arm absolute comes from a new public
  helper in `analyze` (`per_arm_absolute_scores`), so all stats stay in `analyze`
  and the card only projects.
- `tier: ADVISORY` and `mode` travel on the card so a subset/advisory result can
  never be mistaken for an authoritative leaderboard entry.

## CLI surface

A `card` command group (the repo's idiom for multi-action verbs):

- `bench card emit <experiment-dir> [--corpus m.json] [--out card.json]`
  — read-only; writes the canonical JSON (stdout if no `--out`).
- `bench card compare <a.json> <b.json>` — verifies matching
  `(battery_sha, battery_basis, primary_metric)`; on match prints the per-arm
  absolute scores side by side plus each card's delta; on mismatch **refuses
  loudly** ("not comparable: different task set / metric").

## Module layout (single responsibility)

- `harness/analyze/report.py`: add `per_arm_absolute_scores(...)` (a per-arm
  summary statistic — an analyze concern).
- `harness/analyze/card.py` (new): `build_card`, `battery_sha`, `compare_cards`,
  canonical serialization. Pure projection; recomputes nothing statistical.
- `harness/analyze/cli.py`: register the `card` group.

## Slice plan

1. **Card + compare (this slice).** `per_arm_absolute_scores`, `card.py`, the
   `card emit` / `card compare` verbs, and tests: determinism, co-equal
   score+delta, battery_sha same-tasks/changed-tasks, compare match + loud
   refusal, and a SWE-bench materialized run producing a card with the swe-bench
   battery identity + per-arm resolved rates. README verb docs + usage-guide
   section.
2. *(Future, not now)* a human-facing render (markdown/HTML) of the card, and a
   published reference dossier of two real models on a SWE-bench subset.

## What this does and does not claim

- **Does:** make two verdi runs of the same battery machine-verifiably
  comparable, and a single run citable with tamper-evident provenance.
- **Does not:** turn verdi into a leaderboard, vouch for corpus
  representativeness, or lift the `ADVISORY` tier. The card is legible *and*
  honest about its scope.
