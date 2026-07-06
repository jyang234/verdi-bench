"""Golden serialization-guard scenario builders [refactor 01 §1].

Single source of truth for the Phase-0 golden fixtures: the committed golden
ledger (lock → trials → grades → judge verdicts → findings_rendered), the
constructor-replay ledger covering every registered event type, the anchor
store, and the render byte-fixtures (findings markdown, dossiers, card).

Both the checked-in regeneration script (``tests/fixtures/data/regen_goldens.py``)
and the guard tests (``tests/test_golden_*.py``) import THIS module, so the
bytes a test regenerates are produced by exactly the code that produced the
committed fixture — any writer-side serialization drift breaks byte equality.

Determinism contract:

* every ledger write uses a fixed :class:`~harness.ledger.events.EventContext`
  (synthetic monotonic clock, fixed actor — no wall clock);
* instrument identity is pinned via :func:`pin_instrument` (the provenance
  stamp would otherwise embed the CURRENT git sha, making every fixture
  commit-dependent — the [refactor 01 §1] item-2 trap);
* the experiment spec/rubric are literal byte constants (never re-serialized
  through yaml, so a pyyaml formatting change cannot move the locked sha);
* the lock is taken with *relative* paths under a ``contextlib.chdir``, so the
  ledgered ``spec_path`` is machine-independent;
* all randomness is seeded from the locked spec seed.
"""

from __future__ import annotations

import contextlib
import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

# --- pinned identity ---------------------------------------------------------
# The provenance stamp on every event is {version, git_sha} from
# harness.version.instrument_identity(). git_sha is the CURRENT checkout HEAD,
# so an unpinned fixture would change bytes on every commit. The golden
# fixtures pin an obviously-synthetic identity instead; tests assert no live
# sha ever reaches fixture bytes.
PINNED_INSTRUMENT_VERSION = "0.0.0+golden"
PINNED_INSTRUMENT_GIT_SHA = "f" * 40

GOLDEN_EXPERIMENT_ID = "golden-mini-ab"
GOLDEN_ACTOR = "golden-fixture"

# Head hash of the committed golden ledger's last line — the pinned constant
# [refactor 01 §1 item 1]. Changing it is changing the serialization contract:
# only a deliberate, human-approved regeneration may update it (see
# tests/fixtures/data/regen_goldens.py).
GOLDEN_HEAD_HASH = "71711ccc47842cf48739828b48bcbf5f625abc55d14587e952500b04a454bf1c"

# Anchor-store timestamps (injected — anchors take ts as a parameter).
GOLDEN_ANCHOR_TS_LOCK = "2026-02-03T05:00:00+00:00"
GOLDEN_ANCHOR_TS_HEAD = "2026-02-03T05:00:01+00:00"

# Analysis parameters for the golden renders — reduced from the CLI defaults so
# the guard tests stay fast; the render *pipeline* (and therefore its bytes)
# is identical, only resample counts differ.
COVERAGE_N_SIM = 40
N_BOOT = 500

# The experiment spec as literal bytes. Its sha256 is committed inside the
# golden ledger's experiment_locked event, so this constant and the committed
# tests/fixtures/data/golden_experiment.yaml must stay byte-identical
# (asserted by the guard tests). Never reformat.
EXPERIMENT_YAML = """\
# Golden serialization-guard experiment [refactor 01 §1]. The sha256 of this
# exact file is committed in golden_ledger.ndjson's experiment_locked event —
# do not reformat.
arms:
  - name: control
    platform: claude_code
    model: anthropic/claude-haiku-4-5-20251001
    training_cutoff: "2025-02-01T00:00:00Z"
    payload: {}
  - name: treatment
    platform: codex
    model: openai/gpt-4.1-mini-2025-04-14
    training_cutoff: "2025-04-01T00:00:00Z"
    payload: {}
corpus:
  id: public-mini
  version: 1.0.0
repetitions: 2
primary_metric: holdout_pass_rate
decision_rule: delta_holdout_pass_rate > 0
judge:
  model: fake/deterministic-2026-01-01
  rubric: rubrics/golden-rubric.md
  orders: both
  temperature: 0
seed: 20260203
cost_ceiling:
  amount: 10.0
  currency: USD
"""

