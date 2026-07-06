"""Trajectory capture — the versioned per-trial step record [EVAL-12 AC-1, AC-2].

``TrajectoryRecord`` is a versioned contract: the ordered steps of a trial,
normalized across adapters. Every non-kind step field is ``Optional``; a null
means *unmeasurable by this adapter* and is never estimated [§7.8, EVAL-4-D004].
The record persists as a per-trial artifact (``artifacts/trajectory.json``) in
canonical JSON, and the sha256 of the exact persisted bytes is ledgered as the
additive ``trajectory_sha`` field on the trial event [EVAL-12-D001].

Capture honesty [AC-2]: the serialized record passes the EVAL-4 secret scrub
before persisting, and is re-validated afterwards — a scrub that breaks the
record's structure, an unwritable artifact, or a read-back mismatch raises
:class:`TrajectoryCorruptError`, which fails the trial closed
(``trial_infra_failed(trajectory_corrupt)``, the ``telemetry_corrupt``
precedent). An engine/adapter that cannot produce a trajectory yields *no*
record at all — honest absence is distinguishable from an empty step list.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from .redact import redact_text

TRAJECTORY_SCHEMA_VERSION = 3
TRAJECTORY_FILENAME = "trajectory.json"

# EVAL-21 AC-3 [D003: closed-role-vocabulary]: the ONLY values an agent label
# may take — role, optionally with a small ordinal (worker-1). The value space
# cannot spell a model, vendor, platform, or arm identity, so attribution
# needs no scrub and the blind subsystem stays untouched. Extending this set
# is a schema-version bump (the detector-vocabulary precedent — the
# closed-enum test forces it).
AGENT_ROLES = frozenset({
    "planner", "executor", "orchestrator", "router", "critic",
    "reviewer", "tester", "researcher", "worker",
})
_AGENT_LABEL_RE = re.compile(
    r"^(?:%s)(?:-\d{1,3})?$" % "|".join(sorted(AGENT_ROLES))
)

# The bucket ``slice_by_agent`` files null-agent steps under [EVAL-21 AC-6].
# Deliberately outside AGENT_ROLES, so no declared label can collide with it.
UNATTRIBUTED = "unattributed"


def validate_agent_label(v: Optional[str]) -> Optional[str]:
    """Closed-vocabulary agent-role check, shared by the trajectory step and the
    flight recorder's reasoning entry [EVAL-21 AC-3; EVAL-24 AC-6]: ``None``
    (unattributed) or a ``role(-ordinal)`` label, else a ``ValueError`` the
    generic parse surfaces as :class:`~harness.adapters.generic.GenericLogError`.
    Identity leakage is unrepresentable, not scrubbed — a label outside the
    vocabulary is refused."""
    if v is None:
        return v
    if not _AGENT_LABEL_RE.fullmatch(v):
        raise ValueError(
            f"agent label {v!r} is not in the closed role vocabulary "
            f"{sorted(AGENT_ROLES)} (optionally '-<ordinal>', e.g. 'worker-2') [EVAL-21 AC-3]"
        )
    return v


class TrajectoryCorruptError(RuntimeError):
    """A trajectory could not be persisted or read back intact [AC-2].

    Distinct from an *absent* trajectory (an adapter returning ``None``), which
    is a legitimate, honest state — this error is the fail-closed path.
    """


class TrajectoryStep(BaseModel):
    """One normalized step. Non-kind fields are null when unmeasurable [D004]."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["tool_call", "file_edit", "test_run", "message"]
    relative_ts: Optional[float] = None
    tokens: Optional[int] = None
    cost: Optional[float] = None
    files_touched: Optional[list[str]] = None
    exit_code: Optional[int] = None
    # v2 additive field [EVAL-11-D005]: the shell command a step executed.
    # "" = measured, the step is not a shell command (the codex files=[]
    # precedent); null = unmeasurable — a v1 record reads back null throughout.
    command: Optional[str] = None
    # v3 carries TWO additive fields, approved the same day by two parallel
    # stories over the same base: 'detail' (EVAL-14-D004, the observability
    # lineage) and 'agent' (EVAL-21-D001, the multi-model lineage). Both are
    # null-defaulted, so records written by either pre-merge branch read back
    # under the merged model unchanged.
    # The step's content, kind-dependent — message text, a file_edit's patch
    # material, a tool_call/test_run's output — read from the native log,
    # never reconstructed. "" = measured empty; null = the platform did not
    # expose it (the command precedent). Pre-v3 records read back null
    # throughout; no reader may require it. Renderers that leave the operator
    # tier (dossier, timeline) exclude it [EVAL-15 guardrails]; capture rides
    # the same persist-time scrub as every other string field.
    detail: Optional[str] = None
    # Closed-vocabulary role label [EVAL-21 AC-1, D001] attributing the step
    # to a sub-agent of a multi-agent workflow. Null = unattributed — the
    # honest state for single-agent platforms and v1/v2 records, which read
    # back null throughout; no reader may require it.
    agent: Optional[str] = None

    @field_validator("agent")
    @classmethod
    def _agent_in_vocabulary(cls, v: Optional[str]) -> Optional[str]:
        # AC-3: identity leakage is unrepresentable, not scrubbed — a label
        # outside the closed vocabulary ('llama-planner', free text) is refused
        # at the schema, which the generic parse surfaces as GenericLogError.
        # Shared with the flight recorder's ReasoningEntry [EVAL-24 AC-6].
        return validate_agent_label(v)


