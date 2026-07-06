"""OTLP span → trajectory / flight-recorder normalizer [refactor 10 §1-4].

A registered :class:`~harness.adapters.base.Adapter` with platform id ``otlp``,
sibling of ``claude_code`` / ``codex``. Unlike the log-reading adapters it does
not parse ``agent_log.json``: it projects the redacted on-disk
``artifacts/otlp_spans.json`` (spec 09's :class:`OtlpCaptureRecord`) into the
FROZEN trajectory v3 / flight-recorder v3 fields — **into existing fields only,
so no schema-version bump** [refactor 10 §2].

The projection is a **closed whitelist** [refactor 10 §2]: an attribute crosses
into a trajectory/flight-recorder field only if a rule below names it. Everything
else — most critically ``gen_ai.request.model``, ``gen_ai.system``, ``service.*``
and all resource attributes — is dropped on the floor, so vendor/model/arm
identity cannot survive into the judge-adjacent trajectory (the same design
intent as the closed role vocabulary, ``trajectory.py:34-39``). The mapping is
pinned by ``OTLP_MAPPING_VERSION`` + golden fixture pairs [refactor 10 §4]; a
mapping change breaks a golden and forces a version bump + regen in one commit.

Determinism [refactor 10 §2]: all timing derives from span data (the harness
contributes no clock); selected spans sort by ``(start_time_unix_nano, span_id)``
so a shuffled batch order yields byte-identical output.

Imports [refactor 10 §5]: decoding already happened at capture, so this module
imports no protobuf and no LLM client. The redacted ``otlp_spans.json`` wrapper is
re-validated through a **thin read-only mirror** of spec 09's ``OtlpCaptureRecord``
(:class:`_SpanCapture` below) rather than importing ``hermetic.otlp_decode``: the
A5 contract set forbids that edge transitively (``grade``/``judge`` reach
``harness.adapters`` via ``plan.lock``'s ``known_platforms``, and the deterministic
grader/blind judge must never reach the span decoder). The mirror is input-only —
the OUTPUT records are the real frozen ``TrajectoryStep`` / ``ReasoningEntry``; the
live spec-09 ``persist_spans`` → this reader round trip in the docker e2e is the
drift guard.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, ConfigDict

from ..run.trajectory import TrajectoryStep, validate_agent_label
from .base import Adapter, Telemetry
from .base import coerce_float as _float
from .base import coerce_int as _int

if TYPE_CHECKING:  # runtime import is lazy — flight_recorder imports adapters.base,
    from ..run.flight_recorder import ReasoningEntry  # so a module-level edge cycles


class _SpanBatch(BaseModel):
    """Read-only mirror of ``hermetic.otlp_decode.OtlpBatch`` [refactor 10 §5]. The
    ``resource_spans`` list is external-shaped OTLP-JSON, passed through intact."""

    model_config = ConfigDict(extra="forbid")
    content_type: str
    resource_spans: list = []


class _SpanCapture(BaseModel):
    """Read-only mirror of ``hermetic.otlp_decode.OtlpCaptureRecord`` [refactor 10
    §5] — the ``otlp_spans.json`` wrapper this adapter re-validates. Kept in
    lockstep with spec 09's frozen wrapper (a live-artifact round trip in the docker
    e2e catches any drift loudly)."""

    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    trial_id: str
    batches: list[_SpanBatch]

# The mapping-table version [refactor 10 §4]. NOT stored in the frozen
# trajectory/flight-recorder records (no field addition, no byte change) — it is
# pinned instead by the golden fixture pairs. Any change to a rule below breaks a
# golden and must bump this constant + regenerate the goldens in the same commit.
OTLP_MAPPING_VERSION = 1

# DECISION D-10-2 [refactor 10 §2, accepted]: the file-edit tool-name set. A
# mechanical lookup, not an inference — an unknown tool stays a generic
# ``tool_call``. Starts minimal (claude_code parity, ``claude_code.py:18``, plus
# common OTel-emitted names); byte-affecting and golden-pinned, extended only via
# an ``OTLP_MAPPING_VERSION`` bump.
_FILE_EDIT_TOOLS = frozenset(
    {
        "Edit", "Write", "MultiEdit", "NotebookEdit",  # claude_code.py:18 parity
        "write_file", "edit_file", "create_file", "str_replace_editor",  # common OTel
    }
)

# The GenAI operation names that are one LLM call each → a ``message`` step [§2].
_LLM_OPERATIONS = frozenset({"chat", "text_completion", "generate_content"})

# Frozen test-runner classification [refactor 10 §2]: a ``test_run`` is NEVER
# inferred from a span name — only from an explicit ``verdi.test_run`` OR a
# ``verdi.command`` whose first token is an unambiguous test runner. The
# conservative ``codex.py`` ``parsed_cmd == "test"`` posture: a miss (an
# unrecognized test command → generic tool_call) is under-specific, never wrong.
_SOLO_TEST_RUNNERS = frozenset({"pytest", "py.test", "jest", "vitest", "tox", "rspec", "phpunit"})
_SUBCMD_TEST_RUNNERS = frozenset(
    {"go", "cargo", "npm", "yarn", "pnpm", "make", "mvn", "gradle", "dotnet"}
)


class SpanMappingError(RuntimeError):
    """An ``otlp_spans.json`` structure could not be normalized [refactor 10 §3].

    Raised when the artifact's wrapper is invalid, or a whitelisted attribute
    violates a mapping rule (e.g. ``verdi.agent`` outside the closed role
    vocabulary). Declared telemetry that lies fails the trial CLOSED — the seam
    maps it to ``trial_infra_failed(spans_corrupt)`` (A12), mirroring the
    :class:`~harness.run.trajectory.TrajectoryCorruptError` discipline. Distinct
    from *honest absence* (no otlp_spans.json, or zero selected spans), which
    yields ``None`` and no artifact.
    """


# --- OTLP-JSON value + field helpers ----------------------------------------
# OTLP-JSON (proto3 JSON mapping, camelCase) wraps every attribute value in an
# AnyValue object and encodes int64 as a STRING. These helpers unwrap that shape
# read-only; an unrecognized shape is ``None`` (dropped), never guessed.


def _any_value(v):
    """Unwrap an OTLP AnyValue to a Python scalar/list; unknown shape → ``None``.

    proto3 JSON encodes int64 as a string (``{"intValue": "1200"}``), so integer
    values are parsed back to ``int``; a non-numeric ``intValue`` is unmeasurable
    (``None``), never crashes the projection."""
    if not isinstance(v, dict):
        return v  # a bare scalar from a non-standard emitter — tolerate
    if "stringValue" in v:
        return v["stringValue"]
    if "boolValue" in v:
        return v["boolValue"]
    if "intValue" in v:
        try:
            return int(v["intValue"])
        except (TypeError, ValueError):
            return None
    if "doubleValue" in v:
        return v["doubleValue"]
    if "arrayValue" in v:
        inner = v["arrayValue"]
        vals = inner.get("values", []) if isinstance(inner, dict) else []
        return [_any_value(x) for x in vals]
    return None


def _attributes(carrier: dict) -> dict:
    """A span's (or event's) ``attributes`` list folded into ``{key: value}``.

    A malformed entry (no string ``key``) is skipped — the collector's noise, not
    this trial's fault, the ``decode_envelope_lines`` tolerance."""
    out: dict = {}
    for a in carrier.get("attributes", []) or []:
        if isinstance(a, dict) and isinstance(a.get("key"), str):
            out[a["key"]] = _any_value(a.get("value"))
    return out