RUBRIC_MD = """\
# Golden judging rubric

Judge on correctness against the task prompt; prefer the response whose
solution the holdout evidence supports.
"""

# Task set committed at lock (task_commitment) — ids feed the card battery.
GOLDEN_TASKS: list[dict] = [
    {
        "id": f"task{i}",
        "prompt": f"Golden task {i}: write solution.py implementing spec {i}.",
        "holdouts_dir": f"holdouts/task{i}",
    }
    for i in range(5)
]

# The realized outcome grid: control passes every (task, rep) cell; treatment
# fails exactly these cells. Per-task deltas (control − treatment, reps
# averaged) come out [0.5, 0.5, 1.0, 0.5, 0.0] — a genuinely detected effect
# whose selfcheck genuinely passes, so the official fence clears without being
# touched [refactor 01 §1 item 4].
_TREATMENT_FAILS: set[tuple[str, int]] = {
    ("task0", 1),
    ("task1", 1),
    ("task2", 0),
    ("task2", 1),
    ("task3", 1),
}

GOLDEN_TASK_CREATED_AT = "2026-01-15T00:00:00Z"


@contextlib.contextmanager
def pin_instrument() -> Iterator[None]:
    """Pin instrument identity for every provenance-stamping consumer.

    ``instrument_identity`` is imported *by name* into ``harness.ledger.events``
    and ``harness.analyze.report``, so each bound reference must be swapped —
    patching only ``harness.version`` would leave the consumers reading the
    live git sha [refactor 01 §1 item 2 trap].
    """
    import harness.analyze.report as report_mod
    import harness.ledger.events as events_mod
    import harness.version as version_mod

    def pinned() -> dict:
        return {
            "version": PINNED_INSTRUMENT_VERSION,
            "git_sha": PINNED_INSTRUMENT_GIT_SHA,
        }

    targets = (version_mod, events_mod, report_mod)
    originals = [t.instrument_identity for t in targets]
    for t in targets:
        t.instrument_identity = pinned
    try:
        yield
    finally:
        for t, orig in zip(targets, originals):
            t.instrument_identity = orig


def golden_clock(start: Optional[datetime] = None) -> Callable[[], str]:
    """A deterministic monotonic clock: one second per tick from a fixed base."""
    base = start or datetime(2026, 2, 3, 4, 5, 6, tzinfo=timezone.utc)
    ticks = iter(range(1_000_000))

    def clock() -> str:
        return (base + timedelta(seconds=next(ticks))).isoformat()

    return clock


def golden_ctx():
    """The fixed EventContext every golden ledger write uses."""
    from harness.ledger.events import EventContext

    return EventContext(
        experiment_id=GOLDEN_EXPERIMENT_ID, actor=GOLDEN_ACTOR, clock=golden_clock()
    )


def golden_manifest():
    """The pre-registered corpus manifest the official fence and card cite."""
    from harness.corpus.commit import task_content_sha
    from harness.corpus.registry import Calibration, CorpusManifest, TaskEntry

    return CorpusManifest(
        corpus_id="public-mini",
        semver="1.0.0",
        kind="public",
        tasks=[
            TaskEntry(
                task_id=t["id"],
                sha=task_content_sha(t),
                status="admitted",
                created_at=GOLDEN_TASK_CREATED_AT,
            )
            for t in GOLDEN_TASKS
        ],
        calibration=Calibration(status="full-run-validated"),
    )


