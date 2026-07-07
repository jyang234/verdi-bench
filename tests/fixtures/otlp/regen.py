"""Regenerate the OTLP normalization golden pairs [refactor 10 §4/§6.1].

The committed fixtures under ``tests/fixtures/otlp/`` are the **normative mapping
spec**: hand-built ``*.spans.json`` OTLP-JSON exports (the redacted
``otlp_spans.json`` wrapper an OTLP arm produces) paired with the byte-exact
``*.trajectory.json`` / ``*.flight_recorder.json`` the :class:`OtlpAdapter`
projects them into. ``test_eval_otlp_goldens.py`` asserts the adapter reproduces
these bytes; a mapping change breaks a golden and forces an ``OTLP_MAPPING_VERSION``
bump + a reviewed regen here — the [refactor 01] golden discipline applied to the
projection.

Run ``uv run python -m tests.fixtures.otlp.regen`` to rewrite the goldens after a
DELIBERATE, reviewed mapping change. The fixtures below are Python for legibility;
they render to the committed JSON. Determinism: every timestamp is a fixed offset
from a pinned epoch — no wall clock.
"""

from __future__ import annotations

import json
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent
_EPOCH_NS = 1_700_000_000_000_000_000  # pinned; the harness contributes no clock


# --- OTLP-JSON (proto3 JSON, camelCase, AnyValue-wrapped) builders ----------
def sv(s: str) -> dict:
    return {"stringValue": s}


def iv(n: int) -> dict:
    return {"intValue": str(n)}  # proto3 JSON encodes int64 as a string


def dv(x: float) -> dict:
    return {"doubleValue": x}


def bv(b: bool) -> dict:
    return {"boolValue": b}


def av(*vals: dict) -> dict:
    return {"arrayValue": {"values": list(vals)}}


def attr(key: str, value: dict) -> dict:
    return {"key": key, "value": value}


def span(span_id, offset_s, attrs, *, name="span", parent=None, events=None) -> dict:
    s = {
        "name": name,
        "spanId": span_id,
        "startTimeUnixNano": str(_EPOCH_NS + int(offset_s * 1e9)),
        "endTimeUnixNano": str(_EPOCH_NS + int(offset_s * 1e9) + 1_000_000),
        "attributes": [attr(k, v) for k, v in attrs],
    }
    if parent is not None:
        s["parentSpanId"] = parent
    if events is not None:
        s["events"] = events
    return s


def event(name: str, content: str) -> dict:
    return {"name": name, "attributes": [attr("content", sv(content))]}


def capture(trial_id: str, spans: list, *, resource=None) -> dict:
    """One-batch ``otlp_spans.json`` wrapper around a scopeSpans span list."""
    rs = {"scopeSpans": [{"scope": {"name": "verdi.fixture"}, "spans": spans}]}
    if resource is not None:
        rs["resource"] = {"attributes": [attr(k, v) for k, v in resource]}
    return {
        "schema_version": 1,
        "trial_id": trial_id,
        "batches": [{"content_type": "application/json", "resource_spans": [rs]}],
    }


# --- the fixture corpus ------------------------------------------------------
# Each entry pins specific §2 rules; the DROPPED comments name identity that must
# NOT survive the whitelist projection.
def _langchain() -> dict:
    """LangChain/LangSmith-style: a chat LLM span + a tool span + an infra HTTP span
    (dropped by selection). Pins message tokens/detail, tool_call, selection."""
    return capture(
        "fixture-langchain",
        [
            span(
                "a1", 0.0, name="ChatOpenAI",
                attrs=[
                    ("gen_ai.operation.name", sv("chat")),
                    ("gen_ai.request.model", sv("gpt-4o-2024-08-06")),  # DROPPED
                    ("gen_ai.system", sv("openai")),                    # DROPPED
                    ("gen_ai.usage.input_tokens", iv(1200)),
                    ("gen_ai.usage.output_tokens", iv(340)),
                    ("gen_ai.content.completion", sv("The sum is 42.")),
                ],
            ),
            span(
                "a2", 0.5, name="tool:duckduckgo_search", parent="a1",
                attrs=[
                    ("gen_ai.operation.name", sv("execute_tool")),
                    ("gen_ai.tool.name", sv("duckduckgo_search")),
                    ("gen_ai.tool.arguments", sv('{"query": "capital of france"}')),
                ],
            ),
            span(
                "a3", 0.6, name="HTTP POST",  # infra span: no gen_ai.*/verdi.* → DROPPED
                attrs=[("http.method", sv("POST")), ("http.url", sv("https://api.openai.com/v1"))],
            ),
        ],
    )


