# 06 — Ledger, forensics, contamination, process (Phases 1, 4–5)

**DECISIONS required:** A7 (events.py reorganization — byte-identity proven
by golden, approval still required as it is the named write seam); rubric
plumbing for analyze (§7).

## 1. `LedgerView` — one read facade (Phase 1; highest value / zero risk)

98 `find_events` call sites re-implement the same joins: trial indexes
built independently in ≥6 modules, latest-grade-by-trial in ≥8
(`forensics/scan.py:114-123` iterates GRADE twice), quarantine sets,
latest-report-wins, and a 70-line manual "full trial story" scan
(`status/trial.py:42-110`). Every `find_events` call re-reads and re-parses
the whole file (`query.py:96-98`); `analyze/report.py` does ~25 such calls
per render and `serve/bundle.py:91` calls a full-read helper once per
trial.

```python
# harness/ledger/view.py
class LedgerView:
    """Parse once; typed, memoized projections. Read-only by construction."""
    def __init__(self, ledger: Path, *, verify: bool = False): ...
    def by_kind(self, kind: str) -> list[dict]: ...
    def trials(self) -> list[TrialEventView]: ...        # handles trial_record +
                                                         # hoisted sha fields
    def latest_grade_by_trial(self) -> dict[str, dict]: ...
    def grades_by_trial(self) -> dict[str, list[dict]]: ...
    def verdicts_by_comparison(self) -> dict[str, dict]: ...
    def quarantined_trial_ids(self) -> set[str]: ...
    def latest(self, kind: str) -> dict | None: ...
    def trial_story(self, trial_id: str) -> TrialStory: ...   # the status/trial join
```

Strictly additive: `find_events` stays; consumers migrate opportunistically
starting with the worst offenders (analyze, status/trial, serve/bundle,
forensics/scan). Equivalence tests pin `LedgerView(p).x == hand_rolled(p)`
on the Phase-0 golden ledger. The SDK re-exports it
([02](02-experiment-sdk.md) §5).

## 2. Declarative event registry (Phase 5, gated on the Phase-0 goldens; **A7**)

`events.py` is 905 lines: 31 `register_event` calls + ~31 constructors that
all build a dict, apply per-field omit-if-None policy, and call `emit`.
Structural variance across all 31 is exactly: (a) which optional fields are
omit-if-None vs always-present-nullable (e.g. `record_selfcheck` writes
nullable fields always, `events.py:421-431`; `record_grade` omits five when
None, `:282-291`); (b) two constructors with light validation
(`:742-756`); (c) one payload reshape (`record_trial`'s sha hoist,
`:196-203`).

Target: an `EventSpec` table capturing precisely those degrees of freedom —

```python
EventSpec(name="grade", required=(...), omit_if_none=(...),
          always_nullable=(...), validate=None, reshape=None)
```

— with the 31 public constructors kept as thin wrappers (98 call sites
untouched). `REGISTERED_EVENTS` derives from the table; a new event becomes
a table row + wrapper, and the payload shape finally exists as data a
reader, docs generator, or meta-test can consume (fixing the D9 class
permanently: the "all shipped events registered" test sweeps the table).

Safety: `canonical_line` sorts keys (`chain.py:56-58`), so assembly order
is irrelevant — byte-identity is decided solely by (key set, values), which
the Phase-0 constructor-replay golden pins before, during, and after the
conversion. Physical file split is optional; if done, `harness/ledger/
events/` re-exports everything under the same module path so the
import-linter contract strings and all imports stay valid.

## 3. Detector & channel registries (Phase 4)

**Gaming detectors** have the right seam (pure functions over a frozen
`TrialEvidence` allowlist, `detectors.py:99-118`) but weak registration:
the id string is declared three times (`DETECTOR_IDS`, the returned dict,
`DETAIL_DETECTOR_IDS`), and nothing enforces the planted/clean fixture
convention for future detectors.

```python
@dataclass(frozen=True)
class Detector:
    id: str
    requires_detail: bool
    run: Callable[[TrialEvidence], dict | None]
DETECTORS: tuple[Detector, ...] = (...)
# DETECTOR_IDS / DETAIL_DETECTOR_IDS derived; run_detectors stamps d.id centrally
```

Plus a meta-test: every id in `DETECTOR_IDS` has a planted-violation case
*and* a clean case in the `_PLANTED_CASES` convention
(`tests/test_eval11_detectors.py:56-124`) — fixture ownership becomes
enforced, not customary. `FORENSICS_VOCABULARY_VERSION` remains the
sanctioned change lever; ids and emitted dict shapes are ledgered and never
change.

