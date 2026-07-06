"""Findings schema — the refusal taxonomy + typed findings models [refactor 07 §1].

The versioned, hash-covered heart of the findings package: the closed
``CantAnalyzeReason`` set + the ``AnalyzeError`` hierarchy the fence raises
[AN-3], and the pydantic models a render/dossier/card all read.

``FindingsDocument.model_dump_json()`` bytes are covered by ``findings_sha256``
(the CLI stamps the mode/watermark, then hashes) — so this module's
serialization is a versioned contract. ``ComparisonFinding.stats``/``.effect``/
``.decision`` were raw dicts read by ``.get``/``[]``/``in`` in three artifacts;
they are now the typed :class:`ComparisonStats` / :class:`EffectBlock` /
:class:`Decision` sub-models, which still read AND serialize exactly like the
plain dicts they replace (only explicitly-set keys exist, in declaration order),
so the golden byte-fixtures are unchanged [refactor 07 §1, §6].
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Iterator, Literal, Optional

from pydantic import BaseModel, ConfigDict, model_serializer


# --- refusal taxonomy [AN-3] -----------------------------------------------
class AnalyzeError(RuntimeError):
    """Base for analyze-stage failures."""


class UnregisteredOfficialError(AnalyzeError):
    """Official render requested for a non-pre-registered metric [AC-5]."""


class CalibrationIncompleteError(AnalyzeError):
    """Official render requested before the corpus is full-run-validated."""


class CorpusMismatchError(AnalyzeError):
    """Official render requested against a corpus that is not the pre-registered
    one — a different id/semver, or one missing tasks the experiment ran [AN-2]."""


class RubricMismatchError(AnalyzeError):
    """Official render requested where a verdict's rubric hash disagrees with the
    lock's committed rubric_sha256 — the rubric was swapped after lock [D-P7-6]."""


class SelfcheckRequiredError(AnalyzeError):
    """Official render requested without a passed ledgered selfcheck [EVAL-1-D008]."""


class ProvenanceError(AnalyzeError):
    """A finding is missing provenance, or the head hash no longer verifies."""


class DisclosureError(AnalyzeError):
    """Process scores rendered without the unblinded disclosure block [EVAL-9 AC-2]."""


class AsymmetricContaminationError(AnalyzeError):
    """Official render requested with asymmetric flagged contamination — one
    arm's model flagged on a task another arm is not, so the pairing itself is
    invalid; exploratory still renders, watermarked [EVAL-10 AC-5, D001]."""


class InsulationAlarmError(AnalyzeError):
    """Official render requested while the latest contamination probe carries a
    holdout-leak insulation alarm [F-M-C3, EVAL-4 AC-9] — an insulation
    VIOLATION that must be investigated (and, if intentional, resolved through
    the ledgered quarantine ceremony + re-scan), never rendered past."""


class CorrectionMismatchError(AnalyzeError):
    """Official render whose multi-arm correction differs from a prior official
    render's recorded correction [F-H7] — one experiment, one pre-registered
    decision procedure; a second official procedure is the post-hoc degree of
    freedom the lock exists to prevent."""


class CantAnalyzeReason(str, Enum):
    """Closed set of fail-closed analyze-refusal reasons [AN-3]."""

    calibration_incomplete = "calibration_incomplete"
    corpus_mismatch = "corpus_mismatch"
    unregistered_metric = "unregistered_metric"
    disclosure_missing = "disclosure_missing"
    provenance_invalid = "provenance_invalid"
    rubric_mismatch = "rubric_mismatch"
    selfcheck_required = "selfcheck_required"
    asymmetric_contamination = "asymmetric_contamination"
    insulation_alarm = "insulation_alarm"
    correction_mismatch = "correction_mismatch"
    analyze_error = "analyze_error"


def cant_analyze_reason(exc: AnalyzeError) -> CantAnalyzeReason:
    """Map an ``AnalyzeError`` to its enumerated ``cant_analyze`` reason.

    Every official-fence refusal must carry its own distinguishable reason in
    this closed set [AN-3] — a generic ``analyze_error`` fallback would erase
    which gate refused. The Phase-7 fence checks (rubric-swap, missing/failed
    selfcheck) are mapped here alongside the calibration/corpus/disclosure ones.
    """
    return {
        CalibrationIncompleteError: CantAnalyzeReason.calibration_incomplete,
        CorpusMismatchError: CantAnalyzeReason.corpus_mismatch,
        UnregisteredOfficialError: CantAnalyzeReason.unregistered_metric,
        DisclosureError: CantAnalyzeReason.disclosure_missing,
        ProvenanceError: CantAnalyzeReason.provenance_invalid,
        RubricMismatchError: CantAnalyzeReason.rubric_mismatch,
        SelfcheckRequiredError: CantAnalyzeReason.selfcheck_required,
        AsymmetricContaminationError: CantAnalyzeReason.asymmetric_contamination,
        InsulationAlarmError: CantAnalyzeReason.insulation_alarm,
        CorrectionMismatchError: CantAnalyzeReason.correction_mismatch,
    }.get(type(exc), CantAnalyzeReason.analyze_error)


