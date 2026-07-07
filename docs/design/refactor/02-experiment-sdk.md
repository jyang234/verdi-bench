# 02 — Experiment SDK: the write path (Phases 1–2)

The single largest gap the audit found: the library can *validate* everything
and *author* nothing. `ExperimentSpec.from_dict` exists
(`harness/schema/experiment.py:361`) but no serializer; tasks.yaml has no
model at all (`harness/corpus/commit.py:89-112` validates id-uniqueness
only); `run.config.yaml` is hand-parsed (`harness/run/settings.py:86-161`);
every stage verb's body is a typer closure with no library equivalent
(`harness/run/cli.py:35-120`), which is why shakedown drives a subprocess
(`scripts/shakedown/_harness.py:41-51`) and why the "example spec" exists as
≥5 divergent copies (docs, test builders, author page template, entrypoint
fixture blocks, shakedown scripts) — several still naming a retired model id.

**DECISIONS required:** A5 (import-linter additions + "sdk is a leaf"
contract); A9 (tasks read-side strictness — recommended: lint verb only).

## 1. Layering rule

Primitives live in the subsystem that owns the concern; `harness/sdk` only
composes and re-exports. Concretely:

| Capability | Owner (new code) | SDK role |
|---|---|---|
| Spec model + serializer | `harness/schema` (`spec_to_yaml`) | re-export |
| Task model (`TaskSpec`) + writer | `harness/schema/tasks.py` (model), `harness/corpus/commit.py` keeps the loader | builder sugar |
| Run-config model | `harness/run/settings.py` (`RunConfigFile`, see [04](04-run-engine.md) §4) | builder sugar |
| Manifest builder | `harness/corpus` (three near-identical hand-writers exist: `tripwires.py:33-38`, `official.py:36-41`, `test_eval6_analyze.py:38-50`) | re-export |
| Stage functions | each stage subsystem (`run/api.py`, `grade/api.py`, …) | `ExperimentWorkspace` methods |
| Ledger reading | `harness/ledger/view.py` (`LedgerView`, [06](06-ledger-telemetry.md) §1) | re-export |
| Rubric + starter templates | `harness/sdk/templates/` (data files) | owner |
| Infra managers | `harness/hermetic` ([04](04-run-engine.md) §1) | re-export |

Import direction: `sdk → subsystems`, never the reverse — proposed as a new
import-linter contract (**A5**), alongside adding `harness.sdk` to the
ledger-writes and harbor-confinement source lists (the completeness meta-test
`tests/test_import_contracts.py:40-60` forces this in the same commit).
`harness.review` must not import `harness.sdk` (it would transitively reach
serve/status/author observers' modules is *not* the issue — sdk is; keep the
reviewer surface consuming only its own subsystem, as today).

## 2. Builders (Phase 1)

All builders emit through the **existing** pydantic validators — one
validation source, zero new rules.

```python
# harness/schema/serialize.py
def spec_to_yaml(spec: ExperimentSpec) -> str:
    """Serialize for *pre-lock* writing only. The lock hashes raw file bytes
    (harness/plan/lock.py:99-106); nothing may ever rewrite a locked file —
    assert_lock already refuses drift (lock.py:288-297)."""

# harness/sdk/experiment.py
class Experiment:                       # thin, fluent; collects then validates
    def __init__(self, name, *, seed: int, cost_ceiling_usd: float, currency="USD"): ...
    def arm(self, name, *, model, platform="generic", image=None, payload=None,
            training_cutoff=None, aux_models=(), model_hosts=None) -> "Experiment": ...
    def judge(self, model, *, rubric: str | Path | None = None,
              orders="both", temperature=0, escalation=None) -> "Experiment": ...
    def task(self, task: Task) -> "Experiment": ...
    def decision(self, metric="holdout_pass_rate", op=">", threshold=0.0) -> "Experiment": ...
    def build(self) -> tuple[ExperimentSpec, list[TaskSpec], RubricText]: ...
    def write(self, dir) -> ExperimentWorkspace:   # experiment.yaml, tasks.yaml,
        ...                                        # rubric.md, holdouts/, run.config.yaml
```

- Defaults are the documented recommendations (`orders: both`,
  `temperature: 0`); **seed and cost ceiling are required arguments** — the
  determinism and cost-fence directives forbid silent defaults for them.
