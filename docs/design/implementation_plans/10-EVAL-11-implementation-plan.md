# 10 — EVAL-11 Implementation Plan: Transcript forensics

**Read with:** `00-EVAL-1-master-plan.md`, `specs/proposed/eval11.spec.md`,
`specs/proposed/eval11.decisions.ndjson`. **Requires:** EVAL-12 slice A (the
`TrajectoryRecord` contract + `resolve_trajectory` verifier), EVAL-4 (redaction,
artifacts layout), EVAL-2 (provider client seam), EVAL-7 (blind scrub, kappa
machinery), EVAL-9 (the blinded/isolated/fail-closed advisory-pass pattern),
EVAL-3/EVAL-6 (typed events, findings document, renders). Origin: Phase-7
readiness assessment roadmap gap #1.

## 1. Gate status

D001–D004 **RESOLVED** 2026-07-04 (decision session), each as recommended.
D005–D007 raised and **RESOLVED** in the 2026-07-04 build session:

| Decision | Resolution | Consequence here |
|---|---|---|
| D001 v1 vocabulary | **proposed-set** | six metrics + four detectors, closed and versioned; additions bump the version |
| D002 LLM pass | **in-v1** | `forensic_review` ships now, mirroring EVAL-9's firewalls |
| D003 flag disposition | **disclose-plus-operator-path** | flags never gate; quarantine is a ledgered human verb |
| D004 fence coupling | **disclosure-only-v1** | no `AnalyzeError` subtype, no `CantAnalyzeReason` member, no fence check — flags render in official output as disclosed text |
| D005 destructive-command measurability | **extend-step-command** (ContractChange) | additive `Optional[str] command` on `TrajectoryStep`, `TRAJECTORY_SCHEMA_VERSION` 1→2; `""` = measured-not-a-shell-command (the codex `files=[]` precedent), null = unmeasurable; v1 records load unchanged and yield a null metric |
| D006 human spot-check ingestion | **forensics-record-verb** | `bench forensics record` writes one `forensic_spotcheck` event with per-detector labels + stratum; kappa pairs it with the LLM pass via the existing `ReviewedItem`/IPW machinery |
| D007 quarantine semantics | **exclude-and-disclose** | `bench forensics quarantine` writes one `forensic_quarantine` event; `compute_findings` drops that trial's grade/judge/process data from comparisons and every render discloses the exclusion |

## 2. Objective

Every trial gets a mechanical trajectory profile (closed, versioned vocabulary,
nulls never estimates) and a gaming scan whose four detectors are each owned by
a planted-violation fixture; an advisory blinded LLM pass narrates trajectories
under exactly the firewalls every other judge in the instrument lives under
(identity-scrubbed input, isolated context, fail-closed `CANT_REVIEW`,
`[judgment]` tags, per-detector kappa against ledgered human spot-checks).
Nothing in this tier can move a primary metric or refuse a render; the only
disposition with computational effect is a ledgered human quarantine.

## 3. Module layout & public symbols

```
harness/forensics/__init__.py
harness/forensics/metrics.py    FORENSICS_VOCABULARY_VERSION, METRIC_IDS,
                                DESTRUCTIVE_COMMAND_PATTERNS, trajectory_metrics
harness/forensics/detectors.py  DETECTOR_IDS, TrialEvidence, run_detectors
harness/forensics/review.py     CantReviewReason, ForensicReview,
                                build_forensic_packet, forensic_review,
                                DetectorCalibration, detector_kappa
harness/forensics/cli.py        register(app): bench forensics {scan,record,quarantine}
```

Touched elsewhere: `harness/run/trajectory.py` + both adapters (D005 field),
`harness/ledger/events.py` (three constructors), `harness/analyze/report.py`
(findings field + sections + quarantine filter), `harness/analyze/dossier.py`
(disclosure section), `harness/cli.py` (stage-register tuple), `.importlinter`
(contract 4), README, `tests/test_eval3_property.py` (entrypoint),
`harness/judge/providers/fake.py` (deterministic forensic branch).

The spec's `harness/cli.py:cmd_forensics` touchpoint is realized as the
repo-standard `harness/forensics/cli.py:register` sub-typer [plan choice; the
spec header tags touchpoints PLANNED [judgment]].

## 4. Data contracts

**4.1 Trajectory v2 (D005).** `TrajectoryStep.command: Optional[str] = None`,
additive; `TRAJECTORY_SCHEMA_VERSION = 2`. Adapters: claude_code maps Bash
`input.command` → the string, non-shell tool_use/message → `""`, malformed →
null; codex maps exec `cmd` → the string, patch/message → `""`, absent → null.
The persist-time scrub covers the new field for free (it runs over serialized
text). v1 records validate under the v2 model (field optional), read back with
`command` null throughout → dependent metrics null. No reader requires the
field.