def _span_field(span: dict, camel: str, snake: str):
    """A span field by its camelCase proto3-JSON name, tolerating snake_case (the
    ``_resource_spans`` top-level tolerance, applied per field)."""
    return span[camel] if camel in span else span.get(snake)


def _start_ns(span: dict) -> int:
    """``startTimeUnixNano`` as an int (proto3 JSON encodes it as a string); an
    absent/invalid start is 0 so an arbitrary forest still sorts deterministically."""
    try:
        return int(_span_field(span, "startTimeUnixNano", "start_time_unix_nano"))
    except (TypeError, ValueError):
        return 0


def _span_id(span: dict) -> str:
    """The span id as it appears in the export — the deterministic sort tie-break
    and the parent-chain key. Both sides of the chain come from the same export
    (same encoding), so linkage matches regardless of hex/base64."""
    v = _span_field(span, "spanId", "span_id")
    return v if isinstance(v, str) else ""


def _parent_id(span: dict) -> str:
    v = _span_field(span, "parentSpanId", "parent_span_id")
    return v if isinstance(v, str) else ""


def _selected(attrs: dict) -> bool:
    """§2 span selection: only spans carrying a ``gen_ai.*`` or ``verdi.*``
    attribute. HTTP/DB/other infra spans are trajectory-altitude noise and stay
    only in the raw artifact."""
    return any(k.startswith("gen_ai.") or k.startswith("verdi.") for k in attrs)


