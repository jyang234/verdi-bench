"""claude-code adapter [EVAL-4 AC-2, Phase-0 spike 3].

Parses claude-code's native result log. Fields it exposes map to telemetry;
anything absent stays ``None`` and lands in ``telemetry_nulls`` — never estimated.
"""

from __future__ import annotations

from typing import Optional

from ..run.trajectory import TrajectoryStep
from .base import Adapter, Telemetry
from .base import coerce_float as _float
from .base import coerce_int as _int

# Closed tool-name table: tools whose invocation IS a file edit. A mechanical
# lookup, not an inference — an unknown tool stays a generic tool_call.
_FILE_EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})


class ClaudeCodeAdapter(Adapter):
    platform = "claude_code"
    speaks_generic_format = False  # native log format; verdi-format keys are inert

    def normalize(self, native_log: dict) -> Telemetry:
        usage = native_log.get("usage") or {}
        duration_ms = native_log.get("duration_ms")
        tool_calls = native_log.get("tool_use_count")
        if tool_calls is None and isinstance(native_log.get("messages"), list):
            # fall back to counting tool_use blocks in the transcript
            tool_calls = sum(
                1
                for m in native_log["messages"]
                for c in (m.get("content") or [])
                if isinstance(c, dict) and c.get("type") == "tool_use"
            )
        return Telemetry(
            tokens_in=_int(usage.get("input_tokens")),
            tokens_out=_int(usage.get("output_tokens")),
            tokens_cache=_int(usage.get("cache_read_input_tokens")),
            cost=_float(native_log.get("total_cost_usd")),
            wall_time_s=(_float(duration_ms) / 1000.0) if duration_ms is not None else None,
            tool_calls=_int(tool_calls),
        )

    def normalize_trajectory(self, native_log: dict) -> Optional[list[TrajectoryStep]]:
        """Message stream → steps [EVAL-12 AC-1].

        claude-code's result log exposes ordered content blocks but no per-step
        timings, token splits, costs, or exit codes — those stay null [D004].
        A ``test_run`` is not natively labeled either, so it is never emitted
        here (labeling one from command text would be estimation, not
        measurement). No ``messages`` key at all ⇒ no trajectory (``None``),
        the honest absent state [AC-2].

        ``command`` [EVAL-11-D005]: a Bash tool_use carries its command string;
        every non-shell step is a measured ``""``; a malformed Bash input is
        null (unmeasurable), never guessed.

        ``detail`` [EVAL-14-D004, v3]: read, never reconstructed — a text
        block's own text for a ``message``; a file-edit tool's input rendered
        verbatim by :func:`_edit_detail` for a ``file_edit``; the paired
        ``tool_result`` content (matched by tool_use id, the log's own join)
        for a ``tool_call``. A shape this table doesn't recognize stays null.
        """
        messages = native_log.get("messages")
        if not isinstance(messages, list):
            return None
        steps: list[TrajectoryStep] = []
        step_by_tool_use: dict[str, int] = {}  # tool_use id → index into steps
        for m in messages:
            if not isinstance(m, dict):
                continue
            for c in m.get("content") or []:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "text":
                    raw_text = c.get("text")
                    steps.append(
                        TrajectoryStep(
                            kind="message",
                            command="",
                            detail=raw_text if isinstance(raw_text, str) else None,
                        )
                    )
                elif c.get("type") == "tool_use":
                    name = c.get("name")
                    tool_input = c.get("input")
                    file_path = (
                        tool_input.get("file_path") if isinstance(tool_input, dict) else None
                    )
                    if name == "Bash":
                        raw_cmd = (
                            tool_input.get("command") if isinstance(tool_input, dict) else None
                        )
                        command = raw_cmd if isinstance(raw_cmd, str) else None
                    else:
                        command = ""
                    is_edit = name in _FILE_EDIT_TOOLS
                    steps.append(
                        TrajectoryStep(
                            kind="file_edit" if is_edit else "tool_call",
                            files_touched=[str(file_path)] if file_path else None,
                            command=command,
                            # an edit's detail is its input (the patch material);
                            # a tool_call's arrives later via its tool_result
                            detail=_edit_detail(name, tool_input) if is_edit else None,
                        )
                    )
                    tool_id = c.get("id")
                    if isinstance(tool_id, str):
                        step_by_tool_use[tool_id] = len(steps) - 1
                elif c.get("type") == "tool_result":
                    idx = step_by_tool_use.get(c.get("tool_use_id"))
                    if idx is not None and steps[idx].detail is None:
                        steps[idx].detail = _result_text(c.get("content"))
        return steps


def _edit_detail(name: Optional[str], tool_input) -> Optional[str]:
    """A file-edit tool's patch material, rendered verbatim from its input.

    Edit/MultiEdit expose old/new string pairs; Write and NotebookEdit expose
    the content being written. The rendering only labels and joins what the
    log carries — malformed input is null, never guessed [D004].
    """
    if not isinstance(tool_input, dict):
        return None
    if name in ("Write", "NotebookEdit"):
        content = tool_input.get("content", tool_input.get("new_source"))
        return content if isinstance(content, str) else None
    edits = tool_input.get("edits") if name == "MultiEdit" else [tool_input]
    if not isinstance(edits, list):
        return None
    blocks: list[str] = []
    for e in edits:
        if not isinstance(e, dict):
            return None
        old, new = e.get("old_string"), e.get("new_string")
        if not (isinstance(old, str) and isinstance(new, str)):
            return None
        blocks.append(f"--- old_string\n{old}\n+++ new_string\n{new}")
    return "\n".join(blocks)


def _result_text(content) -> Optional[str]:
    """A tool_result's textual content: a bare string, or the joined text
    blocks of a content list. Anything else is unmeasurable (null)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [
            b.get("text")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
        ]
        if texts:
            return "\n".join(texts)
    return None