def _pydantic_ai() -> dict:
    """pydantic-ai-style: agent run with two model requests + a tool, spans emitted
    OUT of start order in the file. Pins ordering + partial-usage honesty (a span
    reporting only output_tokens → null tokens, the absent half never imputed)."""
    return capture(
        "fixture-pydantic-ai",
        [
            span(
                "b3", 1.0, name="chat", parent="b1",
                attrs=[
                    ("gen_ai.operation.name", sv("chat")),
                    ("gen_ai.usage.output_tokens", iv(60)),  # only output → null (no imputation)
                ],
            ),
            span(
                "b1", 0.0, name="chat",
                attrs=[
                    ("gen_ai.operation.name", sv("chat")),
                    ("gen_ai.request.model", sv("claude-3-5-sonnet-20241022")),  # DROPPED
                    ("gen_ai.usage.input_tokens", iv(800)),
                    ("gen_ai.usage.output_tokens", iv(120)),
                ],
            ),
            span(
                "b2", 0.25, name="running tool: get_weather", parent="b1",
                attrs=[
                    ("gen_ai.tool.name", sv("get_weather")),
                    ("gen_ai.tool.arguments", sv('{"city": "Paris"}')),
                ],
            ),
        ],
    )


def _multi_agent() -> dict:
    """Multi-agent trace with verdi.agent labels (worker-1, critic-2). Pins agent
    attribution, cost, exit_code, command."""
    return capture(
        "fixture-multi-agent",
        [
            span(
                "c1", 0.0, name="orchestrator",
                attrs=[
                    ("gen_ai.operation.name", sv("chat")),
                    ("verdi.agent", sv("worker-1")),
                    ("gen_ai.usage.input_tokens", iv(500)),
                    ("gen_ai.usage.output_tokens", iv(100)),
                    ("verdi.cost_usd", dv(0.012)),
                ],
            ),
            span(
                "c2", 0.5, name="critic",
                attrs=[
                    ("gen_ai.operation.name", sv("chat")),
                    ("verdi.agent", sv("critic-2")),
                    ("gen_ai.usage.input_tokens", iv(300)),
                    ("gen_ai.usage.output_tokens", iv(80)),
                    ("verdi.cost_usd", dv(0.008)),
                ],
            ),
            span(
                "c3", 1.0, name="worker-exec", parent="c1",
                attrs=[
                    ("gen_ai.tool.name", sv("bash")),
                    ("verdi.agent", sv("worker-1")),
                    ("verdi.command", sv("ls -la")),
                    ("verdi.exit_code", iv(0)),
                ],
            ),
        ],
    )


def _reasoning() -> dict:
    """Reasoning-bearing trace exercising turn ancestor linkage: reasoning via the
    gen_ai.content.reasoning ATTRIBUTE and via a verdi.reasoning EVENT, plus a
    root reasoning span with no selected ancestor (turn=None)."""
    return capture(
        "fixture-reasoning",
        [
            span(
                "d1", 0.0, name="planner",
                attrs=[
                    ("gen_ai.operation.name", sv("chat")),
                    ("gen_ai.usage.input_tokens", iv(200)),
                    ("gen_ai.usage.output_tokens", iv(50)),
                ],
            ),
            span(
                "d2", 0.1, name="planner-reasoning", parent="d1",
                attrs=[("gen_ai.content.reasoning", sv("Search for X first, then compute."))],
            ),
            span(
                "d3", 0.5, name="worker", parent="d1",
                attrs=[
                    ("gen_ai.operation.name", sv("chat")),
                    ("verdi.agent", sv("worker-2")),
                    ("gen_ai.usage.input_tokens", iv(300)),
                    ("gen_ai.usage.output_tokens", iv(90)),
                ],
            ),
            span(
                "d4", 0.6, name="worker-reasoning", parent="d3",
                attrs=[("verdi.agent", sv("worker-2"))],
                events=[event("verdi.reasoning", "Let me verify the edge case.")],
            ),
            span(
                "d5", 2.0, name="unlinked-reasoning",
                attrs=[("gen_ai.content.reasoning", sv("A final unlinked thought."))],
            ),
        ],
    )


def _file_edit() -> dict:
    """D-10-2 exerciser: the accepted file-edit tool set, files-path parsing
    (verdi.files + tool-argument file_path/path), and test_run classification
    (verdi.test_run, pytest solo runner, `go test` subcommand) vs tool_call."""
    return capture(
        "fixture-file-edit",
        [
            span(
                "e1", 0.0, name="Edit",
                attrs=[
                    ("gen_ai.tool.name", sv("Edit")),
                    ("gen_ai.tool.arguments", sv('{"file_path": "/ws/main.py", "old_string": "a", "new_string": "b"}')),
                ],
            ),
            span(
                "e2", 0.1, name="write_file",
                attrs=[
                    ("gen_ai.tool.name", sv("write_file")),
                    ("verdi.files", av(sv("/ws/out.txt"))),
                ],
            ),
            span(
                "e3", 0.2, name="str_replace_editor",
                attrs=[
                    ("gen_ai.tool.name", sv("str_replace_editor")),
                    ("gen_ai.tool.arguments", sv('{"path": "/ws/x.py"}')),
                ],
            ),
            span(
                "e4", 0.3, name="pytest",
                attrs=[
                    ("verdi.test_run", bv(True)),
                    ("verdi.command", sv("pytest tests/ -q")),
                    ("verdi.exit_code", iv(0)),
                ],
            ),
            span(
                "e5", 0.4, name="grep",
                attrs=[
                    ("gen_ai.tool.name", sv("grep")),
                    ("verdi.command", sv("grep -r foo")),
                ],
            ),
            span(
                "e6", 0.5, name="go-test",
                attrs=[
                    ("gen_ai.tool.name", sv("bash")),
                    ("verdi.command", sv("go test ./...")),
                    ("verdi.exit_code", iv(2)),
                ],
            ),
            span(
                "e7", 0.6, name="Read",
                attrs=[("gen_ai.tool.name", sv("Read"))],  # not a file-edit tool → tool_call
            ),
        ],
    )