class TrajectoryRecord(BaseModel):
    """Versioned, ordered per-trial trajectory [AC-1]."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = TRAJECTORY_SCHEMA_VERSION
    trial_id: str
    platform: str
    steps: list[TrajectoryStep]


def canonical_bytes(record: TrajectoryRecord) -> bytes:
    """Canonical serialization — the ledger chain's own JSON conventions, so
    the artifact is byte-deterministic and ``trajectory_sha`` is well-defined."""
    return json.dumps(
        record.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def trajectory_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def persist_trajectory(
    record: TrajectoryRecord,
    artifacts_dir,
    extra_patterns: Optional[list[str]] = None,
) -> str:
    """Scrub → re-validate → write → read back; return the persisted sha256.

    The scrub runs over the serialized text so every string field — present and
    future — passes the same EVAL-4 door the workspace does, including the
    injected provider-key literals in ``extra_patterns`` [AC-2]. Any failure is
    :class:`TrajectoryCorruptError`: a corrupt or unwritable trajectory fails
    the trial closed, never persists silently wrong bytes.
    """
    text = canonical_bytes(record).decode("utf-8")
    scrubbed, n_hits = redact_text(text, extra_patterns)
    if n_hits:
        # only a scrub that actually rewrote bytes can have broken the record's
        # structure; re-validate before those bytes become the artifact
        try:
            TrajectoryRecord.model_validate(json.loads(scrubbed))
        except (json.JSONDecodeError, ValidationError) as e:
            raise TrajectoryCorruptError(
                f"trajectory for {record.trial_id} is not a valid record after "
                f"redaction — refusing to persist a broken artifact [AC-2]: {e}"
            ) from e
    data = scrubbed.encode("utf-8")
    path = Path(artifacts_dir) / TRAJECTORY_FILENAME
    try:
        path.write_bytes(data)
        readback = path.read_bytes()
    except OSError as e:
        raise TrajectoryCorruptError(
            f"trajectory for {record.trial_id} could not be written to {path}: {e}"
        ) from e
    if readback != data:
        raise TrajectoryCorruptError(
            f"trajectory artifact {path} did not read back byte-identical; "
            "refusing a sha over bytes that are not on disk [AC-2]"
        )
    return trajectory_sha256(data)


def parse_trajectory(data: bytes, *, source: str = "trajectory artifact") -> TrajectoryRecord:
    """Parse trajectory bytes; anything invalid is :class:`TrajectoryCorruptError`."""
    try:
        return TrajectoryRecord.model_validate(json.loads(data.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as e:
        raise TrajectoryCorruptError(
            f"{source} is corrupt (present but not a valid "
            f"v{TRAJECTORY_SCHEMA_VERSION} record): {e}"
        ) from e


def load_trajectory(path) -> TrajectoryRecord:
    """Load a persisted trajectory artifact; corrupt content fails loud.

    Callers distinguish *absent* (no file — pre-EVAL-12 trial or an engine that
    honestly produced none) from *corrupt* (present but unreadable) themselves;
    only the latter is an error state [AC-2].
    """
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise TrajectoryCorruptError(f"trajectory artifact {path} unreadable: {e}") from e
    return parse_trajectory(raw, source=f"trajectory artifact {path}")


def slice_by_agent(record: TrajectoryRecord) -> dict[str, list[TrajectoryStep]]:
    """Group a trajectory's steps by agent label [EVAL-21 AC-6].

    The forensics substrate: order is preserved within each group, and steps
    with a null ``agent`` (single-agent platforms, v1/v2 records) land in the
    explicit :data:`UNATTRIBUTED` bucket — never dropped, never redistributed.
    ``UNATTRIBUTED`` cannot collide with a real label (it is outside the
    closed vocabulary).
    """
    groups: dict[str, list[TrajectoryStep]] = {}
    for step in record.steps:
        groups.setdefault(step.agent or UNATTRIBUTED, []).append(step)
    return groups


def resolve_trajectory(artifacts_path, ledgered_sha) -> tuple[str, Optional[TrajectoryRecord]]:
    """Resolve a trial's trajectory to ``(status, record-or-None)``.

    The closed status vocabulary: ``verified`` (artifact bytes hash to the
    ledgered sha — the only status that yields a record), ``absent`` (no
    ledgered sha: pre-EVAL-12 trial or an honestly absent trajectory),
    ``missing_artifact``, ``sha_mismatch``, and ``corrupt`` (present but
    unreadable or unparseable). The sha and the parsed record come from the
    same read, so a record is never evidence unless its exact bytes matched
    the chain; readers (the dossier today, EVAL-11 forensics next) get one
    verifier instead of each growing their own. Never raises — coverage gaps
    are data with a named reason, and the run-path fail-closed door is
    :func:`persist_trajectory`, not here.
    """
    if ledgered_sha is None:
        return "absent", None
    if not artifacts_path:
        return "missing_artifact", None
    path = Path(artifacts_path) / TRAJECTORY_FILENAME
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return "missing_artifact", None
    except OSError:
        return "corrupt", None
    if trajectory_sha256(raw) != ledgered_sha:
        return "sha_mismatch", None
    try:
        return "verified", parse_trajectory(raw, source=f"trajectory artifact {path}")
    except TrajectoryCorruptError:
        return "corrupt", None
