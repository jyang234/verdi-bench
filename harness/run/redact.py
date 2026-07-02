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

# Text-ish artifacts we scan. Keys leak into config/code/logs of many shapes,
# so the set is broad; obvious binaries (images, archives, compiled) are skipped.
_SCANNED_SUFFIXES = {
    ".txt", ".json", ".jsonl", ".log", ".md", ".patch", ".diff",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".properties",
    ".py", ".sh", ".bash", ".zsh", ".env", ".xml", ".csv", ".tsv", ".html",
    ".js", ".ts", ".go", ".rb", ".java", ".rs", ".sql", ".tf", ".pem", "",
}
_BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".gz", ".tar", ".bin",
    ".so", ".o", ".a", ".class", ".pyc", ".ico", ".woff", ".woff2",
}


def redact_text(text: str, extra_patterns: list[str] | None = None) -> tuple[str, int]:
    return secret_pattern_list(extra_patterns).scrub(text)


def redact_artifacts(artifacts_dir, extra_patterns: list[str] | None = None) -> int:
    """Scrub every scannable file under ``artifacts_dir`` in place.

    Returns the total number of secrets scrubbed. This is the sole write barrier
    between raw capture and persisted artifacts, so it must not silently skip a
    file: non-UTF-8 content is read via latin-1 (a lossless byte↔codepoint map)
    so ASCII key patterns still scrub without corrupting the surrounding bytes.
    """
    artifacts_dir = Path(artifacts_dir)
    patterns = secret_pattern_list(extra_patterns)
    total = 0
    if not artifacts_dir.exists():
        return 0
    for path in artifacts_dir.rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in _BINARY_SUFFIXES or suffix not in _SCANNED_SUFFIXES:
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        text = raw.decode("latin-1")  # bijective for bytes 0-255; never raises
        scrubbed, n = patterns.scrub(text)
        if n:
            path.write_bytes(scrubbed.encode("latin-1"))
            total += n
    return total
