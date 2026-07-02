"""Versioned process rubric [EVAL-9 §M1, AC-1, D003].

A ``ProcessRubric`` is loaded from a versioned YAML file — anchored ordinal
scales (1..5), five v1 dimensions. The dimensions live in the **file**, not
code, so a rubric change is a data edit + version bump. The ``rubric_version`` is
stamped into every ``process_score`` event; fixtures score against a pinned
rubric so a silent anchor edit can't retro-desync existing scores.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

VALID_CORRELATES = {"tokens", "tool_calls", "wall_time", "retries", "timeouts"}
SCALE_MIN = 1
SCALE_MAX = 5

_DEFAULT_RUBRIC = Path(__file__).parent / "rubrics" / "process-v1.yaml"


class Dimension(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    scale: int = SCALE_MAX
    anchors: dict[int, str]
    telemetry_correlates: list[str]

    @field_validator("scale")
    @classmethod
    def _scale_is_five(cls, v: int) -> int:
        if v != SCALE_MAX:
            raise ValueError(f"v1 dimensions use a 1..{SCALE_MAX} scale; got scale={v}")
        return v

    @field_validator("telemetry_correlates")
    @classmethod
    def _known_correlates(cls, v: list[str]) -> list[str]:
        unknown = [c for c in v if c not in VALID_CORRELATES]
        if unknown:
            raise ValueError(f"unknown telemetry correlates {unknown}; allowed {sorted(VALID_CORRELATES)}")
        return v

    @model_validator(mode="after")
    def _anchors_cover_scale(self) -> "Dimension":
        expected = set(range(SCALE_MIN, self.scale + 1))
        if set(self.anchors) != expected:
            raise ValueError(
                f"dimension {self.id!r} anchors must cover exactly {sorted(expected)}; "
                f"got {sorted(self.anchors)}"
            )
        return self

    def is_valid_score(self, value: int) -> bool:
        return SCALE_MIN <= value <= self.scale


class ProcessRubric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rubric_version: str
    dimensions: list[Dimension]

    @model_validator(mode="after")
    def _unique_ids(self) -> "ProcessRubric":
        ids = [d.id for d in self.dimensions]
        if len(ids) != len(set(ids)):
            raise ValueError("dimension ids must be unique")
        if not ids:
            raise ValueError("a rubric needs at least one dimension")
        return self

    def dimension(self, dim_id: str) -> Dimension | None:
        for d in self.dimensions:
            if d.id == dim_id:
                return d
        return None

    @property
    def dimension_ids(self) -> list[str]:
        return [d.id for d in self.dimensions]

    def render(self) -> str:
        """Human/judge-facing rubric text (anchors inline)."""
        blocks = []
        for d in self.dimensions:
            anchors = "\n".join(f"    {k}: {d.anchors[k]}" for k in sorted(d.anchors))
            blocks.append(f"## {d.name} ({d.id}), scale 1..{d.scale}\n{anchors}")
        return f"# Process rubric {self.rubric_version}\n\n" + "\n\n".join(blocks)

    @classmethod
    def from_yaml(cls, path=None) -> "ProcessRubric":
        path = Path(path) if path is not None else _DEFAULT_RUBRIC
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls.model_validate(data)


def default_rubric() -> ProcessRubric:
    return ProcessRubric.from_yaml(_DEFAULT_RUBRIC)
