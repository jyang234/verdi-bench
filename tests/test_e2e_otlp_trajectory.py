"""Live OTLP span → trajectory — end-to-end, closing the loop with spec 09
[refactor 10 §6.6].

Docker-marked. Extends spec 09's capture round trip: a REAL ``opentelemetry-sdk``
program emits a ``gen_ai.*`` chat span through the OTLP/HTTP protobuf exporter to
the managed :class:`TraceCollector`; the envelope is decoded (via the
``verdi-bench[otlp]`` extra), ``persist_spans`` writes the redacted artifact, the
registered ``otlp`` adapter projects that on-disk artifact into a trajectory,
``persist_trajectory`` writes it, and ``resolve_trajectory`` returns ``verified`` —
the whole spec 09 → spec 10 chain over live bytes, and the drift guard for the
``_SpanCapture`` wrapper mirror (a real ``persist_spans`` artifact must re-validate).

Builds the emitter image at test time; if the build cannot reach a registry the
test skips honestly, never silently passes (the sibling test's discipline).
"""

from __future__ import annotations

import json
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

from harness.adapters.otlp import OtlpAdapter
from harness.hermetic.network import METERED_NETWORK
from harness.hermetic.otlp_decode import SPANS_FILENAME, decode_envelope_lines, persist_spans
from harness.hermetic.tracing import MANAGED_COLLECTOR_NAME, TraceCollector
from harness.run.trajectory import TrajectoryRecord, persist_trajectory, resolve_trajectory
from tests.fixtures.docker import DOCKER_AVAILABLE

pytestmark = pytest.mark.docker

_TRIAL = "otlp-traj-e2e-trial"
_EMITTER = "verdi-otlp-traj-e2e-emitter"
_IMAGE = "verdi-bench/otlp-traj-emitter-e2e:latest"

# A real OTel program emitting a gen_ai chat span: the model id (dropped by the
# whitelist) rides gen_ai.request.model; the usage + completion cross into the
# message step. The unmodified SDK exports OTLP/HTTP protobuf by default.
_EMITTER_SRC = textwrap.dedent(
    """
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    provider = TracerProvider(resource=Resource.create({"service.name": "verdi-e2e-agent"}))
    provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    with trace.get_tracer("verdi-e2e").start_as_current_span("chat gpt-4o") as span:
        span.set_attribute("gen_ai.operation.name", "chat")
        span.set_attribute("gen_ai.request.model", "gpt-4o-2024-08-06")
        span.set_attribute("gen_ai.usage.input_tokens", 12)
        span.set_attribute("gen_ai.usage.output_tokens", 5)
        span.set_attribute("gen_ai.content.completion", "hello from otel")
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
    ctx = tmp_path_factory.mktemp("otlp-traj-img")
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
    for _ in range(50):
        p = Path(log_path)
        if p.exists():
            records = [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if any(r.get("trial") == _TRIAL for r in records):
                return records
        time.sleep(0.1)
    return []


@pytest.mark.skipif(not DOCKER_AVAILABLE, reason="no docker daemon available")
def test_live_spans_normalize_to_a_verified_trajectory(emitter_image, tmp_path):
    """real OTel SDK → collector → decode → persist_spans → normalize →
    persist_trajectory → resolve_trajectory == verified."""
    log = tmp_path / "otlp" / "otlp.jsonl"
    _rm(_EMITTER, MANAGED_COLLECTOR_NAME)
    try:
        with TraceCollector.managed(log_path=log, keep_raw=True) as cfg:
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
            assert any(r.get("trial") == _TRIAL for r in records), records

            # spec 09: decode + persist the redacted span artifact
            capture = decode_envelope_lines(
                Path(cfg.log_path).read_text(encoding="utf-8").splitlines(), _TRIAL
            )
            assert capture.batches, "decoded record has no span batches"
            artifacts = tmp_path / "artifacts"
            artifacts.mkdir()
            spans_sha = persist_spans(capture, artifacts)

            # spec 10: the registered adapter projects the ON-DISK artifact (the
            # dual-source invariant) — read it back exactly as the run seam would
            on_disk = json.loads((artifacts / SPANS_FILENAME).read_text(encoding="utf-8"))
            steps = OtlpAdapter().normalize_trajectory(on_disk)
            assert steps is not None, "live gen_ai span produced no trajectory step"
            assert steps[0].kind == "message"
            assert steps[0].tokens == 17  # 12 input + 5 output
            assert steps[0].detail == "hello from otel"
            # the model id rode a non-whitelisted attribute → dropped
            record = TrajectoryRecord(trial_id=_TRIAL, platform="otlp", steps=steps)
            traj_sha = persist_trajectory(record, artifacts)
            assert b"gpt-4o" not in (artifacts / "trajectory.json").read_bytes()

            status, resolved = resolve_trajectory(artifacts, traj_sha)
            assert status == "verified"
            assert [s.kind for s in resolved.steps] == ["message"]
            assert spans_sha and traj_sha  # both artifacts are chain-bound
    finally:
        _rm(_EMITTER, MANAGED_COLLECTOR_NAME)
        subprocess.run(["docker", "network", "rm", METERED_NETWORK], capture_output=True)
