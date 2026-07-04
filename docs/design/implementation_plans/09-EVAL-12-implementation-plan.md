# 09 — EVAL-12 Implementation Plan: Trajectory capture + comparison dossier

**Read with:** `00-EVAL-1-master-plan.md`, `specs/proposed/eval12.spec.md`,
`specs/proposed/eval12.decisions.ndjson`. **Requires:** EVAL-4 (run seam,
redaction, adapters, §7.8 telemetry honesty), EVAL-3 (typed ledger events,
one-event property), EVAL-6 (findings document, renders, pre-registration
fence), EVAL-8 (corpus fence), Phase-7 (selfcheck gate, rubric commitment).
Origin: 2026-07-04 observability directive.

## 1. Gate status

All four local decisions **RESOLVED** 2026-07-04, each as recommended:

| Decision | Resolution | Consequence here |
|---|---|---|
| D001 trajectory_sha contract change | **approve-additive-field** | top-level additive field on the `trial` event, `task_commitment`/`rubric_sha256` insert-only-when-present pattern; absent = pre-EVAL-12 trial, no reader may require it |
| D002 rendering technology | **jinja2-inline-svg** | jinja2 (already a pinned dep) + inline SVG; interaction via native `<details>` collapse — zero JS, inside D002's "minimal inline JS for collapse/toggle only" envelope |
| D003 LLM narrative | **excluded-v1** | verdict layer is computed-only templates, enforced by a template-inventory test |
| D004 verb surface | **artifact-of-analyze** | dossier is a third artifact of `bench analyze`; same single `findings_rendered` event, no new verb, no new entrypoint |

**Sequencing note.** EVAL-10/11 are not built. The spec's own AC text makes
their integrations conditional ("contamination asymmetry once EVAL-10 lands",
"forensic flags once EVAL-11 lands"), so this story lands both slices now:
slice A is the substrate EVAL-11 consumes; the dossier's forensic/contamination
sections appear only when their events exist (today: never), leaving explicit
seams rather than placeholders.

## 2. Objective

One versioned trajectory record per trial, captured at the run seam under the
same honesty rules as all telemetry (post-redaction, fail-loud, null-never-
estimated); and one self-contained three-layer HTML dossier per experiment that
answers "A or B, how sure, at what cost" without ever saying more than the
findings compute — the prettiest render obeys exactly the fence the plainest
one does.

## 3. Module layout & public symbols

```
harness/run/trajectory.py     TrajectoryRecord, TrajectoryStep, STEP_KINDS,
                              persist_trajectory, load_trajectory,
                              TrajectoryCorruptError, TRAJECTORY_FILENAME
harness/adapters/base.py      Adapter.normalize_trajectory (abstract)
harness/adapters/claude_code.py / codex.py   per-platform normalization
harness/analyze/timeline.py   trial_timeline
harness/analyze/dossier.py    render_dossier, VERDICT_TEMPLATES
```

Owns no new verb and no new subsystem. `.importlinter` contract 1 gains
`harness.run.trajectory` as a source module (the contract's run submodules are
enumerated, so a new module must opt in); the contract *count* is unchanged and
the README's "3 import-linter contracts" stays true.

## 4. Data contracts

**4.1 `TrajectoryRecord` v1** [AC-1]: `{schema_version: 1, trial_id, platform,
steps: [TrajectoryStep]}`, `extra="forbid"`. Step schema:
`{kind: tool_call|file_edit|test_run|message (closed), relative_ts, tokens,
cost, files_touched, exit_code}` — every non-kind field `Optional`; a null
means *unmeasurable by this adapter*, never estimated [§7.8, EVAL-4-D004].
Serialized as canonical JSON (`sort_keys`, compact separators — the ledger's
own canonicalization convention) so the artifact is byte-deterministic and the
sha well-defined.

