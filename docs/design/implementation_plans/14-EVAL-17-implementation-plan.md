# EVAL-17 implementation plan — authoring surface

Spec: `docs/design/specs/eval17.spec.md`; decisions D001–D004 resolved in
`eval17.decisions.ndjson` (new verb, text-pane fidelity, no stage
execution, plain-directory drafts).

## M1 — harness/author subsystem (AC-1..AC-3)

- `server.py`: route posture enforced by shape — previews are GETs over the
  saved draft's bytes (`/api/validate|power|schedule|sha` wrap
  `from_yaml_text`, `mde_check` (quick knob labeled, lock recomputes full),
  `derive_schedule`, `load_task_dicts`); the only POSTs are the two
  ceremony endpoints: `/api/draft` (allowlisted files
  `experiment.yaml|tasks.yaml|rubrics/*.md`, refused into locked dirs) and
  `/api/lock` (`lock_experiment` verbatim, launch-bound actor, explicit
  `attested_by`, typed refusals as their own messages). `lock_kwargs` is
  the operational MDE tuning `lock_experiment` already accepts.
- `page.py`: text-pane-canonical editor (template button generates into the
  pane once, D002), validation/power/schedule/sha panels reading the last
  save, and the ceremony card (sha shown, attestation required,
  underpowered acknowledgment revealed by the typed refusal); locked
  drafts render read-only.
- `cli.py`: `bench author <root> [--actor]` via `resolve_actor` — refused
  loudly, never "unknown".

## M2 — contracts + docs (AC-4)

`harness.author` added to the harbor-confinement and
ledger-writes-only-via-events source lists; README documents the verb; the
operator `bench serve` posture suites are untouched.

## M3 — tests (AC-1..AC-5)

`tests/test_eval17_author.py`: preview purity with endpoint↔seam equality
and byte-digest checks; the one-event ceremony incl. the underpowered
refusal→acknowledgment flow (ack inline on the lock event, PL-14); a
headless page drive of the full template→edit→save→lock path; post-lock
write refusals; posture (needles, actor exit, allowlist, method refusals);
and payload parity — the ceremony and `bench plan` locking identical bytes
produce identical genesis events modulo provenance and the absolute
spec_path.

## Out of scope (unchanged from the spec)

Stage execution from the browser (D003); post-lock edits of any kind;
corpus curation UI; auth/remote hosting.
