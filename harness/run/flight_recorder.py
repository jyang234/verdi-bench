"""Flight recorder — per-trial reasoning capture, operator-tier [EVAL-24 AC-1].

The trajectory (EVAL-12/15/16) records a trial's *actions*; the flight recorder
records its *reasoning* — the chain of thought by which each arm reached its
answer. It persists as a per-trial artifact (``artifacts/flight_recorder.json``)
in canonical JSON, bound to the chain by an additive ``flight_recorder_sha`` on
the trial event [EVAL-24-D001, the ``trajectory_sha`` precedent].

It is deliberately a **separate** artifact from the graded trajectory: verbose,
identity-leaky reasoning never enters the closed ``TrajectoryStep`` vocabulary
the deterministic detectors and the official path consume. Reasoning is
operator-tier and advisory-review-fed only — never the judge packet, the
deterministic grade, or the pre-registration fence (isolation by construction,
EVAL-24 AC-2). A ``ReasoningEntry`` may carry an optional ``agent`` role
[EVAL-24 AC-6] attributing the reasoning to a sub-agent of a multi-agent
workflow, over the SAME closed EVAL-21 vocabulary as the trajectory; null =
unattributed (single-agent reasoning).

Capture honesty mirrors the trajectory [AC-1]: the serialized record passes the
EVAL-4 secret scrub before persisting and is re-validated after; a scrub that
breaks it, an unwritable artifact, or a read-back mismatch raises
:class:`FlightRecorderCorruptError`, which fails the trial closed. A platform
that exposes no reasoning yields *no* record — honest absence is distinguishable
from an empty recorder [AC-4].
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from ..adapters.base import coerce_float, coerce_int
from .redact import redact_text
from .trajectory import UNATTRIBUTED, validate_agent_label

# v2 [EVAL-24 AC-6] adds the optional per-entry ``agent`` role (multi-agent
# reasoning attribution) the trajectory-additive-field way: a v1 recorder reads
# back with null agent throughout, no reader may require it.
FLIGHT_RECORDER_SCHEMA_VERSION = 2
FLIGHT_RECORDER_FILENAME = "flight_recorder.json"

# EVAL-24-D003 [fixed-per-trial-byte-cap]: a documented per-trial reasoning byte
# budget. An over-budget recorder is never truncated at capture (honest capture);
# the advisory review that reads it degrades to CANT_REVIEW(context_overflow) — a
# named coverage gap [AC-3]. Sized (in UTF-8 bytes of reasoning content) under
# the review's ~100k-token / chars-4 ceiling with margin.
DEFAULT_REASONING_BUDGET_BYTES = 262_144


class FlightRecorderCorruptError(RuntimeError):
    """A flight recorder could not be persisted or read back intact [AC-1].

    Distinct from an *absent* recorder (an adapter/platform exposing no
    reasoning), which is a legitimate honest state — this is the fail-closed
    path, the :class:`TrajectoryCorruptError` precedent.
    """


class ReasoningEntry(BaseModel):
    """One reasoning span. ``content`` is the reasoning text; ``tokens``/``cost``
    are Optional and null-honest (a boolean or non-number becomes null, never an
    imputed value — the Telemetry coercion, shared) [EVAL-4-D004].

    ``agent`` [EVAL-24 AC-6] optionally attributes the reasoning to a sub-agent of
    a multi-agent workflow, over the SAME closed EVAL-21 role vocabulary as the
    trajectory (``planner``/``worker-2``/…); null = unattributed — single-agent
    reasoning and v1 records, which read back null throughout."""

    model_config = ConfigDict(extra="forbid")

    content: str
    tokens: Optional[int] = None
    cost: Optional[float] = None
    agent: Optional[str] = None

    @field_validator("tokens", mode="before")
    @classmethod
    def _coerce_tokens(cls, v):
        return coerce_int(v)

    @field_validator("cost", mode="before")
    @classmethod
    def _coerce_cost(cls, v):
        return coerce_float(v)

    @field_validator("agent")
    @classmethod
    def _agent_in_vocabulary(cls, v: Optional[str]) -> Optional[str]:
        return validate_agent_label(v)


class FlightRecorder(BaseModel):
    """Versioned, ordered per-trial reasoning record [AC-1]."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = FLIGHT_RECORDER_SCHEMA_VERSION
    trial_id: str
    platform: str
    entries: list[ReasoningEntry]

    def content_bytes(self) -> int:
        """Total UTF-8 bytes of reasoning content — what the byte budget bounds."""
        return sum(len(e.content.encode("utf-8")) for e in self.entries)

    def as_transcript(self) -> str:
        """Reasoning rendered as the advisory review's transcript input [AC-3]."""
        return "\n\n".join(e.content for e in self.entries)


