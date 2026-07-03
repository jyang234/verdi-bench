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
| **EVAL-8** | Corpus import (idempotent), stratified calibration, mining, curation gate, boundary | ✅ |
| **EVAL-6** | Analyze: paired bootstrap, effect sizes, confound flags, pre-registration fence | ✅ |
| **EVAL-7** | Human review packet (offline, blinded), capture-then-reveal, kappa estimator seam | ✅ |
| **EVAL-9** | Process rubric: isolated judge scoring, firewalls, weighted-kappa calibration | ✅ |

All EVAL-1 child stories are built. The fast suite
(`uv run pytest -m "not docker"`) is green — over 400 tests — plus a
`docker`-marked suite of real-container tests (a real grade container and a real
Harbor trial) run with `-m docker` in a dedicated CI job on Docker-capable
runners; 3 import-linter contracts kept. AC-mapped tests are **enforced per
story**: collection fails if any story's pre-registered acceptance criteria (from
its `eval<N>.spec.md`) lack a `test_ac<N>_*` test, or if an AC test is duplicated
or names an AC its story does not declare. `--ac-report` additionally prints the
exercised AC numbers.

## Provisional decisions

Per the master-plan decision-gate dashboard, these are implemented to the
recommended option behind a seam (a resolution is a config-sized diff):

- **D007** (power variance source) — `AssumedVariance` flags results
  `assumption_based_mde`; `CalibrationVariance` seam ready for EVAL-8 data.
- **D008** (lock hardening) — external `anchors` subsystem, on by default,
  cleanly severable.
- **D006** (kappa threshold / min sample) — `0.6 / 20` as config defaults in the
  judge `escalation` block.
- **EVAL-6 D004** (CI method by coverage) — `percentile`/`bca`/`cluster_robust_t`
  behind a `CIMethod` seam; selected by empirical coverage under `nullsim.py`.
- **EVAL-7 D003** (kappa estimator) — IPW default (floor reweighted `1/0.2`) with
  floor-only sensitivity, behind the `KappaEstimator` seam; EVAL-9 inherits it.
- **EVAL-9 D001–D004** — per-trial absolute scoring, judge+human scorers, the
  five v1 dimensions, and full-or-`CANT_SCORE` transcript policy, each
  parameterized so a decision flip is contained.

## Usage

```bash
uv sync
uv run bench plan   experiment.yaml --ledger ledger.ndjson   # validate + lock
uv run bench run    <experiment-dir>                          # execute trials
uv run bench grade  <experiment-dir>                          # deterministic grades
uv run bench analyze <experiment-dir> --exploratory                # watermarked findings
uv run bench analyze <experiment-dir> --official --corpus m.json   # fenced official render
uv run bench verify-chain ledger.ndjson [--against-anchor anchors.ndjson]
uv run bench anchor ledger.ndjson --out anchors.ndjson

uv run bench corpus import <tasks-dir> --cache <dir>   # idempotent public import
uv run bench corpus subset <manifest> --seed 1234      # stratified calibration subset
uv run bench corpus mine <mr.json> --ticket t.txt --out cand.json
uv run bench corpus review <cand.json>                 # curation view
uv run bench review record <experiment-dir> --comparison-id c1 --winner A ...
uv run bench review reveal <experiment-dir> --comparison-id c1   # refuses pre-verdict
uv run bench process record <experiment-dir> --trial-id t1 --comparison-id c1 --scores s.json
```

`bench run` defaults to the hermetic **fake** engine (fast, no Docker).
`--engine harbor` runs the real container path: digest-pinned images
(`--pull=never`), the task prompt + arm delivered read-only at
`/verdi/request.json` (outside the graded workspace), provider keys env-injected
and redacted at capture, egress confined to the metering proxy on an internal
docker network with per-trial JSONL attribution, and containers killed on
timeout. Operational wiring (proxy, quotas, provider-key names) comes from an
optional `run.config.yaml` + the environment — never the sha-locked
`experiment.yaml` or the ledger. Its container behavior is covered by
`docker`-marked tests in CI (`uv run pytest -m docker`).

`bench grade` defaults to `--runner docker` (the real network-less grading
container), with `--runner local` for the no-daemon fake/test path.

## Development

```bash
uv run pytest -m "not docker" -q     # fast suite
uv run lint-imports                  # structural contracts
uv run pytest --ac-report            # recompute AC coverage
```

> **Python:** the spec binds 3.12+. This checkout's `requires-python` is relaxed
> to `>=3.11` because the 3.12 standalone build is unreachable in the current
> environment; 3.12 compatibility is verified by a `compileall` gate under a real
> 3.12 interpreter in the CI `py312-compat` job.
