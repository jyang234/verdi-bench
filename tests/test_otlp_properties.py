"""OTLP normalization property tests [refactor 10 §6.5, §7].

Over arbitrary span forests: every emitted step validates under the FROZEN
``TrajectoryStep`` (the closed kind enum + role regex are the oracle), the
projection is order-invariant (a shuffled forest yields byte-identical output),
and it is a strict identity whitelist (a canary laced through non-whitelisted
attributes — the position real model ids / vendor names / arm names occupy —
never reaches the emitted bytes). A separate strategy feeds arbitrary attribute
shapes to pin that a malformed forest degrades to a typed outcome, never an
uncaught crash.
"""

from __future__ import annotations

import copy

from hypothesis import given, settings
from hypothesis import strategies as st

from harness.adapters.otlp import OtlpAdapter, SpanMappingError
from harness.run.flight_recorder import FlightRecorder
from harness.run.flight_recorder import canonical_bytes as fr_bytes
from harness.run.trajectory import (
    _AGENT_LABEL_RE,
    AGENT_ROLES,
    TrajectoryRecord,
    TrajectoryStep,
)
from harness.run.trajectory import canonical_bytes as traj_bytes

_ADAPTER = OtlpAdapter()

# The canary occupies the position a real model id / vendor / arm name would in a
# non-whitelisted attribute — the whitelist must drop it (§4/§5).
_CANARY = "IDENTITY_LEAK_CANARY_7f3a9c"
_ROLES = sorted(AGENT_ROLES)


def _sv(s):
    return {"stringValue": s}


def _iv(n):
    return {"intValue": str(n)}


_valid_agents = st.one_of(
    st.sampled_from(_ROLES),
    st.builds(lambda r, n: f"{r}-{n}", st.sampled_from(_ROLES), st.integers(0, 999)),
)


@st.composite
def _span(draw, index: int, parents: list[str]):
    """A single OTLP span: valid whitelisted attributes (benign values) plus the
    identity canary laced through non-whitelisted attributes and the span name."""
    sid = f"span{index:03d}"
    attrs = []
    if draw(st.booleans()):
        attrs.append({"key": "gen_ai.operation.name",
                      "value": _sv(draw(st.sampled_from(["chat", "text_completion", "execute_tool"])))})
    if draw(st.booleans()):
        attrs.append({"key": "gen_ai.tool.name",
                      "value": _sv(draw(st.sampled_from(["Edit", "Write", "Read", "bash", "grep"])))})
    if draw(st.booleans()):
        attrs.append({"key": "gen_ai.tool.arguments", "value": _sv('{"x": 1}')})
    if draw(st.booleans()):
        attrs.append({"key": "gen_ai.usage.input_tokens", "value": _iv(draw(st.integers(0, 9999)))})
    if draw(st.booleans()):
        attrs.append({"key": "gen_ai.usage.output_tokens", "value": _iv(draw(st.integers(0, 9999)))})
    if draw(st.booleans()):
        attrs.append({"key": "verdi.cost_usd", "value": {"doubleValue": draw(st.floats(0, 9, allow_nan=False, allow_infinity=False))}})
    if draw(st.booleans()):
        attrs.append({"key": "verdi.exit_code", "value": _iv(draw(st.integers(0, 255)))})
    if draw(st.booleans()):
        attrs.append({"key": "verdi.command", "value": _sv(draw(st.sampled_from(["ls", "pytest x", "go test ./..."])))})
    if draw(st.booleans()):
        attrs.append({"key": "verdi.test_run", "value": {"boolValue": True}})
    agent = draw(st.none() | _valid_agents)
    if agent is not None:
        attrs.append({"key": "verdi.agent", "value": _sv(agent)})
    if draw(st.booleans()):
        attrs.append({"key": "gen_ai.content.completion", "value": _sv("ok")})
    if draw(st.booleans()):
        attrs.append({"key": "gen_ai.content.reasoning", "value": _sv("thinking")})
    # the identity canary: only ever in NON-whitelisted attributes + the span name
    attrs += [
        {"key": "gen_ai.request.model", "value": _sv(_CANARY)},
        {"key": "gen_ai.system", "value": _sv(_CANARY)},
        {"key": "service.name", "value": _sv(_CANARY)},
        {"key": "custom.arm.name", "value": _sv(_CANARY)},
    ]
    span = {
        "spanId": sid,
        "name": _CANARY,  # the span name is not a whitelisted source
        "startTimeUnixNano": str(draw(st.integers(0, 5_000_000_000_000_000_000))),
        "attributes": attrs,
    }
    parent = draw(st.none() | (st.sampled_from(parents) if parents else st.none()))
    if parent is not None:
        span["parentSpanId"] = parent
    return span


