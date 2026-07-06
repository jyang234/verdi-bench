"""Envelope decode + the redacted, sha-bound span artifact [refactor 09 §5].

Turns the collector's raw envelope lines into OTLP-JSON, wraps them in a
versioned :class:`OtlpCaptureRecord`, and persists it through the SAME
scrub -> revalidate -> write -> readback door as the trajectory
(:func:`~harness.run.trajectory.persist_versioned_artifact`). The persisted sha
binds the artifact to the chain (the ``spans_sha`` field on the ``trial`` event,
A13). Decoding is deterministic and post-trial — raw bytes are the evidence,
interpretation is replayable.

Rent-vs-build boundary [refactor 09 §1]: ``resource_spans`` is externally-shaped
OTLP-JSON and passes through **intact** (it is evidence, not our schema); the
wrapper, the canonical bytes, and the sha are ours.

Protobuf bodies are decoded with ``opentelemetry-proto`` (+``protobuf``), shipped
as the optional extra ``verdi-bench[otlp]`` (A14). The import is **lazy**; a
protobuf envelope encountered without the extra installed raises
:class:`OtlpDependencyError` with an actionable message — configured capture
never silently degrades.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Iterable, Optional

from pydantic import BaseModel, ConfigDict, ValidationError

from harness.run.trajectory import persist_versioned_artifact

SPANS_SCHEMA_VERSION = 1
SPANS_FILENAME = "otlp_spans.json"


class OtlpDependencyError(RuntimeError):
    """A protobuf OTLP envelope was encountered without the ``verdi-bench[otlp]``
    extra installed [refactor 09 §5, A14]. Fail loud with an actionable message —
    configured capture must never silently degrade to dropping protobuf spans."""


class SpansCaptureError(RuntimeError):
    """The span artifact could not be canonicalized, persisted, or read back intact
    [refactor 09 §5]. Distinct from an *absent* artifact (no collector configured,
    which yields no record at all) — this is the fail-closed path (the
    :class:`~harness.run.trajectory.TrajectoryCorruptError` precedent)."""


class OtlpBatch(BaseModel):
    """One accepted envelope's decoded payload. ``content_type`` is the transport
    the span batch arrived as; ``resource_spans`` is the OTLP-JSON ``resourceSpans``
    array, external-shaped and passed through intact."""

    model_config = ConfigDict(extra="forbid")
    content_type: str
    resource_spans: list = []


class OtlpCaptureRecord(BaseModel):
    """Versioned per-trial span capture — the workspace artifact ``otlp_spans.json``."""

    model_config = ConfigDict(extra="forbid")
    schema_version: int = SPANS_SCHEMA_VERSION
    trial_id: str
    batches: list[OtlpBatch]


def _load_proto():
    """Lazily import the optional protobuf decoder [A14]. An absent extra is a
    loud, actionable failure — never a silent drop of protobuf spans."""
    try:
        from google.protobuf.json_format import MessageToDict
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
            ExportTraceServiceRequest,
        )
    except ImportError as e:  # the extra is not installed
        raise OtlpDependencyError(
            "decoding a protobuf OTLP envelope requires the optional dependency "
            "`verdi-bench[otlp]` (opentelemetry-proto + protobuf); install it with "
            "`uv sync --extra otlp` — configured capture never silently degrades "
            "[refactor 09 §5, A14]"
        ) from e
    return ExportTraceServiceRequest, MessageToDict


def _resource_spans(payload: dict) -> list:
    """The OTLP-JSON ``resourceSpans`` array (camelCase per the proto3 JSON
    mapping; a snake_case emitter is tolerated), passed through intact."""
    if not isinstance(payload, dict):
        return []
    return payload.get("resourceSpans", payload.get("resource_spans", [])) or []


def _decode_protobuf(raw: bytes) -> dict:
    request_cls, message_to_dict = _load_proto()
    msg = request_cls()
    msg.ParseFromString(raw)
    return message_to_dict(msg)  # camelCase OTLP-JSON: {"resourceSpans": [...]}


def _decode_envelope(envelope: dict) -> OtlpBatch:
    content_type = envelope.get("content_type", "")
    if "body_json" in envelope:
        payload = envelope["body_json"]
    elif "body_b64" in envelope:
        payload = _decode_protobuf(base64.b64decode(envelope["body_b64"]))
    else:
        payload = {}
    return OtlpBatch(content_type=content_type, resource_spans=_resource_spans(payload))


def decode_envelope_lines(lines: Iterable[str], trial_id: str) -> OtlpCaptureRecord:
    """Filter the shared envelope log by ``trial_id`` and decode each matching line
    into a batch [refactor 09 §4/§5].

    The selection rule mirrors ``_scan_proxy_log`` (``base.py:249``): only lines
    whose ``trial`` equals ``trial_id`` count, so an interleaved multi-trial log
    extracts exactly by id and an unattributed ``"-"`` line never attaches to any
    trial. A line that is not a JSON object is skipped without crashing — a
    malformed envelope is the collector's operational fault, not this trial's.
    """
    batches: list[OtlpBatch] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(envelope, dict) or envelope.get("trial") != trial_id:
            continue
        batches.append(_decode_envelope(envelope))
    return OtlpCaptureRecord(trial_id=trial_id, batches=batches)


def canonical_bytes(record: OtlpCaptureRecord) -> bytes:
    """Canonical serialization — the trajectory house recipe PLUS ``allow_nan=False``
    [refactor 09 §5].

    Span payloads are float-rich, so the ``allow_nan=False`` the trajectory recipe
    omits is added here: a NaN/Infinity must fail loudly rather than emit
    non-interoperable JSON that ``verify_chain`` accepts but jq/another verifier
    rejects (the ``ledger/chain.py:46-58`` discipline). FROZEN — the returned sha
    rides the ledger hash chain (``spans_sha``).

    The dump is python-mode, not ``mode="json"``: pydantic's JSON mode silently
    coerces a non-finite float to ``null``, which would defeat ``allow_nan=False``.
    Python mode preserves it so ``json.dumps`` rejects it loudly. For this
    JSON-native schema (no datetimes/bytes/enums) the two modes are byte-identical
    on valid data, so the canonical bytes are unchanged."""
    try:
        return json.dumps(
            record.model_dump(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except ValueError as e:
        raise SpansCaptureError(
            f"otlp spans for {record.trial_id} contain non-finite floats "
            f"(NaN/Infinity), which are not interoperable JSON — refusing to persist "
            f"a span artifact independent verifiers would reject [refactor 09 §5]: {e}"
        ) from e


def spans_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def persist_spans(
    record: OtlpCaptureRecord, artifacts_dir, extra_patterns: Optional[list[str]] = None
) -> str:
    """Scrub -> re-validate -> write -> read back; return the persisted sha256.

    The FROZEN spans recipe over the shared
    :func:`~harness.run.trajectory.persist_versioned_artifact` path [refactor 09
    §5]: every string field (span attributes may name secrets/models) passes the
    EVAL-4 secret door, and a corrupt/unwritable artifact raises
    :class:`SpansCaptureError`. This is the first scrub of the double door — the
    per-trial artifact is written before the seam's whole-workspace redaction pass,
    which scrubs it again [refactor 09 §6]."""
    return persist_versioned_artifact(
        record,
        artifacts_dir,
        SPANS_FILENAME,
        canonicalize=canonical_bytes,
        model=OtlpCaptureRecord,
        error=SpansCaptureError,
        label="otlp spans",
        ac="refactor 09 §5",
        extra_patterns=extra_patterns,
    )


def parse_spans(data: bytes, *, source: str = "otlp spans artifact") -> OtlpCaptureRecord:
    """Parse span-artifact bytes; anything invalid is :class:`SpansCaptureError`."""
    try:
        return OtlpCaptureRecord.model_validate(json.loads(data.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as e:
        raise SpansCaptureError(
            f"{source} is corrupt (present but not a valid v{SPANS_SCHEMA_VERSION} "
            f"record): {e}"
        ) from e


def resolve_spans(artifacts_path, ledgered_sha) -> tuple[str, Optional[OtlpCaptureRecord]]:
    """Resolve a trial's span capture to ``(status, record-or-None)`` [refactor 09 §5].

    Mirrors :func:`~harness.run.trajectory.resolve_trajectory` exactly: the closed
    status vocabulary is ``verified`` (artifact bytes hash to the ledgered sha — the
    only status that yields a record), ``absent`` (no ledgered sha: no collector was
    configured), ``missing_artifact``, ``sha_mismatch``, and ``corrupt``. A record
    is never evidence unless its exact bytes matched the chain. Never raises —
    coverage gaps are data with a named reason; the run-path fail-closed door is
    :func:`persist_spans`, not here."""
    if ledgered_sha is None:
        return "absent", None
    if not artifacts_path:
        return "missing_artifact", None
    path = Path(artifacts_path) / SPANS_FILENAME
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return "missing_artifact", None
    except OSError:
        return "corrupt", None
    if spans_sha256(raw) != ledgered_sha:
        return "sha_mismatch", None
    try:
        return "verified", parse_spans(raw, source=f"otlp spans artifact {path}")
    except SpansCaptureError:
        return "corrupt", None
