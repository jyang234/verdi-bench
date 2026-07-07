# 01 — Safety nets & defect fixes (Phase 0)

Everything in later phases assumes the guards in this document exist. Nothing
here changes product behavior except the eight defect fixes, each of which
ships with a failing reproduction test first (CLAUDE.md: no fixes by
inspection).

**DECISIONS required before this phase:** D2's `FINGERPRINT_VERSION` bump
(approval A4); whether `VERDI_REQUIRE_PROXY` gets a CI consumer or is
documented local-only.

## 1. Golden serialization guards (the refactor's foundation)

**Problem.** The chain writer and verifier share one `canonical_line`
implementation (`harness/ledger/chain.py:46-58`), so a serialization drift
stays self-consistent: every test passes while every pre-existing on-disk
ledger becomes unverifiable. The audit found **zero** committed `.ndjson`
fixtures and zero golden-byte assertions in the suite. The same gap covers
the anchor format — which is *deliberately different* (`ensure_ascii`
defaults to True at `harness/ledger/anchors.py:73`, unlike the chain's
`ensure_ascii=False`) and currently protected by nothing.

**Work.**

1. `tests/fixtures/data/golden_ledger.ndjson` — a small, complete,
   committed ledger (lock → trials → grades → verdict → findings) generated
   once with a fixed `EventContext` (`clock`/`actor` are already injectable,
   `harness/ledger/events.py:44-54`). Tests:
   - `verify_chain` passes on the committed bytes;
   - the head hash equals a pinned constant;
   - a canonicalization drift (monkeypatched `sort_keys=False`, changed
     separators, `ensure_ascii=True`) makes verification of the committed
     file fail — proving the guard actually guards.
2. **Constructor replay golden.** Call every one of the 31 typed
   constructors in `harness/ledger/events.py` with fixed context and
   representative payloads (including each omit-if-None and
   always-present-nullable field both ways); assert the emitted lines are
   byte-identical to a committed fixture. This is the *enabling gate* for
   the declarative event registry ([06](06-ledger-telemetry.md) §2): after
   the conversion the same replay must produce the same bytes.
3. `tests/fixtures/data/golden_anchor.ndjson` — pinned anchor-record bytes
   (`{head_hash, height, ts}`, ascii-escaped) + `verify_against_anchor`
   pass/fail pair.
4. **Render byte-fixtures.** For the golden scenario ledger: committed
   `findings.exploratory.md`, `findings.official.md`, dossier HTML, and
   `card.json` byte-diffs (current determinism tests only do within-process
   double renders, `tests/test_eval6_analyze.py:111-123`). These gate the
   `report.py` decomposition ([07](07-analysis-surfaces.md)) — every split
   step must leave the fixtures byte-identical.

## 2. Test-fixture extraction (unpins test-file moves)

16 cross-test-file imports currently weld test files together (§3 of the
tests audit): `rich_experiment` lives in
`tests/test_eval14_observability_ui.py:50-153` and is imported by 5 other
files; `_run_lint` from `test_import_contracts` by 4; plus
`_reasoning_experiment` / `_linked_experiment` chains.

**Work** (pure moves, no behavior):

- `tests/fixtures/scenarios.py` — `rich_experiment`, `_reasoning_experiment`,
  `_linked_experiment` (rename public), staged for later replacement by SDK
  builders in Phase 2.
- `tests/fixtures/servers.py` — a `serve_root(...)` context manager
  replacing the 7 hand-rolled `make_server` + thread + shutdown blocks
  (`test_eval13_observability.py:330`, `test_eval14_page_drive.py:20-24`, …).
- `tests/fixtures/lint.py` — `_run_lint`/`_REPO`.
- `tests/fixtures/grading.py` — `write_holdout_results(workspace, passed, *,
  assertion_id="h1")` killing the 25 literal `holdout_results.json` writes
  across 9 files (the shape is a public grade seam; one writer).
- `tests/fixtures/tamper.py` — byte-flip + canonical re-encode vectors from
  `scripts/shakedown/tripwires.py:69-88` and the forged-lock helper from
  `tests/test_eval3_lock.py:348-376`, so shakedown and tests share one
  adversarial toolkit (test-utils, **not** the public SDK).

