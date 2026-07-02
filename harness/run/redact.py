"""Secret redaction at artifact capture [EVAL-4 §M4, AC-8, D004].

Runs over transcripts/logs before anything is written to ``artifacts/<trial>/``.
Uses the shared pattern-list mechanism from ``harness/blind/core.py`` but with
the **secrets** list (kept separate from identity blinding — secrets ≠ identity).
Known key patterns are scrubbed in place. EVAL-9 AC-4 assumes redaction happened
here, upstream of every scorer.
"""

from __future__ import annotations

from pathlib import Path

from ..blind.core import secret_pattern_list

# text-ish artifacts we scan; binaries are left alone
_SCANNED_SUFFIXES = {".txt", ".json", ".log", ".md", ".jsonl", ".patch", ".diff", ""}


def redact_text(text: str, extra_patterns: list[str] | None = None) -> tuple[str, int]:
    return secret_pattern_list(extra_patterns).scrub(text)


def redact_artifacts(artifacts_dir, extra_patterns: list[str] | None = None) -> int:
    """Scrub every scannable file under ``artifacts_dir`` in place.

    Returns the total number of secrets scrubbed. This is the sole write barrier
    between raw capture and persisted artifacts.
    """
    artifacts_dir = Path(artifacts_dir)
    patterns = secret_pattern_list(extra_patterns)
    total = 0
    if not artifacts_dir.exists():
        return 0
    for path in artifacts_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _SCANNED_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        scrubbed, n = patterns.scrub(text)
        if n:
            path.write_text(scrubbed, encoding="utf-8")
            total += n
    return total