def _iter_spans(record: _SpanCapture):
    """Yield every span dict across all batches → resourceSpans → scopeSpans."""
    for batch in record.batches:
        for rs in batch.resource_spans:
            if not isinstance(rs, dict):
                continue
            for scope in _span_field(rs, "scopeSpans", "scope_spans") or []:
                if not isinstance(scope, dict):
                    continue
                for span in scope.get("spans", []) or []:
                    if isinstance(span, dict):
                        yield span


def _looks_like_test(command: Optional[str]) -> bool:
    """§2: is the command an explicit test-runner invocation? Conservative — the
    first token must be an unambiguous solo runner (``pytest``) or a
    build-tool + ``test`` subcommand (``go test``); anything else is not a test."""
    if not isinstance(command, str):
        return False
    tokens = command.split()
    if not tokens:
        return False
    first = tokens[0].rsplit("/", 1)[-1]
    if first in _SOLO_TEST_RUNNERS:
        return True
    return first in _SUBCMD_TEST_RUNNERS and len(tokens) > 1 and tokens[1] == "test"


def _agent(attrs: dict) -> Optional[str]:
    """§2 agent rule: ``verdi.agent`` ONLY, validated by ``validate_agent_label``.
    Present-but-invalid fails closed (:class:`SpanMappingError` → spans_corrupt);
    absent → ``None`` (unattributed). NEVER derived from ``service.name`` or a
    resource attribute — that would be an identity leak by construction."""
    raw = attrs.get("verdi.agent")
    if raw is None:
        return None
    try:
        return validate_agent_label(raw if isinstance(raw, str) else str(raw))
    except ValueError as e:
        raise SpanMappingError(
            f"verdi.agent {raw!r} is outside the closed role vocabulary — declared "
            f"telemetry that lies fails the trial closed [refactor 10 §3]: {e}"
        ) from e


def _tokens(attrs: dict) -> Optional[int]:
    """§2 tokens: ``gen_ai.usage.input_tokens + output_tokens``; null unless BOTH
    halves are present. A step's ``tokens`` is a total, so a total with an
    unmeasured half is unmeasurable — returned null, never imputed by treating the
    absent half as 0 [D004: nulls are flagged, never imputed]."""
    in_tok = _int(attrs.get("gen_ai.usage.input_tokens"))
    out_tok = _int(attrs.get("gen_ai.usage.output_tokens"))
    if in_tok is None or out_tok is None:
        return None
    return in_tok + out_tok


def _tool_detail(attrs: dict) -> Optional[str]:
    """§2 detail for a tool step: tool name + whitelisted args, joined; else null.

    Only the whitelisted ``gen_ai.tool.name`` / ``gen_ai.tool.arguments`` cross —
    no non-whitelisted attribute reaches ``detail`` (the identity whitelist)."""
    name = attrs.get("gen_ai.tool.name")
    args = attrs.get("gen_ai.tool.arguments")
    parts = [p for p in (name, args) if isinstance(p, str) and p]
    return " ".join(parts) if parts else None


def _files_touched(kind: str, attrs: dict) -> Optional[list[str]]:
    """§2 files_touched: ``verdi.files`` (string list) first; else, for a
    ``file_edit``, a path parsed from the whitelisted ``gen_ai.tool.arguments``
    JSON (``file_path``/``path``/``filename``); else null."""
    raw = attrs.get("verdi.files")
    if isinstance(raw, list):
        return [str(f) for f in raw]
    if kind == "file_edit":
        args = attrs.get("gen_ai.tool.arguments")
        if isinstance(args, str):
            try:
                parsed = json.loads(args)
            except json.JSONDecodeError:
                return None
            if isinstance(parsed, dict):
                for key in ("file_path", "path", "filename"):
                    p = parsed.get(key)
                    if isinstance(p, str) and p:
                        return [p]
    return None


