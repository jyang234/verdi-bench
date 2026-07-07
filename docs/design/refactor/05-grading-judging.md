# 05 — Grading & judging (Phases 3–5)

**DECISIONS required:** A2 (versioned `holdout.json` schema), A3 (inline
`holdout:` task sugar), A8 (verdict-format ownership — explicitly deferred
to the human, not bundled).

## 1. Holdouts become a first-class polymorphic concept (Phase 3)

Today a "holdout" has no library representation: materialize writes
arbitrary JSON (`harness/corpus/materialize.py:104-114`), execution is
delegated wholesale to out-of-repo grader images, and the local runner just
reads a pre-placed `holdout_results.json` (`grade/container.py:263-275`).
Consequence: every shakedown script hand-runs assertion strings via
`python -c` and injects results (`harbor.py:54-58,100-107`,
`harbor_multiagent.py:68-77,124-129`, `_harness.py:81-93`), and tests write
the results JSON literally 25 times.

```python
# harness/grade/holdouts.py
class Holdout(BaseModel):                      # discriminated on `kind`
    id: str = "h1"
class AssertionHoldout(Holdout):               # kind="assertion"
    expression: str                            # exactly what shakedown hand-rolls
class PytestFileHoldout(Holdout):              # kind="pytest"
    path: str                                  # file under holdouts/<task>/
class CommandHoldout(Holdout):                 # kind="command"
    argv: list[str]                            # exit 0 = pass

    def materialize(self, holdouts_dir: Path) -> None: ...
    def execute(self, workspace: Path) -> list[Assertion]: ...   # subprocess,
        # PYTHONDONTWRITEBYTECODE=1 (the divergence the two shakedown copies
        # already grew), timeout, emits the existing assertions shape
```

- **`holdout.json` v1 (A2):** `{"schema_version": 1, "kind": ..., ...}`.
  A file without `kind` stays what it is today — opaque input for a bespoke
  grader image. Nothing existing breaks; declared kinds gain library
  execution.
- **Runners.** `LocalExecutingGradeRunner` joins the runner family: executes
  declared holdouts in a subprocess instead of expecting a pre-placed file.
  Its `grader_name` is non-`"docker"`, so analyze already stamps results
  ADVISORY with zero code change (`analyze/report.py:351-354`). The docker
  path gains a **generic grader entrypoint** — `harness.grade.run_holdouts`,
  sibling of `run_plugin.py` — which executes declared kinds inside the
  V2 nonce fence in the shipped grader image ([03](03-images-and-environments.md)
  §3); bespoke benchmark images remain fully legal.
- **Authoring sugar (A3).** `Task(holdout=AssertionHoldout(...))` in the SDK
  compiles to `holdouts/<id>/holdout.json` + the existing `holdouts_dir`
  key. Optionally an inline `holdout:` task field — additive, sha-covered,
  written pre-lock only.
- **Insulation unchanged:** the agent-visible/holdout split and
  `agent_visible_leak` check (`materialize.py:39-45,125-135`), the holdout
  canary defense, and grade's exclusion of `holdout_results.json` from
  judged diffs stay exactly as-is. The `holdout_results.json` filename gets
  **one** constant (today defined independently at `grade/container.py:34`
  and `judge/assemble.py:22`).

Migration: shakedown holdout dicts become `AssertionHoldout`s; `inject_grades`
and both `run_holdout` copies are deleted; the arm-blind fake-engine
injection pattern (a *designed* operator step, `docs/design/shakedown.md:99-104`)
gets a first-class name in test fixtures (`write_holdout_results`,
[01](01-safety-nets.md) §2) rather than 28 hand-rolled copies.

Constraining tests: eval5 grade ACs, materialize insulation ACs, tripwire
#14 (holdout canary), e2e pipeline; fence transport tags and nonce
discipline are frozen (`container.py:60-65,450-456`).

## 2. `grade/container.py` split + runner protocol (Phase 5)

Four concerns in one 494-line file, with the fail-closed logic
triple-copied: `_run_plugins_in_container` re-implements the fresh-copy
discipline, the exit-code classification, *and* the fence-extraction ladder
(`container.py:377-437` vs `:459-494`, `:218-235`, `:148-179`).

- Split: `fence.py` (pair/extract/parse — the versioned transport),
  `runners.py` (Docker/Local/LocalExecuting), `isolation.py` (one
  fresh-copy + stale-removal helper), plugin launch beside `plugins/`.
- Formalize the runner seam: `preflight`, `grader_name`,
  `runs_plugins_in_container`, `grades_in_place` become declared protocol
  members instead of four `getattr` probes with silent defaults
  (`container.py:288-290,350,366,445`) — a new runner must decide, not
  inherit accidents.
