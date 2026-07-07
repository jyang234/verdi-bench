"""Live in-trial OTLP capture — end-to-end [refactor 09 §8].

Docker-marked. Builds a tiny image running the REAL ``opentelemetry-sdk`` + the
OTLP/HTTP protobuf exporter, and proves two things live:

* the capture round trip through the managed :class:`TraceCollector` — the
  collector records a protobuf envelope attributed to the trial,
  ``decode_envelope_lines`` decodes it (via the ``verdi-bench[otlp]`` extra),
  ``persist_spans`` writes the redacted artifact, ``resolve_spans`` verifies; and
* the **NO_PROXY** pin (§8): with a metered trial's ``HTTP_PROXY`` set, capture
  still succeeds because the collector host is in ``NO_PROXY`` — the exporter's
  plaintext POST bypasses the CONNECT-only metering proxy (which would answer 405
  and drop the span) — and the collector produces **zero** lines in the proxy log.

Unlike the FROM-scratch gcc emitters elsewhere this pip-installs the SDK at build
time; if the build genuinely cannot reach a registry the tests skip honestly
(like the gcc-gated tests), never silently pass.
"""

from __future__ import annotations

import base64
import json
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

from harness.hermetic.metering import MANAGED_PROXY_NAME, MeteringProxy
from harness.hermetic.network import EGRESS_NETWORK, METERED_NETWORK
from harness.hermetic.otlp_decode import (
    SPANS_FILENAME,
    decode_envelope_lines,
    persist_spans,
    resolve_spans,
    spans_sha256,
)
from harness.hermetic.tracing import MANAGED_COLLECTOR_NAME, TraceCollector
from tests.fixtures.docker import DOCKER_AVAILABLE

pytestmark = pytest.mark.docker

_TRIAL = "otlp-e2e-trial"
_EMITTER = "verdi-otlp-e2e-emitter"
_IMAGE = "verdi-bench/otlp-emitter-e2e:latest"

# A real OTel program: emit one span through the OTLP/HTTP protobuf exporter, which
# reads OTEL_EXPORTER_OTLP_ENDPOINT + OTEL_EXPORTER_OTLP_HEADERS from the env (exactly
# what harbor injects). SimpleSpanProcessor exports synchronously on span end;
# requests (the exporter's transport) honors HTTP_PROXY/NO_PROXY from the env.
_EMITTER_SRC = textwrap.dedent(
    """
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    provider = TracerProvider(resource=Resource.create({"service.name": "verdi-e2e"}))
    provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    with trace.get_tracer("verdi-e2e").start_as_current_span("verdi-e2e-span") as span:
        span.set_attribute("verdi.e2e.marker", "captured")
    provider.force_flush()
    provider.shutdown()
    print("emitted", flush=True)
    """
)

_DOCKERFILE = textwrap.dedent(
    """
    FROM python:3.12-slim
    RUN pip install --no-cache-dir \
        opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
    COPY emit.py /emit.py
    CMD ["python", "/emit.py"]
    """
)


def _rm(*names: str) -> None:
    for n in names:
        subprocess.run(["docker", "rm", "-f", n], capture_output=True)


@pytest.fixture(scope="module")
def emitter_image(tmp_path_factory):
    """Build the OTLP emitter image once, or skip honestly if pip can't reach a
    registry from the docker build."""
    ctx = tmp_path_factory.mktemp("otlp-img")
    (ctx / "emit.py").write_text(_EMITTER_SRC, encoding="utf-8")
    (ctx / "Dockerfile").write_text(_DOCKERFILE, encoding="utf-8")
    build = subprocess.run(
        ["docker", "build", "-t", _IMAGE, str(ctx)], capture_output=True, text=True
    )
    if build.returncode != 0:
        pytest.skip(
            "could not build the OTLP emitter image (pip could not reach a registry "
            f"from the docker build): {build.stderr.strip()[-400:]}"
        )
    return _IMAGE