**4.2 The artifact + sha binding** [AC-1, D001]: the record persists as
`<artifacts>/trajectory.json` beside `transcript.txt`/`agent_log.json`;
`sha256` over the exact persisted bytes rides the `trial` event as a top-level
additive `trajectory_sha` (insert-only-when-present in `record_trial`,
mirroring `rubric_sha256` on the lock event). Absent field = pre-EVAL-12 trial
or honest-absent trajectory; **no reader may require it** (D001 migration).

**4.3 Capture honesty** [AC-2]: capture input is the **post-redaction**
`agent_log.json` (redact_artifacts has already scrubbed it on disk), and the
serialized record passes `redact_text` (with the seam's injected-key extra
patterns) once more before persisting — the same double-door the workspace
gets. An unparseable input, an unwritable artifact, or a scrub that breaks the
record's structure raises `TrajectoryCorruptError`, which the scheduler maps to
`trial_infra_failed(trajectory_corrupt)` (the `telemetry_corrupt`/
`redaction_error` precedent). An adapter that finds no trajectory content
returns `None` → no artifact, no sha (honest absent ≠ empty steps list).

**4.4 Dossier** [AC-3–AC-7]: single self-contained HTML, three layers
(verdict / analyst / auditor) in one artifact,
`findings.<mode>.dossier.html`, written by the same `run_analyze` invocation
that writes the markdown, before/after the same single `findings_rendered`
event ordering (event first, files after — AN-3). Fence parity by
construction: `render_dossier` delegates to `render_markdown` for validation,
so every current and future fence check applies identically and a refusing
ledger raises the same `AnalyzeError` subtype → same `cant_analyze` reason.
Verdict-layer sentences come exclusively from the module-level
`VERDICT_TEMPLATES` registry whose placeholders a test inventories against the
allowed [computed] findings fields [AC-5, D003].

## 5. Implementation sequence

**M1 — Trajectory contract.** `harness/run/trajectory.py`: models, canonical
serialization, `persist_trajectory` (scrub → validate → write → read-back →
sha), `load_trajectory` (corrupt ⇒ `TrajectoryCorruptError`).

**M2 — Adapter normalization.** `Adapter.normalize_trajectory(native_log) ->
Optional[list[TrajectoryStep]]`; claude_code maps its message stream (text →
`message`, tool_use → `file_edit` via a closed tool-name table else
`tool_call`; per-step timings/exit codes unmeasurable ⇒ null); codex maps its
event list (native command classification drives `test_run` vs `tool_call`,
measured `exit_code`/`relative_ts`; per-step tokens/cost unmeasurable ⇒ null).
The two platforms' nulls are deliberately asymmetric — the codex-cost
precedent, per field.

**M3 — Seam + ledger wiring.** `run_trial` captures after `redact_artifacts`;
`TrialRecord` gains a transport-only `trajectory_sha`; `record_trial` hoists it
to the top-level additive event field (popping it from the embedded record so
the `trial_record` payload shape is unchanged for every pre-EVAL-12 reader);
`_PER_TRIAL_REASONS` gains `TrajectoryCorruptError → "trajectory_corrupt"`.

**M4 — Timeline extraction.** `harness/analyze/timeline.py:trial_timeline`
reads trial events + trajectory artifacts into per-task, per-arm, per-trial
rows with an explicit trajectory status (`verified | absent | missing_artifact
| sha_mismatch | corrupt`) — partial coverage is data, never silence; null
telemetry stays null for the renderer to phrase as "not measured".

**M5 — Dossier renderer.** `harness/analyze/dossier.py`: jinja2 `DictLoader`
templates (string constants — no package-data assets), autoescaped; inline
SVG per-task delta chart and per-trial timeline strips computed from findings
+ timeline fields with fixed-precision coordinates; watermark on every layer
and section when exploratory; ADVISORY banner + all disclosure blocks
(confounds, integrity, tier, ledger consistency, overrides, rubric caveat,
process disclosure) repeated in every layer; auditor layer surfaces provenance,
chain status from the findings' `verify`-derived `chain_ok`, and selfcheck
status.

