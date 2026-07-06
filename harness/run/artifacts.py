"""Run-side artifact reading [refactor 06 §8].

A trial's post-redaction transcript (``<artifacts>/transcript.txt``) is a
run-side artifact; the forensics scan and the process scorer both consume it, so
the reader lives here once instead of duplicated in each consumer.
"""

from __future__ import annotations

from pathlib import Path


def read_transcript(artifacts_path) -> str:
    """The trial's post-redaction transcript, or ``""`` if absent.

    An absent transcript is honest emptiness — a downstream scorer fails closed
    to CANT (``CANT_REVIEW``/``CANT_SCORE``) on it, never a fabricated review.
    ``errors="replace"`` decodes redacted bytes; a genuine read error raises
    rather than being swallowed into fake evidence.
    """
    if not artifacts_path:
        return ""
    p = Path(artifacts_path) / "transcript.txt"
    if not p.is_file():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")
