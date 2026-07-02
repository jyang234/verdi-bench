# CLAUDE.md

Guidance for Claude when working in this repository. These directives are
binding; when they conflict with convenience or speed, the directives win.

## Project overview

verdi-bench is a benchmark-grade A/B evaluation instrument for agent stacks:
pre-registered experiments, repeated paired trials in hermetic containers,
insulated arms, deterministic-first grading, and an outcome-blind advisory LLM
judge. Every operation is a hash-chained ledger event.

- Package code lives in `harness/` (subsystems: `plan`, `run`, `grade`,
  `judge`, `ledger`, `schema`, `adapters`, `blind`, plus scaffolded
  `analyze`/`review`/`process`/`corpus`).
- Tests live in `tests/`, named `test_eval<N>_*.py` per story. AC-mapped tests
  are named `test_ac<N>_*` so acceptance-criteria coverage is recomputable
  (`uv run pytest --ac-report`).
- Tooling: `uv` for env/deps, `pytest` (+ `hypothesis`) for tests,
  `import-linter` for structural contracts. Python code must stay
  3.12-compatible even though the local floor is 3.11 (see `pyproject.toml`).

## Core engineering directives

### 1. Single responsibility principle

Every module, class, and function does one thing. Concretely:

- Keep subsystem boundaries intact: `plan`, `run`, `grade`, `judge`, `ledger`,
  etc. each own one concern. Do not reach across them except through their
  public seams; the import-linter contracts enforce this and must stay green.
- If a function grows a second responsibility (e.g. "validate and also
  persist"), split it before merging.
- New behavior goes in the subsystem that owns that concern — never bolted
  onto whichever file was already open.
- A change description that needs the word "and" between two unrelated
  behaviors is a signal to split the change.

### 2. `make verify` after every feature — no exceptions

`make verify` runs all unit and integration tests in the repo plus the
import-linter structural contracts:

```bash
make verify
```

It MUST be run — and MUST pass — after each feature, fix, or refactor is
implemented, before the work is considered done or committed. No exceptions:
not for "trivial" changes, not for docs-adjacent code changes, not under time
pressure. If `make verify` fails, the feature is not finished; fix the code
(or, when the test is genuinely wrong, fix the test per directive 3) and run
it again until it passes.

### 3. Tests must evaluate real behavior — no tampering

Tests exist to catch real defects. Faking a passing result is forbidden:

- Never weaken, delete, skip, or `xfail` a failing test to make the suite
  green. A failing test means the code is wrong until proven otherwise.
- Never hardcode expected outputs, stub out the code under test, overwrite
  fixtures/holdouts to match buggy output, or add sleeps/retries to mask
  nondeterminism.
- Mocks and fakes are for isolating external boundaries (e.g. the fake engine
  vs. a live Docker daemon, LLM clients), never for replacing the logic being
  tested.
- New features and bug fixes come with tests that exercise real observable
  behavior — inputs and outputs a user or downstream subsystem would actually
  see — and that fail if the feature is broken.
- If a test is genuinely incorrect or obsolete, say so explicitly, explain
  why, and get the human's agreement before changing it (see directive 4).

This repo is itself an evaluation instrument; its credibility depends on its
own tests being honest. Treat any temptation to game the suite as a defect
report on the design.

### 4. The human decides — when in doubt, ask

Claude may (and should) hold opinions and make clear recommendations, but the
human user always has final judgement on approach and design decisions.

- When a requirement is ambiguous, when multiple reasonable designs exist,
  when a change would alter public seams, schemas, ledger semantics, or
  pre-registration behavior, or when work grows beyond the requested scope:
  stop and ask before proceeding.
- Present options with a concrete recommendation and its trade-offs — not an
  open-ended "what do you want?".
- Never silently substitute your preferred approach for one the human chose,
  and never proceed on a guess when the guess is cheap to confirm.
- Small, reversible implementation details within an agreed approach do not
  need a check-in; direction-setting decisions do.

## Working conventions

- Match the existing code style: typed Python, module docstrings that cite the
  master-plan section they implement, `from __future__ import annotations`.
- Keep changes minimal and focused; do not refactor unrelated code in passing.
- Fast iteration loop while developing: `uv run pytest -m "not docker" -q`.
  The full gate is still `make verify` before finishing.
- Docker-marked tests require a live daemon; everything else must run
  hermetically.