def _trial_and_grade(
    ledger: Path,
    ctx,
    *,
    trial_id: str,
    task_id: str,
    task_sha: str,
    arm: str,
    repetition: int,
    passed: bool,
) -> None:
    """One trial record + its grade, matching the real run/grade schemas."""
    from harness.adapters.base import Flags, Outcome, Provenance, Telemetry, TrialRecord
    from harness.ledger.events import record_grade, record_trial

    telemetry = Telemetry(
        tokens_in=500, tokens_out=200, tokens_cache=50, cost=0.5,
        wall_time_s=12.0, tool_calls=3,
    )
    record = TrialRecord.assemble(
        trial_id=trial_id,
        task_id=task_id,
        arm=arm,
        repetition=repetition,
        outcome=Outcome.completed,
        telemetry=telemetry,
        provenance=Provenance(image_digest=f"sha256:golden-{arm}", engine="fake"),
        flags=Flags(),
        artifacts_path=f"/golden/artifacts/{trial_id}",
    )
    record_trial(ledger, ctx, trial_record=record.model_dump(mode="json"))
    record_grade(
        ledger,
        ctx,
        trial_id=trial_id,
        task_sha=task_sha,
        assertions=[
            {"id": "h1", "source": "holdout_test", "result": "pass" if passed else "fail"}
        ],
        binary_score=passed,
    )


def _verdict(
    *,
    comparison_id: str,
    task_id: str,
    winner: str,
    reason: str,
    rubric_sha256: str,
    judge_model: str,
    ts: str,
) -> dict:
    """A schema-valid judge verdict, dumped exactly as the real judge does."""
    from harness.judge.schema import (
        Confidence,
        Evidence,
        Verdict,
        VerdictProvenance,
        Winner,
    )

    win = Winner(winner)
    evidence = []
    if win in (Winner.A, Winner.B):
        evidence = [
            Evidence(kind="diff", response=win.value, hunk="+ return a + b"),
            Evidence(kind="holdout", response=win.value, ref="h1"),
        ]
    cant = win is Winner.CANT_JUDGE
    verdict = Verdict(
        winner=win,
        reason=reason,
        evidence=evidence,
        confidence=Confidence.low if cant else Confidence.high,
        order_inconsistent=False,
        provenance=VerdictProvenance(
            judge_model=judge_model,
            rubric_sha256=rubric_sha256,
            packet_sha256=hashlib.sha256(
                f"golden-packet-{comparison_id}".encode("utf-8")
            ).hexdigest(),
            call_ids=[f"call-{comparison_id}-o1"] if cant
            else [f"call-{comparison_id}-o1", f"call-{comparison_id}-o2"],
            orders="both",
            temperature=0.0,
            ts=ts,
            usage=None if cant else {"input_tokens": 1200, "output_tokens": 250},
        ),
        source="judge",
        comparison_id=comparison_id,
        task_class="default",
        task_id=task_id,
        arm_map={"A": "control", "B": "treatment"},
        single_order=False,
    )
    return verdict.model_dump(mode="json")


def _stamped_findings_json(findings, mode: str) -> str:
    """Stamp mode/watermark into the findings JSON exactly as the analyze CLI
    does before hashing it into the findings_rendered event."""
    from harness.analyze.report import _WATERMARK

    findings.mode = mode
    if mode != "official":
        findings.watermark = _WATERMARK
    return findings.model_dump_json()


@dataclass
class GoldenScenario:
    """Where a generated golden scenario landed, plus its outcome facts."""

    root: Path
    experiment_yaml: Path
    rubric: Path
    ledger: Path
    anchors: Path
    findings_exploratory_md: Path
    exploratory_dossier: Path
    card_json: Path
    head_hash: str
    selfcheck_passed: bool
    findings_official_md: Optional[Path] = None
    official_dossier: Optional[Path] = None
    official_refusal: Optional[str] = None
    artifacts: dict[str, Path] = field(default_factory=dict)


