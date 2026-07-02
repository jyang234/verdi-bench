"""Confound disclosure [EVAL-2 §M5, AC-6].

``judge_vendor_overlap`` derives the vendor from each model-id prefix (judge and
every arm). Overlap ⇒ a confound flag registered on the experiment, rendered
later in the report header (EVAL-6) and review-packet header (EVAL-7). Same-vendor
is **legal and always disclosed** [D001] — this never blocks a run, it discloses.
"""

from __future__ import annotations

from dataclasses import dataclass


def _vendor(model_id: str) -> str:
    return model_id.split("/", 1)[0]


@dataclass
class VendorOverlap:
    overlap: bool
    judge_vendor: str
    arm_vendors: dict[str, str]
    overlapping_arms: list[str]

    def as_flag(self) -> dict:
        return {
            "flag": "judge_vendor_overlap",
            "overlap": self.overlap,
            "judge_vendor": self.judge_vendor,
            "arm_vendors": self.arm_vendors,
            "overlapping_arms": self.overlapping_arms,
        }


def judge_vendor_overlap(spec) -> VendorOverlap:
    """Compute the judge/arm vendor overlap for an :class:`ExperimentSpec`."""
    judge_vendor = _vendor(spec.judge.model)
    arm_vendors = {arm.name: _vendor(arm.model) for arm in spec.arms}
    overlapping = [name for name, v in arm_vendors.items() if v == judge_vendor]
    return VendorOverlap(
        overlap=bool(overlapping),
        judge_vendor=judge_vendor,
        arm_vendors=arm_vendors,
        overlapping_arms=overlapping,
    )