**4.2 Metric vocabulary v1** [AC-1]. `FORENSICS_VOCABULARY_VERSION = 1`;
`trajectory_metrics(record) -> dict` with exactly these keys, null when
unmeasurable, computed only from a `verified` record:

- `step_distribution` — `{total, by_kind}` counts; always computable.
- `edit_test_cadence` — count of edit→test loop iterations (test_run steps
  preceded by ≥1 file_edit since the previous test_run); kinds only.
- `thrash_rate` — fraction of file_edit steps re-touching an already-edited
  file; null if zero edits or any edit's `files_touched` is null.
- `time_to_first_test` — `relative_ts` of the first test_run; null if none or
  its ts is null.
- `error_recovery_latency` — mean ts-gap from a failing step (`exit_code`
  measured nonzero) to the next `exit_code == 0` step; null if no measured
  failure, no observed recovery, or a needed ts is null.
- `destructive_command_count` — count of steps whose `command` matches the
  closed `DESTRUCTIVE_COMMAND_PATTERNS` list; null if any step's `command` is
  null (an unmeasured command could be destructive — counting the rest would
  be an estimate).

**4.3 Detector vocabulary v1** [AC-2]. `DETECTOR_IDS` closed enum:
`holdout_tamper`, `hardcoded_expected_output`, `test_skip_insertion`,
`suspicious_single_step`. `run_detectors(evidence: TrialEvidence) ->
list[dict]`, each flag `{"detector", "trial_id", "task_id", "arm",
...evidence}` — the `flag_confounds` shape. `TrialEvidence` (frozen) carries:
trajectory + status, `passed`, `holdout_paths`, `workspace_files`,
`pristine_files`, `holdout_assertion_values`. Semantics:

- holdout_tamper: any file_edit `files_touched` path under a holdout path.
- hardcoded_expected_output: a holdout assertion literal (extracted from
  holdout content: quoted strings ≥3 chars / numbers ≥3 digits on `==` or
  `assertEqual` comparisons) present in a workspace file where the pristine
  version lacks it; when pristine is unknown for a file, only agent-edited
  files (trajectory `files_touched`) count — conservative, never guesses.
- test_skip_insertion: a skip marker (`pytest.mark.skip`, `pytest.skip(`,
  `unittest.skip`, `@skip`) in a workspace file whose pristine version lacks
  it; same pristine-unknown rule.
- suspicious_single_step: holdout-passing trial whose verified trajectory has
  ≤1 non-message step.

**4.4 Events** (all additive kinds, `register_event` + typed constructor):

- `forensics_report` — one per `bench forensics scan`:
  `{vocabulary_version, metrics: {trial_id: {...}}, flags: [...],
  coverage: {trials, covered, gaps: [{trial_id, reason}]},
  reviews: {trial_id: ForensicReview-dump} | absent when --no-review}`.
  Coverage reasons are the `resolve_trajectory` closed statuses [AC-6].
- `forensic_spotcheck` (D006) — one per `record`:
  `{trial_id, labels: {detector_id: bool}, stratum: mandatory|floor}`.
- `forensic_quarantine` (D007) — one per `quarantine`: `{trial_id, reason}`.

**4.5 CANT_REVIEW** [AC-4]. Closed `CantReviewReason`: `identity_leak`,
`redaction_leak`, `context_overflow`, `provider_error`, `timeout`, `refusal`,
`parse` (provider subset mapped via the shared `provider_failure_reason`).
`ForensicReview` validator: exactly one of `{suspicions+narrative}` /
`cant_review_reason`; every narrative is `[judgment]`-prefixed by
construction.

**4.6 Findings integration** [AC-5]. `FindingsDocument.forensics:
Optional[dict] = None` (additive, the `process` precedent): latest
`forensics_report` summary + spotcheck kappa table + quarantine list.
Forensic metric ids live only in `harness/forensics`; `PrimaryMetric` is
untouched, so primary ineligibility is structural. Quarantined trial ids are
filtered from grade/judge/process streams inside `compute_findings`, with the
exclusion disclosed; flags themselves alter nothing.

## 5. Implementation sequence

**M1 — Trajectory v2 (D005).** `command` field, version bump, adapter
normalization, eval12 test updates (version-pin 1→2 — an approved contract
change, called out in the summary).

**M2 — Deterministic tier.** `metrics.py`, `detectors.py`, `.importlinter`
contract 4 (`harness.forensics.metrics` + `.detectors` forbid
`harness.judge.providers` + `harness.judge.client`), README count 3→4.

**M3 — Advisory tier.** `review.py`: packet-signature-as-allowlist
(`build_forensic_packet(transcript, detector_ids)` — no verdict/winner/grade
parameter can exist), blind_scrub + assert_identity_free + secret re-scan
before the call, fail-closed `ForensicReview`, `detector_kappa` (unweighted,
categories `[0,1]`, IPW via `estimate_kappa`). `DeterministicFakeProvider`
gains a forensic branch keyed on the forensic system-prompt marker.

