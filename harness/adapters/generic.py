"""Generic adapter — the zero-code path onto the adapter seam [EVAL-4 AC-2,
EVAL-12 AC-1].

Any test subject whose trial image writes ``artifacts/agent_log.json`` in the
verdi normalized log format runs under ``platform: generic`` with no
harness-side code. The format is deliberately nothing but the instrument's own
interfaces — :class:`~harness.adapters.base.Telemetry` field-for-field and
:class:`~harness.run.trajectory.TrajectoryStep` step-for-step — so supporting
a new shape of test subject means emitting the record verdi-bench already
speaks, not teaching verdi-bench a new one. The full format spec, integration
tiers, and multi-agent guidance live in ``docs/adapters.md``.

Format v1 (v2 is a superset — see below)::

    {
      "verdi_log_version": 1,
      "telemetry":  {"tokens_in": …, "tokens_out": …, "tokens_cache": …,
                     "cost": …, "wall_time_s": …, "tool_calls": …},
      "trajectory": [{"kind": "tool_call", "relative_ts": …, "tokens": …,
                      "cost": …, "files_touched": […], "exit_code": …,
                      "command": …}, …]
    }

Format v2 [EVAL-14] adds multi-agent attribution: an ``agent`` field on
trajectory steps (closed role vocabulary — see
``harness.run.trajectory.AGENT_ROLES``) and a top-level ``telemetry_by_model``
object keyed strictly by the models the locked spec declared (EVAL-13's
primary + aux set), each value a Telemetry-shaped block. v1 logs parse
unchanged forever.

Honesty rules split on declaration. A log with **no** ``verdi_log_version``
never claimed the format: telemetry is all-null and the trajectory honestly
absent, exactly like any other unparseable native content [D004]. A log that
**declares** the format is a self-attestation, so structural violations inside
it — an unsupported version, an unknown telemetry key, a non-list trajectory,
a malformed step — are corruption and raise :class:`GenericLogError` (the
trial fails closed via the scheduler's per-trial door [RN-15]), never silent
nulls: a typo'd ``token_in`` must not launder into "unmeasured".
"""

from __future__ import annotations

from typing import Optional

from pydantic import ValidationError

from ..run.trajectory import TrajectoryStep
from .base import Adapter, Telemetry

GENERIC_LOG_VERSION = 2
SUPPORTED_LOG_VERSIONS = frozenset({1, 2})
VERSION_KEY = "verdi_log_version"


class GenericLogError(ValueError):
    """A log that declared the verdi normalized format violates it.

    Never raised for a log that did not declare the format — that is honest
    absence, not corruption."""


def declared_version(native_log: dict) -> Optional[int]:
    """The log's declared format version.

    ``None`` when the log does not claim the format at all; raises
    :class:`GenericLogError` when it claims a version this parser does not
    speak — mis-parsing a future format as v1 would be silently wrong data.
    """
    v = native_log.get(VERSION_KEY)
    if v is None:
        return None
    # bool is an int subclass: `True == 1` would silently pass as v1
    if isinstance(v, bool) or v not in SUPPORTED_LOG_VERSIONS:
        raise GenericLogError(
            f"{VERSION_KEY} {v!r} is not supported (this parser speaks versions "
            f"{sorted(SUPPORTED_LOG_VERSIONS)}); refusing to guess at another "
            "version's semantics"
        )
    return v


def normalize_generic(native_log: dict) -> Telemetry:
    """Verdi normalized log → :class:`Telemetry`.

    The ``telemetry`` block validates through the :class:`Telemetry` model
    itself, so the schema every ``TrialRecord`` embeds is the format's single
    source of truth: omitted fields are honest nulls, unknown keys are refused
    (``extra="forbid"``), and there is no second field list to drift.
    """
    if declared_version(native_log) is None:
        return Telemetry()
    tel = native_log.get("telemetry")
    if tel is None:
        return Telemetry()  # omitted block: nothing measured, all honest nulls
    try:
        return Telemetry.model_validate(tel)
    except ValidationError as e:
        raise GenericLogError(
            f"telemetry block is not a valid Telemetry object "
            f"(fields are the TrialRecord telemetry fields; omit or null what "
            f"you cannot measure, never guess): {e}"
        ) from e


def normalize_generic_trajectory(native_log: dict) -> Optional[list[TrajectoryStep]]:
    """Verdi normalized log → ordered shared-schema steps.

    Steps validate through :class:`TrajectoryStep` directly — the format *is*
    the shared schema. An absent ``trajectory`` key is the honest no-trajectory
    state (``None``), distinct from an empty step list [EVAL-12 AC-2].
    """
    if declared_version(native_log) is None:
        return None
    raw = native_log.get("trajectory")
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise GenericLogError(
            f"trajectory must be a list of steps, got {type(raw).__name__}; "
            "omit the key entirely for an honestly absent trajectory"
        )
    steps: list[TrajectoryStep] = []
    for i, item in enumerate(raw):
        try:
            steps.append(TrajectoryStep.model_validate(item))
        except ValidationError as e:
            raise GenericLogError(f"trajectory[{i}] is not a valid step: {e}") from e
    return steps


def normalize_generic_by_model(
    native_log: dict, declared_models: list[str]
) -> Optional[dict[str, Telemetry]]:
    """The v2 ``telemetry_by_model`` block → per-model :class:`Telemetry`
    [EVAL-14 AC-2, D002].

    Keys must name models the locked spec declared (EVAL-13's primary + aux
    set) — attributing spend to a model the pre-registration never mentioned
    is a contradiction, refused loudly, not data. Absent block (or a v1 /
    non-verdi log) is honest ``None``. Self-reported attribution: exploratory
    cross-check data only, never the authoritative telemetry stream [AC-4].
    """
    if declared_version(native_log) != 2:
        return None
    raw = native_log.get("telemetry_by_model")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise GenericLogError(
            f"telemetry_by_model must be an object keyed by declared model ids, "
            f"got {type(raw).__name__}"
        )
    undeclared = sorted(set(raw) - set(declared_models))
    if undeclared:
        raise GenericLogError(
            f"telemetry_by_model keys {undeclared} name models the locked spec "
            f"never declared (declared: {declared_models}); attribution to an "
            "unregistered model is a contradiction, not data [EVAL-14 AC-2]"
        )
    out: dict[str, Telemetry] = {}
    for model, block in raw.items():
        try:
            out[model] = Telemetry.model_validate(block)
        except ValidationError as e:
            raise GenericLogError(
                f"telemetry_by_model[{model!r}] is not a valid Telemetry object: {e}"
            ) from e
    return out


def by_model_delta(by_model: dict[str, Telemetry], totals: Telemetry) -> dict[str, float]:
    """Per-field mismatch between by-model sums and the whole-trial totals
    [EVAL-14 AC-4]. Surfaced as a flag, never reconciled in either direction
    (the proxy_cost_delta precedent). A field is only comparable when the
    total is measured AND at least one by-model block measured it; nulls stay
    out of the arithmetic entirely."""
    from .base import TELEMETRY_FIELDS

    delta: dict[str, float] = {}
    for f in TELEMETRY_FIELDS:
        total = getattr(totals, f)
        parts = [getattr(t, f) for t in by_model.values() if getattr(t, f) is not None]
        if total is None or not parts:
            continue
        d = round(sum(parts) - total, 6)
        if d != 0:
            delta[f] = d
    return delta


class GenericAdapter(Adapter):
    """The registered ``generic`` platform: the :class:`Adapter` base defaults
    (which parse the normalized format) with no platform-specific overrides."""

    platform = "generic"
