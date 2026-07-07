"""OtlpAdapter span → trajectory/flight-recorder mapping [refactor 10 §2-4, §7].

The adapter half of the golden discipline: the registered ``otlp`` adapter
reproduces every committed golden BYTE-FOR-BYTE (the mapping pin — a rule change
breaks a golden and forces an ``OTLP_MAPPING_VERSION`` bump + regen), plus the
closed-vocabulary fail-closed (mirroring ``test_eval21_attribution.py:115-138``),
input-shuffle determinism, and honest absence. Selection/ordering/field rules are
pinned by the goldens; these tests pin the behaviors a static golden cannot.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from harness.adapters import get_adapter
from harness.adapters.otlp import (
    OTLP_MAPPING_VERSION,
    OtlpAdapter,
    SpanMappingError,
    _FILE_EDIT_TOOLS,
)
from harness.run.flight_recorder import (
    FLIGHT_RECORDER_FILENAME,
    FlightRecorder,
    persist_flight_recorder,
)
from harness.run.trajectory import (
    TRAJECTORY_FILENAME,
    TrajectoryRecord,
    persist_trajectory,
    slice_by_agent,
)
from tests.fixtures.otlp.regen import FIXTURE_DIR, FIXTURES

_ADAPTER = OtlpAdapter()


def _spans(name: str) -> dict:
    return json.loads((FIXTURE_DIR / f"{name}.spans.json").read_text(encoding="utf-8"))


def _persisted_trajectory(steps, trial_id, tmp_path: Path) -> bytes:
    persist_trajectory(TrajectoryRecord(trial_id=trial_id, platform="otlp", steps=steps), tmp_path)
    return (tmp_path / TRAJECTORY_FILENAME).read_bytes()


def _persisted_reasoning(entries, trial_id, tmp_path: Path) -> bytes:
    persist_flight_recorder(
        FlightRecorder(trial_id=trial_id, platform="otlp", entries=entries), tmp_path
    )
    return (tmp_path / FLIGHT_RECORDER_FILENAME).read_bytes()


# --- registration ------------------------------------------------------------
def test_otlp_adapter_registered_native_format():
    a = get_adapter("otlp")
    assert isinstance(a, OtlpAdapter)
    assert a.platform == "otlp"
    assert a.speaks_generic_format is False  # spans are a native format


# --- byte-exact golden reproduction = the mapping pin ------------------------
@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_adapter_reproduces_trajectory_golden_byte_for_byte(name, tmp_path):
    """The core §4 pin: the adapter projects each fixture into the EXACT committed
    trajectory bytes. A mapping-rule change alters these bytes → this fails →
    forcing an OTLP_MAPPING_VERSION bump + a reviewed regen in one commit."""
    spans = _spans(name)
    steps = _ADAPTER.normalize_trajectory(spans)
    assert steps is not None, f"{name} produced no trajectory"
    got = _persisted_trajectory(steps, spans["trial_id"], tmp_path)
    golden = (FIXTURE_DIR / f"{name}.trajectory.json").read_bytes()
    assert got == golden, f"{name} trajectory drifted from its golden"


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_adapter_reproduces_flight_recorder_golden_or_honest_absence(name, tmp_path):
    """Where a fixture carries reasoning the adapter reproduces the committed
    flight-recorder bytes; where it does not, both the adapter (``None``) and the
    corpus (no golden file) agree on honest absence."""
    spans = _spans(name)
    entries = _ADAPTER.normalize_reasoning(spans)
    golden_path = FIXTURE_DIR / f"{name}.flight_recorder.json"
    if entries is None:
        assert not golden_path.exists(), f"{name}: adapter absent but a golden exists"
        return
    got = _persisted_reasoning(entries, spans["trial_id"], tmp_path)
    assert got == golden_path.read_bytes(), f"{name} flight recorder drifted from its golden"


# --- OTLP_MAPPING_VERSION drift discipline -----------------------------------
def test_mapping_version_pinned():
    """Literal pin, deliberately not derived: the byte-exact golden tests are the
    drift detector; this asserts the version they are pinned AT. Bumping the
    mapping without bumping this (and regenerating goldens) fails the suite."""
    assert OTLP_MAPPING_VERSION == 1


def test_file_edit_tool_set_is_the_d_10_2_accepted_set():
    """D-10-2 pin: the file-edit tool set is byte-affecting (it decides file_edit
    vs tool_call), so it is pinned literally — extension is an OTLP_MAPPING_VERSION
    bump, never a silent widening."""
    assert _FILE_EDIT_TOOLS == frozenset(
        {
            "Edit", "Write", "MultiEdit", "NotebookEdit",
            "write_file", "edit_file", "create_file", "str_replace_editor",
        }
    )


# --- closed-vocabulary: agent fail-closed (mirrors eval21:115-138) -----------
def _one_chat_span(agent):
    attrs = [{"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}}]
    if agent is not None:
        attrs.append({"key": "verdi.agent", "value": {"stringValue": agent}})
    return {
        "schema_version": 1,
        "trial_id": "t",
        "batches": [
            {
                "content_type": "application/json",
                "resource_spans": [
                    {"scopeSpans": [{"spans": [{"spanId": "s1", "startTimeUnixNano": "1", "attributes": attrs}]}]}
                ],
            }
        ],
    }


@pytest.mark.parametrize("good", ["worker-42", "critic-2", "planner"])
def test_valid_agent_label_accepted(good):
    steps = _ADAPTER.normalize_trajectory(_one_chat_span(good))
    assert steps[0].agent == good


@pytest.mark.parametrize("bad", ["llama-planner", "gpt-4-worker", "the good arm", "worker-1234"])
def test_agent_outside_vocabulary_fails_closed(bad):
    """A ``verdi.agent`` outside the closed role vocabulary is declared telemetry
    that lies → SpanMappingError (→ spans_corrupt), never laundered or scrubbed."""
    with pytest.raises(SpanMappingError, match="closed role vocabulary"):
        _ADAPTER.normalize_trajectory(_one_chat_span(bad))


def test_absent_agent_is_unattributed():
    steps = _ADAPTER.normalize_trajectory(_one_chat_span(None))
    assert steps[0].agent is None
    # and it files under the explicit UNATTRIBUTED bucket, never dropped
    record = TrajectoryRecord(trial_id="t", platform="otlp", steps=steps)
    assert set(slice_by_agent(record)) == {"unattributed"}


def test_multi_agent_slices_by_role():
    steps = _ADAPTER.normalize_trajectory(_spans("multi_agent"))
    groups = slice_by_agent(TrajectoryRecord(trial_id="t", platform="otlp", steps=steps))
    assert set(groups) == {"worker-1", "critic-2"}
    assert len(groups["worker-1"]) == 2 and len(groups["critic-2"]) == 1


# --- determinism: shuffled input → identical bytes ---------------------------
def _shuffle(spans: dict, seed: int) -> dict:
    """Reorder batches AND the spans within each scope by a seeded permutation —
    the collector's batch/flush order is not something the projection may depend
    on (determinism directive)."""
    import random

    rng = random.Random(seed)
    out = copy.deepcopy(spans)
    rng.shuffle(out["batches"])
    for batch in out["batches"]:
        for rs in batch["resource_spans"]:
            for scope in rs.get("scopeSpans", []):
                rng.shuffle(scope["spans"])
    return out


@pytest.mark.parametrize("name", sorted(FIXTURES))
@pytest.mark.parametrize("seed", [1, 7, 99])
def test_shuffled_input_yields_identical_bytes(name, seed, tmp_path):
    spans = _spans(name)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    base_steps = _ADAPTER.normalize_trajectory(spans)
    base = _persisted_trajectory(base_steps, spans["trial_id"], tmp_path / "a")
    shuf_steps = _ADAPTER.normalize_trajectory(_shuffle(spans, seed))
    shuf = _persisted_trajectory(shuf_steps, spans["trial_id"], tmp_path / "b")
    assert shuf == base, f"{name} not order-invariant under seed {seed}"


# --- honest absence ----------------------------------------------------------
def test_absent_artifact_is_none():
    """``{}`` (the seam's absent-artifact sentinel) → None, no artifact, no sha."""
    assert _ADAPTER.normalize_trajectory({}) is None
    assert _ADAPTER.normalize_reasoning({}) is None


def test_zero_selected_spans_is_none():
    """A present artifact with only infra spans (no gen_ai.*/verdi.*) → honest
    absence, not a fabricated empty trajectory."""
    infra = {
        "schema_version": 1,
        "trial_id": "t",
        "batches": [
            {
                "content_type": "application/json",
                "resource_spans": [
                    {"scopeSpans": [{"spans": [
                        {"spanId": "h1", "startTimeUnixNano": "1",
                         "attributes": [{"key": "http.method", "value": {"stringValue": "GET"}}]}
                    ]}]}
                ],
            }
        ],
    }
    assert _ADAPTER.normalize_trajectory(infra) is None
    assert _ADAPTER.normalize_reasoning(infra) is None


def test_empty_batches_is_none():
    assert _ADAPTER.normalize_trajectory({"schema_version": 1, "trial_id": "t", "batches": []}) is None


def test_invalid_wrapper_fails_closed():
    """A present-but-invalid wrapper (batches not a list) is spans_corrupt, not
    honest absence — declared telemetry that cannot be trusted fails the trial."""
    with pytest.raises(SpanMappingError):
        _ADAPTER.normalize_trajectory({"batches": {"not": "a list"}})
