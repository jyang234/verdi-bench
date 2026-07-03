"""``bench analyze`` [EVAL-6 §M6].

Asserts the experiment lock, computes findings as a pure function of
``(ledger, seed)``, renders official or exploratory, and writes only the
findings output plus a single ``findings_rendered`` provenance event. Official
renders refuse off-registration metrics and an un-calibrated corpus; exploratory
renders carry the watermark on every section.
"""

from __future__ import annotations

import getpass
import hashlib
from pathlib import Path

import typer


def _actor() -> str:
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover
        return "unknown"


def register(app: typer.Typer) -> None:
    @app.command()
    def analyze(
        experiment_dir: Path = typer.Argument(..., help="Directory with experiment.yaml"),
        official: bool = typer.Option(False, "--official", help="Official render (fenced)"),
        exploratory: bool = typer.Option(
            False, "--exploratory", help="Exploratory render (watermarked)"
        ),
        corpus: Path = typer.Option(
            None, "--corpus", help="Corpus manifest.json for provenance + calibration gate"
        ),
        html: bool = typer.Option(False, "--html", help="Render HTML instead of markdown"),
    ) -> None:
        """Render pre-registered official or exploratory findings."""
        from ..ledger.events import (
            EventContext,
            record_cant_analyze,
            record_findings_rendered,
        )
        from ..plan.lock import assert_lock
        from ..schema.experiment import ExperimentSpec
        from .report import (
            AnalyzeError,
            cant_analyze_reason,
            compute_findings,
            render_html,
            render_markdown,
        )

        if official and exploratory:
            raise typer.BadParameter("choose at most one of --official/--exploratory")
        mode = "official" if official else "exploratory"

        experiment_dir = Path(experiment_dir)
        spec_path = experiment_dir / "experiment.yaml"
        ledger_path = experiment_dir / "ledger.ndjson"
        assert_lock(spec_path, ledger_path)
        spec = ExperimentSpec.from_yaml(spec_path)
        ctx = EventContext(experiment_id=experiment_dir.name, actor=_actor())

        manifest = None
        if corpus is not None:
            from ..corpus.registry import CorpusManifest

            manifest = CorpusManifest.load(corpus)

        # AN-3: a refused render (calibration incomplete, provenance invalid,
        # disclosure missing, unregistered metric) must land exactly one
        # cant_analyze event, never escape the CLI with zero events.
        try:
            findings = compute_findings(ledger_path, spec, spec.seed, corpus_manifest=manifest)
            renderer = render_html if html else render_markdown
            rendered = renderer(findings, ledger_path, mode, corpus_manifest=manifest)
        except AnalyzeError as e:
            record_cant_analyze(
                ledger_path, ctx, mode=mode, reason=cant_analyze_reason(e).value, detail=str(e)
            )
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)

        suffix = "html" if html else "md"
        out_json = experiment_dir / "findings.json"
        out_render = experiment_dir / f"findings.{mode}.{suffix}"
        findings_json = findings.model_dump_json()

        # AN-3: ledger the render *before* writing the findings files, so an
        # interrupted write leaves a provenance record (the render is a pure
        # function of (ledger, seed), re-derivable) rather than orphan files with
        # no event.
        record_findings_rendered(
            ledger_path,
            ctx,
            mode=mode,
            primary_metric=findings.primary_metric,
            ledger_head_hash=findings.provenance.ledger_head_hash,
            findings_sha256=hashlib.sha256(findings_json.encode("utf-8")).hexdigest(),
        )
        out_json.write_text(findings_json, encoding="utf-8")
        out_render.write_text(rendered, encoding="utf-8")
        typer.echo(f"rendered {mode} findings → {out_render}")
