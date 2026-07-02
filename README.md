# verdi-bench

A benchmark-grade A/B evaluation instrument for agent stacks, models, and
configurations: pre-registered experiments, repeated paired trials in hermetic
containers, insulated arms, deterministic-first grading, and an outcome-blind
advisory LLM judge. Every operation is a hash-chained ledger event; every local
record is stamped `ADVISORY`.

## Status

Implemented stories (following the `00-EVAL-1` master-plan build order):

| Story | Scope | State |
|---|---|---|
| **M0** | Repo scaffolding, provenance helper, import-linter contracts, AC hook | ✅ |
| **EVAL-3** | Experiment schema, hash-chained ledger, plan lock, power/MDE, interleave | ✅ |
| **EVAL-4** | Run seam, adapters, hermetic Harbor engine, cost guard, insulation | ✅ |
| **EVAL-5** | Deterministic grading, flake baseline, grader plugins | ✅ |
| **EVAL-2** | Outcome-blind configurable LLM judge, calibration | ✅ |

Not yet built: EVAL-6 (analyze + pre-registration fence), EVAL-7 (human review),
EVAL-8 (corpus import/mining), EVAL-9 (process rubric). Their touchpoint
namespaces (`analyze/`, `review/`, `process/`, `corpus/`) are scaffolded and a
few shared seams already exist (`analyze/confounds.py`, `append_human_verdict`).

146 tests green (full AC-1..AC-9 coverage per built story); 3 import-linter
contracts kept.

## Provisional decisions

Per the master-plan decision-gate dashboard, these are implemented to the
recommended option behind a seam (a resolution is a config-sized diff):

- **D007** (power variance source) — `AssumedVariance` flags results
  `assumption_based_mde`; `CalibrationVariance` seam ready for EVAL-8 data.
- **D008** (lock hardening) — external `anchors` subsystem, on by default,
  cleanly severable.
- **D006** (kappa threshold / min sample) — `0.6 / 20` as config defaults in the
  judge `escalation` block.

## Usage

```bash
uv sync
uv run bench plan   experiment.yaml --ledger ledger.ndjson   # validate + lock
uv run bench run    <experiment-dir>                          # execute trials
uv run bench grade  <experiment-dir>                          # deterministic grades
uv run bench verify-chain ledger.ndjson [--against-anchor anchors.ndjson]
uv run bench anchor ledger.ndjson --out anchors.ndjson
```

`bench run`/`grade` default to the hermetic **fake** engine (fast, no Docker);
`--engine harbor` selects the real container path (requires a Docker daemon;
those tests are marked `docker`).

## Development

```bash
uv run pytest -m "not docker" -q     # fast suite
uv run lint-imports                  # structural contracts
uv run pytest --ac-report            # recompute AC coverage
```

> **Python:** the spec binds 3.12+. This checkout's `requires-python` is relaxed
> to `>=3.11` because the 3.12 standalone build is unreachable in the current
> environment; the code stays 3.12-compatible.