def _kind_and_command(attrs: dict) -> Optional[tuple[str, Optional[str]]]:
    """Classify a selected span into ``(kind, command)`` per the §2 table, or
    ``None`` if the span is selected but is not a trajectory action (e.g. a
    reasoning-only span).

    Precedence: gen_ai tool/LLM semantics first, then verdi execution signals.
    Within the tool family: explicit ``test_run`` > ``file_edit`` > ``tool_call``.
    ``command`` is ``verdi.command`` for tool-family steps (else null), ``""`` for
    a message (measured — a message is not a shell command, the claude_code/codex
    precedent)."""
    operation = attrs.get("gen_ai.operation.name")
    tool_name = attrs.get("gen_ai.tool.name")
    command = attrs.get("verdi.command")
    command = command if isinstance(command, str) else None
    has_tool = operation == "execute_tool" or isinstance(tool_name, str)
    has_verdi_exec = (
        attrs.get("verdi.test_run") is not None
        or command is not None
        or attrs.get("verdi.exit_code") is not None
    )

    if has_tool:
        return _refine_tool(attrs, tool_name, command), command
    if operation in _LLM_OPERATIONS:
        return "message", ""
    if has_verdi_exec:
        return _refine_tool(attrs, tool_name, command), command
    return None  # selected but not an action — may still carry reasoning


def _refine_tool(attrs: dict, tool_name, command: Optional[str]) -> str:
    if attrs.get("verdi.test_run") is True or _looks_like_test(command):
        return "test_run"
    if isinstance(tool_name, str) and tool_name in _FILE_EDIT_TOOLS:
        return "file_edit"
    return "tool_call"


def _reasoning_content(span: dict, attrs: dict) -> Optional[str]:
    """§2 reasoning content: the ``gen_ai.content.reasoning`` attribute, or the
    text (``content`` attribute) of a span event named ``gen_ai.reasoning`` /
    ``verdi.reasoning``. Whitelisted sources only."""
    attr = attrs.get("gen_ai.content.reasoning")
    if isinstance(attr, str):
        return attr
    for ev in span.get("events", []) or []:
        if not isinstance(ev, dict) or ev.get("name") not in ("gen_ai.reasoning", "verdi.reasoning"):
            continue
        text = _attributes(ev).get("content")
        if isinstance(text, str):
            return text
    return None


def _parse_record(native_log: dict) -> Optional[_SpanCapture]:
    """Validate the redacted ``otlp_spans.json`` wrapper [refactor 10 §1/§3].

    ``{}`` (the seam's absent-artifact sentinel) or a wrapper with no ``batches``
    is honest absence → ``None``. A present-but-invalid wrapper is
    :class:`SpanMappingError` (→ spans_corrupt): declared telemetry that cannot be
    trusted fails the trial closed."""
    if not native_log or "batches" not in native_log:
        return None
    try:
        return _SpanCapture.model_validate(native_log)
    except Exception as e:  # pydantic ValidationError — a wrapper that lies
        raise SpanMappingError(
            "otlp_spans.json wrapper is not a valid capture record — failing the "
            f"trial closed [refactor 10 §3]: {e}"
        ) from e


def _ordered_selected(record: _SpanCapture) -> tuple[list[tuple[dict, dict]], int]:
    """The selected spans, sorted ``(start_time_unix_nano, span_id)`` [§2], each
    paired with its folded attributes, plus ``t0`` (the min start of the set)."""
    selected: list[tuple[dict, dict]] = []
    for span in _iter_spans(record):
        attrs = _attributes(span)
        if _selected(attrs):
            selected.append((span, attrs))
    selected.sort(key=lambda pair: (_start_ns(pair[0]), _span_id(pair[0])))
    t0 = _start_ns(selected[0][0]) if selected else 0
    return selected, t0


def _relative_ts(span: dict, t0: int) -> float:
    """``(start − t0) / 1e9`` rounded to milliseconds [§2]."""
    return round((_start_ns(span) - t0) / 1e9, 3)


def _step_index(selected: list[tuple[dict, dict]]) -> tuple[dict, dict]:
    """``(span_id → step index, span_id → span)`` over the selected set, using the
    SAME classification/order as :meth:`OtlpAdapter.normalize_trajectory`, so a
    reasoning entry's ``turn`` index agrees byte-for-byte with the trajectory."""
    step_index_by_id: dict[str, int] = {}
    span_by_id: dict[str, dict] = {}
    n_steps = 0
    for span, attrs in selected:
        sid = _span_id(span)
        if sid:
            span_by_id[sid] = span
        if _kind_and_command(attrs) is not None:
            if sid:
                step_index_by_id[sid] = n_steps
            n_steps += 1
    return step_index_by_id, span_by_id


