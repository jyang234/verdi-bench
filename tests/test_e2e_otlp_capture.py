"""Live in-trial OTLP capture — end-to-end [refactor 09 §8].

Docker-marked. Builds a tiny image running the REAL ``opentelemetry-sdk`` + the
OTLP/HTTP protobuf exporter, stands the managed :class:`TraceCollector` up, runs
the emitter on the metered network with the OTel env the engine would inject
(endpoint + ``x-verdi-trial`` header), and proves the round trip: the collector
records a protobuf envelope attributed to the trial, ``decode_envelope_lines``
decodes it (via the ``verdi-bench[otlp]`` extra), ``persist_spans`` writes the
redacted artifact, and ``resolve_spans`` returns ``verified``.

Unlike the FROM-scratch gcc emitters elsewhere this pip-installs the SDK at build
time; if the build genuinely cannot reach a registry the test skips honestly
(like the gcc-gated tests), never silently passes.
"""

from __future__ import annotations

import base64
import json
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

from harness.hermetic.network import METERED_NETWORK
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
# what harbor injects). SimpleSpanProcessor exports synchronously on span end.
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


@pytest.mark.skipif(not DOCKER_AVAILABLE, reason="no docker daemon available")
def test_otlp_capture_round_trips_through_the_managed_collector(tmp_path):
    ctx = tmp_path / "img"
    ctx.mkdir()
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
                    _IMAGE,
                ],
                capture_output=True, text=True, timeout=120,
            )
            assert proc.returncode == 0, f"emitter failed: {proc.stderr}"

            # the export is synchronous, but give the collector's write a moment
            records = []
            for _ in range(50):
                if Path(cfg.log_path).exists():
                    records = [
                        json.loads(ln)
                        for ln in Path(cfg.log_path).read_text(encoding="utf-8").splitlines()
                        if ln.strip()
                    ]
                    if any(r.get("trial") == _TRIAL for r in records):
                        break
                time.sleep(0.1)

            mine = [r for r in records if r.get("trial") == _TRIAL]
            assert mine, f"no envelope attributed to {_TRIAL}: {records}"
            # the unmodified SDK exports protobuf by default → body_b64, never parsed
            assert all("body_b64" in r for r in mine), mine
            assert all(base64.b64decode(r["body_b64"]) for r in mine)

            # decode this trial's slice (needs the verdi-bench[otlp] extra) + persist
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
