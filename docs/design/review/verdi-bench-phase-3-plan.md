# verdi-bench — Phase 3 plan: the §7.2 fail-closed sweep

**Date:** 2026-07-03 · **Follows:** Phase 2 (merged to `main`, PRs #7/#8) ·
**Source of record:** `verdi-bench-review-consolidated.md` §5 Phase 3 + §3.4 (Judge),
§3.5 (Analyze), §3.6 (Corpus), §3.7 (Review), §3.8 (Process), §3.9 (XC-3), §6
(readiness gate). Orientation: `verdi-bench-phase-3-handoff.md`.
**Branch:** `claude/verdi-bench-phase-3-plan-5ilatc` (branched from `main`, which
already contains Phase 1 + Phase 2 + the handoff).

## Context

Phase 1 made results *integrity* real (chain verified at every stage entry, lock
hardened, task-content commitment, real grade path). Phase 2 made the *execution
path* real (hermetic metered Harbor trials, honest cost guard/quarantine/
baseline). Both are on `main`.

The master-plan §6 invariant Phase 3 owns — **"Fail closed; no operation without
a ledger event"** — is still **false in every un-hardened stage** (§2.3 of the
review). Phase 1 built the reference model: grade's `cant_grade(reason)` taxonomy
(`record_cant_grade`, a transient/terminal split, an enumerated set of reasons)
and the deterministic one-event property test. Phase 3 extends that discipline to
**judge, process, review, analyze, and corpus**, and makes the one-event property
*mechanically* enforce it across every ledgered operation rather than the three
it covers today.

The §9 branch/merge question in the handoff is **already resolved**: Phase 2 is
merged, this branch is cut from `main`, so Phase 3 builds on the merged base with
nothing to stack or reconcile.

### Re-verification against the current tree (not `01641cd`)

The consolidated review's line numbers are pre-Phase-1. I re-located every Phase 3
finding against the working tree at branch HEAD. **All of them reproduce**; Phases
1–2 shifted surrounding code and closed the two corpus items the handoff flags as
done (CO-3, CO-5) but touched none of the Phase 3 fail-closed holes. Current
locations and the concrete escape each represents:

**Cross-cutting infrastructure**
- **Entrypoint registry** (`harness/entrypoints.py`) holds exactly **three**
  entrypoints — `plan-lock` (`plan/lock.py:192`), `run-trial`
  (`run/interleave.py:334`), `grade-trial` (`grade/deterministic.py:196`). The
  property sweep (`tests/test_eval3_property.py:11-14`) **hardwires** those three
  imports and asserts only non-emptiness (`:21`), so "later stories join
  automatically" fails open (XC-3).
- **PL-14** confirmed: the acknowledged-underpowered path emits **two** events —
  `record_experiment_locked` (`plan/lock.py:124`) then
  `record_acknowledged_underpowered` (`plan/lock.py:136`). The property fixture
  (`write_experiment_yaml`) never sets an underpowered `hypothesized_effect`, so
  the sweep never exercises the two-event path.
- **Event funnel** (`harness/ledger/events.py`): registered types confirmed; there
  is **no** `CANT_ANALYZE`, `task_admitted`, calibration-run, or subset-draw event
  type. `cant_grade` (`:212`) is the only "cant" type. `judge_verdict` subsumes
  CANT_JUDGE via `winner` (`:281-283`); `process_score` subsumes CANT_SCORE via
  per-dimension values (`:413-420`).

**Judge (EVAL-2) — `harness/judge/`**
- **JD-2 OPEN:** `provider = provider or get_provider(config.model)`
  (`client.py:101`) runs *before* the fail-closed envelope; an unknown prefix
  raises `ProviderError` (`providers/base.py:45`) and exits `judge_pair` with
  **zero** events. The `_cant(...)` helper (`client.py:115`) is never reached.
- **JD-3 OPEN:** `resp["choices"][0]["message"]["content"]` (`openai.py:18`) and
  `resp["candidates"][0]…["parts"]` (`google.py:19-20`) raise `KeyError`/
  `IndexError` on an error-shaped or safety-blocked 200; the client's only parse
  handler catches `(ValueError, ValidationError, JSONDecodeError)`
  (`client.py:147`), **not** those, so they escape with no event. Anthropic's
  `.get()` chain (`anthropic.py:26-29`) does not raise but **misclassifies** an
  error body as `CANT_JUDGE("parse")`.