@st.composite
def _captures(draw):
    """A capture wrapper: 0..6 spans across 1..3 batches, parents drawn from
    earlier spans so the tree is acyclic-ish (the walker is cycle-guarded anyway)."""
    n = draw(st.integers(0, 6))
    spans = []
    ids: list[str] = []
    for i in range(n):
        s = draw(_span(i, list(ids)))
        spans.append(s)
        ids.append(s["spanId"])
    # scatter the spans across a few batches
    n_batches = draw(st.integers(1, 3))
    buckets: list[list] = [[] for _ in range(n_batches)]
    for s in spans:
        buckets[draw(st.integers(0, n_batches - 1))].append(s)
    batches = [
        {"content_type": "application/json",
         "resource_spans": [{"scopeSpans": [{"spans": b}]}]}
        for b in buckets
    ]
    return {"schema_version": 1, "trial_id": "prop", "batches": batches}


def _traj_and_fr_bytes(capture: dict) -> bytes:
    steps = _ADAPTER.normalize_trajectory(capture)
    entries = _ADAPTER.normalize_reasoning(capture)
    out = b""
    if steps is not None:
        out += traj_bytes(TrajectoryRecord(trial_id="prop", platform="otlp", steps=steps))
    if entries is not None:
        out += fr_bytes(FlightRecorder(trial_id="prop", platform="otlp", entries=entries))
    return out


@settings(max_examples=250)
@given(_captures())
def test_every_emitted_step_validates_under_the_frozen_model(capture):
    """The closed kind enum + role regex are the oracle: every step the adapter
    emits re-validates, and its ``turn`` links (reasoning) are non-negative."""
    steps = _ADAPTER.normalize_trajectory(capture)
    if steps is not None:
        for s in steps:
            assert isinstance(s, TrajectoryStep)
            TrajectoryStep.model_validate(s.model_dump())  # extra=forbid oracle
            assert s.kind in ("tool_call", "file_edit", "test_run", "message")
            assert s.agent is None or _AGENT_LABEL_RE.fullmatch(s.agent)
    entries = _ADAPTER.normalize_reasoning(capture)
    if entries is not None:
        n_steps = len(steps or [])
        for e in entries:
            assert e.turn is None or 0 <= e.turn < n_steps
            assert e.agent is None or _AGENT_LABEL_RE.fullmatch(e.agent)


@settings(max_examples=250)
@given(_captures(), st.integers(0, 2**32))
def test_projection_is_order_invariant(capture, seed):
    """Determinism: reordering batches AND the spans within them yields byte-
    identical trajectory + flight-recorder output (the collector's flush order is
    not something the projection may depend on)."""
    import random

    base = _traj_and_fr_bytes(capture)
    rng = random.Random(seed)
    shuffled = copy.deepcopy(capture)
    rng.shuffle(shuffled["batches"])
    for batch in shuffled["batches"]:
        for rs in batch["resource_spans"]:
            for scope in rs["scopeSpans"]:
                rng.shuffle(scope["spans"])
    assert _traj_and_fr_bytes(shuffled) == base


@settings(max_examples=250)
@given(_captures())
def test_identity_canary_never_leaks(capture):
    """§4/§5 whitelist property, generalized: the canary laced through every
    non-whitelisted attribute (and the span name) never reaches the emitted bytes."""
    assert _CANARY.encode() not in _traj_and_fr_bytes(capture)


@settings(max_examples=200)
@given(
    st.lists(
        st.fixed_dictionaries(
            {
                "spanId": st.text(min_size=1, max_size=6),
                "startTimeUnixNano": st.one_of(st.text(max_size=8), st.integers()),
                "attributes": st.lists(
                    st.fixed_dictionaries(
                        {"key": st.text(max_size=20), "value": st.one_of(st.none(), st.dictionaries(st.text(max_size=8), st.text(max_size=8), max_size=2))}
                    ),
                    max_size=4,
                ),
            },
            optional={"parentSpanId": st.text(max_size=6)},
        ),
        max_size=5,
    )
)
def test_arbitrary_attribute_shapes_degrade_to_a_typed_outcome(spans):
    """Robustness: a malformed forest (garbage keys, missing values, non-numeric
    starts) never raises an UNCAUGHT exception — only a valid list / None, or the
    typed SpanMappingError (fail loudly, never a swallowed crash)."""
    capture = {
        "schema_version": 1, "trial_id": "prop",
        "batches": [{"content_type": "application/json", "resource_spans": [{"scopeSpans": [{"spans": spans}]}]}],
    }
    try:
        steps = _ADAPTER.normalize_trajectory(capture)
        entries = _ADAPTER.normalize_reasoning(capture)
    except SpanMappingError:
        return  # a declared-but-lying attribute failing closed is allowed
    assert steps is None or isinstance(steps, list)
    assert entries is None or isinstance(entries, list)