Constraint: AC enforcement binds to *filename prefixes*, not paths
(`tests/ac_coverage.py:40,174`), so fixture moves are free; the
`test_eval<N>_` / `test_ac<N>_` prefixes of real test files never change.

## 3. CI corrections

- **Browser marker (fixes D6).** Register a `browser` marker in
  `pyproject.toml`, mark the five browser-drive files, replace the
  four-path enumeration at `.github/workflows/ci.yml:74-78` with
  `-m browser` under `VERDI_REQUIRE_BROWSER=1`. Today
  `tests/test_serve_legibility.py`'s browser tests silently never run in CI.
- **Shakedown in CI.** `make shakedown` (L1 + L3, hermetic — no keys, no
  Docker) becomes a CI job; a refactor that breaks a fence's exact reason
  string currently passes CI.
- **`VERDI_REQUIRE_PROXY`** — the guard exists
  (`tests/test_proxy_ci_guard.py`) but no job sets it. **DECISION:** wire it
  into the docker job or document it local-only.
- Fail-closed switches (`VERDI_REQUIRE_DOCKER`/`_BROWSER`) and their guard
  tests are untouched.

## 4. Defect fixes (reproduce-first, one commit each)

| # | Fix | Test first |
|---|---|---|
| D1 | `harness/cli.py:192-194`: catch `ModuleNotFoundError` only when `e.name` is the stage module itself; re-raise transitive misses | a stage CLI stub raising `ModuleNotFoundError("somelib")` must abort registration loudly, not drop the verb |
| D2 | `harness/run/reuse.py:77` reads `plugins`; normalize to `plugin_ids` (doc key, `docs/usage-guide.md:143`; grade key, `harness/grade/cli.py:39`) | fingerprint of a doc-conformant `plugin_ids:` task must change when plugin list changes (fails today). **Approval A4**: bump `FINGERPRINT_VERSION` (`harness/run/control_reuse.py:37-40`), old bundles refuse cleanly |
| D3 | plugin registration transport: import built-in plugin modules in `harness/grade/plugins/__init__.py` (explicit `BUILTIN_PLUGINS` list), drop the side-effect import at `harness/grade/cli.py:19-20` | `python -m harness.grade.run_plugin groundwork` path (unit-level: `run_plugin` resolves a registered plugin without the CLI having been imported) |
| D4 | `harness/process/score.py:198`: `provider_model` becomes required (mirror the D002 posture at `harness/forensics/review.py:237-240`) | calling without a model raises; no 404-able default remains |
| D5 | `harness/judge/packet.py:216-221`: secret scan covers holdout blobs like the identity scan; add a meta-test asserting every `Packet`/`ResponseArtifacts` text field is covered by **both** scans, so the blob list can never silently drift again | a secret planted in holdout results must be caught |
| D7 | import `Optional` in `harness/analyze/nullsim.py` | `typing.get_type_hints(coverage_of_method)` no longer raises |
| D8 | add the correction-consistency item to `harness/analyze/fence.py` (interim; structurally fixed by the unified fence in [07](07-analysis-surfaces.md)) | `/api/fence` parity test with the render fence on a chain carrying a differently-corrected prior official render |
| D9 | rewrite `tests/test_eval3_events.py:34-52` to sweep the live registry (all 31, and future, event types) | n/a (test-only; state explicitly in the PR that a drifted guard test is being corrected, per CLAUDE.md) |
| D10 | trivia batch: `harbor_multiagent.py:10` docstring, `report.py:1908` stray f-string, fence comment numbering, retired model ids in `harness/author/page.py:118` template + `harness/process/score.py:391-400` fixture block + `tests/fixtures/builders.py` | readme-consistency & existing suites |

## 5. Gate for Phase 0

- `make verify` green; `make shakedown` green and running in CI.
- Goldens 1–4 committed and demonstrably sensitive (each has a
  "drift breaks it" counter-test).
- `grep -rn "^from tests.test_\|import tests.test_" tests/` returns nothing.
- All defect fixes merged or explicitly deferred by the human with a note.
