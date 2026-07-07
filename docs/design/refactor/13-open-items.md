# 13 — Open-items disposition (post-closeout)

**Status:** accepted 2026-07-06 (operator, interactive) — executes the seven
open items recorded in [12-closeout.md](12-closeout.md). Three implementation
workstreams (OI-A trivia, OI-B refusal base, OI-C A8 framing) + two formal
closures. Review model for this pass: the orchestrator (Fable) reviews every
diff directly; no reviewer subagents.

## Dispositions

| Item | Disposition |
|---|---|
| 1 A8 packet framing | **EXECUTE** (OI-C) — approved contract change, framing v2 |
| 2 `VerdiRefusal` base | **EXECUTE** (OI-B) |
| 3 scheduler split | **WON'T-DO** — recorded in decisions.ndjson; no active pain, pin-dense file |
| 4 CI proxy wiring | **CLOSE** (OI-A) — comment + decision record updated to the accurate rationale |
| 5 size notes | **EXECUTE** (OI-A) |
| 6 holdout-filename drift | **EXECUTE** (OI-A) — equality meta-test, not a move |
| 7 sidecar collisions | **DEFERRED** — recorded; spec a plan-14 only if it bites in real usage |

## OI-A — trivia batch (three independent commits)

1. **Size rationales (item 5):** one- or two-line size-reason notes in the
   module docstrings of `harness/ledger/events.py` (the 31-event registry +
   thin wrappers ARE the module), `harness/analyze/findings/sections.py`
   (canonical section model + every body builder; the ordering note already
   explains why not split further), `harness/analyze/dossier.py` (one
   three-layer artifact renderer + its closed sentence registry),
   `harness/analyze/card.py` (one versioned comparability artifact: model,
   serialization, renders, refusals). Match `extract.py:11-20`'s existing
   note style. No code changes.
2. **Close P0-PROXY-CI (item 4):** rewrite the `ci.yml` docker-job comment to
   the accurate current rationale — the managed-proxy path is fail-closed
   covered in CI by its own live e2e (`test_e2e_managed_proxy`); the gated
   `test_reference_proxy_log_attributes_allow_and_deny` exercises the
   external reference-Squid deployment, which CI does not provision, so
   `VERDI_REQUIRE_PROXY` stays an operator-run switch. Amend the
   `P0-PROXY-CI` line in decisions.ndjson to `closed` with that rationale.
3. **Holdout-filename equality meta-test (item 6):** a small test asserting
   `harness.grade.runners.HOLDOUT_RESULTS ==
   harness.run.workspace._GRADER_OUTPUT ==
   harness.contamination.scan._GRADER_OUTPUT` (import the private names —
   the test is the sanctioned cross-layer reader), with a docstring naming
   the layering reason the constants are not single-sourced. Add a
   `must match` cross-reference comment beside each copy.

Constraints: zero behavior change; `make verify` green per commit.

## OI-B — `VerdiRefusal` base (item 2)

Goal: a refusal type a verb forgot to enumerate becomes a clean named
refusal (exit 2) instead of a raw traceback — completing the fail-loud story
`cli_common.py`'s docstring explicitly defers.

1. **`harness/errors.py`** (new leaf module, stdlib-only): `class
   VerdiRefusal(Exception)` — a mixin-style base; docstring states the
   contract (str(err) is the operator-facing refusal; carrying it means
   "refused for a stated reason", never an internal bug). Add the module to
   the `.importlinter` all-packages source lists (the completeness meta-test
   forces this).
2. **Reparent the refusal hierarchies** onto it — mechanically, at the BASE
   class of each existing family so leaf types inherit transitively:
   `SpecError`, `LockError`, `TaskCommitmentError` family,
   `CantAnalyzeReason`'s `AnalyzeError`, grade's refusal bases
   (`GraderUnavailableError`, `GradingContainerError`, holdout errors),
   judge input/rubric errors, corpus refusal types (admit/manifest/
   benchmark/mine), contamination + process + review + forensics refusal
   types, hermetic `MeteringProxyError`/`TraceCollectorError`/
   `OtlpCoherenceError`, run's typed refusals, sdk workspace refusals,
   ledger `ActorResolutionError`/chain-integrity refusals. Sweep every
   `refusal_exit(...)` enumeration and every `except (A, B, ...)` CLI ladder
   to build the complete inventory; anything enumerated by a CLI today MUST
   end up a `VerdiRefusal`. Exception messages are pinned all over — adding
   a base changes no message and no existing isinstance behavior.