- **JD-13 OPEN (fail-closed sub-part):** a connect-phase timeout is delivered by
  urllib as `URLError` (an `OSError`, not `TimeoutError`), so it falls through the
  `TimeoutError` arm (`_http.py:25`) into the `URLError` arm (`:27`) → `timeout`
  is mislabeled `provider_error`. (The deterministic-label and `packet_sha256`
  sub-parts of JD-13 are scoped to Phase 5 — see Decisions.)

**Process (EVAL-9) — `harness/process/`**
- **PR-1 OPEN:** `{"scores":[3,4,5]}` → `raw = json.loads(...).get("scores", {})`
  yields a list (`score.py:114`), then `raw.get(d.id)` (`:117`) raises
  `AttributeError`, past the `except (ValueError, json.JSONDecodeError)`
  (`:185`) → escape, no `process_score` event, breaking the "always appends
  exactly one event" docstring (`score.py:147`).
- **PR-2 OPEN:** `build_process_packet` (`packet.py`, raises `RedactionLeakError`
  at `packet.py:77`) is called at `score.py:168`, *before* any `try` → escape
  with zero events; should be `CANT_SCORE(redaction_leak)`.
- **PR-3 OPEN:** `provider = provider or get_provider(provider_model)`
  (`score.py:178`) sits before the `try` (`:179`) → unknown prefix escapes.
- **PR-4 OPEN:** reasons are ad-hoc strings — `"unparsed"` (`score.py:120`),
  `"out_of_range"` (`:122`), `"context_overflow"` (`:176`), `"provider_error"`
  (`:182`, absorbing timeout/refusal), `"parse"` (`:186`), `"human_cant"`
  (`cli.py:51`). Both `"parse"` and `"unparsed"` exist; a judge-declared
  per-dimension `CANT_SCORE` is ledgered as `"unparsed"`; `cant_score_reason` is a
  free `Optional[str]` (`score.py:65`).
- **PR-7 OPEN:** `process record` drives its loop off `rubric.dimensions` and maps
  a missing/typoed key (`raw.get(d.id) is None`) to `CANT_SCORE("human_cant")`
  (`cli.py:47-51`), silently degrading a real score; keys not matching any rubric
  dim are silently ignored.
- **PR-8 OPEN:** neither `record_human_process_score` (`score.py:201-237`) nor
  `ProcessScore` (`score.py:83-89`) validates `scores` against the rubric; the
  rubric already exposes `dimension_ids` / `dimension(id)` (`rubric.py:75-83`) but
  nothing consults it — unknown/subset/duplicate dims ledger cleanly.

**Review (EVAL-7) — `harness/review/`** (Phase-1 `0a6ac9d` added only
`assert_chain(ledger_path)` at `record.py:78`, closing the forged-line/premature-
unblind vector; it touched none of RV-1/8/9.)
- **RV-1 OPEN:** `record_human_verdict` (`record.py:37-60`) guards only
  `verdict.source == "human"` (`:51`) — no check for an existing verdict or an
  existing reveal, so verdict → reveal → second (unblinded) verdict is accepted
  and duplicate verdicts all append.
- **RV-8 OPEN:** `reveal_comparison` (`record.py:63-97`) never checks for an
  existing reveal → **duplicate reveals allowed**; the no-verdict path raises
  `RevealError` (`:80-84`) with no record; a bare `append_human_verdict` (no
  integrity) closes the comparison (`calibrate.py:86-92`) yet `human_verdict_exists`
  requires `"integrity"` (`record.py:32`) so reveal stays locked.
- **RV-9 OPEN:** `reveal_comparison` takes the **first** judge verdict
  (`record.py:87-91`, `break`) while the kappa joins take the **last**
  (`sample.py:150-153`, `calibrate.py:97-100`); `review record` (`cli.py:28-61`)
  builds a verdict from a raw `--comparison-id` with **no existence check**
  (mistyped ids record then silently drop from kappa); integrity-less verdicts
  still calibrate (`pairs_from_ledger`, `calibrate.py:102-114`); the CLI omits
  `task_class` (`cli.py:53-56`) so every CLI verdict lands in `"default"`.

