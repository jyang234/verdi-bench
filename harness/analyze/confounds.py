"""Confound disclosure [EVAL-2 §M5 + EVAL-6 §M3, D002].

``judge_vendor_overlap`` (EVAL-2) derives the vendor from each model-id prefix.
``flag_confounds`` (EVAL-6) emits the auto confound-flag set [EVAL-6-D002]:
``interleave_imbalance``, ``provider_error_asymmetry``,
``telemetry_null_asymmetry``, ``egress_violations``, ``version_drift`` — plus
``judge_vendor_overlap``. Flags **ride** findings; they never suppress them
(disclosure over suppression). A constructed fixture that exhibits exactly one
condition yields exactly that flag; a clean fixture yields none.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..ledger import events
from ..ledger.query import find_events
from ..schema.judge_config import model_vendor


def _vendor(model_id: str) -> str:
    # JD-7: the vendor is the '<provider>/' prefix (one shared definition in
    # schema.judge_config). A prefix-less id has no vendor to compare — returning
    # the whole string made overlap silently wrong, so fail loudly instead.
    # Arm/judge model ids are prefix-validated at the schema, so this only fires on
    # a malformed id that slipped the schema.
    vendor = model_vendor(model_id)
    if vendor is None:
        raise ValueError(
            f"model id {model_id!r} has no '<provider>/' prefix; vendor is undefined [JD-7]"
        )
    return vendor


@dataclass
class VendorOverlap:
    overlap: bool
    judge_vendor: str
    arm_vendors: dict[str, str]
    overlapping_arms: list[str]
    # EVAL-20 AC-3 (additive): each arm's full declared vendor set, and exactly
    # which declared model(s) share the judge vendor — so an aux-model overlap
    # is named, not merely implied. `arm_vendors` keeps its primary-vendor
    # semantics for existing readers.
    arm_vendor_sets: dict[str, list[str]] = field(default_factory=dict)
    overlapping_models: dict[str, list[str]] = field(default_factory=dict)

    def as_flag(self) -> dict:
        return {
            "flag": "judge_vendor_overlap",
            "overlap": self.overlap,
            "judge_vendor": self.judge_vendor,
            "arm_vendors": self.arm_vendors,
            "overlapping_arms": self.overlapping_arms,
            "arm_vendor_sets": self.arm_vendor_sets,
            "overlapping_models": self.overlapping_models,
        }


def judge_vendor_overlap(spec) -> VendorOverlap:
    """Judge/arm vendor overlap over each arm's FULL declared model set
    [EVAL-20 AC-3]: a workflow routing to a judge-vendor sub-model overlaps
    exactly as a judge-vendor primary does, and the flag names which model."""
    judge_vendor = _vendor(spec.judge.model)
    arm_vendors: dict[str, str] = {}
    arm_vendor_sets: dict[str, list[str]] = {}
    overlapping_models: dict[str, list[str]] = {}
    for arm in spec.arms:
        models = arm.declared_models()
        arm_vendors[arm.name] = _vendor(arm.model)
        arm_vendor_sets[arm.name] = sorted({_vendor(m) for m in models})
        hits = [m for m in models if _vendor(m) == judge_vendor]
        if hits:
            overlapping_models[arm.name] = hits
    overlapping = sorted(overlapping_models)
    return VendorOverlap(
        overlap=bool(overlapping),
        judge_vendor=judge_vendor,
        arm_vendors=arm_vendors,
        overlapping_arms=overlapping,
        arm_vendor_sets=arm_vendor_sets,
        overlapping_models=overlapping_models,
    )


# ---------------------------------------------------------------------------
# EVAL-6 automatic confound flags [D002]
# ---------------------------------------------------------------------------
from ..adapters.base import TELEMETRY_FIELDS  # noqa: E402  (grouped with EVAL-6 code)

INTERLEAVE_IMBALANCE_THRESHOLD = 0.34  # normalized mean-position range


def _trial_records(ledger_path) -> list[dict]:
    return [ev["trial_record"] for ev in find_events(ledger_path, events.TRIAL)]


def _flag_interleave_imbalance(ledger_path) -> dict | None:
    """Arm run-position balance from the realized ``executed_order`` [D002].

    A well-randomized schedule spreads each arm evenly across execution
    positions; a systematic front/back skew (one arm mostly early) is a
    confound. We measure each arm's normalized mean position and flag when the
    across-arm range exceeds the threshold.
    """
    order_ev = find_events(ledger_path, events.EXECUTED_ORDER)
    if not order_ev:
        return None
    order = order_ev[-1]["order"]
    # count only real (completed/timeout) trials — infra retries reran as new ids
    positions: dict[str, list[int]] = defaultdict(list)
    seq = [e for e in order if e.get("outcome") != "infra_failed"]
    n = len(seq)
    if n < 2:
        return None
    for i, e in enumerate(seq):
        positions[e["arm"]].append(i)
    denom = n - 1
    mean_pos = {arm: (sum(idxs) / len(idxs)) / denom for arm, idxs in positions.items()}
    if len(mean_pos) < 2:
        return None
    spread = max(mean_pos.values()) - min(mean_pos.values())
    if spread > INTERLEAVE_IMBALANCE_THRESHOLD:
        return {
            "flag": "interleave_imbalance",
            "arm_mean_position": {k: round(v, 4) for k, v in sorted(mean_pos.items())},
            "spread": round(spread, 4),
        }
    return None


def _flag_provider_error_asymmetry(ledger_path) -> dict | None:
    """Infra/provider failures counted per arm; asymmetry is a confound [D002]."""
    counts: dict[str, int] = defaultdict(int)
    arms: set[str] = set()
    for rec in _trial_records(ledger_path):
        arms.add(rec["arm"])
    for ev in find_events(ledger_path, events.TRIAL_INFRA_FAILED):
        counts[ev["arm"]] += 1
        arms.add(ev["arm"])
    if not counts:
        return None
    per_arm = {a: counts.get(a, 0) for a in arms}
    hi = max(per_arm.values())
    lo = min(per_arm.values())
    # asymmetric iff one arm has failures the other lacks, or a >1 gap
    if hi > 0 and (lo == 0 or hi - lo >= 2):
        return {
            "flag": "provider_error_asymmetry",
            "infra_failures_by_arm": dict(sorted(per_arm.items())),
        }
    return None


def _flag_telemetry_null_asymmetry(ledger_path) -> dict | None:
    """A telemetry field null in one arm's adapter but not the other's [D002, AC-7].

    This is also the signal the renderer uses to exclude a metric from official
    comparison (asymmetric nulls are excluded and flagged, never imputed).
    """
    # per arm: fields that are EVER null, and fields that are EVER present
    ever_null: dict[str, set[str]] = defaultdict(set)
    ever_present: dict[str, set[str]] = defaultdict(set)
    arms: set[str] = set()
    for rec in _trial_records(ledger_path):
        arm = rec["arm"]
        arms.add(arm)
        nulls = set(rec.get("telemetry_nulls", []))
        for f in TELEMETRY_FIELDS:
            if f in nulls:
                ever_null[arm].add(f)
            else:
                ever_present[arm].add(f)
    if len(arms) < 2:
        return None
    asymmetric: list[str] = []
    for f in TELEMETRY_FIELDS:
        null_in = {a for a in arms if f in ever_null[a]}
        present_in = {a for a in arms if f in ever_present[a] and f not in ever_null[a]}
        # field null in at least one arm and cleanly present in at least one other
        if null_in and present_in and null_in != arms:
            asymmetric.append(f)
    if asymmetric:
        return {"flag": "telemetry_null_asymmetry", "fields": sorted(asymmetric)}
    return None


def _flag_egress_violations(ledger_path) -> dict | None:
    """Any trial flagged with an egress violation [D002]."""
    offenders = [
        rec["trial_id"]
        for rec in _trial_records(ledger_path)
        if rec.get("flags", {}).get("egress_violation")
    ]
    if offenders:
        return {"flag": "egress_violations", "trials": sorted(offenders)}
    return None


def _flag_version_drift(ledger_path) -> dict | None:
    """Image digest / agent version varying within an arm across the run [D002]."""
    digests: dict[str, set[str]] = defaultdict(set)
    versions: dict[str, set[str]] = defaultdict(set)
    for rec in _trial_records(ledger_path):
        prov = rec.get("provenance", {})
        arm = rec["arm"]
        if prov.get("image_digest") is not None:
            digests[arm].add(prov["image_digest"])
        if prov.get("agent_binary_version") is not None:
            versions[arm].add(prov["agent_binary_version"])
    drifted = {
        arm
        for arm in set(digests) | set(versions)
        if len(digests.get(arm, set())) > 1 or len(versions.get(arm, set())) > 1
    }
    if drifted:
        return {
            "flag": "version_drift",
            "arms": sorted(drifted),
            "image_digests_by_arm": {a: sorted(digests[a]) for a in sorted(drifted)},
            "agent_versions_by_arm": {a: sorted(versions[a]) for a in sorted(drifted)},
        }
    return None


def flag_confounds(ledger_path, spec) -> list[dict]:
    """Emit exactly the [D002] confound-flag set present in this experiment.

    Returns only flags whose condition holds — a clean run yields an empty list.
    Flags disclose; they never suppress a finding.
    """
    flags: list[dict] = []
    overlap = judge_vendor_overlap(spec)
    if overlap.overlap:
        flags.append(overlap.as_flag())
    for detector in (
        _flag_interleave_imbalance,
        _flag_provider_error_asymmetry,
        _flag_telemetry_null_asymmetry,
        _flag_egress_violations,
        _flag_version_drift,
    ):
        flag = detector(ledger_path)
        if flag is not None:
            flags.append(flag)
    return flags


def asymmetric_null_fields(ledger_path) -> list[str]:
    """Telemetry fields excluded from official comparison for asymmetric nulls."""
    flag = _flag_telemetry_null_asymmetry(ledger_path)
    return flag["fields"] if flag else []
