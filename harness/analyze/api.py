"""``analyze`` stage API [refactor 02 §3].

The importable entry points behind ``bench analyze`` / ``bench selfcheck`` /
``bench card`` [EVAL-6 §M6]: compute findings as a pure function of
``(ledger, seed)``, render official or exploratory behind the fence, run the
D008 coverage selfcheck, and project the result card. The typer verbs
(``harness/analyze/cli.py``) are thin shells over these — argument parsing,
actor resolution, refusal→exit mapping, echo. ``run_analyze`` /
``run_selfcheck_cli`` keep their names (re-exported from the CLI for the
existing analyze tests).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


def run_analyze(experiment_dir, *, mode: str, corpus=None, html: bool = False,
                actor: str = "unknown"):
    """Render findings, ledgering exactly one event either way [EVAL-6 §M6, AN-3].

    Returns the render path on success (after emitting ``findings_rendered``), or
    ``None`` on a fail-closed refusal (after emitting exactly one ``cant_analyze``).
    A refused render never escapes with zero events, and on success the event is
    written *before* the findings files (re-derivable render, no orphan artifacts).
    """
    from ..ledger.events import (
        EventContext,
        record_cant_analyze,
        record_findings_rendered,
    )
    from ..plan.lock import assert_lock
    from .dossier import render_dossier
    from .report import (
        AnalyzeError,
        cant_analyze_reason,
        compute_findings,
        render_html,
        render_markdown,
    )

    experiment_dir = Path(experiment_dir)
    spec_path = experiment_dir / "experiment.yaml"
    ledger_path = experiment_dir / "ledger.ndjson"
    spec = assert_lock(spec_path, ledger_path).spec  # PRA-M1: no second spec read
    ctx = EventContext(experiment_id=experiment_dir.name, actor=actor)

    # AN-3: a refused render lands exactly one cant_analyze event, never escapes.
    # The corpus-manifest load is inside the envelope too, so a malformed --corpus
    # fails closed to cant_analyze rather than escaping with no event.
    try:
        manifest = None
        if corpus is not None:
            from ..corpus.registry import CorpusManifest

            try:
                manifest = CorpusManifest.load(corpus)
            except Exception as e:  # bad path / malformed manifest JSON
                raise AnalyzeError(f"could not load corpus manifest {corpus}: {e}") from e
        # F-H7: the multi-arm decision policy comes from the sha-locked spec —
        # there is deliberately no analyze-time knob for it.
        findings = compute_findings(ledger_path, spec, spec.seed, corpus_manifest=manifest)
        renderer = render_html if html else render_markdown
        rendered = renderer(findings, ledger_path, mode, corpus_manifest=manifest)
        # EVAL-12 AC-7/D004: the dossier rides the same invocation as a third
        # artifact — same fence (render_dossier delegates to render_markdown's
        # validators), same single findings_rendered event, no new verb.
        dossier = render_dossier(findings, ledger_path, mode, corpus_manifest=manifest)
    except AnalyzeError as e:
        record_cant_analyze(
            ledger_path, ctx, mode=mode, reason=cant_analyze_reason(e).value, detail=str(e)
        )
        return None

    suffix = "html" if html else "md"
    out_json = experiment_dir / "findings.json"
    out_render = experiment_dir / f"findings.{mode}.{suffix}"
    # F-L7: stamp the mode (and the exploratory watermark) into the JSON
    # artifact itself BEFORE hashing, so the citable bytes are unambiguous.
    findings.mode = mode
    if mode != "official":
        from .report import _WATERMARK

        findings.watermark = _WATERMARK
    findings_json = findings.model_dump_json()

    # AN-3: ledger the render *before* writing the files, so an interrupted write
    # leaves a re-derivable provenance record rather than orphan artifacts.
    record_findings_rendered(
        ledger_path,
        ctx,
        mode=mode,
        primary_metric=findings.primary_metric,
        ledger_head_hash=findings.provenance.ledger_head_hash,
        findings_sha256=hashlib.sha256(findings_json.encode("utf-8")).hexdigest(),
        # F-H7: the APPLIED policy — "none" when there is no multi-arm family
        # (n_pairs == 1), the spec-locked value otherwise.
        multi_arm_correction=(findings.multi_arm or {}).get("correction", "none"),
    )
    out_json.write_text(findings_json, encoding="utf-8")
    out_render.write_text(rendered, encoding="utf-8")
    (experiment_dir / f"findings.{mode}.dossier.html").write_text(dossier, encoding="utf-8")
    return out_render


def run_selfcheck_cli(experiment_dir, *, actor: str = "unknown", n_sim: int = 200,
                      n_boot: int = 10_000) -> dict:
    """Compute + ledger the D008 coverage selfcheck; return the result [7I-1].

    Appends exactly one additive ``selfcheck`` event. The seed derives from the
    locked spec seed, so the check is deterministic and cannot be re-rolled."""
    from ..ledger.events import EventContext, record_selfcheck
    from ..plan.lock import assert_lock
    from .selfcheck import run_selfcheck

    experiment_dir = Path(experiment_dir)
    spec_path = experiment_dir / "experiment.yaml"
    ledger_path = experiment_dir / "ledger.ndjson"
    spec = assert_lock(spec_path, ledger_path).spec  # PRA-M1: no second spec read
    ctx = EventContext(experiment_id=experiment_dir.name, actor=actor)
    result = run_selfcheck(ledger_path, spec, n_sim=n_sim, n_boot=n_boot)
    record_selfcheck(ledger_path, ctx, **result)
    return result


@dataclass(frozen=True)
class CardEmitOutcome:
    """A rendered result card: the serialized ``text`` plus the battery identity
    the CLI names when it writes the card to a file."""

    text: str
    battery_sha: str
    battery_basis: str


def emit_card(experiment_dir, *, corpus=None, fmt: str = "json", out=None) -> CardEmitOutcome:
    """Build + render a benchmark result card — a read-only projection of an
    analyzed run. Requires a prior ``bench analyze``. Raises ``CardError`` (the
    CLI maps to exit 2); writes the card to ``out`` when given."""
    from ..corpus.commit import load_task_dicts
    from ..plan.lock import assert_lock
    from .card import (
        build_card, render_card_html, render_card_markdown, serialize_card,
    )

    experiment_dir = Path(experiment_dir)
    spec = assert_lock(
        experiment_dir / "experiment.yaml", experiment_dir / "ledger.ndjson"
    ).spec
    manifest = None
    if corpus is not None:
        from ..corpus.registry import CorpusManifest

        manifest = CorpusManifest.load(corpus)
    task_ids = [t["id"] for t in load_task_dicts(experiment_dir)]
    card = build_card(
        experiment_dir / "ledger.ndjson", spec,
        task_ids=task_ids, corpus_manifest=manifest,
    )
    text = (
        serialize_card(card) if fmt == "json"
        else render_card_markdown(card) if fmt == "md"
        else render_card_html(card)
    )
    if out is not None:
        Path(out).write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    b = card.battery  # typed ResultCard [refactor 07 §5]
    return CardEmitOutcome(text=text, battery_sha=b["battery_sha"], battery_basis=b["battery_basis"])


def compare_card_files(card_a, card_b) -> dict:
    """Compare two card json files; refuse loudly (``CardError``, the CLI maps to
    exit 2) if they graded different task sets/metrics."""
    import json as _json

    from .card import compare_cards

    a = _json.loads(Path(card_a).read_text(encoding="utf-8"))
    b = _json.loads(Path(card_b).read_text(encoding="utf-8"))
    return compare_cards(a, b)