**Analyze (EVAL-6) — `harness/analyze/`**
- **AN-3 OPEN:** the analyze CLI body (`cli.py:39-85`) has **no try/except**.
  `CalibrationIncompleteError` (raised `report.py:574` via `_assert_official_
  calibration`, reached from `render_markdown` at `cli.py:67`) — and the sibling
  refusals `ProvenanceError`, `DisclosureError`, `UnregisteredOfficialError`, and
  `AnalyzeError` from `compute_findings` — all propagate out with **zero events**.
  There is **no `CANT_ANALYZE` event type**. On the success path files are written
  first (`cli.py:73-74`) and `record_findings_rendered` second (`:77-84`).

**Corpus (EVAL-8) — `harness/corpus/`** (CO-3 closed by `47ee047`; CO-5 closed by
`6491cc0` — do **not** re-do.)
- **CO-1 OPEN:** boundary enforcement is declaration-only — `assert_boundary`
  (`registry.py:121-141`) validates the declared `boundary_path` string, never the
  actual destination; `save(path)` (`:215-221`) writes to any `path`; `corpus mine
  --out` writes ticket text + holdout contents anywhere (`cli.py:83`). `INSTRUMENT_
  ROOT` (`registry.py:31`) is referenced only inside `assert_boundary`.
- **CO-4 OPEN:** `record_calibration_run` (`registry.py:186-198`) mutates in-memory
  manifest JSON only; no CLI verb, no run hook, **no ledger event**. `official_
  ready` (`:200-203`) reads `calibration.status` straight from hand-editable JSON.
- **CO-6 OPEN:** `e1bbe40` ("validate task ids") touched `commit.py`, **not**
  `public.py`; `task_id` still flows unsanitized from `source.fetch()` into
  `tasks_dir / f"{task_id}.json"` (`public.py:136-137`) — `"../../escaped"` escapes
  the cache. `TaskEntry.task_id` is a bare `str` (`registry.py:49`).
- **CO-9 OPEN:** `corpus subset` records the draw only in mutable manifest JSON
  (`stratify.py:92` sets `manifest.calibration.subset`, `cli.py:65` saves) with
  **no ledger event**; re-import (`public.py:135-139`) never prunes cache blobs of
  dropped tasks (manifest/cache drift).
- Admission today: `admit_task` (`admit.py:49-88`) verifies the chain (`:65`,
  Phase 1) and flips in-memory `task.status = "admitted"` (`:86`) but writes **no**
  ledger event — the admission decision itself is unledgered.

