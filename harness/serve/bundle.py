"""Static bundle export [EVAL-19 AC-1, D001].

The operator view in archive form: one self-contained HTML file — the same
``OPERATOR_PAGE`` document with its data embedded where the live page has
``const BUNDLE = null;`` — that opens from the filesystem with no server. It
is a snapshot with provenance, not a live view: the chain verdict, status,
events, trial details, compare payload, and fence report are the read seams'
outputs at bundle time, so two bundles over the same (ledger, artifacts)
input are byte-identical and comparable. Bundling is a pure read — it
appends no event and mutates nothing in the experiment directory.

The needle property carries to the archive: the embedded JSON is
ASCII-serialized and then *inerted* — each substring that a self-containment
scan treats as an external or active reference is rewritten with a
``\\uXXXX`` escape for one of its characters. ``JSON.parse`` restores the
original strings (the page renders them via ``textContent`` as data), but
the document bytes carry no ``http://``, ``href=``, ``<`` … — an archived
egress attempt is evidence to display, never a link to follow.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..analyze.fence import official_fence_report
from ..analyze.timeline import trial_timeline
from ..ledger import events
from ..ledger.query import find_events, tail_events
from ..status.aggregate import compute_status
from ..status.trial import trial_detail
from .compare import paired_comparisons
from .page import OPERATOR_PAGE
from .workspace import _summary_row

# The one-line seam the live page reserves for the archive [page.py docstring].
_BUNDLE_MARKER = "const BUNDLE = null;"

# (needle, inert form): the escape targets one character, so the literal
# needle never appears in the bundle bytes while JSON.parse round-trips the
# original string. Order is irrelevant: no needle is a substring of another
# needle's replacement, and no replacement output contains a needle.
_INERT: tuple[tuple[str, str], ...] = (
    ("https://", "https:\\u002f/"),
    ("http://", "http:\\u002f/"),
    ("src=", "sr\\u0063="),
    ("href=", "hre\\u0066="),
    ("url(", "ur\\u006c("),
    ("@import", "@impor\\u0074"),
    ("<", "\\u003c"),
)


class BundleError(RuntimeError):
    """The bundle could not be written safely — e.g. the page's replacement
    seam is missing or ambiguous. Refused loudly, never a best-effort file."""


def collect_bundle_data(experiment_dir, *, corpus_manifest=None) -> dict:
    """Every read the live page can ask for, gathered once [AC-1].

    Pure function of the experiment directory's file state: status, the full
    event tail with its cursor, the timeline, per-trial detail for every
    trial on the ledger, the compare payload, and the fence report — the
    exact payloads the corresponding ``/api/…`` routes would serve.
    """
    experiment_dir = Path(experiment_dir)
    ledger_path = experiment_dir / "ledger.ndjson"
    status = compute_status(experiment_dir)
    tail, next_offset = tail_events(ledger_path, 0)
    trial_ids = [
        ev["trial_record"]["trial_id"] for ev in find_events(ledger_path, events.TRIAL)
    ]
    return {
        "experiment": experiment_dir.name,
        "experiments": [_summary_row(experiment_dir.name, status)],
        "status": status,
        "events": tail,
        "next_offset": next_offset,
        "timeline": trial_timeline(ledger_path),
        "trials": {tid: trial_detail(experiment_dir, tid) for tid in trial_ids},
        "compare": paired_comparisons(experiment_dir, corpus_manifest=corpus_manifest),
        "fence": official_fence_report(experiment_dir, corpus_manifest=corpus_manifest),
    }


def _inert_json(data: dict) -> str:
    """Canonical, ASCII, needle-free serialization of the bundle data.

    ``sort_keys`` + fixed separators make the blob deterministic;
    ``ensure_ascii`` guarantees the inerting replacements operate on pure
    ASCII. Every replaced substring can only occur inside a JSON string
    (none is valid JSON syntax), so the ``\\uXXXX`` escapes stay legal.
    """
    blob = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    for needle, inert in _INERT:
        blob = blob.replace(needle, inert)
    return blob


def write_bundle(experiment_dir, out_path, *, corpus_manifest=None) -> Path:
    """Write the self-contained snapshot; returns the written path [AC-1].

    Byte-deterministic for a fixed (ledger, artifacts) input, and a pure
    read of ``experiment_dir`` — the only write is ``out_path``.
    """
    if OPERATOR_PAGE.count(_BUNDLE_MARKER) != 1:
        raise BundleError(
            f"the operator page must contain the bundle seam {_BUNDLE_MARKER!r} "
            f"exactly once (found {OPERATOR_PAGE.count(_BUNDLE_MARKER)}); "
            "refusing to write a bundle that would not embed its data"
        )
    data = collect_bundle_data(experiment_dir, corpus_manifest=corpus_manifest)
    html = OPERATOR_PAGE.replace(_BUNDLE_MARKER, "const BUNDLE = " + _inert_json(data) + ";")
    out = Path(out_path)
    out.write_text(html, encoding="utf-8")
    return out