3. **Boundary wrap for third-party validation:** raw pydantic
   `ValidationError` escaping `ExperimentSpec.from_dict`/`from_yaml_text`
   (the tripwire extra-key / single-arm vectors) is wrapped at the schema
   boundary into a `SpecError`-family refusal **preserving the pydantic
   message text verbatim in `str()`** — the tripwire needles ("Extra inputs
   are not permitted", "at least 2 items") and every message-pinning test
   must keep matching. Same wrap anywhere else a raw `ValidationError`
   reaches a CLI (grep the CLIs; `RunConfigFile.parse` consumers already
   surface `ValueError` messages — wrap only where a traceback escapes
   today).
4. **`refusal_exit` end-state:** catches `VerdiRefusal` uniformly (keeping
   the per-verb `code=` parameter and any deliberate narrow enumerations
   where a verb maps different types to different codes); the module
   docstring's deferred-work paragraph is replaced by the delivered
   contract. Verbs' explicit enumerations may then shrink where they were
   pure boilerplate — but any verb that maps DIFFERENT refusals to
   DIFFERENT exit codes or messages keeps its explicit ladder.
5. **Deliberate behavior changes, each listed in the commit:** previously
   tracebacking paths now exit 2 with the same message text. Update the
   affected pins honestly: tripwires vectors that today pass via tracebacked
   output (needles unchanged; disposition strings unchanged), any CliRunner
   test asserting `exit_code == 1` + exception for these paths.
6. **New tests:** a meta-test that every exception type named in any
   `refusal_exit` enumeration is a `VerdiRefusal`; a boundary test that an
   unmapped-but-VerdiRefusal error surfaces as exit 2 with its message; the
   schema-wrap tests (message-verbatim property).

Constraints: no message text changes anywhere; byte-goldens untouched;
contracts stay 10/10 (plus the new module in the lists).

## OI-C — A8 packet framing v2 (item 1; approved contract change)

Goal: the verdict-JSON response contract becomes harness-owned packet
framing — un-omittable, rubric-independent — and the rubric returns to pure
judgment criteria.

1. **Framing:** `harness/judge/packet.py`'s system/framing scaffolding gains
   the canonical verdict-JSON block (the exact contract text currently in
   `harness/sdk/templates/judge-rubric.md` — winner/reason/evidence/
   confidence shape, evidence-locator rules, output-JSON-only). The framing
   fingerprint / packet-format version that feeds `packet_sha256` bumps —
   this is the DESIGNED lever; state the old/new version identifiers.
   Parse layer (`schema.py`, balanced extraction) unchanged.
2. **Template slims:** `judge-rubric.md` drops the format block (keeps
   judgment criteria + a one-line note that the response format is
   harness-supplied). The A8 single-source property inverts: the contract
   literal now lives in exactly ONE file, `harness/judge/packet.py` — grep-
   proven; the P7-1 sweep-style check moves/extends accordingly if one pins
   the old location.
3. **Golden ceremony (the sensitive part — explicit and documented):**
   `tests/fixtures/data/golden_ledger.ndjson` contains fake-judge
   `judge_verdict` events whose provenance embeds `packet_sha256`; if (and
   only if) the regeneration replay recomputes packets under framing v2,
   this is the ledger golden's FIRST-EVER regen: follow
   `regen_goldens.py`'s documented process, update `GOLDEN_HEAD_HASH` by
   HUMAN-STYLE explicit constant edit (the regen script refuses to
   auto-update — that refusal is the ceremony), and document in the commit
   message exactly which lines moved and why. First VERIFY whether the
   committed-bytes tests actually recompute packets (the
   `test_scenario_regenerates_byte_identical` path) — if the committed
   ledger remains self-consistent and no test recomputes packet bytes,
   say so and leave the golden untouched. Enumerate the disposition of
   EVERY golden fixture (ledger, constructors, renders, dossiers, card) in
   the report: changed-with-sanction or verified-unchanged.
4. **Fake-provider compatibility:** `DeterministicFakeProvider` routes on
   system-prompt substrings — verify framing v2 doesn't reroute it; update
   its routing keys deliberately if the framing text moves them.
5. **Tests:** packet framing tests updated deliberately (each listed); a new
   test asserting the verdict-JSON block is present in the assembled system
   framing for a rubric that lacks it AND not duplicated when an old-style
   rubric still embeds it is NOT required (duplication is harmless and
   old-style rubrics are user data) — instead assert the framing block's
   presence unconditionally; the judge e2e paths (fake) stay green; L2/L6
   real-judge scripts unchanged (their SDK rubric now arrives slim).
6. **Docs:** usage-guide §2.2 (rubric = judgment criteria; format is
   harness-owned), one-line touches where the old split is described.

Constraints: `CantJudgeReason` untouched; blinding scans untouched (the
framing is harness text, no new packet field); order-debias unchanged;
`rubric_sha256` lock commitment semantics unchanged (rubrics remain
sha-pinned user data — they just no longer carry the format).

## Sequencing

OI-A ∥ OI-B ∥ OI-C concurrently (disjoint file sets; OI-A's docstring
targets don't intersect OI-B's exception files or OI-C's judge files).
Orchestrator reviews each diff directly before merging; `make verify` +
shakedown after every merge; the full battery (docker, browser if surfaces
touched) at the end.