- Docker mechanics route through `harness/hermetic` ([04](04-run-engine.md)
  §1), deleting the duplicated hardened-argv recipe.

## 3. Provider seam hardening (Phase 4)

The `Provider` protocol declares one of a provider's four real obligations
(`providers/base.py:56-57`); usage travels via a `last_usage` mutable
side-channel read by `getattr` (`judge/client.py:96`) — forget to set it
and the token ceiling silently never accumulates.

- `complete(...) -> Completion(text: str, usage: Usage | None)`; delete the
  side-channel; four call sites migrate mechanically (`judge/client.py:93-97`,
  `process/score.py:255`, `forensics/review.py:242`,
  `contamination/probe.py:197`).
- Registry dict replaces the if/elif in `get_provider`
  (`providers/base.py:78-101`); vendor imports stay lazy (grade/status/serve
  LLM-free contracts depend on it); unknown prefixes still raise
  `ProviderError` → `CANT_*(provider_error)`.
- Wording constraint: `test_ac1_no_vendor_denylist_in_code` greps for
  denylist-family words in these files — keep comments clean.
- The shared fail-closed *envelope* around providers is unified in
  [06](06-ledger-telemetry.md) §4 (it spans process/forensics/contamination
  too).

## 4. One judging session, two verdict sinks (Phase 4)

`judge/cli.py:146-171` and `judge/reuse.py:164-203` duplicate the loop
(skip-already with twin `_is_transient` copies, ceiling check + stop event,
packet build, `judge_pair`, usage accumulation); the pairing assemblers are
near-parallel as well (`assemble.py:148-188` vs `reuse.py:46-131`), and two
different functions named `comparisons_from_ledger` exist in the codebase.

- Extract `JudgingSession` (comparison source + verdict sink injected);
  both paths become thin callers; one `_is_transient`.
- Rename the review-side `comparisons_from_ledger`
  (`review/sample.py:68`) or the judge one to kill the name collision.
- Event kinds, idempotency semantics, and ordering are pinned by the eval2
  CLI/token-ceiling suites — zero observable change.

## 5. Blinding: from discipline to property (Phase 4)

The chain is strong (packet allowlist-by-signature, canary scan, fail-closed
CANT on leak), but two refactor hazards are hand-maintained:

- The scan blob lists are per-field by hand — the secret scan already
  drifted (D5, fixed in Phase 0 with a field-coverage meta-test:
  every text-bearing `Packet`/`ResponseArtifacts` field must appear in both
  the identity and secret scans, enforced by introspection).
- `judge_pair(canaries=None)` silently degrades to the generic corpus for
  any new call site that forgets the argument (`client.py:110`). Make
  `canaries` required at the `JudgingSession` layer (the session derives it
  from the locked spec once); the low-level default stays for tests.
- `arm_map` remains verdict-event-only — an assertion that it never enters
  `Packet`/render inputs joins the packet tests.

## 6. Task typing (Phase 1, with D2)

`TaskSpec` ([02](02-experiment-sdk.md) §2) normalizes the vocabulary that
caused the live `plugins`/`plugin_ids` drift (D2). Hash discipline: the
task-content sha continues to hash the **raw source entry**
(`corpus/commit.py:38-48`), never a re-serialized model — the model types
reads, not bytes. The fake-path scripting fields (`fake_holdout_output`,
`fake_plugin_output`, `types.py:42-44`) are documented as the fake-engine
test seam and emitted only by builders, not hand-written.

## 7. Verdict format ownership (**A8 — decision, not a refactor**)

The verdict-JSON instruction lives in user rubrics (the packet system
prompt never specifies it, `packet.py:55-61`; shakedown embeds it,
`harbor_multiagent.py:105-116`). Moving it into harness-owned framing would
centralize parsing guarantees but shifts `packet_sha256` (framing
fingerprint, `packet.py:95-110`) and interacts with post-lock rubric
pinning. Recommendation: adopt it behind a framing-version bump, with the
SDK rubric template ([02](02-experiment-sdk.md) §2) as the interim single
source. Present both options to the human; do not bundle into other work.
Related robustness item (either way): replace the greedy `\{.*\}` verdict
extraction (`client.py:45`) with balanced-brace extraction — fail-closed
behavior preserved, fewer spurious CANT_JUDGE(parse).

## 8. Invariants

Grade/judge event shapes; `CantGradeReason`/`CantJudgeReason` values +
transient subsets (idempotency keys on them); rubric sha commitment;
packet sha + framing fingerprint (moves only under A8); fence transport
tags + CSPRNG nonce; both-orders + order-inconsistent→TIE; the
grade-has-no-LLM-clients contract (any holdout/runner code stays
structurally LLM-free); one-event entrypoint registrations
(`deterministic.py:212-218`, `client.py:298-304`, …) keep firing at import
with unchanged names through any module split.