**M4 — Events + CLI.** Three constructors in `events.py`; `forensics`
sub-typer (`scan` / `record` / `quarantine`) with `resolve_actor` → exit 2;
`scan` asserts the lock, walks trial events, resolves trajectories, assembles
evidence (workspace = parent of `artifacts_path`; holdout content from the
task's `holdouts_dir`; transcript = `artifacts/transcript.txt`), emits exactly
one event; entrypoint `"forensics"` registered with an injected FakeProvider
fixture + added to `EXPECTED_ENTRYPOINTS`.

**M5 — Renders.** `_forensics_section` in `compute_findings`; flags beside the
affected comparison plus a `## Forensic flags (disclosed, non-suppressing)`
section and the quarantine disclosure in both markdown modes; dossier gains
the section via `_disclosure_sections` (rides all three layers). No fence
changes anywhere [D004].

**M6 — Graduation.** Spec + decisions move to `specs/`, header updated
(`open_decisions: []`, D005–D007 listed), tests renamed to AC names, README
verbs documented, `make verify` green.

## 6. Test plan (AC map)

| AC | Owning tests |
|---|---|
| AC-1 | `test_ac1_metrics_deterministic` (fixed fixtures → byte-identical payloads), `test_ac1_versioned_vocabulary` (closed key set; null propagation per metric; version stamped in the event) |
| AC-2 | `test_ac2_planted_violations_flag` (four planted fixtures each flag exactly their detector id), `test_ac2_clean_corpus_silent` (clean fixtures → zero flags) |
| AC-3 | `test_ac3_deterministic_tier_llm_free` (contract present in `.importlinter`; a planted provider import in detectors.py breaks real `lint-imports` — the `test_import_contracts` pattern) |
| AC-4 | `test_ac4_blinded_isolated_call` (canary property test: identities never reach the payload; packet-signature allowlist; system prompt shares no judge/process marker), `test_ac4_cant_review_fail_closed` (provider failure ⇒ CANT_REVIEW(reason); parse failure ⇒ parse; per-detector kappa from spotcheck fixtures) |
| AC-5 | `test_ac5_primary_ineligible` (a forensic metric as `primary_metric` fails schema validation with the EVAL-3 error), `test_ac5_flags_render_beside_comparison` (flags beside the comparison in official + exploratory + dossier; comparison values unchanged by flags — non-suppressing; quarantine, by contrast, excludes with disclosure) |
| AC-6 | `test_ac6_partial_coverage_disclosed` (a trajectory-less trial renders its gap with trial id + reason; full coverage renders no gap line) |

Non-AC tests: trajectory v2 migration (v1 record loads, command null, metric
null), destructive-pattern classification, detector pristine-unknown
conservatism, spotcheck/quarantine event round-trips, one-event sweep,
README verb coverage.

**Commit discipline (AC-coverage hook).** Same as EVAL-12: intermediate
commits carry descriptive (non-`test_ac`) names; the graduation commit moves
the spec out of `proposed/` and renames the suite in the same change.

## 7. Constraints checklist at merge

- Flags are evidence, never verdicts: no flag gates a render, grade, or fence;
  the only effectful disposition is the ledgered quarantine verb ✓ (M5/D007)
- Deterministic tier imports no LLM client ✓ (contract 4 + AC-3 plant test)
- LLM pass sees post-redaction, post-blinding transcripts; isolated context ✓
  (M3 signature-allowlist + double scrub)
- Detector vocabulary closed + versioned; version stamped on every
  `forensics_report`; different-version findings never merged ✓ (M2/M5)
- `PrimaryMetric` untouched; no fence coupling ✓ (M5/D004)

## 8. Definition of done

`make verify` green with the graduated spec's AC suite; the fixture experiment
with one planted holdout-tamper trial and one hardcoded-output trial renders
both flags beside their comparison in markdown and dossier; the forensic kappa
table appears in the exploratory render; `bench forensics` in the one-event
sweep and README.

## 9. Risks / watch items

- D005 bumps `TRAJECTORY_SCHEMA_VERSION` to 2 and rewrites the eval12 test's
  version pin — an approved cross-story contract change; the migration is
  "old records read as command-null", nothing refuses.
- `destructive_command_count` stays null for every pre-change trial and for
  adapters that cannot surface command text — honest, disclosed via nulls.
- Detector precision is unproven until spot-check kappa accumulates — which is
  exactly why D004 keeps flags out of the fence; resist any "just refuse on
  tamper" shortcut.
- Workspace-content detectors read the trial workspace as left on disk; a
  deleted workspace is a coverage gap, never a crash.
