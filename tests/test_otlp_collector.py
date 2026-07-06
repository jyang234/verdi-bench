"""The stdlib OTLP collector container against a live socket [refactor 09 §2, §8].

Drives ``harness.hermetic._collector_container`` on an ephemeral port (the
container is a mounted script, never imported by the harness at runtime — a test
importing it is fine) and asserts the envelope framing, gzip handling, the
400-on-unattributed contract, ``COLLECTOR_LOG`` basename honoring, and the
determinism property (same request sequence → byte-identical log, seq-only).
"""

from __future__ import annotations

import base64
import gzip
import http.client
import json
import socket
import threading
from pathlib import Path

import pytest

from harness.hermetic import _collector_container as coll


@pytest.fixture()
def collector(tmp_path, monkeypatch):
    """A live collector on an ephemeral port, logging to a tmp file, seq reset."""
    log = tmp_path / "otlp.jsonl"
    monkeypatch.setattr(coll, "LOG", str(log))
    coll._seq = 0
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(16)
    port = srv.getsockname()[1]
    threading.Thread(target=coll._serve, args=(srv,), daemon=True).start()
    yield port, log


def _post(port, body, headers, path="/v1/traces", method="POST"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    resp.read()
    conn.close()
    return resp.status


def _lines(log: Path) -> list[dict]:
    return [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_json_body_inlined_as_body_json(collector):
    port, log = collector
    payload = json.dumps({"resourceSpans": [{"scopeSpans": [{"spans": [{"name": "op"}]}]}]})
    status = _post(port, payload, {"Content-Type": "application/json", "x-verdi-trial": "trial-1"})
    assert status == 200
    (rec,) = _lines(log)
    assert rec == {
        "trial": "trial-1",
        "seq": 0,
        "content_type": "application/json",
        "body_json": {"resourceSpans": [{"scopeSpans": [{"spans": [{"name": "op"}]}]}]},
    }


def test_protobuf_body_base64_in_body_b64(collector):
    port, log = collector
    body = b"\x0a\x05hello\x10\x2a"  # arbitrary bytes — the collector never parses them
    status = _post(
        port, body, {"Content-Type": "application/x-protobuf", "x-verdi-trial": "trial-2"}
    )
    assert status == 200
    (rec,) = _lines(log)
    assert rec["trial"] == "trial-2"
    assert rec["content_type"] == "application/x-protobuf"
    assert base64.b64decode(rec["body_b64"]) == body
    assert "body_json" not in rec  # protobuf is never parsed


def test_gzip_content_encoding_is_decompressed(collector):
    port, log = collector
    payload = {"resourceSpans": [{"k": "v"}]}
    gz = gzip.compress(json.dumps(payload).encode("utf-8"))
    status = _post(
        port,
        gz,
        {
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
            "x-verdi-trial": "trial-3",
        },
    )
    assert status == 200
    (rec,) = _lines(log)
    assert rec["body_json"] == payload  # stored decompressed, not the gzip bytes


def test_missing_trial_header_records_dash_and_answers_400(collector):
    port, log = collector
    payload = json.dumps({"resourceSpans": []})
    status = _post(port, payload, {"Content-Type": "application/json"})  # no x-verdi-trial
    assert status == 400  # fail loud to the client
    (rec,) = _lines(log)  # ...but recorded, so operators can count it
    assert rec["trial"] == "-"
    assert rec["body_json"] == {"resourceSpans": []}


def test_seq_is_the_only_ordering_key_no_wall_clock(collector):
    port, log = collector
    for i in range(3):
        _post(port, json.dumps({"i": i}), {"Content-Type": "application/json", "x-verdi-trial": "t"})
    recs = _lines(log)
    assert [r["seq"] for r in recs] == [0, 1, 2]
    # no timestamp of any kind in the envelope — seq is the sole ordering signal
    for r in recs:
        assert set(r) == {"trial", "seq", "content_type", "body_json"}


def test_determinism_same_sequence_byte_identical(collector, tmp_path, monkeypatch):
    """§8: same request sequence → byte-identical log (no timestamps, seq-only)."""
    port, log = collector
    bodies = [json.dumps({"n": n}) for n in ("α", "β", "γ")]
    hdr = {"Content-Type": "application/json", "x-verdi-trial": "t"}
    for b in bodies:
        _post(port, b, hdr)
    first = log.read_bytes()

    # Replay the identical sequence from a reset counter into a fresh log.
    log.write_bytes(b"")
    coll._seq = 0
    for b in bodies:
        _post(port, b, hdr)
    assert log.read_bytes() == first


def test_wrong_path_and_method_are_404(collector):
    port, log = collector
    # /v1/logs and /v1/metrics are 404 in v1; a GET is 404.
    assert _post(port, b"", {"Content-Type": "application/json"}, path="/v1/logs") == 404
    assert _post(port, b"", {"Content-Type": "application/json"}, path="/v1/metrics") == 404
    assert _post(port, b"", {}, path="/v1/traces", method="GET") == 404
    assert not log.exists() or _lines(log) == []  # nothing recorded for a rejected route


def test_unsupported_content_type_is_415(collector):
    port, log = collector
    status = _post(port, b"plain", {"Content-Type": "text/plain", "x-verdi-trial": "t"})
    assert status == 415
    assert not log.exists() or _lines(log) == []


def test_custom_collector_log_basename_is_honored(tmp_path, monkeypatch):
    """The 988af58 basename lesson at the container tier: the collector writes to
    exactly the COLLECTOR_LOG the host injected, never a hardcoded default."""
    custom = tmp_path / "mounted" / "custom-otlp.jsonl"
    custom.parent.mkdir(parents=True)
    monkeypatch.setattr(coll, "LOG", str(custom))
    coll._seq = 0
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(16)
    port = srv.getsockname()[1]
    threading.Thread(target=coll._serve, args=(srv,), daemon=True).start()
    _post(port, json.dumps({"resourceSpans": []}), {"Content-Type": "application/json", "x-verdi-trial": "t"})
    assert custom.exists(), "collector did not honor the custom COLLECTOR_LOG basename"
    assert _lines(custom)[0]["trial"] == "t"