def build_golden_scenario(root: Path | str) -> GoldenScenario:
    """Generate the complete golden experiment under ``root`` [refactor 01 §1].

    Event sequence: experiment_locked → chain_anchor → 20×(trial, grade) →
    executed_order → 10 judge verdicts → calibration_run → selfcheck →
    findings_rendered(exploratory) → findings_rendered(official). The anchor
    store carries a post-lock checkpoint and a final head checkpoint; the card
    is emitted read-only after the last render. If the official fence refuses,
    the exploratory artifacts are still produced and the refusal reason is
    reported — the fence is never weakened to force an official render.
    """
    from harness.analyze.card import build_card, serialize_card
    from harness.analyze.dossier import render_dossier
    from harness.analyze.report import AnalyzeError, compute_findings, render_markdown
    from harness.analyze.selfcheck import run_selfcheck
    from harness.corpus.commit import task_content_sha
    from harness.ledger.anchors import anchor_record, write_anchor
    from harness.ledger.chain import head_hash
    from harness.ledger.events import (
        record_calibration_run,
        record_chain_anchor,
        record_executed_order,
        record_findings_rendered,
        record_selfcheck,
    )
    from harness.plan.lock import lock_experiment

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    experiment_yaml = root / "experiment.yaml"
    experiment_yaml.write_text(EXPERIMENT_YAML, encoding="utf-8")
    rubric = root / "rubrics" / "golden-rubric.md"
    rubric.parent.mkdir(parents=True, exist_ok=True)
    rubric.write_text(RUBRIC_MD, encoding="utf-8")
    ledger = root / "ledger.ndjson"
    anchors = root / "anchors.ndjson"

    ctx = golden_ctx()
    manifest = golden_manifest()
    task_shas = {t["id"]: task_content_sha(t) for t in GOLDEN_TASKS}

    with pin_instrument():
        # Lock with RELATIVE paths so the ledgered spec_path is not a machine-
        # dependent absolute path (it is recorded verbatim on the lock event).
        with contextlib.chdir(root):
            outcome = lock_experiment(
                Path("experiment.yaml"),
                Path("ledger.ndjson"),
                ctx=ctx,
                task_dicts=GOLDEN_TASKS,
                n_sim=8,
                n_boot=40,
                deltas=[0.2, 0.4],
            )
        spec = outcome.spec
        rubric_sha = outcome.event["rubric_sha256"]

        # Post-lock external checkpoint, `bench anchor` style: ledger event
        # first, then the external store write [PRA-L5].
        rec1 = anchor_record(ledger, ts=GOLDEN_ANCHOR_TS_LOCK)
        record_chain_anchor(
            ledger, ctx, head_hash=rec1["head_hash"], height=rec1["height"]
        )
        write_anchor(anchors, rec1)

        order: list[dict] = []
        for i in range(5):
            task_id = f"task{i}"
            for rep in range(2):
                for arm in ("control", "treatment"):
                    passed = arm == "control" or (task_id, rep) not in _TREATMENT_FAILS
                    trial_id = f"{arm[0]}-{i}-{rep}"
                    _trial_and_grade(
                        ledger,
                        ctx,
                        trial_id=trial_id,
                        task_id=task_id,
                        task_sha=task_shas[task_id],
                        arm=arm,
                        repetition=rep,
                        passed=passed,
                    )
                    order.append(
                        {"trial_id": trial_id, "arm": arm, "outcome": "completed"}
                    )
        record_executed_order(ledger, ctx, order=order)

        from harness.ledger.events import append_verdict

        for i in range(5):
            task_id = f"task{i}"
            for rep in range(2):
                cid = f"cmp-{task_id}-r{rep}"
                if task_id == "task4" and rep == 1:
                    winner, reason = "CANT_JUDGE", "refusal"
                elif (task_id, rep) in _TREATMENT_FAILS:
                    winner = "A"
                    reason = (
                        "Response A implements the spec; B returns a stub — "
                        "holdout evidence ✓."
                    )
                else:
                    winner = "TIE"
                    reason = "Both responses satisfy the holdout; no clear winner."
                append_verdict(
                    ledger,
                    ctx,
                    verdict=_verdict(
                        comparison_id=cid,
                        task_id=task_id,
                        winner=winner,
                        reason=reason,
                        rubric_sha256=rubric_sha,
                        judge_model=spec.judge.model,
                        ts=ctx.clock(),
                    ),
                )

        record_calibration_run(
            ledger,
            ctx,
            corpus_id="public-mini",
            semver="1.0.0",
            kind="full",
            run={"p": 0.5, "rho": 0.3, "n_tasks": 5},
            status="full-run-validated",
        )

        selfcheck = run_selfcheck(ledger, spec, n_sim=COVERAGE_N_SIM, n_boot=N_BOOT)
        record_selfcheck(ledger, ctx, **selfcheck)

        def _compute():
            return compute_findings(
                ledger,
                spec,
                spec.seed,
                corpus_manifest=manifest,
                coverage_n_sim=COVERAGE_N_SIM,
                n_boot=N_BOOT,
            )

        def _record_render(findings, mode: str) -> None:
            findings_json = _stamped_findings_json(findings, mode)
            record_findings_rendered(
                ledger,
                ctx,
                mode=mode,
                primary_metric=findings.primary_metric,
                ledger_head_hash=findings.provenance.ledger_head_hash,
                findings_sha256=hashlib.sha256(
                    findings_json.encode("utf-8")
                ).hexdigest(),
                multi_arm_correction=(findings.multi_arm or {}).get(
                    "correction", "none"
                ),
            )

        f_exploratory = _compute()
        md_exploratory = render_markdown(
            f_exploratory, ledger, "exploratory", corpus_manifest=manifest
        )
        dossier_exploratory = render_dossier(
            f_exploratory, ledger, "exploratory", corpus_manifest=manifest
        )
        _record_render(f_exploratory, "exploratory")

        official_refusal: Optional[str] = None
        md_official: Optional[str] = None
        dossier_official: Optional[str] = None
        f_official = _compute()
        try:
            md_official = render_markdown(
                f_official, ledger, "official", corpus_manifest=manifest
            )
            dossier_official = render_dossier(
                f_official, ledger, "official", corpus_manifest=manifest
            )
        except AnalyzeError as e:
            official_refusal = str(e)
        else:
            _record_render(f_official, "official")

        card = build_card(
            ledger,
            spec,
            task_ids=[t["id"] for t in GOLDEN_TASKS],
            corpus_manifest=manifest,
        )
        card_text = serialize_card(card)

        rec2 = anchor_record(ledger, ts=GOLDEN_ANCHOR_TS_HEAD)
        write_anchor(anchors, rec2)

    md_exploratory_path = root / "findings.exploratory.md"
    md_exploratory_path.write_text(md_exploratory, encoding="utf-8")
    dossier_exploratory_path = root / "findings.exploratory.dossier.html"
    dossier_exploratory_path.write_text(dossier_exploratory, encoding="utf-8")
    card_path = root / "card.json"
    card_path.write_text(card_text, encoding="utf-8")

    scenario = GoldenScenario(
        root=root,
        experiment_yaml=experiment_yaml,
        rubric=rubric,
        ledger=ledger,
        anchors=anchors,
        findings_exploratory_md=md_exploratory_path,
        exploratory_dossier=dossier_exploratory_path,
        card_json=card_path,
        head_hash=head_hash(ledger),
        selfcheck_passed=bool(selfcheck["passed"]),
        official_refusal=official_refusal,
    )
    if md_official is not None and dossier_official is not None:
        scenario.findings_official_md = root / "findings.official.md"
        scenario.findings_official_md.write_text(md_official, encoding="utf-8")
        scenario.official_dossier = root / "findings.official.dossier.html"
        scenario.official_dossier.write_text(dossier_official, encoding="utf-8")

    scenario.artifacts = {
        "golden_experiment.yaml": experiment_yaml,
        "golden_rubric.md": rubric,
        "golden_ledger.ndjson": ledger,
        "golden_anchor.ndjson": anchors,
        "golden_findings.exploratory.md": md_exploratory_path,
        "golden_findings.exploratory.dossier.html": dossier_exploratory_path,
        "golden_card.json": card_path,
    }
    if scenario.findings_official_md is not None:
        scenario.artifacts["golden_findings.official.md"] = scenario.findings_official_md
        scenario.artifacts["golden_findings.official.dossier.html"] = (
            scenario.official_dossier
        )
    return scenario


