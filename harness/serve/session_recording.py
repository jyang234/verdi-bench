"""Captured claude-session transcript → operator feed [flight-recorder charter].

A ``platform: claude_code`` arm exposes no verdi-format trajectory or reasoning
(its adapter honestly yields ``None`` for both), but the CLI's full session is
captured verbatim under ``<artifacts_path>/claude-session/**/*.jsonl`` — one
JSONL object per event. This module is the serve-tier presentation adapter that
turns that raw capture into an ordered feed the operator page can render, so the
richer evidence on disk stops being invisible in the flight-recorder section.

It is deliberately a *serve* concern, not a status/ledger one: it reads only
operator-tier artifacts and renders them unblinded, exactly like the compare
screen's reasoning column [EVAL-24 AC-5]. It imports nothing beyond the stdlib —
the observability-LLM-free contract [EVAL-13 AC-7] stays trivially green.

Two seams:

* :func:`normalize_session` — the pure core. A list of ``(label, text)`` sources
  (already read into memory, in the order to concatenate) becomes a payload of
  per-file feed entries. Every ``detail`` is capped (a runaway tool result can
  never swamp the page), the entry count is capped across all files, and an
  unparseable JSONL line is *counted* and skipped, never crashed on and never
  silently dropped. Pure over its inputs, so it is exhaustively unit-testable.
* :func:`load_session_recording` — the thin impure loader that finds the
  ``*.jsonl`` files under a trial's ``artifacts_path`` (ledger-derived, never
  client input [PRA-M10]), reads them in sorted path order, and calls the core.

A feed entry is ``{kind: "message"|"tool_use"|"tool_result", role, name?,
detail}``. Content blocks map as: ``text``/``thinking`` → a ``message`` entry
(the thinking span is the agent's reasoning — surfaced, not dropped, per the
charter's max-observability mandate), ``tool_use`` → a ``tool_use`` entry naming
the tool, ``tool_result`` → a ``tool_result`` entry. Any other block kind, and
any event whose ``type`` is not ``user``/``assistant``, is skipped without
error (transcripts carry ``ai-title``, ``attachment``, ``queue-operation`` … —
none are feed rows).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional

# The subdirectory (under a trial's artifacts_path) the claude_code capture
# writes its verbatim session transcript into.
SESSION_DIRNAME = "claude-session"

# Per-entry ``detail`` character budget: a single tool result or reasoning span
# that dumps a whole file can otherwise dominate the feed. Truncation is marked
# in-band with the elided count, so the operator sees the body was cut and by
# how much (~ because the marker itself adds a few characters).
DEFAULT_DETAIL_CAP = 2000

# Total feed-entry budget across all concatenated files. Beyond it entries are
# counted into ``more_entries`` (the page renders "… N more entries"), never
# appended — a 10k-turn transcript stays a bounded payload.
DEFAULT_ENTRY_CAP = 500

_MESSAGE_EVENT_TYPES = frozenset({"user", "assistant"})


def _cap_detail(text: str, cap: int) -> str:
    """Cap ``text`` to ``cap`` characters, marking any truncation with the count
    of elided characters (in-band, so a plain textContent render shows it)."""
    if len(text) <= cap:
        return text
    elided = len(text) - cap
    return text[:cap] + f"\n[… {elided} chars elided]"


def _entry(kind: str, role: str, detail: str, cap: int, name: Optional[str] = None) -> dict:
    entry = {"kind": kind, "role": role, "detail": _cap_detail(detail, cap)}
    if name is not None:
        entry["name"] = name
    return entry


def _stringify_tool_input(value: object) -> str:
    """Render a tool_use ``input`` compactly and deterministically. A dict is
    canonical JSON (sorted keys — no dict-ordering assumption); a string passes
    through; anything else is stringified rather than dropped."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _stringify_tool_result(content: object) -> str:
    """Render a tool_result ``content`` — a string, or a list of blocks (the
    ``{"type": "text", "text": ...}`` shape, plus any others json-dumped), or a
    fallback stringification. Never raises."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, dict):
                parts.append(json.dumps(block, sort_keys=True, ensure_ascii=False))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    try:
        return json.dumps(content, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(content)


def _entries_for_event(event: dict, cap: int) -> list[dict]:
    """The feed entries one JSONL event contributes — empty for any event that
    is not a user/assistant message (skipped without error)."""
    if event.get("type") not in _MESSAGE_EVENT_TYPES:
        return []
    message = event.get("message")
    if not isinstance(message, dict):
        return []
    role = message.get("role") or event.get("type")
    content = message.get("content")
    if isinstance(content, str):
        return [_entry("message", role, content, cap)]
    if not isinstance(content, list):
        return []
    out: list[dict] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            out.append(_entry("message", role, str(block.get("text", "")), cap))
        elif btype == "thinking":
            out.append(_entry("message", role, str(block.get("thinking", "")), cap))
        elif btype == "tool_use":
            out.append(_entry("tool_use", role, _stringify_tool_input(block.get("input")),
                              cap, name=block.get("name")))
        elif btype == "tool_result":
            out.append(_entry("tool_result", role, _stringify_tool_result(block.get("content")), cap))
        # any other block kind (image, redacted_thinking, …) is not a feed row
    return out


def normalize_session(
    sources: Iterable[tuple[str, str]],
    *,
    detail_cap: int = DEFAULT_DETAIL_CAP,
    entry_cap: int = DEFAULT_ENTRY_CAP,
) -> dict:
    """Pure: ordered ``(label, text)`` sources → the session-recording payload.

    ``sources`` is the already-read transcript files in concatenation order. The
    result is::

        {"files": [{"label", "entries": [...], "skipped_lines"}],
         "skipped_lines": <total>, "more_entries": <capped-off>, "entry_count"}

    The entry cap is global across files: once ``entry_cap`` entries have been
    emitted, further ones are counted into ``more_entries`` rather than appended
    — but every line is still parsed so ``skipped_lines`` stays honest past the
    cap. A blank/whitespace-only line is not "unparseable"; only a line that
    fails :func:`json.loads` counts as skipped.
    """
    files_out: list[dict] = []
    total_skipped = 0
    total_entries = 0
    more_entries = 0
    for label, text in sources:
        entries_out: list[dict] = []
        skipped = 0
        for raw_line in text.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:  # json.JSONDecodeError is a ValueError subclass
                skipped += 1
                continue
            if not isinstance(event, dict):
                continue  # valid JSON but not an event object → not a feed row
            for entry in _entries_for_event(event, detail_cap):
                if total_entries < entry_cap:
                    entries_out.append(entry)
                    total_entries += 1
                else:
                    more_entries += 1
        total_skipped += skipped
        files_out.append({"label": label, "entries": entries_out, "skipped_lines": skipped})
    return {
        "files": files_out,
        "skipped_lines": total_skipped,
        "more_entries": more_entries,
        "entry_count": total_entries,
    }


def load_session_recording(
    artifacts_path: object,
    *,
    detail_cap: int = DEFAULT_DETAIL_CAP,
    entry_cap: int = DEFAULT_ENTRY_CAP,
) -> Optional[dict]:
    """Load and normalize a trial's captured session, or ``None`` when it has
    none — honest absence, so a trial without a transcript is untouched.

    ``artifacts_path`` is the trial's LEDGER ``artifacts_path`` (never client
    input): only ``*.jsonl`` files under its ``claude-session`` subtree are read,
    in sorted path order, each labeled by its path relative to that subtree. A
    genuinely unreadable file raises (the served-500 / fail-loud path) rather
    than being silently dropped; malformed UTF-8 is tolerated in-band via
    ``errors="replace"`` so bad bytes surface as replacement characters.
    """
    if not artifacts_path:
        return None
    base = Path(artifacts_path) / SESSION_DIRNAME
    if not base.is_dir():
        return None
    files = sorted(base.rglob("*.jsonl"))
    if not files:
        return None
    sources = [
        (path.relative_to(base).as_posix(), path.read_text(encoding="utf-8", errors="replace"))
        for path in files
    ]
    return normalize_session(sources, detail_cap=detail_cap, entry_cap=entry_cap)


def attach_session_recording(detail: dict) -> dict:
    """Add a ``session_recording`` field to a ``trial_detail`` payload when the
    trial captured one — mutating and returning ``detail`` in place.

    The artifacts path is read from the ledgered ``record`` (``trial_detail``
    already resolved it from the chain), keeping client input trial-id-only. A
    trial without a captured session is left byte-for-byte as it was. Shared by
    the ``/api/trial`` route and the static bundle so both stay route-identical.
    """
    record = detail.get("record") or {}
    recording = load_session_recording(record.get("artifacts_path"))
    if recording is not None:
        detail["session_recording"] = recording
    return detail
