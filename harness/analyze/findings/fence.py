"""The official-fence check list [refactor 07 §1].

ONE ordered list of :class:`FenceCheck` objects the render side and the observer
both consume, so the D8 drift class — a check that exists on one side only —
becomes unrepresentable. Each check names its ``id`` (the observer item id), a
human ``name``, and an ``evaluate`` that returns a :class:`FenceOutcome`
``(state, detail, error)``:

* :func:`assert_official_fence` (render side) iterates the list and raises the
  first non-``ok`` outcome's typed ``AnalyzeError`` — the exact refusal wording
  is preserved verbatim from the previous ``_assert_official_calibration``;
* :func:`official_fence_outcomes` (observer side, in ``analyze/fence.py``)
  itemizes every outcome with its state, so ``official_ready`` and the compare
  screen cannot show ready while the render would refuse.

:func:`validate_for_render` is the single render-side validation entry the
markdown render AND the dossier call — the dossier no longer renders-and-discards
markdown just for the side-effects [refactor 07 §1].
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from ...contamination.summary import latest_probe, probe_asymmetries
from ...ledger import events
from ...ledger.query import ledger_head_hash, verify
from ...ledger.view import LedgerView
from .extract import _judge_summary, _lock_event
from .model import (
    AnalyzeError,
    AsymmetricContaminationError,
    CalibrationIncompleteError,
    CorpusMismatchError,
    CorrectionMismatchError,
    DisclosureError,
    FindingsDocument,
    InsulationAlarmError,
    ProvenanceError,
    RubricMismatchError,
    SelfcheckRequiredError,
    UnregisteredOfficialError,
)
from .sections import asymmetry_line
from ..selfcheck import latest_selfcheck, selfcheck_status


# --- render-side pre-checks (not part of the official-calibration list) ------
def _validate_provenance(findings: FindingsDocument) -> None:
    p = findings.provenance
    for name in ("instrument_version", "instrument_git_sha", "ledger_head_hash", "judge"):
        if getattr(p, name) in (None, ""):
            raise ProvenanceError(f"findings provenance missing {name} [AC-6]")


def _validate_process_disclosure(findings: FindingsDocument) -> None:
    """Process scores may never render without the unblinded disclosure [EVAL-9 AC-2]."""
    if findings.process is None:
        return
    disclosure = findings.process.get("disclosure")
    if not disclosure or disclosure.get("unblinded") is not True:
        raise DisclosureError(
            "findings include process scores but no unblinded disclosure block; "
            "process scores never render without disclosure [EVAL-9 AC-2]"
        )


def _assert_head_hash(findings: FindingsDocument, ledger_path) -> None:
    """Cross-check the recorded head hash against verify_chain at render time [AC-6]."""
    result = verify(ledger_path)
    if not result.ok:
        raise ProvenanceError(f"ledger chain does not verify at render: {result.detail}")
    current = ledger_head_hash(ledger_path)
    if current != findings.provenance.ledger_head_hash:
        raise ProvenanceError(
            "ledger head hash changed since the findings were computed "
            f"(recorded {findings.provenance.ledger_head_hash[:12]}…, "
            f"now {current[:12]}…) — findings are stale [AC-6]"
        )


# --- shared fence helpers ----------------------------------------------------
def _task_ids_run(ledger_path) -> set[str]:
    """The set of task ids the experiment actually ran (from trial records)."""
    return {ev["trial_record"]["task_id"] for ev in LedgerView(ledger_path).by_kind(events.TRIAL)}


def _ledgered_calibration_status(ledger_path, corpus_id: str, semver: str) -> Optional[str]:
    """The latest calibration status **on the chain** for a corpus, or None.

    Reads ``calibration_run`` events (last-write-wins in ledger order) rather than
    the hand-editable ``manifest.calibration.status`` [CO-4]."""
    status = None
    for ev in LedgerView(ledger_path).by_kind(events.CALIBRATION_RUN):
        if ev.get("corpus_id") == corpus_id and ev.get("semver") == semver:
            status = ev.get("status")
    return status


def _assert_no_insulation_alarms(ledger_path) -> None:
    """Refuse an official render while the latest probe carries insulation
    alarms [F-M-C3]. Probes predating the recorded field simply lack it."""
    alarms = (latest_probe(ledger_path) or {}).get("alarms") or []
    if alarms:
        detail = "; ".join(alarms)
        raise InsulationAlarmError(
            f"official render refused: {len(alarms)} holdout-leak insulation "
            f"alarm(s) on the latest contamination probe — {detail}. Investigate; "
            "if intentional, quarantine the trial (ledgered) and re-run "
            "`bench contamination probe` [F-M-C3, EVAL-4 AC-9]"
        )


def effective_multi_arm_correction(spec) -> str:
    """The multi-arm correction an official render of ``spec`` would apply —
    the value the render fence gates on [F-H7, refactor 01 §4 D8].

    Mirrors ``compute_findings``: the ``multi_arm`` block (and with it a
    correction) exists only for a >2-arm family — one comparison is built per
    arm beyond ``arms[0]``, so with two arms there is a single pre-registered
    pair, no decision family, and the render passes ``"none"`` regardless of
    the spec field. The observer fence evaluates the correction-consistency
    item with this value so it cannot show ready while the render refuses."""
    return spec.multi_arm_correction if len(spec.arms) > 2 else "none"


def _assert_correction_consistent(correction: str, ledger_path) -> None:
    """Refuse an official render whose applied multi-arm correction differs from
    any prior official render's recorded correction [F-H7]. Render events that
    predate the recorded field are skipped — a legacy chain is not refused on,
    but the first post-field official render pins the procedure for the rest."""
    for ev in LedgerView(ledger_path).by_kind(events.FINDINGS_RENDERED):
        if ev.get("mode") != "official":
            continue
        prior = ev.get("multi_arm_correction")
        if prior is not None and prior != correction:
            raise CorrectionMismatchError(
                f"official render refused: a prior official render used "
                f"multi-arm correction {prior!r}; this render would use "
                f"{correction!r} — one pre-registered decision procedure per "
                "experiment [F-H7]"
            )


# --- the check model ---------------------------------------------------------
@dataclass(frozen=True)
class FenceContext:
    """The inputs a fence check reads, built from a computed render
    (``findings``) or from an observer's ledger+spec read. The two sources
    resolve into the same fields so one ``evaluate`` serves both [refactor 07 §1]."""

    ledger_path: object
    corpus_manifest: object
    spec_corpus: dict  # {"id": ..., "version": ...}
    lock: dict
    correction: str
    # render-only: the CI method the render deploys (selfcheck must have validated
    # it). ``None`` on the observer, which has no computed render to check against.
    deployed_ci_method: Optional[str] = None
    # render-only: findings.contamination["asymmetric"] as a defense-in-depth
    # fallback for summary-only flags; the observer trusts the ledgered probe alone.
    contamination_fallback: list = field(default_factory=list)


@dataclass(frozen=True)
class FenceOutcome:
    """One check's result: ``ok`` | ``failed`` | ``unchecked``, a human ``detail``,
    and the typed ``error`` the render side raises for a non-``ok`` outcome."""

    state: str
    detail: str = ""
    error: Optional[AnalyzeError] = None


@dataclass(frozen=True)
class FenceCheck:
    """One official-fence requirement: its observer ``id`` + ``name`` and the
    ``evaluate`` both sides call [refactor 07 §1]."""

    id: str
    name: str
    evaluate: Callable[[FenceContext], FenceOutcome]


def _eval_corpus_identity(ctx: FenceContext) -> FenceOutcome:
    sc = ctx.spec_corpus
    if ctx.corpus_manifest is None:
        detail = (
            "official findings require a full-run-validated corpus manifest; none "
            f"provided for {sc['id']}@{sc['version']} [EVAL-8 AC-2]"
        )
        return FenceOutcome("unchecked", detail, CalibrationIncompleteError(detail))
    m = ctx.corpus_manifest
    if m.corpus_id != sc["id"] or m.semver != sc["version"]:
        detail = (
            f"official render cites corpus {m.corpus_id}@"
            f"{m.semver}, but the experiment pre-registered "
            f"{sc['id']}@{sc['version']}; the primary metric is "
            "official only against the corpus it was registered on [AN-2]"
        )
        return FenceOutcome("failed", detail, CorpusMismatchError(detail))
    return FenceOutcome("ok")


def _eval_corpus_coverage(ctx: FenceContext) -> FenceOutcome:
    sc = ctx.spec_corpus
    if ctx.corpus_manifest is None:
        detail = (
            "official findings require a full-run-validated corpus manifest; none "
            f"provided for {sc['id']}@{sc['version']} [EVAL-8 AC-2]"
        )
        return FenceOutcome("unchecked", detail, CalibrationIncompleteError(detail))
    m = ctx.corpus_manifest
    missing = sorted(t for t in _task_ids_run(ctx.ledger_path) if not m.is_schedulable(t))
    if missing:
        detail = (
            f"official render cites {m.corpus_id}@{m.semver}, "
            f"but tasks {missing} were run and are not admitted in it; the manifest "
            "does not cover the experiment's data [AN-2]"
        )
        return FenceOutcome("failed", detail, CorpusMismatchError(detail))
    return FenceOutcome("ok")


def _eval_calibration(ctx: FenceContext) -> FenceOutcome:
    sc = ctx.spec_corpus
    status = _ledgered_calibration_status(ctx.ledger_path, sc["id"], sc["version"])
    if status != "full-run-validated":
        detail = (
            f"corpus {sc['id']}@{sc['version']} is not "
            f"full-run-validated on the chain (ledgered status={status!r}); a "
            "manifest JSON status alone does not satisfy the fence — calibrate "
            "through a ledgered calibration_run before the first official finding "
            "[EVAL-8 AC-2, CO-4]"
        )
        return FenceOutcome("failed", detail, CalibrationIncompleteError(detail))
    return FenceOutcome("ok")


def _eval_rubric(ctx: FenceContext) -> FenceOutcome:
    # D-P7-6: a legacy lock (no committed hash) is not refused — a caveat rides
    # the render instead; the observer names it so.
    locked_rubric_sha = ctx.lock.get("rubric_sha256")
    if locked_rubric_sha is None:
        return FenceOutcome(
            "ok", "legacy lock: no rubric hash committed (render adds a caveat)"
        )
    verdict_shas = _judge_summary(ctx.ledger_path)["rubric_shas"]
    disagreeing = sorted(s for s in verdict_shas if s != locked_rubric_sha)
    if disagreeing:
        detail = (
            f"official render refused: verdict rubric hash(es) {disagreeing} "
            f"disagree with the locked rubric_sha256 {locked_rubric_sha}; the "
            "judging rubric was swapped after the lock [D-P7-6]"
        )
        return FenceOutcome("failed", detail, RubricMismatchError(detail))
    return FenceOutcome("ok")


def _eval_selfcheck(ctx: FenceContext) -> FenceOutcome:
    status = selfcheck_status(ctx.ledger_path)
    if status != "current":
        detail = {
            "missing": "no selfcheck has been run",
            "failed": "the selfcheck failed",
            "stale": "the selfcheck predates later trials/grades — it validated an "
                     "older dataset than this render analyzes",
        }[status]
        full = (
            f"official render refused: {detail}. Run `bench selfcheck "
            "<experiment-dir>` and pass it before the first official finding "
            "[EVAL-1-D008]"
        )
        return FenceOutcome("failed", full, SelfcheckRequiredError(full))
    # The selfcheck validated a specific CI method; the render must deploy that
    # same method, else the coverage the gate certified is not the coverage of
    # the interval actually shown [review #2]. The observer has no deployed
    # method to check, and names the validated one instead.
    validated = (latest_selfcheck(ctx.ledger_path) or {}).get("selected_method")
    if ctx.deployed_ci_method is not None and validated != ctx.deployed_ci_method:
        full = (
            f"official render refused: the selfcheck validated CI method "
            f"{validated!r} but the render deploys {ctx.deployed_ci_method!r}; re-run `bench "
            "selfcheck <experiment-dir>` so the validated and deployed methods "
            "agree [EVAL-1-D008]"
        )
        return FenceOutcome("failed", full, SelfcheckRequiredError(full))
    return FenceOutcome("ok", f"validated CI method: {validated}")


def _eval_contamination(ctx: FenceContext) -> FenceOutcome:
    # Recomputed from the LEDGERED probe event; the findings-based list is
    # defense in depth for summary-only flags (empty on the observer).
    asymmetric = probe_asymmetries(latest_probe(ctx.ledger_path)) or ctx.contamination_fallback
    if asymmetric:
        joined = "; ".join(asymmetry_line(a) for a in asymmetric)
        detail = (
            f"official render refused: asymmetric flagged contamination — "
            f"{joined}. The pairing is invalid for these tasks; exploratory "
            "still renders, watermarked, with the full summary [EVAL-10 AC-5]"
        )
        return FenceOutcome("failed", detail, AsymmetricContaminationError(detail))
    return FenceOutcome("ok")


def _eval_insulation(ctx: FenceContext) -> FenceOutcome:
    try:
        _assert_no_insulation_alarms(ctx.ledger_path)
    except InsulationAlarmError as e:
        return FenceOutcome("failed", str(e), e)
    return FenceOutcome("ok")


def _eval_correction(ctx: FenceContext) -> FenceOutcome:
    try:
        _assert_correction_consistent(ctx.correction, ctx.ledger_path)
    except CorrectionMismatchError as e:
        return FenceOutcome("failed", str(e), e)
    return FenceOutcome("ok")


# The one ordered fence-check list [refactor 07 §1] — the same order
# _assert_official_calibration refused in, itemized by the observer.
FENCE_CHECKS: list[FenceCheck] = [
    FenceCheck("corpus_identity", "pre-registered corpus cited", _eval_corpus_identity),
    FenceCheck("corpus_coverage", "every task run is admitted", _eval_corpus_coverage),
    FenceCheck("calibration", "corpus full-run-validated (ledgered)", _eval_calibration),
    FenceCheck("rubric", "rubric matches the lock", _eval_rubric),
    FenceCheck("selfcheck", "coverage selfcheck current", _eval_selfcheck),
    FenceCheck("contamination", "no asymmetric flagged contamination", _eval_contamination),
    FenceCheck("insulation", "no holdout-leak insulation alarms", _eval_insulation),
    FenceCheck("correction", "multi-arm correction consistent", _eval_correction),
]


def render_context(findings: FindingsDocument, corpus_manifest, ledger_path) -> FenceContext:
    """The fence context a computed render evaluates against [AN-2, D-P5-2]."""
    return FenceContext(
        ledger_path=ledger_path,
        corpus_manifest=corpus_manifest,
        spec_corpus=findings.spec_corpus,
        lock=_lock_event(ledger_path),
        correction=(findings.multi_arm or {}).get("correction", "none"),
        deployed_ci_method=findings.ci_selection.get("selected_method"),
        contamination_fallback=(findings.contamination or {}).get("asymmetric", []),
    )


def assert_official_fence(findings: FindingsDocument, corpus_manifest, ledger_path) -> None:
    """Bind the official fence to corpus identity + integrity [AN-2, D-P5-2].

    Iterates the shared :data:`FENCE_CHECKS`, raising the first non-``ok``
    outcome's typed ``AnalyzeError`` — the exact wording, order, and
    ``cant_analyze`` reasons the previous ``_assert_official_calibration``
    produced, now the same list the observer itemizes."""
    ctx = render_context(findings, corpus_manifest, ledger_path)
    for check in FENCE_CHECKS:
        outcome = check.evaluate(ctx)
        if outcome.state != "ok":
            raise outcome.error


def validate_for_render(
    findings: FindingsDocument,
    ledger_path,
    mode: str,
    *,
    metric: Optional[str] = None,
    corpus_manifest=None,
) -> None:
    """The single render-side validation the markdown render AND the dossier run
    [refactor 07 §1]: provenance, process disclosure, head-hash/chain verify, and
    (official) the metric gate + the calibration fence. The dossier calls THIS
    instead of rendering-and-discarding a full markdown render for its side effects."""
    _validate_provenance(findings)
    _validate_process_disclosure(findings)
    _assert_head_hash(findings, ledger_path)
    if mode == "official":
        if metric is not None and metric != findings.primary_metric:
            raise UnregisteredOfficialError(
                f"official render requested for {metric!r}, but the pre-registered "
                f"primary metric is {findings.primary_metric!r}; only the "
                "primary metric + decision rule are official [AC-5]"
            )
        assert_official_fence(findings, corpus_manifest, ledger_path)