def canonical_bytes(record: FlightRecorder) -> bytes:
    """Canonical serialization — the ledger chain's own JSON conventions, so the
    artifact is byte-deterministic and ``flight_recorder_sha`` is well-defined
    (the ``trajectory.canonical_bytes`` convention)."""
    return json.dumps(
        record.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def flight_recorder_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def persist_flight_recorder(
    record: FlightRecorder,
    artifacts_dir,
    extra_patterns: Optional[list[str]] = None,
) -> str:
    """Scrub → re-validate → write → read back; return the persisted sha256.

    The scrub runs over the serialized text so every string field (the reasoning
    content especially) passes the same EVAL-4 secret door the trajectory does,
    including the injected provider-key literals in ``extra_patterns`` [AC-1].
    Any failure is :class:`FlightRecorderCorruptError`: a corrupt or unwritable
    recorder fails the trial closed, never persists silently wrong bytes.
    """
    text = canonical_bytes(record).decode("utf-8")
    scrubbed, n_hits = redact_text(text, extra_patterns)
    if n_hits:
        try:
            FlightRecorder.model_validate(json.loads(scrubbed))
        except (json.JSONDecodeError, ValidationError) as e:
            raise FlightRecorderCorruptError(
                f"flight recorder for {record.trial_id} is not a valid record "
                f"after redaction — refusing to persist a broken artifact [AC-1]: {e}"
            ) from e
    data = scrubbed.encode("utf-8")
    path = Path(artifacts_dir) / FLIGHT_RECORDER_FILENAME
    try:
        path.write_bytes(data)
        readback = path.read_bytes()
    except OSError as e:
        raise FlightRecorderCorruptError(
            f"flight recorder for {record.trial_id} could not be written to {path}: {e}"
        ) from e
    if readback != data:
        raise FlightRecorderCorruptError(
            f"flight recorder artifact {path} did not read back byte-identical; "
            "refusing a sha over bytes that are not on disk [AC-1]"
        )
    return flight_recorder_sha256(data)


def parse_flight_recorder(data: bytes, *, source: str = "flight recorder artifact") -> FlightRecorder:
    """Parse recorder bytes; anything invalid is :class:`FlightRecorderCorruptError`."""
    try:
        return FlightRecorder.model_validate(json.loads(data.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as e:
        raise FlightRecorderCorruptError(
            f"{source} is corrupt (present but not a valid "
            f"v{FLIGHT_RECORDER_SCHEMA_VERSION} record): {e}"
        ) from e


def resolve_flight_recorder(artifacts_path, ledgered_sha) -> tuple[str, Optional[FlightRecorder]]:
    """Resolve a trial's flight recorder to ``(status, record-or-None)``.

    The closed status vocabulary mirrors :func:`resolve_trajectory`: ``verified``
    (artifact bytes hash to the ledgered sha — the only status that yields a
    record), ``absent`` (no ledgered sha: a trial that captured no reasoning),
    ``missing_artifact``, ``sha_mismatch``, and ``corrupt``. A record is never a
    consumer's evidence unless its exact bytes matched the chain. Never raises —
    the run-path fail-closed door is :func:`persist_flight_recorder`, not here.
    """
    if ledgered_sha is None:
        return "absent", None
    if not artifacts_path:
        return "missing_artifact", None
    path = Path(artifacts_path) / FLIGHT_RECORDER_FILENAME
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return "missing_artifact", None
    except OSError:
        # present but unreadable (permission/IO) is not honest absence — the
        # resolve_trajectory parity: a non-FileNotFound read fault is corrupt.
        return "corrupt", None
    if flight_recorder_sha256(data) != ledgered_sha:
        return "sha_mismatch", None
    try:
        return "verified", parse_flight_recorder(data, source=f"flight recorder artifact {path}")
    except FlightRecorderCorruptError:
        return "corrupt", None


def slice_reasoning_by_agent(record: FlightRecorder) -> dict[str, list[ReasoningEntry]]:
    """Group reasoning by sub-agent role [EVAL-24 AC-6, the ``slice_by_agent``
    precedent]. Order is preserved within each group; entries with a null agent
    (single-agent reasoning, v1 records) land in the explicit ``UNATTRIBUTED``
    bucket — never dropped, never redistributed, and it cannot collide with a
    real role (it is outside the closed vocabulary)."""
    groups: dict[str, list[ReasoningEntry]] = {}
    for entry in record.entries:
        groups.setdefault(entry.agent or UNATTRIBUTED, []).append(entry)
    return groups
