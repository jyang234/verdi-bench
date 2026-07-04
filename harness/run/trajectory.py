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
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, ValidationError

from .redact import redact_text

TRAJECTORY_SCHEMA_VERSION = 1
TRAJECTORY_FILENAME = "trajectory.json"


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
