# CLAUDE.md

Binding directives for working in this repository. When they conflict with
convenience or speed, the directives win.

## Project overview

verdi-bench is a benchmark-grade A/B evaluation instrument for agent stacks:
pre-registered experiments, paired trials in hermetic containers, insulated
arms, deterministic-first grading, an identity-blind advisory LLM judge, and a
hash-chained event ledger. Its credibility depends on its own correctness —
treat every silent failure or gamed test as a defect in the instrument itself.

- Code: `harness/` — one subsystem per concern (`plan`, `run`, `grade`,
  `judge`, `ledger`, `schema`, `adapters`, `blind`; `analyze`/`review`/
  `process`/`corpus` are scaffolded).
- Tests: `tests/test_eval<N>_*.py`; AC-mapped tests are named `test_ac<N>_*`
  (`uv run pytest --ac-report` recomputes coverage).
- Tooling: `uv`, `pytest` + `hypothesis`, `import-linter`. Code must stay
  3.12-compatible even though the local floor is 3.11.

## Commands

```bash
make verify                        # full gate: all tests + import contracts
uv run pytest -m "not docker" -q   # fast inner loop while developing
uv run lint-imports                # structural contracts only
```

## Core directives

### Single responsibility

- One concern per module, class, and function. Split before merging if a
  function grows a second responsibility ("validate and also persist").
- Respect subsystem boundaries; cross them only through public seams. The
  import-linter contracts enforce this and must stay green.
- New behavior goes in the subsystem that owns the concern, never in
  whichever file was already open.

### `make verify` after every change — no exceptions

- Run `make verify` after each feature, fix, or refactor, before the work is
  called done or committed. Not skippable for "trivial" changes or under time
  pressure.
- If it fails, the work is not finished. Fix the code and rerun until green.

### Tests evaluate real behavior — no tampering

- Never weaken, delete, skip, or `xfail` a failing test to get green. A
  failing test means the code is wrong until proven otherwise.
- Never hardcode expected outputs, stub the code under test, or edit
  fixtures/holdouts to match buggy output.
- Mocks isolate external boundaries (Docker, LLM clients) only — never the
  logic being tested.
- Every feature or fix ships with tests that exercise observable behavior and
  fail if it breaks.
- Changing a genuinely wrong test requires saying so explicitly and getting
  human agreement first.

### The human decides — when in doubt, ask

- Ask before proceeding when requirements are ambiguous, multiple reasonable
  designs exist, or work grows beyond the requested scope.
- Give a concrete recommendation with trade-offs, not an open-ended question.
- Never silently substitute your preferred approach for one the human chose.
- Small reversible details within an agreed approach don't need a check-in;
  direction-setting decisions do.

## Quality directives

### Reproduce before fixing

- Write a failing test that reproduces a bug before writing the fix; the fix
  is done when that test passes. No fixes "by inspection".

### Public seams are contracts

- Schemas, ledger event formats, and anything hash-chained or pre-registered
  are versioned contracts. Changing one requires explicit human approval and
  a migration/compatibility story — a silent serialization change can
  invalidate every existing chain.

### Determinism by default

- No wall-clock time, unseeded randomness, dict-ordering assumptions, or
  network calls outside designated seams. Deterministic grading must import
  no LLM client (enforced by contract).

### Fail loudly

- No bare `except:`, no swallowed exceptions, no sentinel values that mask
  failure. Validation errors say what was wrong and where. A crash is better
  than a silently wrong grade.

### Honest reporting

- Report failures faithfully: failing tests, flakes, and skipped steps are
  stated plainly, never presented as done.
- List any judgment calls made without asking in the final summary so the
  human can veto them cheaply.

## Working conventions

- Match existing style: typed Python, `from __future__ import annotations`,
  module docstrings citing the master-plan section they implement.
- Atomic commits: one logical change each; messages explain why, not just
  what.
- No dead code, commented-out code, or ownerless TODOs in a diff — every
  line must be live.
- Fix problems your change caused; mention unrelated problems you notice
  instead of fixing them in the same diff.