def _adversarial() -> dict:
    """§5 blinding meta-fixture: model ids, vendor names, arm-name strings laced
    through NON-whitelisted attributes (and resource attrs); whitelisted fields
    carry benign content only. The test asserts NONE of the laced byte sequences
    appear in the emitted trajectory/flight-recorder bytes."""
    return capture(
        "fixture-adversarial",
        [
            span(
                "f1", 0.0, name="chat",
                attrs=[
                    ("gen_ai.operation.name", sv("chat")),          # whitelisted, benign
                    ("gen_ai.request.model", sv("claude-3-5-sonnet-20241022")),  # DROPPED
                    ("gen_ai.system", sv("anthropic")),             # DROPPED
                    ("gen_ai.prompt", sv("use gpt-4o from openai")),  # DROPPED (not whitelisted)
                    ("llm.vendor", sv("OpenAI")),                   # DROPPED
                    ("custom.arm", sv("treatment-gpt4")),           # DROPPED
                    ("gen_ai.usage.input_tokens", iv(100)),
                    ("gen_ai.usage.output_tokens", iv(20)),
                    ("gen_ai.content.completion", sv("Task complete.")),  # benign
                ],
            ),
            span(
                "f2", 0.1, name="Bash", parent="f1",
                attrs=[
                    ("gen_ai.tool.name", sv("Bash")),               # whitelisted, benign
                    ("gen_ai.tool.arguments", sv('{"command": "echo hi"}')),  # benign
                    ("peer.service", sv("gemini-1.5-pro")),         # DROPPED
                    ("verdi.agent", sv("worker-1")),                # benign role
                    ("gen_ai.content.reasoning", sv("Consider the approach.")),  # benign
                ],
            ),
        ],
        resource=[
            ("service.name", sv("claude-code-arm-A")),  # DROPPED (resource attr)
            ("telemetry.sdk.language", sv("python")),
        ],
    )


FIXTURES = {
    "langchain": _langchain,
    "pydantic_ai": _pydantic_ai,
    "multi_agent": _multi_agent,
    "reasoning": _reasoning,
    "file_edit": _file_edit,
    "adversarial": _adversarial,
}

# The identity byte sequences the adversarial projection must never emit [§5].
ADVERSARIAL_IDENTITY_STRINGS = (
    "claude-3-5-sonnet-20241022", "anthropic", "OpenAI", "openai", "gpt-4o",
    "gemini-1.5-pro", "treatment-gpt4", "claude-code-arm-A",
)


def _golden_bytes(trial_id: str, record) -> bytes:
    """Persist through the FROZEN shared door into a scratch dir and return the
    exact on-disk bytes — the same path the run seam takes, so the golden is the
    real artifact, not a re-derivation."""
    import tempfile

    from harness.run.trajectory import TRAJECTORY_FILENAME, persist_trajectory
    from harness.run.flight_recorder import FLIGHT_RECORDER_FILENAME, persist_flight_recorder
    from harness.run.trajectory import TrajectoryRecord
    from harness.run.flight_recorder import FlightRecorder

    with tempfile.TemporaryDirectory() as d:
        dd = Path(d)
        if isinstance(record, TrajectoryRecord):
            persist_trajectory(record, dd)
            return (dd / TRAJECTORY_FILENAME).read_bytes()
        persist_flight_recorder(record, dd)
        return (dd / FLIGHT_RECORDER_FILENAME).read_bytes()


def regenerate() -> None:
    from harness.adapters.otlp import OtlpAdapter
    from harness.run.flight_recorder import FlightRecorder
    from harness.run.trajectory import TrajectoryRecord

    adapter = OtlpAdapter()
    for name, build in FIXTURES.items():
        spans = build()
        trial_id = spans["trial_id"]
        (FIXTURE_DIR / f"{name}.spans.json").write_text(
            json.dumps(spans, indent=2) + "\n", encoding="utf-8"
        )
        steps = adapter.normalize_trajectory(spans)
        if steps is not None:
            record = TrajectoryRecord(trial_id=trial_id, platform="otlp", steps=steps)
            (FIXTURE_DIR / f"{name}.trajectory.json").write_bytes(
                _golden_bytes(trial_id, record)
            )
        entries = adapter.normalize_reasoning(spans)
        if entries is not None:
            record = FlightRecorder(trial_id=trial_id, platform="otlp", entries=entries)
            (FIXTURE_DIR / f"{name}.flight_recorder.json").write_bytes(
                _golden_bytes(trial_id, record)
            )
    print(f"regenerated {len(FIXTURES)} fixture pairs in {FIXTURE_DIR}")


if __name__ == "__main__":
    regenerate()