def _await_trial_lines(log_path: str) -> list[dict]:
    """Poll the envelope log for this trial's lines (the export is synchronous, but
    give the collector's write a beat)."""
    for _ in range(50):
        p = Path(log_path)
        if p.exists():
            records = [
                json.loads(ln)
                for ln in p.read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
            if any(r.get("trial") == _TRIAL for r in records):
                return records
        time.sleep(0.1)
    return []


@pytest.mark.skipif(not DOCKER_AVAILABLE, reason="no docker daemon available")
def test_otlp_capture_round_trips_through_the_managed_collector(emitter_image, tmp_path):
    log = tmp_path / "otlp" / "otlp.jsonl"
    _rm(_EMITTER, MANAGED_COLLECTOR_NAME)
    try:
        # keep_raw so the assertions can read the envelope log; the D-09-1 default
        # delete-on-teardown is unit-tested in test_otlp_tracing.py.
        with TraceCollector.managed(log_path=log, keep_raw=True) as cfg:
            assert cfg.endpoint == f"http://{MANAGED_COLLECTOR_NAME}:4318"
            proc = subprocess.run(
                [
                    "docker", "run", "--rm", "--name", _EMITTER,
                    "--network", METERED_NETWORK,
                    "--env", f"OTEL_EXPORTER_OTLP_ENDPOINT={cfg.endpoint}",
                    "--env", f"OTEL_EXPORTER_OTLP_HEADERS=x-verdi-trial={_TRIAL}",
                    emitter_image,
                ],
                capture_output=True, text=True, timeout=120,
            )
            assert proc.returncode == 0, f"emitter failed: {proc.stderr}"

            records = _await_trial_lines(cfg.log_path)
            mine = [r for r in records if r.get("trial") == _TRIAL]
            assert mine, f"no envelope attributed to {_TRIAL}: {records}"
            # the unmodified SDK exports protobuf by default → body_b64, never parsed
            assert all("body_b64" in r for r in mine), mine
            assert all(base64.b64decode(r["body_b64"]) for r in mine)

            record = decode_envelope_lines(
                Path(cfg.log_path).read_text(encoding="utf-8").splitlines(), _TRIAL
            )
            assert record.batches, "decoded record has no span batches"
            marker = json.dumps(record.model_dump())
            assert "verdi-e2e-span" in marker and "verdi.e2e.marker" in marker

            sha = persist_spans(record, tmp_path)
            artifact = tmp_path / SPANS_FILENAME
            assert sha == spans_sha256(artifact.read_bytes())
            status, resolved = resolve_spans(tmp_path, sha)
            assert status == "verified"
            assert resolved.batches[0].resource_spans  # OTLP resourceSpans passed through
    finally:
        _rm(_EMITTER, MANAGED_COLLECTOR_NAME)
        subprocess.run(["docker", "network", "rm", METERED_NETWORK], capture_output=True)


@pytest.mark.skipif(not DOCKER_AVAILABLE, reason="no docker daemon available")
def test_no_proxy_lets_the_collector_bypass_the_metering_proxy(emitter_image, tmp_path):
    """§8 NO_PROXY pin, LIVE: a metered trial has HTTP_PROXY set to the CONNECT-only
    metering proxy, whose allowlist does NOT include the collector. Capture still
    succeeds because NO_PROXY makes the exporter's plaintext POST bypass the proxy —
    without it the POST would hit the proxy, be answered 405, and the span dropped —
    and the collector produces ZERO lines in the proxy log."""
    proxy_log = tmp_path / "metering" / "verdi.jsonl"
    otlp_log = tmp_path / "otlp" / "otlp.jsonl"
    _rm(_EMITTER, MANAGED_COLLECTOR_NAME, MANAGED_PROXY_NAME)
    try:
        # allowlist deliberately EXCLUDES the collector: if a span post ever routed
        # through the proxy it would be refused, so a success proves the bypass.
        with MeteringProxy.managed(["api.anthropic.com"], log_path=proxy_log), \
                TraceCollector.managed(log_path=otlp_log, keep_raw=True) as cfg:
            proxy_url = f"http://{_TRIAL}@{MANAGED_PROXY_NAME}:3128"
            proc = subprocess.run(
                [
                    "docker", "run", "--rm", "--name", _EMITTER,
                    "--network", METERED_NETWORK,
                    # the metered-trial egress env harbor injects...
                    "--env", f"HTTP_PROXY={proxy_url}",
                    "--env", f"HTTPS_PROXY={proxy_url}",
                    # ...plus the OTLP env, with the collector pinned OUT of the proxy
                    "--env", f"OTEL_EXPORTER_OTLP_ENDPOINT={cfg.endpoint}",
                    "--env", f"OTEL_EXPORTER_OTLP_HEADERS=x-verdi-trial={_TRIAL}",
                    "--env", f"NO_PROXY={MANAGED_COLLECTOR_NAME}",
                    emitter_image,
                ],
                capture_output=True, text=True, timeout=120,
            )
            assert proc.returncode == 0, f"emitter failed: {proc.stderr}"

            # capture SUCCEEDED despite HTTP_PROXY being set → NO_PROXY was honored
            records = _await_trial_lines(cfg.log_path)
            mine = [r for r in records if r.get("trial") == _TRIAL]
            assert mine, f"NO_PROXY bypass failed — no spans captured: {records}"

            # ...and the collector traffic never touched the metering proxy
            proxy_lines = [
                json.loads(ln)
                for ln in Path(proxy_log).read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
            collector_lines = [
                r for r in proxy_lines
                if MANAGED_COLLECTOR_NAME in str(r.get("host", ""))
            ]
            assert collector_lines == [], (
                f"collector traffic polluted the proxy log: {collector_lines}"
            )
    finally:
        _rm(_EMITTER, MANAGED_COLLECTOR_NAME, MANAGED_PROXY_NAME)
        for net in (EGRESS_NETWORK, METERED_NETWORK):
            subprocess.run(["docker", "network", "rm", net], capture_output=True)