**M6 — CLI + docs.** `run_analyze` renders the dossier inside the AN-3
envelope and writes it beside the markdown; README documents the artifact;
one-event sweep and README verb test unchanged by construction.

## 6. Test plan (AC map)

| AC | Owning tests |
|---|---|
| AC-1 | `test_ac1_normalized_versioned_record` (both platforms → one schema, nulls honest, version stamped), `test_ac1_sha_ledgered_additive` (event sha == artifact bytes; absent field on trajectory-less trial; no reader requires it) |
| AC-2 | `test_ac2_capture_post_redaction` (hypothesis property: planted canaries never reach the persisted record), `test_ac2_corrupt_fails_closed` (unwritable artifact ⇒ `trial_infra_failed(trajectory_corrupt)`; absent ≠ empty) |
| AC-3 | `test_ac3_self_contained_deterministic` (no external URI schemes; two renders byte-identical) |
| AC-4 | `test_ac4_fence_parity` (fence-refusing ledger refuses dossier with the same `cant_analyze` reason as markdown), `test_ac4_watermark_every_layer` (watermark in all three layers; ADVISORY banners in every layer) |
| AC-5 | `test_ac5_verdict_layer_computed_only` (template inventory: every verdict sentence template interpolates only findings fields; null renders the pre-registered phrasing, never "no difference"), `test_ac5_uncertainty_always_present` (CI, MDE, N in the verdict layer; underpowered caveat renders) |
| AC-6 | `test_ac6_side_by_side_timelines` (both arms' trials for a task in one view; chain status matches verify), `test_ac6_null_never_zero` (null telemetry renders "not measured", never 0) |
| AC-7 | `test_ac7_rides_analyze_one_event` (dossier beside markdown from one `bench analyze` invocation with exactly one `findings_rendered`; README documents the artifact) |

**Commit discipline (AC-coverage hook).** The hook enforces per-story AC
coverage all-or-nothing the moment `eval12.spec.md` lives under `specs/`, and
aborts collection if `test_ac*` functions exist in a `test_eval12_*` file while
the spec is still `proposed/`. So: intermediate commits ship each slice **with
its behavior tests under descriptive (non-`test_ac`) names**, every commit
green under `make verify`; the final graduation commit moves the spec out of
`proposed/` and renames the suite to the AC-bound names in the same change —
exactly the "graduates in the same commit as the story's first AC tests"
posture the spec header pre-registers.

## 7. Constraints checklist at merge

- No network references / external assets in the dossier ✓ (AC-3 grep + CSP-free
  static HTML; archivable air-gapped)
- No LLM-generated narrative; verdict layer computed-only ✓ (M5 registry +
  AC-5 inventory test; permanent v1 constraint)
- `trajectory_sha` additive; absent = pre-EVAL-12; no reader requires it ✓
  (M3; readers use `.get`, never refuse)
- Unmeasurable telemetry null end-to-end, rendered "not measured" ✓ (M2/M4/M5)

## 8. Definition of done

`make verify` green including the graduated spec's full AC suite; the fake-
engine e2e pipeline renders `findings.exploratory.dossier.html` beside the
markdown with one `findings_rendered` event; two renders byte-identical; an
official render refuses the dossier exactly when it refuses the markdown; the
README documents the artifact.

## 9. Risks / watch items

- The persist-time scrub re-validates the record after redaction; a secret
  that *is* valid JSON structure could in principle survive as `[REDACTED]`
  inside a path string — that is the intended behavior (content scrubbed,
  structure intact), and a scrub that breaks structure fails closed.
- Per-task rows for the analyst layer are recomputed at render time from the
  ledger (no `FindingsDocument` schema change); if EVAL-11 later wants them in
  findings, that is a separate additive decision.
- Timelines read `artifacts_path` from trial records; the dossier must render
  trial ids, never absolute paths (a path is machine-specific noise and a
  byte-determinism hazard across environments, though not within one render).
- Resist creep: no live dashboards, no LLM summaries, no cross-experiment
  dossiers, no theming (spec out-of-scope list).
