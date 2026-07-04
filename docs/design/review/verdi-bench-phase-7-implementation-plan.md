# verdi-bench — Phase 7 implementation plan: close the register (decisions resolved)

**Date:** 2026-07-04 · **Follows:** `verdi-bench-phase-7-plan.md` (merged to
`main` via PR #13, `de75812`) and the decision session of 2026-07-04 in which
the human accepted all seven D-P7 recommendations plus EVAL-1-D008, each with
the refinements recorded in §1 below.
**Source of record:** the Phase 7 plan's disposition map, backed by
`verdi-bench-audit-verification.md` §3–§5 and
`verdi-bench-review-consolidated.md` §3.
**Branch:** plan authored on `claude/verdi-bench-phase-7-review-hch534`;
implementation follows the established convention — merge this plan, then cut
the implementation branch from `main`.

Every code site below was re-located against the working tree at `de75812`;
line numbers cite that tree. Where this plan pins a mechanic the Phase 7 plan
left open (hash normalization, override provenance format, banner semantics),
the choice is stated inline and flagged in §5 (judgment calls) for cheap veto.

## 1. Decisions as resolved

All eight are **resolved**. The refinements below are part of the resolution,
not implementation latitude; the ndjson event lines to append are given
verbatim in Appendix A and land as the first commit of implementation.

- **REVIEW-D-P7-1 — resolved `unique-names-required-no-cap`.** Duplicate arm
  names refused at plan time with a named error; `min_length=2` kept, no upper
  cap (Phase 5 made analysis pairwise-correct; ≥3-arm designs are supported).
- **REVIEW-D-P7-2 — resolved `daemon-probe-plus-ledgered-retry-flag`,** with
  two refinements: **(a)** the grade/cant_grade event produced by a
  `--retry-terminal` re-attempt carries an additive `override_of` field
  referencing the overridden terminal `cant_grade` (by its ledger line hash),
  so the override is visible in the event itself, not only to someone who goes
  looking; **(b)** both findings renders disclose the count of override-graded
  trials. No new event type is introduced — the override rides as an optional
  field on the existing grade events, actor from provenance.
- **REVIEW-D-P7-3 — resolved `identity-bound-keyring`,** with the refinement
  that the decision record states the residual trust assumption explicitly:
  the bar is as strong as keyring issuance, which is local unhashed operator
  state; a miner holding two keyring entries under two identities is out of
  scope for CO-7. Supersedes the key-only half of EVAL-8-D-P4-3.
- **REVIEW-D-P7-4 — resolved `render-ipw-plus-floor-sensitivity`.**
  `kappa_report` gains its production caller; EVAL-7 D003 recorded `resolved`.
- **REVIEW-D-P7-5 — resolved `remove`.** The `--concurrency` flag and the
  `contention_caveat` stamp are deleted.
- **REVIEW-D-P7-6 — resolved `additive-rubric-sha-in-lock`,** with three
  refinements that are the substance of the approval:
  **(a) commitment point:** `bench plan` **refuses to lock** when the spec
  names a rubric whose file is absent — the judging instrument is part of the
  pre-registration;
  **(b) hash definition:** the lock-side hash is computed exactly as the
  verdict-side hash already is — `sha256(path.read_text(encoding="utf-8")
  .encode("utf-8"))`, matching `judge/packet.py:148`. This is a *normalized-
  text* hash (universal-newline read), which guarantees lock ↔ verdict
  comparability and makes CRLF-checkout drift a non-event;
  **(c) legacy posture:** a pre-Phase-7 lock (absent field) makes `bench
  judge` warn instead of refuse, **and** the official render adds a caveat
  line; additionally the official fence refuses when the lock *does* carry
  `rubric_sha256` and any verdict's provenance hash disagrees with it.
  This is the phase's only change touching a hash-chained event format, and it
  is additive (exactly the `task_commitment` precedent,
  `ledger/events.py:115-143`).
- **REVIEW-D-P7-7 — resolved `env-fallback-then-refuse`,** with the
  refinement that `bench corpus approve` **drops** its `_actor()` fallback and
  requires an explicit `--approver`: once D-P7-3 binds approver identities to
  keys, an identity that is security-relevant must not default from the
  environment. Environment-derived actors remain fine for provenance
  elsewhere; the resolution notes this is fail-loud provenance, not
  authentication.
- **EVAL-1-D008 — resolved `required-before-official`,** with three
  refinements: **(a)** the selfcheck's seed derives from the locked experiment
  seed (`sub_seed(spec.seed, "selfcheck")`), so the check is deterministic and
  cannot be re-run until it passes; **(b)** a failing selfcheck makes the
  experiment **exploratory-only** — the official fence refuses, nothing else
  is blocked; **(c)** the pass criterion is *nominal-within-MC-interval*: the
  selected method passes iff the nominal CI level lies within the Wilson 95%
  interval of the empirically estimated coverage — self-scaling with `n_sim`,
  no magic tolerance constant. Slice 7I is therefore **in scope**. The
  `selfcheck` ledger event is a new *additive* event kind (no existing event
  format changes).

## 2. Disposition map → owning commit

Unchanged rows from the Phase 7 plan, now bound to commits (§3). The phase
cannot exit while any row lacks either an owning test or a recorded decision.

| Item | Owning commit | Disposition |
|---|---|---|
| PL-13 append onto truncated final line | 7A-1 | fix + test |
| `bench anchor` fail-open on tampered ledger | 7A-2 | fix + test |
| `bench plan` unverified append | 7A-3 | fix + test |
| `bench judge` / `review build` re-run duplication | 7A-4 | fix + tests |
| GR-8/GR-11 daemon-down misclassification | 7B-1 | fix + test |
| Terminal-override recourse | 7B-2 | fix + tests (D-P7-2) |
| `grader` stamp write-only (ADVISORY hole) | 7B-3 | fix + test |
| GR-13 owning test | 7B-3 | test |
| PL-9 validation duplication | 7C-1 | fix + tests |
| PL-10 duplicate arm names | 7C-1 | fix + test (D-P7-1) |
| PL-11 `==` in rule DSL | 7C-1 | fix + test |
| GR-12 `actor="unknown"` ×7 | 7C-2 | fix + tests (D-P7-7) |
| RN-18 inert `--concurrency` | 7C-3 | removal (D-P7-5) |
| JD-10 key in URL | 7D-1 | fix + test |
| RN-17 corrupt telemetry | 7D-2 | fix + test |
| PR-9 vendor-overlap + context gate | 7D-3 | fix + tests |
| Rubric content not lock-committed | 7D-4 | fix + tests (D-P7-6) |
| RV-9 join asymmetry; integrity-less calibration | 7E-1 | fix + tests |
| RV-7 ordering test + stale docstrings | 7E-2 | test + docs |
| RV-3 `kappa_report` unrendered / EVAL-7 D003 | 7E-3 | fix + test (D-P7-4) |
| RV-8(c) reference note | 7E-2 | spec note (in-slice rec) |
| CO-7 self-approval label bypass | 7F-1 | fix + tests (D-P7-3) |
| D-1 actions + D002 clarification | 7G-1 | docs + decision event |
| XC-7 README Usage + consistency test | 7G-2 | fix + strengthened test |
| §6 stale rows, N-3 drift, stale docstrings, `shutil`×3, AN-11 record | 7G-3 | docs + decision events |
| Package-`__init__` import blind spot | 7H-1 | fix + planted-violation test |
| CI docker all-skip green | 7H-2 | fix + guard test |
| Owning tests: AN-1, AN-10, arm-payload canary, RN-15, RN-16 | 7H-3 | tests |
| EVAL-1-D008 selfcheck + official gate | 7I-1/7I-2 | feature + fence (resolved) |

Explicitly **not reopened** (recorded decisions stand): CO-2/CO-9 opt-in
gating, metering-proxy unit coverage, JD-13 deterministic labels,
CIMethod/`fractional_score`, quarantine keying, judge packet content.

## 3. Commit plan

Ordering matches the Phase 7 plan: integrity-adjacent correctness first
(7A/7B), hygiene and residue (7C–7F), docs truth-up second-to-last (7G — it
must describe the post-fix reality), enforcement and the gated feature last
(7H/7I). Every commit: reproduce-first failing test, `make verify` green
before commit, no new runtime dependency.

### Commit 0 — decision records

Append the raised+resolved event pairs of Appendix A to
`docs/design/review/review.decisions.ndjson` (D-P7-1…7) and the resolved
event to `docs/design/specs/eval1.decisions.ndjson` (D008). The remaining
decision-file appends land with their owning slices: EVAL-7 D003 (7E-3),
EVAL-2 D002 clarification (7G-1), N-3 amendments + AN-11 acceptance (7G-3),
EVAL-8-D-P4-3 supersession note (7F-1).

### 7A — Fail-closed writers + verb idempotency · P1

**7A-1 — `append_event` refuses a truncated final line (PL-13).**
`ledger/chain.py:83-119`: under the exclusive `flock`, before computing
`head_hash`, stat the file; if non-empty, read the final byte via a separate
read-only open and refuse with a new `TruncatedLedgerError` naming the line
count when it is not `\n` — never concatenate. (Detection today exists only
at verify time, `chain.py:147-156`; `_last_line` at `chain.py:44-74` happily
returns an unterminated line, so today's append chains onto the fragment.)
*Reproduce-first:* strip the trailing newline from a valid two-line ledger,
call `append_event`, assert the current concatenation; after the fix assert
the named refusal and a byte-identical file.

**7A-2 — `bench anchor` chain-verifies before anchoring.**
`ledger/anchors.py:33-45` (`anchor_head`) reads `lines[-1]` via
`_ledger_lines` with **no** chain check — today it anchors a tampered ledger
with exit 0. Fix: `verify_chain` first; broken ⇒ raise; the CLI
(`harness/cli.py:108-127`) exits 1 with the first-broken-line detail and
appends **nothing** — neither the anchor-store line nor the `chain_anchor`
event (`cli.py:125-126`).
*Reproduce-first:* byte-flip one ledger line, run `bench anchor`, assert the
current exit-0 + anchor written; then assert exit 1, anchor store unchanged,
zero events appended.

**7A-3 — `bench plan` chain-verifies an existing ledger.**
`plan/lock.py:82-86` checks only for a prior `experiment_locked` event before
appending. Fix: when the ledger file exists and is non-empty, `assert_chain`
(`ledger/query.py:44-64`) before the `find_events` check — refuses tampered
ledgers, and (with 7A-1's `verify_chain` truncation rule) truncated ones.
`assert_chain`'s absent/empty-file silence keeps the fresh-experiment path
untouched.
*Reproduce-first:* tamper a pre-existing ledger, run `bench plan`, assert
nonzero exit and zero appended events.

**7A-4 — `bench judge` + `bench review build` become idempotent.**
Judge (`judge/cli.py:72-84`) iterates every comparison unconditionally — a
re-run appends a full duplicate verdict set, inflating calibration and
preference statistics. Fix, mirroring `process/cli.py:77-93`: build
`already = {ev["verdict"]["comparison_id"] for ev in
find_events(ledger_path, events.JUDGE_VERDICT)}` and skip; a full re-run
appends zero events and reprints the kappa summary. Review build
(`review/build.py:71-75`) similarly re-records `review_packet_built` per
comparison: for a comparison with an existing event, **reuse the ledgered
`response_map`** (via `record.py:76-83`) instead of re-recording, so the
re-rendered packet is byte-identical to the ledgered blinding state and zero
events are appended.
*Reproduce-first:* run judge twice over a fake-provider ledger and assert the
doubled event count; run build twice and assert duplicated packet events.
Exit tests: second run of each verb appends zero events; the fake-engine e2e
pipeline run twice end-to-end yields byte-identical analysis inputs.

### 7B — Grade robustness · P1 · per D-P7-2

**7B-1 — pre-flight daemon probe; daemon-down is transient.**
Daemon-down makes `docker run` exit **1** on modern docker, which
`container.py:80-85` classifies as terminal `GradingContainerError`
(`container_failure`) — a single outage quarantines healthy task versions and
permanently blocks regrading; only exit 125 (`container.py:74-75`) and spawn
failure (`container.py:70-72`) are transient today. Fix: a `preflight()`
method on `DockerGradeRunner` (`docker version`, bounded timeout) raising
`GraderUnavailableError` on failure; `GradingContainer` delegates (no-op for
`LocalGradeRunner`). `grade/cli.py` calls it once between container
construction (`cli.py:109`) and the trial loop (`cli.py:112`); on probe
failure every pending trial gets `cant_grade(grader_unavailable)` — transient
(`TRANSIENT_CANT_GRADE`, `deterministic.py:43`), regradeable — and the verb
exits nonzero naming the daemon. `flake_baseline` (`grade/baseline.py:37-92`)
gets the same probe at batch start; its existing `GraderUnavailableError`
re-raise (`baseline.py:61-65`) already ledgers nothing (inconclusive, not
flake evidence). Correct the `GradingContainerError` docstring
(`container.py:35-37`) — "the grader ran" is exactly what the daemon-down
mode falsifies.
*Reproduce-first:* through the runner seam (`container.py:53-54`), simulate
daemon-down-exit-1 and assert the **current** terminal
`cant_grade(container_failure)`; after the fix, assert transient
classification and that a subsequent grade succeeds without any override.

**7B-2 — `bench grade --retry-terminal <trial-id>` (ledgered override).**
New repeatable option on `grade/cli.py:64-69`. Semantics: each named trial
must have a **terminal** `cant_grade` and no `grade` (else refuse, naming
what was found); the id is removed from the skip set
(`_completed_trials`, `cli.py:45-59` / gate at `cli.py:115`); the re-attempt
proceeds normally, and the resulting `grade` **or** `cant_grade` event
carries `override_of = <sha256 line hash of the overridden cant_grade line>`
— the ledger-native reference (`chain.py:39-41`). `record_grade`
(`events.py:208-233`) and `record_cant_grade` (`events.py:236-239`) gain the
optional kwarg, written only when set (the `grader`/`fractional_score`
additive idiom, `events.py:231-232`; `emit`'s payload is additive-friendly,
`events.py:81-95`). Analyze gains `_override_summary(ledger_path)` counting
grade-family events with `override_of`, rendered as one disclosure line in
**both** official (`report.py:844-884`) and exploratory (`report.py:887+`)
renders.
*Reproduce-first / exit tests:* override on a terminal `cant_grade` regrades
and stamps `override_of`; override refused for a graded trial and for a
trial with only a transient `cant_grade`; a failed re-attempt appends a new
`cant_grade` that also carries `override_of` (every attempt visible); the
renders disclose the count.

**7B-3 — ADVISORY banner keyed on the grade-level `grader` stamp + GR-13
test.** `_tier_summary` (`report.py:199-216`) reads only trial-provenance
tiers — an explicit `--runner local` grade over trusted trials renders
unflagged (the write-only-stamp hole). Fix: additionally scan grade events;
any event whose `grader` field is **present and ≠ `"docker"`** (i.e.
`"local"` or `"unknown"`, per `container.py:65,112,140-144`) merges ADVISORY
into the tier set; an absent field (pre-stamp ledger) adds no new signal.
The banner itself (`report.py:995-1002`) is unchanged. GR-13 owning test:
assert every completed baseline run in `flake_baseline` evidence carries its
`assertions` vector (`baseline.py:72-80`) — a revert to `{run, passed}` must
fail.
*Reproduce-first:* a ledger with trusted-tier trials and one
`grader="local"` grade currently renders without the banner; after the fix
it banners.

### 7C — Schema & CLI hygiene · P2 · per D-P7-1/5/7

**7C-1 — one validation source; arm-name uniqueness; `==` banned (PL-9,
PL-10, PL-11).** Collapse the duplicated validation
(`experiment.py:168-208` `_prevalidate` duplicates the pydantic validators at
`experiment.py:38-50,139-165`): pydantic validators become the single source,
raising the named `SpecError` subtypes (`schema/errors.py:10-32`); the loader
seam (`from_dict`/`from_yaml_text`, `experiment.py:210-230`) catches
`ValidationError` and re-raises the first wrapped `SpecError` (pydantic v2
preserves the original exception in `errors()[…]["ctx"]["error"]`); delete
`_prevalidate`. New in the same source: **`ArmNameError`** on duplicate arm
names (a live bug — `run`'s `arm_map` silently collapses duplicates), and
`DecisionRuleError("equality on a bootstrap float is never decidable; use >=
or <=")` when the parsed operator is `==` — keep `==` in `_RULE_RE`
(`experiment.py:65-67`) so the refusal names the operator, remove it from
`_OPS` (`experiment.py:68-74`). Arm-count policy per D-P7-1: `min_length=2`
kept (`experiment.py:119`), no cap.
*Tests:* the five existing named errors pinned on **both** loader paths
(behavior-preserving proof of the collapse); duplicate arm name refused with
`ArmNameError` at `bench plan` time; `delta_holdout_pass_rate == 0` refused
with the operator named.

**7C-2 — one shared `resolve_actor()`; refuse over `"unknown"` (GR-12).**
New module `harness/ledger/actor.py` (provenance concern; lives beside
`EventContext`): `resolve_actor(flag_value: str | None) -> str` — explicit
`--actor` wins; else `getpass.getuser()` (which itself consults
`LOGNAME/USER/LNAME/USERNAME` then the pwd database); on `OSError/KeyError`,
raise `ActorResolutionError` naming the `--actor` flag — never ledger
`"unknown"`. Replace the seven swallow sites: `harness/cli.py:22-29`
(`_default_ctx` — plan/anchor), `run/cli.py:121-125`, `grade/cli.py:103-107`,
`analyze/cli.py:19-23`, `review/cli.py:17-21`, `process/cli.py:17-21`,
`corpus/cli.py:20-24`; each ledgering verb gains `--actor`. Per the D-P7-7
refinement, `corpus approve` (`corpus/cli.py:154,161`) drops the `_actor()`
fallback entirely: `--approver` becomes required.
*Tests:* with `getpass.getuser` raising and env cleared, each verb refuses
naming `--actor` (parametrized over the seven); `--actor alice` ledgers
`actor="alice"`; `corpus approve` without `--approver` refuses.

**7C-3 — remove `--concurrency` + `contention_caveat` (RN-18).**
Delete the flag (`run/cli.py:41`, threading at `cli.py:116`), the config
fields (`run/types.py:70,110`), and the stamp (`seam.py:86,112`). Grep-verify
no reader of `contention_caveat` exists before deletion (removal of a
written-only payload field is not a chain-format change; old ledgers keep
the field, no reader requires it). README reflects this in 7G-2.

### 7D — Judge / run / process residue · P2–P3 · per D-P7-6

**7D-1 — Google key moves to a header (JD-10).**
`judge/providers/google.py:31-33` interpolates `?key={key}` into the URL and
passes empty headers — the key leaks through any proxy/request-line log.
Fix: `x-goog-api-key` header, mirroring `openai.py:28-32` /
`anthropic.py:37-41`.
*Test:* through a captured `post_json`, assert the key never appears in the
URL and is present in the header.

**7D-2 — corrupt telemetry fails loud (RN-17).**
`run/engines/harbor.py:314-322` maps `json.JSONDecodeError` to `{}` —
corrupt telemetry silently becomes "no telemetry". Fix: corrupt file ⇒ the
trial fails closed as `trial_infra_failed(telemetry_corrupt)` via the
engine's existing failure-reason channel (the `"daemon_error"` idiom,
`harbor.py:282` → `interleave.py:299-306`). An **absent** log file stays
`{}` — absence is legitimate; corruption is not.
*Test:* a corrupt `agent_log.json` through the seam yields
`trial_infra_failed(telemetry_corrupt)`.

**7D-3 — PR-9: `spec` required; provider context-overflow named.**
`process/score.py:181` defaults `spec=None`, and `score.py:194` silently
degrades `judge_vendor_overlap` to `False` when unknown. Fix: `spec` becomes
required (production always passes it, `process/cli.py:85-91`; only tests
omit it — update them); the overlap bool is honest again. Context gate: the
chars/4 pre-flight (`score.py:219-223`) stays pre-flight-only; the
provider-error mapper additionally recognizes provider-side context-overflow
responses and maps them to `CANT_SCORE(context_overflow)` carrying the
provider's token counts when present, instead of generic `provider_error`
(`score.py:227-237`, `CantScoreReason` at `score.py:49-64`).
*Tests:* omitting `spec` is a `TypeError`; a simulated provider
context-overflow error scores `context_overflow`, not `provider_error`.

**7D-4 — rubric content lock-committed (D-P7-6).**
Plan side: `lock_experiment` (`plan/lock.py:57-156`) resolves
`experiment_dir / spec.judge.rubric`; **absent file ⇒ refuse to lock** with a
new named `RubricCommitmentError` (CLI exit 2, joining the catch at
`cli.py:75-77`); else compute the hash per the D-P7-6 refinement —
`sha256(read_text("utf-8").encode("utf-8"))`, byte-for-byte the computation
`judge/packet.py:148` already performs — and pass it to
`record_experiment_locked`, which gains the optional additive
`rubric_sha256` kwarg (the `task_commitment` idiom, `events.py:132-143`).
Judge side: after the rubric read (`judge/cli.py:59-65`), compare against
`lock_event` (in scope from `cli.py:45`, sibling to `assert_task_commitment`
at `cli.py:51-54`): mismatch ⇒ exit 2 naming both hashes and the post-lock
swap; absent field ⇒ **warn** (pre-Phase-7 lock). Official fence
(`report.py:784-841`): fourth check — when the lock carries `rubric_sha256`
and verdicts exist, every verdict-provenance `rubric_sha256`
(`report.py:354-358` already collects them) must equal it, else refuse
official; when the lock lacks the field, the official render adds a caveat
line instead.
*Tests,* mirroring the `test_eval8_commit.py` swap-refusal shape
(`:41-52,103-118,132-138`): lock → swap rubric → `bench judge` refuses;
legacy lock warns and still judges; `bench plan` refuses when the rubric
file is missing; official render carries the caveat on a legacy lock; the
fence refuses a lock/verdict hash mismatch. The compatibility note is part
of the D-P7-6 resolved event (Appendix A).

### 7E — Review residue · P2–P3 · per D-P7-4

**7E-1 — unify verdict joins; integrity-required calibration (RV-9,
RV-8(f)).** The reveal join is first-wins (`record.py:186-191`, the `break`)
while both kappa joins are last-wins (`sample.py:162-165` dict
comprehension) — on a duplicated ledger, reveal discloses one verdict and
kappa scores another. Fix: reveal becomes last-wins (drop the `break`, keep
the last match); with 7A-4, duplicates can only be legacy. Calibration:
`reviewed_kappa_items` (`sample.py:153-182`) additionally skips human
verdicts without an `integrity` block, aligning it with the reveal gate
(`record.py:43-48`) and the integrity-rate computation
(`report.py:388-399`) — one filter, three call sites agreeing.
*Tests:* a hand-built duplicate-verdict ledger on which reveal and kappa now
agree (the last verdict); an integrity-less human verdict is excluded from
kappa items.

**7E-2 — RV-7 owning ordering test; stale docstrings; RV-8(c) note.**
The seeded shuffle is real (`sample.py:133-139`); nothing owns it. Owning
test: the selected review order equals
`seeded_shuffle(sorted, sub_seed(seed, "review_order"))` **and** the
mandatory (disagreement) items are not a prefix of the order — deleting the
shuffle fails both. Fix the stale "disagreements-first" docstrings in
`review/packet.py:1-8` and `:69-75` (the module does no ordering — it
renders items as received, `packet.py:77`). RV-8(c): spec note in
`docs/design/specs/eval7.spec.md` documenting that post-RV-1 the
`comparison_id` **is** the unique verdict reference; the `verdict_event_id`
field name inside the hash-chained `reveal` event is kept (renaming is
contract churn with no information gain — in-slice recommendation from the
Phase 7 plan).

**7E-3 — sensitivity kappa rendered; EVAL-7 D003 resolved (D-P7-4).**
`kappa_report` (`review/kappa.py:155-184`, returns IPW headline + floor-only
sensitivity) has zero production callers — `calibrate.py:67` calls
`estimate_kappa` directly. Fix: the calibration path uses `kappa_report` per
class; `_judge_calibration` (`report.py:361-385`) carries the sensitivity
estimate; `_judge_calibration_lines` (`report.py:934-956`) renders
"sensitivity (floor-only): κ=…" beside the IPW estimate. Append the EVAL-7
D003 `resolved` event (Appendix A).
*Test:* the exploratory render shows both estimates per class; removing the
`kappa_report` call fails it.

### 7F — Curation identity binding · P2 · per D-P7-3

**7F-1 — identity-bound keyring; relabeled self-approval refused (CO-7).**
`load_keyring` (`attestation.py:63-69`) becomes
`{approver_id: pubkey_hex}` → `dict[str, str]`; a legacy JSON **list** raises
a loud migration error naming the new format and D-P7-3 (the keyring is
local operator state, not hash-chained — no compatibility shim). `admit_task`
(`admit.py:70-151`): resolve `approval["approver"]` in the keyring (unknown
approver ⇒ `UnauthorizedCuratorError`), verify the signature against **the
named approver's own key** — not the self-attested `signer_public_key`
(today's membership test at `admit.py:120-125` lets any authorized-key
holder self-approve by relabeling; probe-confirmed) — cross-check the
attested `signer_public_key` equals the keyring key, then the existing
miner checks (`admit.py:126-139`). Update the property-registration keypair
(`admit.py:154-200`, `keyring={_CURATOR_PUB}` → `{"curator": _CURATOR_PUB}`),
the CLI help text (`corpus/cli.py:228`, "(JSON list)"), and the e2e/CLI
tests that write the on-disk format
(`test_eval8_cli.py:60-61,133-134`; `test_eval8_corpus.py:33`).
*Reproduce-first:* the verification's probe — the miner signs as a different
approver label with their own authorized key — lands as a failing test
(passes admission today), then is refused because the signature does not
verify against the named approver's key. The D-P7-3 resolved event records
the supersession of EVAL-8-D-P4-3's key-only half and the residual trust
assumption.

### 7G — Docs, decisions, disclosure truth-up · P3 (after 7A–7F)

**7G-1 — D-1 actions + D002 clarification.** "outcome-blind" →
"identity-blind" (defined once) in master plan §1 and README lines 5/19;
analysis-side disclosure: a `[computed]`-tagged note in the judge section of
findings (`report.py:354-358` render area, both renders) stating
`judge_preference` is not independent of `holdout_pass_rate` because the
packet includes holdout results by design (D002); append the D002
clarification event to `eval2.decisions.ndjson`. Exit: grep for
"outcome-blind" returns only historical audit docs.

**7G-2 — README Usage mechanically true and enforced (XC-7).** Fix the
Usage block (`README.md:55-73`): `--winner 1|2|TIE|CANT_JUDGE`
(`README.md:70` currently shows `--winner A` — human verdicts are recorded in
the blinded response frame); add the undocumented verbs (`judge`,
`review build`, `review reveal` is present, `process score`,
`corpus approve|calibrate|admit`); reflect 7B/7C flag changes
(`--retry-terminal`, `--actor`, `--concurrency` removed) and 7I's
`selfcheck`. Strengthen `test_readme_consistency.py` (today it pins only the
import-linter contract count, `:17-28`): introspect the fully-registered
typer app (`harness/cli.py:130-155`), collect every `<group> <command>` verb
path, and assert both directions — every verb named in the Usage block
exists, and every registered verb is documented. The checker is exercised
against a planted undocumented verb inside the test itself (register a dummy
command on a copy of the app; assert the checker flags it) — the
reproduce-first artifact, permanently owned.

**7G-3 — record truth-up.** Consolidated review §6: flip the three stale
rows (tamper-evidence, sha-lock, cost-ceiling) with evidence pointers;
correct "12 entrypoints" → the post-7I count. N-3: append amendment events
to EVAL-8-D-P4-1 (`holdout_ref` removed) and EVAL-3-D-P4-1 (loader reads
manifest runs; the official fence reads ledgered events). Stale docstrings:
`run/settings.py:1-16`, `test_eval4_harbor_egress.py:6-7` ("real proxy +
real kill are docker-marked" — they live in other files);
(`container.py:35-37` and `review/packet.py` were fixed in their owning
slices 7B-1/7E-2). Delete the three dead `import shutil`
(`test_eval4_harbor_request.py:12`, `test_e2e_harbor.py:19`,
`test_e2e_pipeline.py:17`). Append the AN-11 accept-as-convention record
(`findings_rendered.experiment_id` = directory basename).

### 7H — Enforcement hardening + missing owning tests · P2–P3

**7H-1 — import blind spot closed (contract + AST test).**
The AST seam test inspects only `node.module` on `ImportFrom`
(`test_eval4_seam.py:97-98`), so `from .engines import harbor` in a package
`__init__` evades it — the verification's planted probe. Fix: also inspect
member names (`[a.name for a in node.names]`), keeping the
`engines/__init__.py` allowance (`:81` — the factory seam legitimately
names harbor). `.importlinter`: extend the harbor contract's source list
(`:16-39`, currently per-submodule) with the package modules
(`harness.run`, and `harness` where expressible) — note the harbor module is
itself a descendant of `harness.run`, so if the pinned import-linter version
cannot scope a package source to its `__init__` alone, the **AST member-name
fix is the owning guard** for the package-`__init__` channel and the
contract keeps its per-submodule enumeration; the planted-violation test
decides. Extend `test_import_contracts.py` (`_CASES`, `:22-25`) with a
package-`__init__` planted case.
*Reproduce-first:* plant `from .engines import harbor` in
`harness/run/__init__.py` on a scratch copy; assert both guards currently
miss it; after the fix, the AST test (and the contract, where expressible)
catches it.

**7H-2 — CI docker job hard-fails on all-skip (XC-1 residual).**
`pytest -m docker` exits 0 when every test skips (`ci.yml:45-56` has no
guard; `VERDI_REQUIRE_DOCKER` appears nowhere in the repo). Fix: set
`VERDI_REQUIRE_DOCKER=1` on the docker job step (`ci.yml:55-56`); in
`tests/fixtures/docker.py`, when the variable is set and the probe fails,
raise at import (collection error — the job cannot green by skipping).
*Test:* unit-test the guard via reload with monkeypatched env + probe.

**7H-3 — the five missing owning tests.**
- **AN-1 swapped-frame:** a verdict fixture whose `arm_map` inverts the
  frame (`{"A": treatment, "B": control}` with treatment ≠ `arms[0]`,
  `report.py:250-257` vs the `arms[0]` assumption at `report.py:528`);
  attribution must follow `arm_map` — every existing AN-1 test is
  frame-aligned (`test_eval6_analyze.py:244-307`), so a regression to
  `arms[0]` passes the whole suite today.
- **AN-10:** assert `ci_selection["n_boot"] == deployed n_boot` in the
  findings (`report.py:545-546` vs `:584`) under a non-default `n_boot`.
- **Arm-payload canary:** the third insulation channel
  (`seam.py:60-66`) — a canary planted in `arm.payload` is refused with
  `HoldoutLeakError`, mirroring the fake-behavior channel test
  (`test_eval4_insulation.py:56-65`); prompt and fake_behavior have owners,
  payload does not.
- **RN-15:** a planned arm absent from `arms` ⇒
  `trial_infra_failed(unknown_arm)` and the cell is appended to
  `executed_order` (`interleave.py:228-230`).
- **RN-16:** a detected-but-unwritable secret ⇒ `RedactionError`
  (`redact.py:73-83`), failing the cell closed (`interleave.py:49`).

### 7I — `bench selfcheck` + official-render gate · per EVAL-1-D008

**7I-1 — the selfcheck computation + verb.**
New `harness/analyze/selfcheck.py` per master plan §7.7, built on the
existing nullsim substrate: extract the experiment's realized per-task
deltas (the same clustering model analyze uses), run
`coverage_from_deltas(deltas, seed=sub_seed(spec.seed, "selfcheck"),
null_model=…)` (`nullsim.py:87-140` — deterministic via namespaced
`sub_seed`, `nullsim.py:115,119`) at the realized N, and apply the D008 pass
criterion: **pass iff the nominal level lies within the Wilson 95% interval
of the selected method's estimated coverage** (n = `n_sim`). `bench
selfcheck` registers in `analyze/cli.py` alongside `analyze`
(entrypoint-registry pattern, `analyze/cli.py:122-144`, so the one-event
property sweep picks it up) and ledgers exactly one additive `selfcheck`
event: `{selected_method, nominal, coverage, mc_interval, n_sim, n_boot,
n_tasks, null_model, passed}`. Insufficient data (`n < 2` clusters,
`nullsim.py:110-111`) ledgers `passed=false` with `null_model=
"insufficient_data"` — an experiment too small to selfcheck cannot render
official.
*Tests:* deterministic (same ledger ⇒ byte-identical event payload); a
well-powered fake-engine fixture passes; a starved fixture fails; exactly
one event per invocation (property sweep).

**7I-2 — the official fence requires a passed selfcheck.**
`_assert_official_calibration` (`report.py:784-841`) gains the fifth check:
a ledgered `selfcheck` event with `passed=true` must exist; absent or failed
⇒ official refused with a message naming `bench selfcheck` (exploratory
renders untouched — D008 refinement (b)). README + master-plan §7.7 pointers
updated (folds into 7G-2's final README state if 7G lands first — ordering
note: 7I may land before 7G-2's README commit or amend it; the README
consistency test forces the two to agree either way).
*Tests:* official render refused without a selfcheck event, with a failed
one, and succeeds with a passed one; the refusal names the verb.

## 4. Phase exit criteria (all testable, unchanged from the Phase 7 plan plus D008)

- The §2 disposition map is fully bound: every row's owning commit merged,
  its test failing on regression, or its decision event appended; the
  verification doc gains the "Phase 7 disposition" appendix.
- Idempotency + fail-closed writers: re-running any verb appends zero
  events; `anchor`/`plan` refuse tampered or truncated ledgers.
- The three headline probes re-run clean: forged grade (0 grade events),
  tampered lock line (every verb refuses — now including the writers), judge
  re-run (0 new events, unchanged findings).
- README mechanically true and enforced (7G-2's checker flags a planted
  undocumented verb).
- Official render additionally requires: rubric-hash agreement (or the
  legacy caveat) and a passed ledgered selfcheck.
- `make verify` green throughout; import-linter contracts kept with the
  extended source lists; no new runtime dependency; the only hash-chained
  format changes are the two **additive** items approved in §1 (D-P7-6's
  `rubric_sha256` field; D008's new `selfcheck` event kind).
- CI: fast + py312 + docker jobs green; the docker job hard-fails on a
  daemon-less runner.

## 5. Judgment calls made in this plan (cheap veto)

1. **`override_of` format** = the overridden `cant_grade` line's sha256 line
   hash — the ledger-native reference; no new event type for the override.
2. **`resolve_actor()` location** = new `harness/ledger/actor.py` (beside
   `EventContext`, which it exists to feed), imported by the seven CLIs.
3. **ADVISORY grader rule** = `grader` present-and-≠-`"docker"` ⇒ ADVISORY;
   absent (pre-stamp ledger) ⇒ no new signal.
4. **Rubric hash** = `sha256(read_text("utf-8").encode("utf-8"))` — chosen to
   be bit-identical to the existing verdict-side computation
   (`packet.py:148`); this is normalized-text, not raw-file-bytes, hashing.
5. **Review-build idempotency** reuses the ledgered `response_map` for
   already-built comparisons, so re-rendered packets match the ledgered
   blinding state even across code changes.
6. **Judge idempotency** skips every comparison with an existing verdict,
   including `CANT_JUDGE` — "one verdict each" per the verb's contract; a
   retry story for transient judge failures is explicitly out of scope.
7. **Import-linter package-source uncertainty**: if the pinned version
   cannot express a package-`__init__`-only source, the AST member-name test
   is the owning guard (the planted-violation test decides which guards
   catch it; at least one must).
8. **Commit count** revised to ~24 atomic commits (the Phase 7 plan
   estimated 15–20 before D008 resolved `required-before-official`, which
   adds 7I's two).

## Appendix A — decision events to append (Commit 0 and owning slices)

`docs/design/review/review.decisions.ndjson` — one `raised`+`resolved` pair
per D-P7 decision (raised lines carry the options/recommendation from the
Phase 7 plan §Decisions; `ts` values stamped at append time; `author:
"jyang"` on resolved lines per the 2026-07-04 session):

```
{"id":"REVIEW-D-P7-1","event":"raised","ts":"2026-07-04T00:00:00Z","ticket":"REVIEW","phase":"phase-7","type":"Requirement","question":"Arm-list policy: unique names required? count cap?","options":["unique-names-required-no-cap","hard-cap-at-2"],"recommended":"unique-names-required-no-cap","spec_ref":"verdi-bench-phase-7-plan.md#decisions","author":"claude/plan"}
{"id":"REVIEW-D-P7-1","event":"resolved","ts":"2026-07-04T00:00:00Z","ticket":"REVIEW","answer":"unique-names-required-no-cap","rationale":"Phase-7 decision session 2026-07-04: recommendations accepted; Phase 5 made analysis pairwise-correct, so >2 arms is a supported design","author":"jyang"}
{"id":"REVIEW-D-P7-2","event":"raised","ts":"2026-07-04T00:00:00Z","ticket":"REVIEW","phase":"phase-7","type":"Requirement","question":"Grade transient taxonomy + terminal-override recourse","options":["probe-only","daemon-probe-plus-ledgered-retry-flag","probe-plus-one-time-reclassification"],"recommended":"daemon-probe-plus-ledgered-retry-flag","spec_ref":"verdi-bench-phase-7-plan.md#decisions","author":"claude/plan"}
{"id":"REVIEW-D-P7-2","event":"resolved","ts":"2026-07-04T00:00:00Z","ticket":"REVIEW","answer":"daemon-probe-plus-ledgered-retry-flag","rationale":"Accepted 2026-07-04 with refinements: override provenance rides on the resulting grade/cant_grade event as additive override_of (line hash of the overridden event); findings renders disclose override-graded trial count; no new event type","author":"jyang"}
{"id":"REVIEW-D-P7-3","event":"raised","ts":"2026-07-04T00:00:00Z","ticket":"REVIEW","phase":"phase-7","type":"Requirement","question":"Curation identity binding for CO-7","options":["identity-bound-keyring","record-limitation-keep-labels"],"recommended":"identity-bound-keyring","spec_ref":"verdi-bench-phase-7-plan.md#decisions","author":"claude/plan"}
{"id":"REVIEW-D-P7-3","event":"resolved","ts":"2026-07-04T00:00:00Z","ticket":"REVIEW","answer":"identity-bound-keyring","rationale":"Accepted 2026-07-04. Supersedes the key-only half of EVAL-8-D-P4-3. Residual trust assumption recorded: the bar is as strong as keyring issuance (local unhashed operator state); one person holding two keyring identities is out of CO-7 scope","author":"jyang"}
{"id":"REVIEW-D-P7-4","event":"raised","ts":"2026-07-04T00:00:00Z","ticket":"REVIEW","phase":"phase-7","type":"Requirement","question":"EVAL-7 D003 disposition: render sensitivity kappa or delete kappa_report","options":["render-ipw-plus-floor-sensitivity","delete-kappa-report-record-ipw-only"],"recommended":"render-ipw-plus-floor-sensitivity","spec_ref":"verdi-bench-phase-7-plan.md#decisions","author":"claude/plan"}
{"id":"REVIEW-D-P7-4","event":"resolved","ts":"2026-07-04T00:00:00Z","ticket":"REVIEW","answer":"render-ipw-plus-floor-sensitivity","rationale":"Accepted 2026-07-04; kappa_report gains its production caller, EVAL-7 D003 resolves as designed","author":"jyang"}
{"id":"REVIEW-D-P7-5","event":"raised","ts":"2026-07-04T00:00:00Z","ticket":"REVIEW","phase":"phase-7","type":"Requirement","question":"The inert --concurrency knob","options":["remove","implement-real-concurrency"],"recommended":"remove","spec_ref":"verdi-bench-phase-7-plan.md#decisions","author":"claude/plan"}
{"id":"REVIEW-D-P7-5","event":"resolved","ts":"2026-07-04T00:00:00Z","ticket":"REVIEW","answer":"remove","rationale":"Accepted 2026-07-04; execution is serial by design, the knob stamps a caveat describing nothing real","author":"jyang"}
{"id":"REVIEW-D-P7-6","event":"raised","ts":"2026-07-04T00:00:00Z","ticket":"REVIEW","phase":"phase-7","type":"ContractChange","question":"Rubric content commitment in the lock","options":["additive-rubric-sha-in-lock","post-hoc-verdict-provenance-only","inline-rubric-into-spec"],"recommended":"additive-rubric-sha-in-lock","spec_ref":"verdi-bench-phase-7-plan.md#decisions","author":"claude/plan"}
{"id":"REVIEW-D-P7-6","event":"resolved","ts":"2026-07-04T00:00:00Z","ticket":"REVIEW","answer":"additive-rubric-sha-in-lock","rationale":"Accepted 2026-07-04 with refinements: (a) plan refuses to lock when the spec's rubric file is absent; (b) hash = sha256(read_text(utf-8).encode(utf-8)), bit-identical to the verdict-side computation in judge/packet.py, so lock and verdict hashes are comparable and CRLF drift is neutral; (c) legacy locks (absent field): judge warns, official render adds a caveat, and the fence refuses lock/verdict hash disagreement when both are present. Additive field on experiment_locked per the task_commitment precedent","migration":"absent field = pre-Phase-7 lock: warn-plus-caveat, never refuse legacy chains","author":"jyang"}
{"id":"REVIEW-D-P7-7","event":"raised","ts":"2026-07-04T00:00:00Z","ticket":"REVIEW","phase":"phase-7","type":"Requirement","question":"Actor provenance policy for the seven CLI swallow sites","options":["env-fallback-then-refuse","keep-unknown-with-warning"],"recommended":"env-fallback-then-refuse","spec_ref":"verdi-bench-phase-7-plan.md#decisions","author":"claude/plan"}
{"id":"REVIEW-D-P7-7","event":"resolved","ts":"2026-07-04T00:00:00Z","ticket":"REVIEW","answer":"env-fallback-then-refuse","rationale":"Accepted 2026-07-04 with refinement: corpus approve drops the environment fallback entirely and requires explicit --approver, because D-P7-3 makes approver identity security-relevant. Elsewhere this is fail-loud provenance, not authentication; headless environments pass --actor","author":"jyang"}
```

`docs/design/specs/eval1.decisions.ndjson`:

```
{"id":"EVAL-1-D008","event":"resolved","ts":"2026-07-04T00:00:00Z","ticket":"EVAL-1","answer":"required-before-official","rationale":"Phase-7 decision session 2026-07-04. Blocker removed by Phase 5 (nullsim at realized N). Refinements: selfcheck seed = sub_seed(spec.seed, 'selfcheck') so the check is deterministic and cannot be rerun-to-pass; failure => exploratory-only (official fence refuses); pass criterion = nominal CI level within the Wilson 95% interval of estimated coverage (self-scaling in n_sim). Ledgered as an additive selfcheck event; official fence gains the check","author":"jyang"}
```

Owning-slice appends: EVAL-7 D003 `resolved` (7E-3,
`eval7.decisions.ndjson`); EVAL-2 D002 clarification (7G-1,
`eval2.decisions.ndjson`); EVAL-8-D-P4-1 and EVAL-3-D-P4-1 amendments +
AN-11 acceptance (7G-3); the EVAL-8-D-P4-3 supersession is carried in
D-P7-3's resolved line above.

## Working method (per CLAUDE.md — unchanged)

Reproduce-first for every fix (plant the violation, watch it fail, fix,
watch it pass); `make verify` before every commit; atomic commits whose
messages say why; single responsibility per module; judgment calls (§5)
listed again in the implementation summary for veto.

## Sizing

~24 atomic commits: 0 (decisions) + 7A×4 + 7B×3 + 7C×3 + 7D×4 + 7E×3 +
7F×1 + 7G×3 + 7H×3 + 7I×2. 7A/7B/7D are the heavy slices; 7F/7G are small;
7I is medium (new verb + fence change).