**Contamination channels** are the least extensible plug-point: inline code
in `run_memory_probe`'s loop (`probe.py:201-273`) with bare evidence-string
literals and shape-implicit payloads consumed by guessing readers
(`summary.py:36-47`). Extract each channel (canary regurgitation,
oracle-prefix-with-control, overlap merge) to a function with a declared
evidence label; give the `contamination_probe` payload a pydantic model
whose `model_dump` is asserted byte-compatible with today's dict (typing is
internal; any *shape* change would be contract-approval).

## 4. Shared fail-closed scorer envelope (Phase 4)

Three copies of the same sequence — empty-input CANT → leak re-scan CANT →
token gate CANT → resolve-provider-inside-envelope CANT → complete → parse
CANT — live in `forensics/review.py:204-263`, `process/score.py:229-276`,
and (variant) `contamination/probe.py:189-301`, with duplicated
`_heuristic_token_count` + limits and a triplicated `_JSON_RE`. One
parameterized envelope (`harness/judge/envelope.py`: reason-enum,
packet-builder, parser injected) serves all three. Constraints: the CANT
enums' **ledgered string values never merge or change** — a shared
transient-derivation helper is fine, the closed sets stay per-stage; the
comments admitting hand-sync ("kept in sync with TRANSIENT_CANT_JUDGE",
`score.py:78-79`) become code.

## 5. `run_forensics` decomposition (Phase 5)

175 lines, ~12 accumulators, one loop mixing ledger indexing, holdout
extraction, trajectory resolution, workspace verification, evidence
assembly, detector run, coverage bookkeeping, flight-recorder splicing,
advisory orchestration, and report assembly (`scan.py:88-264`). Split into
`TrialEvidenceAssembler` (ledger+disk → `TrialEvidence` + coverage notes,
on `LedgerView`), the detector pass, the advisory pass, and a report
builder. The ledgered report dict shape (`scan.py:244-260`) gets a golden
fixture *before* the move. `forensics/review.py`'s two concerns (LLM review
vs kappa calibration) split into two modules at the same time.

## 6. Entrypoint fixture blocks out of prod modules (Phase 4)

Seventeen modules carry import-time one-event sweep plumbing including
inline fixture spec dicts (`plan/lock.py:304-363` rewrites a YAML fixture;
`process/score.py:380-417` embeds a retired-model spec). Keep
`register_entrypoint(name, fn)` calls in the owning modules (import-time
firing and names are the property-test contract) but move fixture
*construction* to the SDK starter template ([02](02-experiment-sdk.md) §2)
— deleting the copy-pasted spec dicts and the YAML-rewriting helper from
production files.

## 7. Rubric plumbing (**DECISION**)

`analyze` folds process diagnostics with the **default v1 rubric**
regardless of what scored (`analyze/report.py:812-815` passes no rubric;
`calibrate.py:105` falls back), so custom-rubric dimensions silently vanish
from kappa/correlation tables while the means table above shows them — two
halves of one section can disagree. Options: (a) derive diagnostics
dimensions from the union of ledgered `dim_id`s (no new inputs, honest);
(b) add `--rubric` to analyze (reads an un-committed file — weaker
provenance). Recommendation: (a), plus ledger the rubric file sha on
`process_score` events (additive field) so (b) becomes possible with
provenance later. Human picks.

## 8. CLI kit

Covered in [02](02-experiment-sdk.md) §3 — the five `_resolve_actor_or_exit`
copies and the `EventContext` idiom collapse there; the duplicated
`_read_transcript` (`forensics/scan.py:75-85` = `process/cli.py:27-36`)
moves to one artifact-reading helper.

## 9. Invariants

Chain canonicalization bytes + `\n` framing + genesis; the **different**
anchor serialization (`ensure_ascii=True`, `anchors.py:73`) — now
golden-pinned; all 31 event names + payload key sets + per-field
null-policy; `record_trial` sha-hoist reader rule; `response_map`/reveal
shapes; one-event property (names + import-time registration);
`METRIC_IDS`/`DETECTOR_IDS`/CANT values/`ContaminationStatus`/probe
evidence strings (all ledgered); canary namespace
(`verdi-bench/contamination-canary/v1`); overlap `_K`/`_WINDOW`/threshold;
rubric-anchor version discipline; typed-constructor import contract
(module paths preserved through any split).
