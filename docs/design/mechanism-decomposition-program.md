# The mechanism-decomposition program

> Design doc, human-approved 2026-07-09. Follow-up to the consistency program
> (`docs/WALKTHROUGH.md`, `runs/consistency/INDEPENDENT-REVIEW.md`). This
> document is the design of record; the implementation plan and any story
> spec (AC-mapped) derive from it.

## The question

The consistency program's one robust effect — gw-r5, bare 0/17 → enforced
16/17 at haiku — is an outcome with an unattributed mechanism. The rung-3
treatment bundles at least three ingredients:

1. **the block itself** — the agent is forced to stop and re-attempt;
2. **the gate's findings content** — the specific violated rule and path,
   fed back into the loop;
3. **the trap's bait** — prompt wording that steers the bare arm into the
   violation ("must not block or delay the send").

The independent review (§5.3) identified exactly this decomposition as the
highest-value cheap follow-up. This program separates the ingredients and,
for free, decomposes the fused score whose composition the review flagged as
the report's biggest omission (§3.1).

Owner-facing framing: if the placebo (ingredient 1 alone) reproduces the
rescue, verdi-go's headline must be rewritten from "the map's findings drive
the fix" to "any forced re-review drives the fix." If it does not, the
enforcement claim gains the mechanism attribution it currently lacks.

## Program shape

Four pieces, fixed order, each informing the next. All new experiments:
haiku (`anthropic/claude-haiku-4-5-20251001`), pinned trial image
`claude-code-groundwork:pinned10` and grader digest from the consistency
program, fresh seeds, per-experiment cost ceilings, publish-the-null, prose
pre-registration hashed into the lock event (see rider). Total new spend
budget: **$15 ceiling, ~$9–10 expected.**

**Stop-and-reassess gate:** after `md-placebo` grades, results go back to the
human before `md-pointer` / `md-debait` lock. If the placebo reproduces the
rescue, their framing (and possibly their designs) change.

### Piece 0 — `decompose-scores` (retrospective, $0)

A standalone analysis script, `scripts/flagship/decompose_scores.py`, that
walks the seven existing `runs/consistency/<E>/ledger.ndjson` chains and, for
every graded trial, re-executes the fused holdout's two components
**separately** against the preserved workspace, inside the pinned grader
image:

- functional channel: copy the hidden feature test, `go test ./...`;
- structural channel: `verdi-groundwork-check <task>`.

Output: a decomposed table (functional-pass / gate-pass / fused-pass per
task × arm × experiment) written as a report addendum under
`runs/consistency/`, cross-checked against the advisory `plugin:groundwork`
assertions already recorded in each grade event.

Constraints:
- **No ledger mutation.** Existing chains are immutable; the script emits an
  analysis artifact, not grade events. No grading-contract change.
- Reuses `harness/grade/holdouts.py` execution machinery
  (`CommandHoldout.execute` semantics) rather than reimplementing grading.
- Workspaces are resolved from each `trial` event's recorded
  `artifacts_path`, exactly as `grade_experiment` does.

### Piece 1 — `md-placebo` (confirmatory, 12 reps, ~$4)

The decisive probe: ingredient 1 (blocking) without ingredient 2 (findings).

- **Task:** gw-r5 only. (gw-r2 excluded by design: at haiku it is a
  capability wall — 4/5 enforced trials exhausted all rounds still blocked —
  so it adds noise, not signal, to a mechanism question.)
- **Arms:** fresh `bare` vs `placebo_gate`, 12 reps each = 24 trials.
- **Treatment (`placebo_gate`):** byte-identical to rung-3 enforced —
  groundwork tools staged, same rung-2 `--append-system-prompt` token, same
  3-round fail-open Stop-hook machinery, same `groundwork-enforce.jsonl`
  logging — except the hook never runs `flowmap graph` / `groundwork
  review` and blocks every Stop (until round exhaustion) with static text:

  > "Review your changes for policy compliance before finishing."

  No findings, no rule names, no paths. The hook does not need the
  tamper-proof base-graph/policy copies (it reads nothing).
- **Comparators:** in-experiment paired bare (primary), plus the historical
  program-wide anchors: bare-haiku r5 = 0/32, enforced-haiku r5 = 16/17.
- **Pre-registered bound readings (frozen at lock):**
  - placebo ≤ 2/12 → the findings content is the active ingredient; the
    enforcement claim is strengthened and gains mechanism attribution.
  - placebo ≥ 9/12 → generic forced re-review suffices; the consistency
    program's headline must be rewritten (the map's content is not doing
    the work on r5).
  - 3–8/12 → both ingredients contribute; report the split, no headline
    change either direction without a follow-up.
- **Secondary endpoints:** rounds-to-clean distribution, cost premium vs
  bare, harm check (placebo must not break the feature tests the bare arm
  passes — recall 100% of bare r5 failures were gate-only).

