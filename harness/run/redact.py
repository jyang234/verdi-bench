"""Secret redaction at artifact capture [EVAL-4 §M4, AC-8, D004].

Runs over the trial workspace before anything persists downstream. Uses the
shared pattern-list mechanism from ``harness/blind/core.py`` but with the
**secrets** list (kept separate from identity blinding — secrets ≠ identity).
Known key patterns are scrubbed in place. EVAL-9 AC-4 assumes redaction happened
here, upstream of every scorer.

Scanning policy [RN-6]: **scan every file except a small denylist of known
binary types**. A suffix *allowlist* fails open — a key in a ``.bak`` /
``.env.local`` / ``.tsx`` file, or one with a suffix the allowlist forgot,
persists verbatim. Over-scanning a binary is harmless (the latin-1 decode never
raises); under-scanning a secret is not. An unreadable file is a loud failure,
never a silent skip [RN-16].
"""

from __future__ import annotations

from pathlib import Path

from ..blind.core import secret_pattern_list

# Known-binary suffixes we skip — scanning them cannot surface a text key and
# only wastes IO. Everything else is scanned: the default is to scan, not skip.
_BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".pdf",
    ".zip", ".gz", ".bz2", ".xz", ".tar", ".7z", ".rar",
    ".bin", ".so", ".o", ".a", ".dylib", ".class", ".pyc", ".pyo",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp3", ".mp4", ".mov", ".avi", ".wav", ".webp", ".webm",
    ".jar", ".war", ".wasm",
}


class RedactionError(RuntimeError):
    """A file under the redaction root could not be read [RN-16].

    Redaction is the sole write barrier between raw capture and persisted
    artifacts, so an unreadable file is surfaced loudly — never a silent skip
    that would let an un-scanned artifact through the barrier."""


def redact_text(text: str, extra_patterns: list[str] | None = None) -> tuple[str, int]:
    return secret_pattern_list(extra_patterns).scrub(text)


def redact_artifacts(artifacts_dir, extra_patterns: list[str] | None = None) -> int:
    """Scrub every scannable file under ``artifacts_dir`` in place.

    Returns the total number of secrets scrubbed. Files are visited in sorted
    order so the count is deterministic. Non-UTF-8 content is read via latin-1
    (a lossless byte↔codepoint map) so ASCII key patterns still scrub without
    corrupting the surrounding bytes. Symlinks are not followed (an
    agent-controlled workspace must not redirect the barrier outside the tree);
    an unreadable regular file raises :class:`RedactionError` [RN-16].
    """
    artifacts_dir = Path(artifacts_dir)
    patterns = secret_pattern_list(extra_patterns)
    total = 0
    if not artifacts_dir.exists():
        return 0
    for path in sorted(artifacts_dir.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        if path.suffix.lower() in _BINARY_SUFFIXES:
            continue
        try:
            raw = path.read_bytes()
        except OSError as e:
            raise RedactionError(f"could not read {path} for redaction: {e}") from e
        text = raw.decode("latin-1")  # bijective for bytes 0-255; never raises
        scrubbed, n = patterns.scrub(text)
        if n:
            path.write_bytes(scrubbed.encode("latin-1"))
            total += n
    return total