# --- dict-parity typed sub-models ------------------------------------------
class _DictModel(BaseModel):
    """A typed model that still reads, writes, and serializes like the plain
    dict it replaces [refactor 07 §1].

    ``ComparisonFinding.stats``/``.effect``/``.decision`` were raw dicts whose
    exact bytes are covered by ``findings_sha256`` and whose keys VARY per
    instance (an excluded comparison has ``stats == {}``; a Holm-adjusted
    decision carries ``holm_p``/``correction``; a floored one carries ``floor``).
    Typing them must not change a single byte, so this base:

    * serializes ONLY the explicitly-set fields, in declaration order — an
      unset ``holm_p`` never appears as ``"holm_p":null``, and an all-unset
      model serializes ``{}`` (not ``null``), matching the plain dict exactly;
    * preserves the mapping reads the three artifacts already do
      (``cf.stats["ci_low"]``, ``"holm_p" in cf.decision``, ``cf.stats or {}``)
      and the in-place writes ``_apply_holm`` performs (``cf.decision["holm_p"]
      = …``), which also record the field as set so it then serializes.

    Declaration order == the dict-insertion order the code produced, so the
    plain-serializer emits the same key sequence today's ``json.dumps`` did
    (the golden fixtures are the proof).
    """

    model_config = ConfigDict(extra="forbid")

    @model_serializer(mode="plain")
    def _serialize_set_fields(self) -> dict[str, Any]:
        was_set = self.__pydantic_fields_set__
        return {k: getattr(self, k) for k in type(self).model_fields if k in was_set}

    # mapping-parity shims — the field was a dict before, read/written as one.
    def __getitem__(self, key: str) -> Any:
        if key not in self.__pydantic_fields_set__:
            raise KeyError(key)
        return getattr(self, key)

    def __setitem__(self, key: str, value: Any) -> None:
        setattr(self, key, value)
        self.__pydantic_fields_set__.add(key)

    def __contains__(self, key: object) -> bool:
        return key in self.__pydantic_fields_set__

    def __bool__(self) -> bool:
        return bool(self.__pydantic_fields_set__)

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-parity read — the same ``.get`` the raw dict offered."""
        return getattr(self, key) if key in self.__pydantic_fields_set__ else default

    def keys(self) -> Iterator[str]:
        return (k for k in type(self).model_fields if k in self.__pydantic_fields_set__)


class ComparisonStats(_DictModel):
    """The paired-bootstrap block (``BootstrapResult.as_dict``) [refactor 07 §1].

    All nine keys are present together or the block is empty (an excluded /
    no-data comparison carries ``{}``); declaration order matches
    ``BootstrapResult.as_dict``."""

    mean_delta: float = 0.0
    ci_low: float = 0.0
    ci_high: float = 0.0
    ci_method: str = ""
    ci_method_requested: str = ""
    ci_method_fell_back: bool = False
    ci_level: float = 0.0
    n_boot: int = 0
    n_tasks: int = 0


class EffectBlock(_DictModel):
    """The effect-size block (``EffectResult.as_dict``) [refactor 07 §1] — both
    keys present together, or empty for an excluded / no-data comparison."""

    mean_paired_delta: float = 0.0
    cliffs_delta: float = 0.0


class Decision(_DictModel):
    """The pre-registered decision block [refactor 07 §1, AN-8].

    ``rule``/``observed_delta``/``detected``/``decides_positive`` are always
    present; ``floor`` rides a structurally-insufficient pair [F-H7] and
    ``holm_p``/``correction`` a Holm-adjusted family [PRA-M4] — declaration
    order is the dict-insertion order those paths produced."""

    rule: str = ""
    observed_delta: Optional[float] = None
    detected: Optional[bool] = None
    decides_positive: Optional[bool] = None
    floor: Optional[str] = None
    holm_p: Optional[float] = None
    correction: Optional[str] = None


# --- findings schema -------------------------------------------------------
class Provenance(BaseModel):
    # every field required ⇒ a render missing any provenance fails validation [AC-6]
    model_config = ConfigDict(extra="forbid")
    instrument_version: str
    instrument_git_sha: str
    corpus: Optional[dict]
    ledger_head_hash: str
    chain_ok: bool
    judge: dict


class ComparisonFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    arm_a: str
    arm_b: str
    n_tasks: int
    stats: ComparisonStats
    effect: EffectBlock
    decision: Decision
    # AN-6: machine-checkable provenance of the claim — "computed" (a deterministic
    # function of the ledger) vs "judgment" (rests on the advisory judge)
    claim_tag: Literal["computed", "judgment"]
    excluded_from_official: bool = False
    exclusion_reason: Optional[str] = None
    # PRA-M4: in a >2-arm design, only the pre-registered primary pair
    # (arms[0] vs arms[1]) carries an official decision by default; additional
    # pairs render their CI/effect but are exploratory (no decision), because the
    # spec pre-registers exactly one decision_rule. With --multi-arm-correction
    # =holm every pair is official under a Holm-adjusted family. Absent field on a
    # 2-arm finding = the single official pair.
    official_decision: bool = True


class MDEBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Optional[float]
    assumption_based_mde: bool
    acknowledged_underpowered: bool
    # F-M-S3: the plan-time MDE assumed the plan-time cluster count; when
    # quarantines/missing grades shrank the realized N, quoting the plan figure
    # overstates sensitivity. The achieved figure is a DISCLOSED 1/sqrt(n)
    # scaling of the plan MDE at the realized N — present only when realized N
    # is smaller than planned; the null phrasing then uses it.
    achieved_value: Optional[float] = None
    realized_n_tasks: Optional[int] = None


class FindingsDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experiment_id: str
    seed: int
    primary_metric: str
    decision_rule: str
    # the pre-registered corpus identity, so the official fence can bind a cited
    # manifest to the spec's corpus without re-reading the spec at render [AN-2]
    spec_corpus: dict
    comparisons: list[ComparisonFinding]
    mde: MDEBlock
    ci_selection: dict
    confounds: list[dict]
    secondary_metrics: dict
    integrity: dict
    # AN-9: orphan grades (no matching trial) counted, never silently dropped
    ledger_consistency: dict
    # AN-11: grade-trust tiers — local/fake results are ADVISORY, surfaced not stamped
    tier: dict
    # D-P7-2: terminal-override disclosure — count of --retry-terminal re-attempts
    overrides: dict = {}
    # D-P7-6: whether the lock committed a rubric_sha256; a legacy lock (False)
    # gets a caveat line in the official render instead of a refusal.
    rubric_committed: bool = True
    # EVAL-10 AC-5: per-arm contamination summary (tri-state counts + flagged
    # task ids + asymmetry) — disclosed in BOTH renders, fenced when asymmetric.
    contamination: dict = {}
    # F-M-J1: judge coverage — terminal CANT_JUDGE comparisons are silently
    # excluded from judge_preference and calibration (a biased missing-data
    # channel when exclusions correlate with outcomes, e.g. a canary salted
    # only on losing trials); the counts are disclosed in both renders.
    judge_coverage: dict = {}
    # F-L7: the render mode stamped INTO findings.json — the citable byte
    # string was mode-ambiguous (nothing in the file said whether it was an
    # official or exploratory computation), and exploratory JSON carried no
    # watermark. Set by run_analyze before serialization, so findings_sha256
    # covers it; None only on a bare compute_findings call that never renders.
    mode: Optional[str] = None
    watermark: Optional[str] = None
    # PRA-M4: multi-arm disclosure — {n_arms, correction, note}. Non-empty and
    # non-optional in the render whenever >2 arms were compared, so k-1
    # simultaneous decisions can never be presented without saying so.
    multi_arm: dict = {}
    process: Optional[dict] = None
    judge_calibration: Optional[dict] = None
    # EVAL-11: forensic flags/coverage/kappa + operator quarantines — additive,
    # disclosure-only (never a fence input, never a primary metric) [D004]
    forensics: Optional[dict] = None
    # control-reuse plan: the EXPLORATORY, UNPAIRED reuse section — an imported
    # control vs the fresh contender. Additive, None on non-reuse ledgers (so
    # official output is byte-identical there); never an official decision, read
    # from the reused_* kinds the official path cannot see.
    reuse: Optional[dict] = None
    provenance: Provenance


def display_mde(mde: MDEBlock) -> Optional[float]:
    """The sensitivity figure honest at the REALIZED N [F-M-S3]: the achieved
    MDE when the realized cluster count fell below plan, else the plan MDE."""
    return mde.achieved_value if mde.achieved_value is not None else mde.value
