"""Envelope decode, the span artifact, and resolve_spans [refactor 09 §5, §8].

The JSON + protobuf decode paths, trial-id filtering (interleaved multi-trial +
"-" exclusion), the canonical-bytes determinism / allow_nan=False, persistence
through the shared door, resolve_spans' closed status vocabulary, and the
protobuf-without-extra loud failure (A14).
"""

from __future__ import annotations

import base64
import json
import sys

import pytest

from harness.hermetic.otlp_decode import (
    SPANS_FILENAME,
    SPANS_SCHEMA_VERSION,
    OtlpCaptureRecord,
    OtlpDependencyError,
    SpansCaptureError,
    canonical_bytes,
    decode_envelope_lines,
    persist_spans,
    resolve_spans,
    spans_sha256,
)


def _json_env(trial, seq, body):
    return json.dumps(
        {"trial": trial, "seq": seq, "content_type": "application/json", "body_json": body}
    )


def _proto_body(service_name="svc", span_name="op") -> bytes:
    """Encode a real OTLP ExportTraceServiceRequest — a genuine protobuf body the
    decoder must handle (fixture encoding with the real lib, decode under test)."""
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )
    from opentelemetry.proto.common.v1.common_pb2 import AnyValue

    req = ExportTraceServiceRequest()
    rs = req.resource_spans.add()
    rs.resource.attributes.add(key="service.name", value=AnyValue(string_value=service_name))
    ss = rs.scope_spans.add()
    ss.spans.add(name=span_name)
    return req.SerializeToString()


# --- JSON decode -------------------------------------------------------------
def test_json_envelope_decodes_resource_spans_passthrough():
    body = {"resourceSpans": [{"scopeSpans": [{"spans": [{"name": "op"}]}]}]}
    rec = decode_envelope_lines([_json_env("t", 0, body)], "t")
    assert rec.schema_version == SPANS_SCHEMA_VERSION == 1
    assert rec.trial_id == "t"
    (batch,) = rec.batches
    assert batch.content_type == "application/json"
    # resource_spans passes through intact (evidence, not our schema)
    assert batch.resource_spans == [{"scopeSpans": [{"spans": [{"name": "op"}]}]}]


def test_multiple_json_envelopes_become_multiple_batches():
    lines = [
        _json_env("t", 0, {"resourceSpans": [{"a": 1}]}),
        _json_env("t", 1, {"resourceSpans": [{"b": 2}]}),
    ]
    rec = decode_envelope_lines(lines, "t")
    assert [b.resource_spans for b in rec.batches] == [[{"a": 1}], [{"b": 2}]]


# --- trial filtering (§8) ----------------------------------------------------
def test_interleaved_multi_trial_extracts_exactly_by_id_and_excludes_dash():
    lines = [
        _json_env("t-A", 0, {"resourceSpans": [{"who": "A"}]}),
        _json_env("t-B", 1, {"resourceSpans": [{"who": "B"}]}),
        _json_env("-", 2, {"resourceSpans": [{"who": "unattributed"}]}),
        _json_env("t-A", 3, {"resourceSpans": [{"who": "A2"}]}),
        "not json at all",  # a malformed collector line is skipped, never crashes
    ]
    rec_a = decode_envelope_lines(lines, "t-A")
    assert [b.resource_spans for b in rec_a.batches] == [[{"who": "A"}], [{"who": "A2"}]]
    rec_b = decode_envelope_lines(lines, "t-B")
    assert [b.resource_spans for b in rec_b.batches] == [[{"who": "B"}]]
    # the "-" line attaches to no trial
    assert decode_envelope_lines(lines, "-").batches  # it is selectable AS "-"
    assert all("unattributed" not in json.dumps(b.resource_spans) for b in rec_a.batches)


def test_zero_matching_lines_is_empty_batches():
    rec = decode_envelope_lines([_json_env("other", 0, {"resourceSpans": []})], "mine")
    assert rec.batches == []