- `Task` carries `prompt`, `image`, `task_class`, optional `holdout=`
  ([05](05-grading-judging.md) §1 — builder writes `holdouts/<id>/…` and the
  `holdouts_dir` key), optional `fake_behavior` (naming, at last, the fake
  engine's scripting API that 32 test files use undocumented).
- `TaskSpec` is `extra="forbid"` on the **write side** only. Read side stays
  lenient (the loader feeds the lock hash; tightening it is **A9** — ship a
  `bench tasks validate` lint verb instead, refusing unknown keys and the
  known drift traps like `holdout_dir` for `holdouts_dir`).
- Templates: one starter spec + one judge rubric (including the verdict-JSON
  format block currently hand-embedded in
  `scripts/shakedown/harbor_multiagent.py:105-116`) as data files under
  `harness/sdk/templates/`. The author surface's draft seeding
  (`harness/author/page.py:116-133`), `tests/fixtures/builders.py`, docs
  snippets, and the entrypoint fixture blocks all consume it — a
  docs-consistency test pins the usage-guide example to the template so the
  ≥5 copies can never diverge again.

## 3. Stage APIs (Phase 1)

Extract each verb body into a plain, typed function; the typer command
becomes a shell (argument parsing, refusal→exit-code mapping, echo). Pattern
per subsystem, e.g.:

```python
# harness/run/api.py
def run_experiment(exp_dir: Path, *, engine: str = "fake", actor: str | None = None,
                   reuse_control: Path | None = None) -> RunOutcome: ...
# harness/grade/api.py
def grade_experiment(exp_dir: Path, *, runner: str = "docker", actor=None) -> GradeOutcome: ...
# likewise judge/api.py, analyze/api.py, forensics/api.py, contamination, process, review, corpus
```

Constraints that make this safe:

- The one-event-per-operation property keys on registered entrypoints
  (`harness/entrypoints.py`, swept by `tests/test_eval3_property.py:43-89`)
  — registrations move with the function bodies, names unchanged.
- Exit codes and refusal text stay in the CLI shells; AC tests that drive
  `CliRunner` (27 files) keep passing untouched.
- The CLI kit (one `refusal_exit` helper + one actor-resolution helper)
  replaces the five copies of `_resolve_actor_or_exit`
  (`corpus/cli.py:22`, `forensics/cli.py:24`, `review/cli.py:18`,
  `contamination/cli.py:20`, `process/cli.py:18`) and the repeated
  try/except-enumerated-errors ceremony (`harness/cli.py:88-96` et al.). A
  refusal type missing from a verb's enumeration currently surfaces as a raw
  traceback; the kit maps any `VerdiRefusal`-derived error uniformly.

## 4. Lock preflight decomposition (Phase 1)

`lock_experiment` is ~170 lines doing 7 jobs with the double-lock check
duplicated inside and outside the flock (`harness/plan/lock.py:77-244`,
`:132`/`:221`), and the author preview re-implements *some* of its checks —
green preview, then `UnknownArmPlatformError` at lock
(`harness/author/server.py:207-232`). Decompose into independently callable
preflight steps (spec-parse+hash, platform capability, chain integrity,
single-lock, power gate, rubric commitment, task commitment); the lock
composes them inside the flock; the author preview composes the **same
list** — parity by construction. Also: single `spec_sha256` source
(delete the duplicate at `harness/author/server.py:366-370`), typed
`MdeReport` with `.to_event_payload()` preserving today's exact keys
(`harness/plan/power.py:225-236`; the `power_gate_skipped` mutation at
`lock.py:172-173` becomes a lock-stage field). Genesis event bytes are
pinned by the Phase-0 constructor-replay golden.

## 5. `ExperimentWorkspace` facade (Phase 2)

```python
# harness/sdk/workspace.py
class ExperimentWorkspace:
    def __init__(self, exp_dir: Path): ...
    ledger: Path                                  # ledger.ndjson beside the spec
    def plan(self, *, actor) -> LockOutcome: ...
    def run(self, *, engine="fake", actor=None) -> RunOutcome: ...
    def grade(self, *, runner="docker") -> GradeOutcome: ...
    def judge(self) -> JudgeOutcome: ...
    def forensics(self, *, review_model=None) -> ForensicsOutcome: ...
    def selfcheck(self) -> SelfcheckOutcome: ...
    def analyze(self, *, exploratory=False, official_corpus=None) -> FindingsDocument: ...
    def verify_chain(self, *, anchors=None) -> ChainVerdict: ...
    def view(self) -> LedgerView: ...             # typed reads; no more hand-rolled joins
    def status(self) -> StatusSnapshot: ...
```

Every method is a one-line delegation to a stage API — the facade owns *no*
logic (single-responsibility directive). This replaces
`scripts/shakedown/_harness.py`'s `bench()` subprocess driver and the
`events`/`event_counts` helpers, and gives tests a third option beyond
CliRunner/subprocess where the CLI itself isn't under test.

Optional, cheap, recommended: `bench init <dir>` scaffolding from the starter
template (the author surface already seeds drafts; this is the no-browser
equivalent).

## 6. Schema facade

`harness/schema/__init__.py` is empty today; re-export `ExperimentSpec`,
`TaskSpec`, `JudgeConfig`, `PrimaryMetric`, and the `SpecError` hierarchy so
SDK users import from one place. No contract impact (import-linter references
the package, not submodules).

## 7. Constraints & invariants

- **Never rewrite a locked file.** Builders serialize pre-lock only; the sha
  is over raw bytes (`lock.py:60-66`). No canonicalize-on-save features.
- New `ExperimentSpec`/`TaskSpec` fields: optional-with-default only
  (compatibility stance at `harness/schema/experiment.py:263-268`);
  `extra="forbid"` makes version skew one-directional — document it.
- The one-event property, AC tests, and CLI text are behavior pins; stage
  extraction is observable-behavior-neutral.
- W2 rot (schema field → author preview → docs drifting apart, as
  `judge.token_ceiling` demonstrably did) gets a forcing function: the
  preview renders from the shared preflight list (§4) and the docs example
  from the shared template (§2).

## 8. Acceptance

- The master plan's north-star snippet runs as written on the fake engine.
- `golden.py` on the SDK is ≲40 lines of calls + assertions; `tripwires.py`
  keeps only its 18 vector definitions + expected reason strings.
- Grep proof: the retired-model example spec exists in exactly one file
  (the template), consumed everywhere else.
