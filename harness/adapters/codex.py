"""codex adapter [EVAL-4 AC-2, Phase-0 spike 3].

Codex's native log exposes token counts and elapsed time but not per-call cache
tokens or a cost figure; those stay ``None`` (→ ``telemetry_nulls``), never
proxy-estimated [D004]. This asymmetry with claude-code is exactly why cross-arm
telemetry comparisons only run on fields both arms measured [EVAL-6 AC-7].
"""

from __future__ import annotations

from typing import Optional

from ..run.trajectory import TrajectoryStep
from .base import Adapter, Telemetry
from .base import coerce_float as _float
from .base import coerce_int as _int


class CodexAdapter(Adapter):
    platform = "codex"
    speaks_generic_format = False  # native log format; verdi-format keys are inert

    def normalize(self, native_log: dict) -> Telemetry:
        usage = native_log.get("token_usage") or {}
        return Telemetry(
            tokens_in=_int(usage.get("prompt_tokens")),
            tokens_out=_int(usage.get("completion_tokens")),
            tokens_cache=None,  # codex does not report cache tokens
            cost=None,          # codex does not report cost
            wall_time_s=_float(native_log.get("elapsed_seconds")),
            tool_calls=_int(native_log.get("tool_calls")),
        )

    def normalize_trajectory(self, native_log: dict) -> Optional[list[TrajectoryStep]]:
        """Event list → steps [EVAL-12 AC-1].

        Codex's native log labels each event (``message`` / ``patch`` /
        ``exec``) and — unlike claude-code — carries per-event elapsed offsets
        and exit codes, so those are measured here; per-step tokens and cost it
        does not report, so those stay null [D004] — the same per-field
        asymmetry as its null telemetry cost. An ``exec`` event is a
        ``test_run`` only when codex's own command classifier says so
        (``parsed_cmd == "test"``); anything else is a generic ``tool_call`` —
        the classification is read from the native log, never inferred here.
        No ``events`` key at all ⇒ no trajectory (``None``) [AC-2].

        ``command`` [EVAL-11-D005]: an ``exec`` event's raw ``cmd`` string when
        present; non-exec steps are a measured ``""``; an exec without a string
        ``cmd`` is null (unmeasurable).
        """
        events = native_log.get("events")
        if not isinstance(events, list):
            return None
        steps: list[TrajectoryStep] = []
        for ev in events:
            if not isinstance(ev, dict):
                continue
            etype = ev.get("type")
            rel = _float(ev.get("elapsed_s"))
            if etype == "message":
                steps.append(TrajectoryStep(kind="message", relative_ts=rel, command=""))
            elif etype == "patch":
                files = ev.get("files")
                steps.append(
                    TrajectoryStep(
                        kind="file_edit",
                        relative_ts=rel,
                        # a list is a measurement — a measured-empty patch stays
                        # [] [D004]; any other shape is unmeasurable, never
                        # iterated on faith (a bare string would shred into
                        # characters)
                        files_touched=(
                            [str(f) for f in files] if isinstance(files, list) else None
                        ),
                        command="",
                    )
                )
            elif etype == "exec":
                raw_cmd = ev.get("cmd")
                steps.append(
                    TrajectoryStep(
                        kind="test_run" if ev.get("parsed_cmd") == "test" else "tool_call",
                        relative_ts=rel,
                        exit_code=_int(ev.get("exit_code")),
                        command=raw_cmd if isinstance(raw_cmd, str) else None,
                    )
                )
        return steps