def _nearest_ancestor_step(
    span: dict, span_by_id: dict[str, dict], step_index_by_id: dict[str, int]
) -> Optional[int]:
    """Walk the parent-span-id chain to the nearest ancestor that is a trajectory
    step; return its step index, else ``None`` [refactor 10 §2]. Cycle-guarded so a
    malformed (self/loop-parented) trace terminates rather than spins."""
    parent = _parent_id(span)
    seen: set[str] = set()
    while parent and parent not in seen:
        seen.add(parent)
        if parent in step_index_by_id:
            return step_index_by_id[parent]
        ancestor = span_by_id.get(parent)
        if ancestor is None:
            break
        parent = _parent_id(ancestor)
    return None


class OtlpAdapter(Adapter):
    """Projects redacted ``otlp_spans.json`` into trajectory + flight-recorder
    records [refactor 10 §2]. Native (non-generic) format — verdi-format keys are
    inert. The seam hands ``normalize_trajectory`` / ``normalize_reasoning`` the
    parsed ``otlp_spans.json`` dict (not ``agent_log.json``); persistence is the
    shared ``persist_trajectory`` / ``persist_flight_recorder`` door — this
    normalizer contains ZERO serialization code."""

    platform = "otlp"
    speaks_generic_format = False  # native span format; verdi-format keys are inert

    def normalize(self, native_log: dict) -> Telemetry:
        # Whole-trial telemetry is the agent-log adapter's concern (the dual-source
        # invariant reads the engine's in-memory native_log); the OTLP arm's
        # per-trial telemetry stays honestly null here — the SPANS feed the
        # trajectory/reasoning captures, not the authoritative telemetry stream.
        return Telemetry()

    def normalize_trajectory(self, native_log: dict) -> Optional[list[TrajectoryStep]]:
        """Span forest → ordered ``TrajectoryStep`` list [refactor 10 §2].

        ``None`` (honest absence) when the artifact is absent or yields zero
        action steps; a wrapper/mapping violation is :class:`SpanMappingError`."""
        record = _parse_record(native_log)
        if record is None:
            return None
        selected, t0 = _ordered_selected(record)
        steps: list[TrajectoryStep] = []
        for span, attrs in selected:
            classified = _kind_and_command(attrs)
            if classified is None:
                continue  # selected but not an action (e.g. reasoning-only)
            kind, command = classified
            exit_code = (
                _int(attrs.get("verdi.exit_code")) if kind in ("test_run", "tool_call") else None
            )
            detail = (
                attrs.get("gen_ai.content.completion") if kind == "message" else _tool_detail(attrs)
            )
            steps.append(
                TrajectoryStep(
                    kind=kind,
                    relative_ts=_relative_ts(span, t0),
                    tokens=_tokens(attrs),
                    cost=_float(attrs.get("verdi.cost_usd")),
                    files_touched=_files_touched(kind, attrs),
                    exit_code=exit_code,
                    command=command,
                    detail=detail if isinstance(detail, str) else None,
                    agent=_agent(attrs),
                )
            )
        return steps or None

    def normalize_reasoning(self, native_log: dict) -> Optional[list[ReasoningEntry]]:
        """Span forest → ordered ``ReasoningEntry`` list [refactor 10 §2].

        Each selected span carrying reasoning (``gen_ai.content.reasoning`` or a
        ``gen_ai.reasoning``/``verdi.reasoning`` event) becomes one entry; ``turn``
        links it to the trajectory step of its nearest selected ancestor via the
        parent-span-id chain (no such ancestor → ``None``). ``None`` (honest
        absence) when no span carries reasoning."""
        from ..run.flight_recorder import ReasoningEntry  # lazy — see module header

        record = _parse_record(native_log)
        if record is None:
            return None
        selected, t0 = _ordered_selected(record)
        step_index_by_id, span_by_id = _step_index(selected)
        entries: list[ReasoningEntry] = []
        for span, attrs in selected:
            content = _reasoning_content(span, attrs)
            if content is None:
                continue
            entries.append(
                ReasoningEntry(
                    content=content,
                    tokens=_tokens(attrs),
                    cost=_float(attrs.get("verdi.cost_usd")),
                    agent=_agent(attrs),
                    relative_ts=_relative_ts(span, t0),
                    turn=_nearest_ancestor_step(span, span_by_id, step_index_by_id),
                )
            )
        return entries or None