# --- constructor replay [refactor 01 §1 item 2] ------------------------------
def build_constructor_replay(ledger: Path | str) -> set[str]:
    """Invoke EVERY typed constructor in harness.ledger.events with fixed
    context and representative payloads; return the event names exercised.

    Each omit-if-None parameter is exercised both ways (absent and present) and
    each always-present-nullable payload field both null and non-null, so the
    committed fixture pins the full key-set behavior of every constructor.
    Payloads deliberately include non-ASCII characters so an ``ensure_ascii``
    canonicalization drift is byte-visible.
    """
    from harness.adapters.base import Flags, Outcome, Provenance, Telemetry, TrialRecord
    from harness.ledger import events as E
    from harness.ledger.events import EventContext

    ledger = Path(ledger)
    ctx = EventContext(
        experiment_id="golden-constructors", actor=GOLDEN_ACTOR, clock=golden_clock()
    )
    mde = {
        "mde": 0.2,
        "method": "paired_binary_bootstrap_sim",
        "flags": ["assumption_based_mde"],
        "n_tasks": 5,
        "repetitions": 2,
        "p": 0.5,
        "rho": 0.3,
        "power_target": 0.8,
        "alpha": 0.05,
        "power_curve": [{"delta": 0.2, "power": 0.9}],
    }

    def trial_record(**overrides) -> dict:
        record = TrialRecord.assemble(
            trial_id=overrides.pop("trial_id", "t-0"),
            task_id="task0",
            arm="control",
            repetition=0,
            outcome=Outcome.completed,
            telemetry=Telemetry(tokens_in=10, tokens_out=5, cost=0.1, wall_time_s=1.5),
            provenance=Provenance(image_digest="sha256:golden", engine="fake"),
            flags=Flags(),
            artifacts_path="/golden/artifacts/t-0",
            **overrides,
        )
        return record.model_dump(mode="json")

    verdict = _verdict(
        comparison_id="cmp-task0-r0",
        task_id="task0",
        winner="B",
        reason="Response B implements the spec — holdout evidence ✓.",
        rubric_sha256="a" * 64,
        judge_model="fake/deterministic-2026-01-01",
        ts="2026-02-03T04:05:06+00:00",
    )

    with pin_instrument():
        # experiment_locked: all omit-if-None fields absent, then all present.
        E.record_experiment_locked(
            ledger, ctx, spec_sha256="b" * 64, spec_path="experiment.yaml",
            seed=1234, mde=mde, attested_by=GOLDEN_ACTOR, method="anchor-plus-actor-v1",
        )
        E.record_experiment_locked(
            ledger, ctx, spec_sha256="b" * 64, spec_path="experiment.yaml",
            seed=1234, mde=mde, attested_by=GOLDEN_ACTOR, method="anchor-plus-actor-v1",
            task_commitment={
                "corpus_id": "public-mini", "semver": "1.0.0",
                "task_shas_sha256": "c" * 64,
            },
            acknowledged_underpowered={"mde": 0.2, "hypothesized_effect": 0.1},
            rubric_sha256="d" * 64,
        )
        E.record_chain_anchor(ledger, ctx, head_hash="e" * 64, height=2)
        # trial: without and with the hoisted trajectory/flight-recorder shas.
        E.record_trial(ledger, ctx, trial_record=trial_record())
        E.record_trial(
            ledger, ctx,
            trial_record=trial_record(
                trial_id="t-1", trajectory_sha="1" * 64, flight_recorder_sha="2" * 64
            ),
        )
        E.record_trial_infra_failed(
            ledger, ctx, trial_id="t-2", task_id="task0", arm="control",
            reason="container exited 137",
        )
        E.record_trial_infra_failed(
            ledger, ctx, trial_id="t-3", task_id="task0", arm="treatment",
            reason="redaction failed — après engine", cost=0.25,
        )
        E.record_run_stopped_cost_ceiling(
            ledger, ctx, accumulated_cost=10.5, ceiling=10.0
        )
        E.record_executed_order(
            ledger, ctx,
            order=[
                {"trial_id": "t-0", "arm": "control", "outcome": "completed"},
                {"trial_id": "t-1", "arm": "treatment", "outcome": "completed"},
            ],
        )
        assertions = [{"id": "h1", "source": "holdout_test", "result": "pass"}]
        E.record_grade(
            ledger, ctx, trial_id="t-0", task_sha="3" * 64,
            assertions=assertions, binary_score=True,
        )
        E.record_grade(
            ledger, ctx, trial_id="t-1", task_sha="3" * 64,
            assertions=assertions, binary_score=True, fractional_score=0.75,
            grader="docker", override_of="4" * 64, workspace_sha256="5" * 64,
            workspace_walk_version=1,
        )
        E.record_cant_grade(ledger, ctx, trial_id="t-2", reason="workspace missing")
        E.record_cant_grade(
            ledger, ctx, trial_id="t-3", reason="holdout crashed ✗",
            override_of="6" * 64,
        )
        E.record_flake_baseline(
            ledger, ctx, task_id="task0", task_sha="3" * 64, k=5,
            results=["pass", "pass", "pass", "pass", "pass"], verdict="stable",
        )
        E.record_flake_baseline(
            ledger, ctx, task_id="task1", task_sha="7" * 64, k=3,
            results=["pass", "fail", "pass"], verdict="flaky",
            workspace_basis="reference_solution",
        )
        E.append_verdict(ledger, ctx, verdict=verdict)
        # human_verdict: bare; integrity with nullable fields null; integrity full.
        human = dict(verdict, source="human")
        E.append_human_verdict(ledger, ctx, verdict=human)
        E.append_human_verdict(
            ledger, ctx, verdict=human, arm_recognized=False,
            arm_guess=None, actual_arm=None,
        )
        E.append_human_verdict(
            ledger, ctx, verdict=human, arm_recognized=True,
            arm_guess="treatment", actual_arm="control",
        )
        # selfcheck: always-present-nullable coverage/mc_interval both ways,
        # omit-if-None validation fields both ways.
        E.record_selfcheck(
            ledger, ctx, selected_method="percentile", nominal=0.95,
            coverage=None, mc_interval=None, n_sim=40, n_boot=500, n_tasks=1,
            null_model="insufficient_data", passed=False,
        )
        E.record_selfcheck(
            ledger, ctx, selected_method="bca", nominal=0.95,
            coverage=0.9375, mc_interval=[0.9, 0.97], n_sim=40, n_boot=500,
            n_tasks=5, null_model="recentered_binary", passed=True,
            validation_coverage=0.945, validation_n_sim=400,
        )
        E.record_cant_analyze(
            ledger, ctx, mode="official", reason="calibration_incomplete"
        )
        E.record_cant_analyze(
            ledger, ctx, mode="exploratory", reason="provenance",
            detail="ledger head hash changed — findings stale",
        )
        E.record_findings_rendered(
            ledger, ctx, mode="exploratory", primary_metric="holdout_pass_rate",
            ledger_head_hash="8" * 64, findings_sha256="9" * 64,
        )
        E.record_findings_rendered(
            ledger, ctx, mode="official", primary_metric="holdout_pass_rate",
            ledger_head_hash="8" * 64, findings_sha256="9" * 64,
            multi_arm_correction="none",
        )
        E.record_judge_stopped_token_ceiling(
            ledger, ctx, accumulated_tokens=120_000, ceiling=100_000
        )
        E.record_review_batch(
            ledger, ctx, batch_id="batch-1",
            comparison_ids=["cmp-task0-r0", "cmp-task1-r0"], seed=1234,
        )
        E.record_review_packet_built(
            ledger, ctx, comparison_id="cmp-task0-r0", task_id="task0",
            task_class="default", response_map={"1": "control", "2": "treatment"},
            seed=1234,
        )
        E.record_reveal(
            ledger, ctx, verdict_event_id="a1" * 32,
            revealed={"arm_map": {"A": "control", "B": "treatment"}},
        )
        E.record_task_admitted(
            ledger, ctx, candidate_id="cand-1", task_sha="3" * 64,
            baseline_ref="baseline/task0",
        )
        E.record_calibration_run(
            ledger, ctx, corpus_id="public-mini", semver="1.0.0", kind="full",
            run={"p": 0.5, "rho": 0.3, "n_tasks": 5}, status="full-run-validated",
        )
        E.record_subset_draw(
            ledger, ctx, corpus_id="public-mini", semver="1.0.0", seed=1234,
            stratum_key="category", task_ids=["task0", "task1"],
            strata={"stratum_key": "category", "allocation": {"default": 2}},
        )
        E.record_curation_approval(
            ledger, ctx, candidate_id="cand-1", task_sha="3" * 64,
            approver="curator-1", signature="00" * 32, signer_public_key="11" * 16,
        )
        E.record_curation_approval(
            ledger, ctx, candidate_id="cand-2", task_sha="7" * 64,
            approver="curator-2", signature="22" * 32, signer_public_key="33" * 16,
            notes="approved — reviewed diff ✓",
        )
        E.record_process_score(
            ledger, ctx,
            process_score={
                "trial_id": "t-0",
                "dimensions": {"planning": 3, "verification": "CANT_SCORE"},
                "provenance": {
                    "unblinded": True,
                    "scorer": "human",
                    "provider_model": "fake/deterministic-2026-01-01",
                },
            },
        )
        E.record_forensics_report(
            ledger, ctx,
            forensics_report={
                "vocabulary_version": 1,
                "flags": [
                    {
                        "detector": "grade_read",
                        "trial_id": "t-0",
                        "task_id": "task0",
                        "arm": "control",
                    }
                ],
                "coverage": {
                    "trials": 2,
                    "covered": 1,
                    "gaps": [{"trial_id": "t-1", "reason": "no trajectory"}],
                },
                "reviews": None,
            },
        )
        E.record_forensic_spotcheck(
            ledger, ctx, trial_id="t-0", labels={"grade_read": True},
            stratum="mandatory",
        )
        E.record_forensic_quarantine(
            ledger, ctx, trial_id="t-0", reason="operator review: gamed holdout"
        )
        E.record_contamination_probe(
            ledger, ctx,
            probe={
                "status": "complete",
                "canary_sha256": "44" * 32,
                "results": [
                    {"arm": "control", "task_id": "task0", "outcome": "clean"},
                    {"arm": "treatment", "task_id": "task0", "outcome": "clean"},
                ],
                "alarms": [],
            },
        )
        E.record_control_reused(
            ledger, ctx, source_experiment_id="golden-source",
            source_ledger_head_hash="55" * 32, bundle_sha256="66" * 32,
            fingerprint={"fingerprint_version": 2, "digest": "77" * 32},
            control_arm="control",
            cells=[{"task_id": "task0", "repetition": 0}],
        )
        E.record_reused_trial(
            ledger, ctx, trial_record=trial_record(trial_id="t-4"),
            reused_from={
                "source_experiment_id": "golden-source", "bundle_sha256": "66" * 32
            },
        )
        E.record_reused_trial(
            ledger, ctx, trial_record=trial_record(trial_id="t-5"),
            reused_from={
                "source_experiment_id": "golden-source", "bundle_sha256": "66" * 32
            },
            diff_sha256="88" * 32,
        )
        E.record_reused_grade(
            ledger, ctx,
            grade={
                "trial_id": "t-4", "task_sha": "3" * 64,
                "assertions": assertions, "binary_score": True,
            },
            reused_from={
                "source_experiment_id": "golden-source", "bundle_sha256": "66" * 32
            },
        )
        E.append_reused_verdict(
            ledger, ctx, verdict=verdict,
            reused_from={
                "source_experiment_id": "golden-source", "bundle_sha256": "66" * 32
            },
        )

    import json

    with open(ledger, "rb") as fh:
        return {json.loads(line)["event"] for line in fh if line.strip()}