### Piece 2 — `md-pointer` (exploratory, 5 reps, ~$2)

The cheapest possible treatment: does merely *pointing at the policy*
rescue anything?

- **Tasks:** gw-r2 + gw-r5 (the two 0%-bare-at-haiku gate-discriminated
  tasks). **Arms:** `bare` vs `policy_pointer`, 5 reps = 20 trials.
- **Treatment (`policy_pointer`):** no tools, no MCP config, no hook. One
  appended system-prompt line:

  > "This repository declares structural policy in `policy.json`; your
  > change must honor it."

- **Readings:** expected null (instructed-rung agents saw BLOCK verdicts
  in-session and shipped anyway) → strengthens the forcing-function story.
  Any material rescue → the enforcement stack is overkill for the
  demonstrated effect and the walkthrough's rung ladder gains a rung 1.5.

### Piece 3 — `md-debait` (exploratory, 5 reps, ~$3)

How much of bare-haiku's 0/32 on r5 is trap engineering?

- **New task `gw-r5b`:** byte-identical copy of `gw-r5` except `prompt.md`
  drops the bait sentence ("Auditing is bookkeeping and must not block or
  delay the send — …"), leaving a neutral statement of the audit
  requirement. Same workspace, solution, holdout construction, policy.
- **Arms:** `bare` vs `ground_verify_enforced`, 5 reps = 20 trials.
- **Readings:** bare violation rate without the bait is the estimate of
  natural (un-steered) violation propensity; the enforced arm checks the
  rescue survives a neutral prompt. Both are scope qualifiers for the
  walkthrough's external-validity section, not headline changes.

## Instrument changes (all reproduce-first, `make verify` green)

### Treatment arming — `images/reference/claude-code-groundwork/agent.py`

- **`placebo_gate`:** a second embedded hook-script constant
  (`PLACEBO_HOOK_PY`) sharing the round-counter / `block()` / logging
  machinery and `MAX_ROUNDS = 3`, minus the build/graph/review subprocess
  calls; a plan-builder branch selecting it; `placebo_gate` added to the
  known-workflow set mapping to the existing rung-2 prompt text. The
  base-graph/policy `file_copies` are skipped for the placebo.
- **`policy_pointer`:** cannot ride the `workflow` mechanism (the code
  couples `--append-system-prompt` to groundwork tools being present, and
  refuses a workflow without the tool). New payload key
  (`system_prompt_extra`) with its own arming path: emits exactly one
  `--append-system-prompt` token, stages no tools, installs no hook.
  Fail-closed posture preserved: unknown payload shapes still mean
  "control."

### Authoring kit — `scripts/flagship/author_consistency.py`

- Two new entries in `GROUNDED_PAYLOADS_BY_WORKFLOW`
  (`placebo_gate`, `policy_pointer` — the latter emitting the
  `system_prompt_extra` payload, not a `workflow` key).
- `EXPECTED_TASK_IDS` extended with `gw-r5b` (the kit refuses any corpus
  that is not exactly the expected id set — the extension is deliberate and
  test-covered).
- The kit stays 2-arm; every program experiment is bare-vs-treatment.

### Corpus — `corpora/groundwork-v0`

- New checked-in task dir `tasks/gw-r5b/`; rebuild via `build_tasks.py`;
  corpus version bumped (`groundwork-v0` → `groundwork-v0.1`) since the
  task set is hash-committed. Existing 16 tasks byte-untouched.

### Rider — hash `PRE-REGISTRATION.md` into the lock event

Closes the provenance gap the review flagged (§3.7): prereg prose is
currently gitignored, unhashed, and mtimes-forgeable.

- `lock_experiment` gains a `prereg_sha256` committed alongside
  `rubric_sha256`, computed over the `PRE-REGISTRATION.md` bytes when the
  file exists beside the spec.
- **Contract note (human-approved with this design):** this extends a
  hash-chained, versioned event format. The field is *optional and
  additive*: absent on all historical ledgers, present on new locks;
  `verify-chain` semantics unchanged (old chains verify as before); tests
  cover both shapes. No migration of existing chains.

## What this program does not do

- No multi-arm (>2) experiment support; the schema allows it, the authoring
  kit does not, and no piece here needs it.
- No behavioral-oracle (`-race`) tasks, no independent corpus, no base-rate
  study, no cross-model arms — those are the next program, contingent on
  the placebo verdict.
- No change to grading contracts, holdout formats, or existing ledgers.

## Order of operations

1. Rider + `decompose-scores` (pure instrument work, $0) — run the
   decomposition, publish the addendum.
2. `md-placebo`: author → lock (prereg hashed) → run → grade → attest →
   verify-chain. **Human checkpoint on results.**
3. `md-pointer`, `md-debait` (framing confirmed or revised at the
   checkpoint).
4. Program addendum to `runs/consistency/` reporting all outcomes,
   including nulls.