# --- protobuf decode ---------------------------------------------------------
def test_protobuf_envelope_decodes_via_opentelemetry_proto():
    env = json.dumps({
        "trial": "t", "seq": 0, "content_type": "application/x-protobuf",
        "body_b64": base64.b64encode(_proto_body("my-svc", "handle")).decode("ascii"),
    })
    rec = decode_envelope_lines([env], "t")
    (batch,) = rec.batches
    assert batch.content_type == "application/x-protobuf"
    # protobuf → camelCase OTLP-JSON, resource_spans passed through
    rs = batch.resource_spans[0]
    assert rs["scopeSpans"][0]["spans"][0]["name"] == "handle"
    attrs = rs["resource"]["attributes"]
    assert {"key": "service.name", "value": {"stringValue": "my-svc"}} in attrs


def test_protobuf_without_extra_raises_actionable(monkeypatch):
    """A14: a protobuf envelope decoded without the verdi-bench[otlp] extra fails
    loud with an actionable message — configured capture never silently degrades."""
    for name in list(sys.modules):
        if name == "opentelemetry" or name.startswith("opentelemetry."):
            monkeypatch.setitem(sys.modules, name, None)
    env = json.dumps({
        "trial": "t", "seq": 0, "content_type": "application/x-protobuf",
        "body_b64": base64.b64encode(b"\x00\x01").decode("ascii"),
    })
    with pytest.raises(OtlpDependencyError, match=r"verdi-bench\[otlp\]"):
        decode_envelope_lines([env], "t")


# --- canonical bytes + allow_nan=False (§8) ----------------------------------
def test_canonical_bytes_are_deterministic_and_sorted():
    rec = OtlpCaptureRecord(
        trial_id="t",
        batches=[{"content_type": "application/json", "resource_spans": [{"z": 1, "a": 2}]}],
    )
    a = canonical_bytes(rec)
    b = canonical_bytes(rec)
    assert a == b  # deterministic
    assert b'"a":2' in a and a.index(b'"a"') < a.index(b'"z"')  # sort_keys


def test_nan_span_float_fails_loud():
    """§8: a NaN in a span float is not interoperable JSON — persistence fails loud
    (allow_nan=False), never emits bytes an independent verifier would reject."""
    rec = OtlpCaptureRecord(
        trial_id="t",
        batches=[{"content_type": "application/json", "resource_spans": [{"v": float("nan")}]}],
    )
    with pytest.raises(SpansCaptureError, match="non-finite"):
        canonical_bytes(rec)


# --- persistence + resolve_spans ---------------------------------------------
def _record(trial="t"):
    return OtlpCaptureRecord(
        trial_id=trial,
        batches=[{"content_type": "application/json", "resource_spans": [{"name": "op"}]}],
    )


def test_persist_then_resolve_verified(tmp_path):
    sha = persist_spans(_record(), tmp_path)
    artifact = tmp_path / SPANS_FILENAME
    assert artifact.exists()
    assert sha == spans_sha256(artifact.read_bytes())
    status, record = resolve_spans(tmp_path, sha)
    assert status == "verified"
    assert record.batches[0].resource_spans == [{"name": "op"}]


def test_resolve_spans_closed_status_vocabulary(tmp_path):
    sha = persist_spans(_record(), tmp_path)
    assert resolve_spans(tmp_path, None) == ("absent", None)  # no collector configured
    assert resolve_spans(None, sha)[0] == "missing_artifact"
    assert resolve_spans(tmp_path / "nope", sha)[0] == "missing_artifact"
    assert resolve_spans(tmp_path, "0" * 64)[0] == "sha_mismatch"
    (tmp_path / SPANS_FILENAME).write_bytes(b"{not json")
    assert resolve_spans(tmp_path, sha)[0] == "sha_mismatch"  # bytes no longer hash


def test_persist_scrubs_secrets(tmp_path):
    """Persistence runs the record through the EVAL-4 secret door — a provider-key
    literal in a span attribute is scrubbed (extra_patterns) before the sha."""
    rec = OtlpCaptureRecord(
        trial_id="t",
        batches=[{"content_type": "application/json",
                  "resource_spans": [{"attr": "sk-secret-value-123"}]}],
    )
    persist_spans(rec, tmp_path, ["sk-secret-value-123"])
    text = (tmp_path / SPANS_FILENAME).read_text(encoding="utf-8")
    assert "sk-secret-value-123" not in text


def test_wrapper_forbids_extra_keys():
    with pytest.raises(Exception):
        OtlpCaptureRecord(trial_id="t", batches=[], surprise="x")
