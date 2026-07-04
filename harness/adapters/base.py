"""``TrialRecord`` and the adapter base [EVAL-4 AC-2, §5.1].

A trial is a sealed event: pinned image in, one prompt in, artifacts and a
normalized ``TrialRecord`` out. Every telemetry field is ``Optional``; a ``null``
means *unmeasurable*, recorded as a paired entry in ``telemetry_nulls`` and
**never imputed or proxy-estimated** [D004]. The proxy meters only as a
cross-check signal, surfaced as a delta — never used to fill a null.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, model_validator

ADVISORY = "ADVISORY"

TELEMETRY_FIELDS = ("tokens_in", "tokens_out", "tokens_cache", "cost", "wall_time_s", "tool_calls")


def coerce_int(v) -> Optional[int]:
    """int(v) for real numbers only; anything else (incl. bool) ⇒ None.

    bool is an int subclass, so ``True`` would coerce to ``1`` — but a boolean
    where a count is expected is *unmeasurable*, and must become null, never an
    imputed 1/0 [D004]."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return int(v)


def coerce_float(v) -> Optional[float]:
    """float(v) for real numbers only; anything else (incl. bool) ⇒ None [D004]."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


class Outcome(str, Enum):
    completed = "completed"
    timeout = "timeout"
    infra_failed = "infra_failed"


class Telemetry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    tokens_cache: Optional[int] = None
    cost: Optional[float] = None
    wall_time_s: Optional[float] = None
    tool_calls: Optional[int] = None

    def null_fields(self) -> list[str]:
        return [f for f in TELEMETRY_FIELDS if getattr(self, f) is None]


class Quotas(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cpus: Optional[float] = None
    mem: Optional[str] = None


class Flags(BaseModel):
    model_config = ConfigDict(extra="allow")  # room for future flags [§5.1 "..."]
    egress_violation: bool = False


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image_digest: Optional[str] = None
    agent_binary_version: Optional[str] = None
    harbor_version: Optional[str] = None
    engine: str = "fake"
    tier: str = ADVISORY  # local records are always ADVISORY [AC-9]
    executed_at: Optional[str] = None
    quotas: Quotas = Quotas()


class TrialRecord(BaseModel):
    """Normalized, engine-agnostic record of a single trial [AC-1, AC-2]."""

    model_config = ConfigDict(extra="forbid")

    trial_id: str
    task_id: str
    arm: str
    repetition: int
    outcome: Outcome
    exit_status: Optional[int] = None
    telemetry: Telemetry = Telemetry()
    telemetry_nulls: list[str] = []
    flags: Flags = Flags()
    provenance: Provenance = Provenance()
    artifacts_path: Optional[str] = None

    @model_validator(mode="after")
    def _nulls_match_telemetry(self) -> "TrialRecord":
        """The nulls list must exactly mirror the None telemetry fields —
        so a null can never be silently imputed nor a value silently dropped."""
        expected = set(self.telemetry.null_fields())
        got = set(self.telemetry_nulls)
        if expected != got:
            raise ValueError(
                f"telemetry_nulls {sorted(got)} does not match null telemetry "
                f"fields {sorted(expected)}; nulls are flagged, never imputed [D004]"
            )
        return self

    @classmethod
    def assemble(
        cls,
        *,
        trial_id: str,
        task_id: str,
        arm: str,
        repetition: int,
        outcome: Outcome,
        telemetry: Telemetry,
        provenance: Provenance,
        exit_status: Optional[int] = None,
        flags: Optional[Flags] = None,
        artifacts_path: Optional[str] = None,
    ) -> "TrialRecord":
        return cls(
            trial_id=trial_id,
            task_id=task_id,
            arm=arm,
            repetition=repetition,
            outcome=outcome,
            exit_status=exit_status,
            telemetry=telemetry,
            telemetry_nulls=telemetry.null_fields(),
            flags=flags or Flags(),
            provenance=provenance,
            artifacts_path=artifacts_path,
        )


class Adapter:
    """Base adapter: agent-native logs → normalized :class:`Telemetry`.

    Subclasses parse their agent's log format. Anything unparseable stays
    ``None`` (→ ``telemetry_nulls``); never estimated [D004].
    """

    platform: str = "base"

    def normalize(self, native_log: dict) -> Telemetry:  # pragma: no cover - abstract
        raise NotImplementedError