**Carry-forward from the Phase 2 review (#10):** `load_run_settings`
(`run/settings.py:86-88`) resolves `provider_keys` with `if name in env` — a key
*named* in `run.config.yaml` but *absent* from the environment is silently dropped,
so an arm runs unauthenticated and the A/B is biased. `test_ac8_provider_key_value_
from_env_not_file` (`tests/test_eval4_runconfig.py:104`) currently **pins** the
silent drop (`assert load_run_settings(...).provider_keys == {}  # absent ⇒ not
injected`). This is a fail-closed defect; raising it needs sign-off because a test
encodes the current behavior as intent.

## Decisions

### Carried forward (resolved, constrain Phase 3)

- **No pending review decision blocks Phase 3.** REVIEW-D-5 (degenerate kappa) is
  Phase 5; REVIEW-D-7 (3.12 gate) is Phase 6; REVIEW-D-1/3/4 are Phase 5
  documentation/statistics items. None gate the fail-closed work.
- **Phase-3 subsystem boundaries with Phase 4/5** (so the sweep does not pull
  connective-tissue or statistics work forward):
  - *Review.* Phase 3 = the **fail-closed guards** (refuse duplicate reveal,
    refuse post-reveal and duplicate verdicts, existence-check comparison ids).
    Unifying the first-vs-last verdict join, the IPW-realized weights, `CANT_JUDGE`
    exclusion from kappa, and `bench review build`/`actual_arm`/`task_class` wiring
    are **Phase 4** (§5 Phase 4, RV-2/3/4/5, JD-5). Phase 3 stops the silent
    *acceptance* of invalid ops; Phase 4 wires and unifies the joins.
  - *Corpus.* Phase 3 = **event types + emission at the owning function** plus the
    write-destination boundary and `task_id` sanitization. The **admission CLI
    verb / mine→manifest insertion** (CO-8) and the **run-path hook** that invokes
    `record_calibration_run` are **Phase 4**; **binding the official fence to the
    ledgered calibration status** (AN-2) is **Phase 5**. `corpus subset` already
    has a CLI path, so its subset-draw event wires end-to-end in Phase 3.
  - *Judge.* Phase 3 = JD-2, JD-3, and JD-13's connect-timeout classification. The
    deterministic-label ("assigned randomly per call") and `packet_sha256`-must-
    cover-the-rendered-message sub-parts of JD-13 are **Phase 5** (§5 Phase 5 re-
    lists JD-13 with JD-8 under packet fencing/provenance) — they are provenance/
    statistics correctness, not fail-closed, and folding them here would grow the
    slice past its concern.

### Confirmed at planning (recorded in the decisions ledger)

- **D-P3-1 (carry-forward #10) — provider-key fail-loud: RESOLVED `raise-missing-
  provider-key`** (jyang, recorded in `docs/design/specs/eval4.decisions.ndjson`
  as `EVAL-4-D-P3-1`). A named-but-absent provider key **fails the run loudly**,
  not silently drop (an unauthenticated arm is a biased comparison, a fail-closed
  defect). AC-8's "a value is never invented" is preserved — no value is fabricated;
  the run refuses when a *named* key cannot be resolved. *Blast radius:*
  `load_run_settings` raises `MissingProviderKeyError(name)` instead of returning
  `{}`; `test_ac8_provider_key_value_from_env_not_file:104` is rewritten —
  `absent ⇒ raises` replaces `absent ⇒ {}`, the one existing test that changes,
  per CLAUDE.md "changing a genuinely wrong test requires saying so explicitly and
  getting human agreement first." Owned by slice **3B**.

### Contract additions (additive, hash-chain-safe — recorded before the slice lands)

Per CLAUDE.md "public seams are contracts" and handoff §5, each new event **type**
is a versioned, hash-chained seam addition. All four are **additive event types**:
old ledgers simply lack them, so no existing chain is invalidated (the guarded case
— adding a *field* to an existing event — does not arise here). Each lands with a
`resolved` entry in the owning `docs/design/specs/evalN.decisions.ndjson` plus a
one-line migration note, mirroring Phase 1's `task_commitment` discipline, and a
typed constructor in `harness/ledger/events.py` (the `ledger-writes-only-via-events`
contract requires it):

| Event type | Owner | Slice | Migration note |
|---|---|---|---|
| `cant_analyze` (reason) | EVAL-6 | 3D | additive; refusal-only, no existing render affected |
| `task_admitted` | EVAL-8 | 3E | additive; complements `curation_approval` (approval ≠ admission) |
| `calibration_run` | EVAL-8 | 3E | additive; ledgers what mutable manifest JSON held |
| `subset_draw` | EVAL-8 | 3E | additive; ledgers the seeded stratified draw |

Judge, process, and review need **no new event type**: CANT_JUDGE rides
`judge_verdict.winner`, CANT_SCORE rides per-dimension `process_score` values, and
review's refusals are **loud precondition rejections** (raise), which is exactly
the spec's wording — EVAL-7 AC-4: *"The CLI refuses to reveal before a verdict +
integrity event is ledgered."* No `cant_reveal` type is introduced; a refused
reveal fails loudly and never silently corrupts. (Flagged as a scoping judgment
call: the review's "ledger refused reveals" phrasing is read as "make refused
reveals fail loudly and never silently succeed," consistent with the spec.)

## Phasing within Phase 3

Six slices. The five subsystem slices (3A–3E) are **independent** and may land in
any order; **3F (the registry sweep) lands last** because it depends on every stage
having registered an entrypoint. Each slice is one logical change (1–3 atomic
commits), ships a **reproduce-first** fault-injection test that fails today
(attempted op → zero events) and passes after (exactly one ledgered outcome), and
`make verify` is green before every commit. Line numbers are the current tree.

### 3A — Judge fail-closed · JD-2, JD-3, JD-13(timeout) · P1 (no new decision)
Move the whole judge operation inside the fail-closed envelope and correct reason
classification.
- **Provider lookup in the envelope (JD-2):** move `get_provider(config.model)`
  (`client.py:101`) *inside* the per-operation `try`, mapping `ProviderError` from
  an unknown prefix to `_cant("provider_error")` (or a dedicated
  `"unknown_provider"` reason) so a bad prefix lands exactly one `judge_verdict`
  with `winner=CANT_JUDGE`.
- **Catch response-shape escapes (JD-3):** widen the parse handler
  (`client.py:147`) to also catch `KeyError`/`IndexError` from provider content
  extraction (`openai.py:18`, `google.py:19-20`) → `_cant("provider_error")` for a
  transport/error body, `_cant("parse")` only for genuine JSON-parse failure of a
  well-formed content string. Fix anthropic's `.get()` chain (`anthropic.py:26-29`)
  to distinguish an error-shaped body from a real parse miss so it stops
  mislabeling `parse`.
- **Timeout classification (JD-13):** in `_http.py:25-28`, detect a connect-phase
  timeout (a `URLError` whose `.reason` is a `socket.timeout`/`TimeoutError`) and
  raise `ProviderTimeout`, so it classifies as `timeout` not `provider_error`.
- *Reason set:* introduce a small `CantJudgeReason` enum (mirroring
  `cant_grade`'s enumerated reasons) covering `identity_leak`, `timeout`,
  `refusal`, `provider_error`, `parse`, `judge_cant_judge`, `malformed` — replacing
  the ad-hoc literals at `client.py:134/142/144/146/149/159/182`.
- **Reproduce-first:** an unknown provider prefix (production `provider=None`)
  today raises with zero events → after, exactly one `judge_verdict`
  (`winner=CANT_JUDGE`, `reason=provider_error`); an error-shaped/safety-blocked
  200 for openai and google today raises `KeyError`/`IndexError` with zero events →
  after, one verdict with `reason=provider_error`; a connect-timeout classifies
  `timeout`. Extends `tests/test_eval2_client.py`.

### 3B — Process fail-closed (+ carry-forward #10) · PR-1, PR-2, PR-3, PR-4, PR-7, PR-8, D-P3-1 · P1
Make `score_trial_process` honor its "always appends exactly one event" docstring,
and validate human-recorded scores against the rubric.
- **Packet + provider inside the envelope (PR-2, PR-3):** move `build_process_
  packet` (`score.py:168`) and `get_provider` (`:178`) inside the fail-closed
  path; a `RedactionLeakError` → `CANT_SCORE(redaction_leak)` (mirroring judge's
  `identity_leak`, `judge/client.py`), an unknown provider prefix →
  `CANT_SCORE(provider_error)`.
- **Catch AttributeError-class parse escapes (PR-1):** guard `_parse_judge_scores`
  so a non-dict `scores` payload (`score.py:114-117`) → `CANT_SCORE(parse)` rather
  than escaping; the cleanest fix is to type-check `raw` is a dict before `.get`,
  and widen the handler at `:185` to the parse-failure family.
- **Reason enum (PR-4):** introduce a `CantScoreReason` enum —
  `redaction_leak`, `context_overflow`, `provider_error`, `timeout`, `refusal`,
  `parse`, `judge_declared`, `out_of_range`, `human_cant` — collapsing the
  `"unparsed"`/`"parse"` split and giving a judge-declared per-dimension
  `CANT_SCORE` its own reason (`judge_declared`) instead of `"unparsed"`.
- **Rubric validation (PR-7, PR-8):** `record_human_process_score` and
  `ProcessScore` validate `scores` against `rubric.dimension_ids` — reject unknown,
  duplicate, or missing dims loudly (a crash beats a silently degraded score);
  `process record` (`cli.py:47-51`) errors on a typoed/unknown key instead of
  mapping it to `human_cant`, and errors on keys that match no rubric dim.
- **Provider-key fail-loud (D-P3-1, confirmed):** `load_run_settings`
  (`settings.py:86-88`) raises `MissingProviderKeyError(name)` when a named key is
  absent from `env`; rewrite `test_ac8_...:104` (`absent ⇒ raises`). *This is the
  one existing test that changes; D-P3-1 is resolved `raise-missing-provider-key`.*
- **Reproduce-first:** `{"scores":[3,4,5]}` today escapes with zero events → after,
  one `process_score` with all dims `CANT_SCORE(parse)`; a redaction leak → one
  event `CANT_SCORE(redaction_leak)` (today zero); an unknown provider prefix →
  one event; `process record` with a misspelled dim id raises (today silently
  `human_cant`); a `ProcessScore` with an unknown/duplicate dim raises; a
  named-but-absent provider key raises (today silently `{}`). Extends
  `tests/test_eval9_process.py`, `tests/test_eval4_runconfig.py`.

### 3C — Review fail-closed · RV-1, RV-8, RV-9 · P1 (no new decision)
Convert silent acceptance of invalid review operations into loud refusals; the
firewall functions become genuine precondition gates.
- **Refuse post-reveal / duplicate verdicts (RV-1):** `record_human_verdict`
  (`record.py:37-60`) reads the ledger and refuses a second verdict for a
  comparison, and refuses any verdict after that comparison's reveal — raising a
  typed `ReviewError`.
- **Refuse duplicate reveals (RV-8):** `reveal_comparison` (`record.py:63-97`)
  refuses when a `reveal` for the comparison already exists.
- **Existence-check comparison ids (RV-9):** `review record` (`cli.py:28-61`) and
  `record_human_verdict` refuse a `comparison_id` that no `judge_verdict` in the
  ledger carries — a mistyped id fails loudly instead of recording a verdict that
  silently drops from kappa.
- *Deliberately deferred to Phase 4* (noted so the reviewer sees the boundary):
  unifying the first-vs-last verdict join (`record.py:87` vs `sample.py:150`),
  excluding `CANT_JUDGE` from kappa, requiring integrity for calibration, and the
  `task_class`/`actual_arm` CLI wiring.
- **Reproduce-first:** verdict → reveal → second verdict today accepts the second
  (unblinded) verdict → after, raises; a duplicate reveal today appends a second
  reveal → after, raises; `review record --comparison-id BOGUS` today records a
  droppable verdict → after, raises. Extends `tests/test_eval7_review.py`.

### 3D — Analyze fail-closed · AN-3 · P1 (new event type `cant_analyze`)
Give the analyze stage the one thing it lacks — a refusal event — and stop the
crash-window between files and provenance.
- **`cant_analyze(reason)` event (new type):** add the typed constructor to
  `events.py` and register it; reasons enumerate the official-path refusals —
  `calibration_incomplete`, `corpus_mismatch`, `provenance_invalid`, `stale_head`,
  `disclosure_missing`, `unsupported_metric` (the `AnalyzeError` subclasses the CLI
  can hit: `CalibrationIncompleteError`, `UnregisteredOfficialError`,
  `ProvenanceError`, `DisclosureError`, and `AnalyzeError` from `compute_findings`).
- **Ledger the refusal (AN-3):** wrap the render in the analyze CLI
  (`cli.py:39-85`, currently untried); an `AnalyzeError` → emit exactly one
  `cant_analyze(reason)` and exit non-zero (no findings files written).
- **Event before (or atomically with) files:** on success, compute findings +
  `findings_sha256` in memory, emit `findings_rendered` **before** writing the
  findings files (`cli.py:73-74` currently precede the event at `:77-84`) — an
  interrupted render then leaves a provenance record with no orphan artifacts
  rather than unprovenanced files (analyze is a pure function of `(ledger, seed)`,
  so the render is re-derivable from the event). *(Minor ordering judgment call,
  flagged for veto.)*
- **Reproduce-first:** an official render against a subset-only corpus today
  escapes with zero events → after, exactly one `cant_analyze(calibration_
  incomplete)`; a stale-head render → one `cant_analyze(stale_head)`. Extends
  `tests/test_eval6_analyze.py`.

### 3E — Corpus fail-closed + audit · CO-1, CO-4, CO-6, CO-9 · P1 (new event types)
Enforce the corpus boundary on writes, sanitize registry-supplied ids, and put
admission / calibration / subset draws on the chain.
- **Write-destination boundary (CO-1):** enforce the internal-corpus boundary on
  the actual destination path in `save()` (`registry.py:215-221`) and `corpus mine
  --out` (`cli.py:83`) — refuse writing an internal corpus/candidate into
  `INSTRUMENT_ROOT` (a resolved-path containment check), not just validate the
  declared string. Flips the §6 "Internal corpora never enter the instrument repo"
  row.
- **Sanitize `task_id` (CO-6):** validate `task_id` at the `public.py` import seam
  (`:101`, `:136-137`) — reject path separators / `..` / absolute paths before it
  reaches `tasks_dir / f"{task_id}.json"`; add a `TaskEntry.task_id` validator so a
  traversal id is unrepresentable. (Dataset-level checksum pinning stays a noted
  Phase-4/5 item — CO-6's second clause is not fail-closed.)
- **`task_admitted` event (new type):** `admit_task` (`admit.py:86`) emits exactly
  one `task_admitted` when its preconditions (chain-verified curation approval +
  clean baseline) hold, instead of only flipping in-memory status. The admission
  CLI verb / mine→manifest insertion (CO-8) stays **Phase 4**; Phase 3 makes the
  admission *decision* leave a ledger event when the function runs (tested at the
  function seam, as the current admission tests already invoke it).
- **`calibration_run` event (new type, CO-4):** `record_calibration_run`
  (`registry.py:186-198`) emits exactly one `calibration_run` event in addition to
  updating the manifest, so calibration status is chain-anchored rather than only
  hand-editable JSON. (The run-path hook and fence-binding are Phase 4/5.)
- **`subset_draw` event (new type, CO-9):** the `corpus subset` CLI path
  (`cli.py:50-66`) emits exactly one `subset_draw` event recording the seeded
  stratified draw; keep `calibration_subset` (`stratify.py`) a **pure** function
  (determinism directive) and emit at the CLI/registry seam via an `events.py`
  constructor. Also prune dropped-task cache blobs on re-import (`public.py:135-139`)
  to close the manifest/cache drift.
- **Reproduce-first:** saving an internal manifest / `mine --out` into the
  instrument repo today succeeds → after, refused; a `task_id` of `"../../escaped"`
  today writes outside the cache → after, refused; `admit_task` today writes no
  event → after, one `task_admitted`; `record_calibration_run` → one
  `calibration_run`; `corpus subset` → one `subset_draw`; a re-import that drops a
  task leaves no stale blob. Extends `tests/test_eval8_corpus.py`,
  `tests/test_eval8_commit.py`.

### 3F — one-event property sweep + PL-14 · XC-3, PL-14 · P1 (structural exit)
Make the property *mechanically* cover every ledgered operation and fold in the
two-event plan path.
- **Register an entrypoint per operation** (each stage module self-registers at
  import, mirroring `plan/lock.py:189-195`): `judge`, `process`, `review-record`,
  `review-reveal`, `analyze`, `corpus-admit`, `corpus-calibration-run`,
  `corpus-subset-draw`. Each entrypoint fn runs **one** representative operation
  against a prepared fixture and appends exactly one event (success or fail-closed
  refusal). Combined with the existing `plan-lock`, `run-trial`, `grade-trial`,
  this brings the sweep to every stage that performs a ledgered operation.
- **Discover, don't hardwire (XC-3):** the sweep imports every stage module (so
  each self-registers) and asserts the registry covers an **explicit expected set**
  of entrypoint names — a stage that forgets to register now *fails* the test
  (fails closed) instead of the current non-emptiness check
  (`test_eval3_property.py:21`).
- **PL-14 — one event for the ack path:** register a `plan-lock-underpowered`
  entrypoint that exercises the acknowledged-underpowered branch and assert it
  emits exactly one event. Fold the two `record_*` calls (`lock.py:124`, `:136`)
  into a single ledgered lock outcome — the acknowledgment becomes part of the one
  `experiment_locked` event (an additive `acknowledged_underpowered` field/section
  on the lock event) rather than a second event, so the "exactly one event per
  operation" property holds for the documented underpowered path. *(This edits the
  shape of an existing hash-chained event → a contract change: it gets an
  EVAL-3 decisions-ledger entry + migration note, and the genesis/`assert_lock`
  tests are checked, since the lock is the chain genesis.)*
- **Reproduce-first:** the strengthened sweep fails today (only 3 of N entrypoints
  registered; ack path emits two) and passes after every stage registers and the
  ack path emits one. Extends `tests/test_eval3_property.py`.

## Phase 3 exit criteria (all testable)

Restating the review's §5 Phase 3 exit against the slices:

1. **A fault-injection test per stage** (judge 3A, process 3B, review 3C, analyze
   3D, corpus 3E) proves an attempted operation that used to escape with zero
   events now emits exactly one refusal/outcome event.
2. **The one-event property sweep** (`test_eval3_property.py`) covers **every
   ledgered stage operation** (entrypoints registered + discovered, expected-set
   asserted), and the acknowledged-underpowered path (PL-14) emits **one** event.
3. **`make verify` green**; the three import-linter contracts stay green; each new
   event type (`cant_analyze`, `task_admitted`, `calibration_run`, `subset_draw`)
   and the PL-14 lock-event reshape carries a decisions-ledger entry + migration
   note.
4. **The §6 readiness-gate row "Fail closed; no operation without a ledger event"**
   flips from `enforced_by: review` to enforced by the all-stage property sweep;
   **"Internal corpora never enter the instrument repo"** flips via the 3E write-
   destination enforcement.

## Working method (per CLAUDE.md)

- **Reproduce before fixing:** every slice ships a fault-injection test that fails
  first (attempted op → zero events / silent accept) and passes after (exactly one
  event / loud refusal). No fixes by inspection.
- **`make verify` green** before each commit; never weaken/skip a test to get
  green. The only existing test that changes is `test_ac8_provider_key_value_from_
  env_not_file` (3B), under the resolved **D-P3-1** sign-off.
- **Single responsibility / boundaries:** each fix lands in the subsystem that owns
  the concern; the `harbor-confined-to-seam`, `grade-has-no-llm-clients`, and
  `ledger-writes-only-via-events` contracts stay green. New events flow only
  through typed `events.py` constructors (the third contract requires it); the
  subset-draw event is emitted at the CLI/registry seam so `calibrate_subset` stays
  a pure deterministic function.
- **Contract discipline:** the four additive event types are additive (old ledgers
  lack them, no chain invalidated); the PL-14 lock-event reshape is the one change
  to an *existing* event and gets a full migration note + genesis-test check.
  Record each in the owning `evalN.decisions.ndjson` before its slice lands.
- **Determinism / fail loudly:** no wall-clock, unseeded randomness, or new network
  seams; refusals say what was wrong and where; a crash beats a silently wrong
  grade/score/verdict/render.
- **Judgment calls flagged for cheap veto:** the review "ledger refused reveals" →
  "raise loudly" reading (Decisions); the analyze event-before-files ordering (3D);
  the JD-13 scope split (Decisions). Direction-setting choices beyond D-P3-1 get a
  check-in.

## Verification

- `uv run pytest -m "not docker" -q` green throughout (current post-Phase-2
  baseline **283 passed, 3 deselected**); Phase 3 adds reproduce-first tests per
  slice and is almost entirely non-Docker (judge/process/review/analyze/corpus).
- `make verify` (full gate + the three import contracts) green before each commit.
- `uv run pytest --ac-report` recomputes AC coverage after new AC-mapped tests
  (`test_ac<N>_*`) land.
- Manual sanity: drive each fail-closed path once (unknown judge provider,
  list-shaped process scores, duplicate reveal, official render on a subset-only
  corpus, a traversal `task_id`) and confirm exactly one ledgered event or a loud
  refusal — no zero-event escape.

## Scope of this approval

Approving authorizes executing **Phase 3 (3A–3F)** as atomic commits with `make
verify` green, adding the four additive event types and the PL-14 lock-event
reshape with decisions-ledger entries + migration notes, and the provider-key
fail-loud change (rewriting the one pinning test) under the now-resolved **D-P3-1**.
All slices 3A–3E are decision-free and can start on approval; 3F lands last. I'll
report at natural breakpoints and check in before Phase 4 (connective tissue). No
PR unless you ask.
