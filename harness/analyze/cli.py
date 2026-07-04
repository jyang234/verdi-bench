"""``bench analyze`` [EVAL-6 §M6].

Asserts the experiment lock, computes findings as a pure function of
``(ledger, seed)``, renders official or exploratory, and writes only the
findings outputs plus a single ``findings_rendered`` provenance event. The
self-contained comparison dossier (``findings.<mode>.dossier.html``) rides the
same invocation as an additional artifact behind the same fence — no new verb,
no extra event [EVAL-12 AC-7, D004]. Official renders refuse off-registration
metrics and an un-calibrated corpus; exploratory renders carry the watermark on
every section.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import typer


def run_analyze(experiment_dir, *, mode: str, corpus=None, html: bool = False, actor: str = "unknown"):
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
    from ..schema.experiment import ExperimentSpec
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
    assert_lock(spec_path, ledger_path)
    spec = ExperimentSpec.from_yaml(spec_path)
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
    from ..schema.experiment import ExperimentSpec
    from .selfcheck import run_selfcheck

    experiment_dir = Path(experiment_dir)
    spec_path = experiment_dir / "experiment.yaml"
    ledger_path = experiment_dir / "ledger.ndjson"
    assert_lock(spec_path, ledger_path)
    spec = ExperimentSpec.from_yaml(spec_path)
    ctx = EventContext(experiment_id=experiment_dir.name, actor=actor)
    result = run_selfcheck(ledger_path, spec, n_sim=n_sim, n_boot=n_boot)
    record_selfcheck(ledger_path, ctx, **result)
    return result


def register(app: typer.Typer) -> None:
    @app.command()
    def selfcheck(
        experiment_dir: Path = typer.Argument(..., help="Directory with experiment.yaml"),
        actor: str = typer.Option(None, "--actor", help="Actor recorded on the selfcheck event [GR-12]"),
    ) -> None:
        """Run the D008 coverage selfcheck; official render requires it to pass."""
        from ..ledger.actor import ActorResolutionError, resolve_actor

        try:
            resolved_actor = resolve_actor(actor)
        except ActorResolutionError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)
        result = run_selfcheck_cli(experiment_dir, actor=resolved_actor)
        status = "PASS" if result["passed"] else "FAIL"
        typer.echo(
            f"selfcheck {status}: method={result['selected_method']} "
            f"coverage={result['coverage']} nominal={result['nominal']} "
            f"mc_interval={result['mc_interval']} null_model={result['null_model']}"
        )
        if not result["passed"]:
            typer.echo(
                "the experiment is exploratory-only until the selfcheck passes "
                "(official render refused)", err=True,
            )

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
        actor: str = typer.Option(
            None, "--actor", help="Actor recorded on the findings event [GR-12]"
        ),
    ) -> None:
        """Render pre-registered official or exploratory findings."""
        from ..ledger.actor import ActorResolutionError, resolve_actor

        if official and exploratory:
            raise typer.BadParameter("choose at most one of --official/--exploratory")
        mode = "official" if official else "exploratory"
        try:
            resolved_actor = resolve_actor(actor)
        except ActorResolutionError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)
        out = run_analyze(experiment_dir, mode=mode, corpus=corpus, html=html, actor=resolved_actor)
        if out is None:
            typer.echo(f"refused {mode} render; recorded cant_analyze", err=True)
            raise typer.Exit(code=2)
        typer.echo(f"rendered {mode} findings → {out}")


# --- one-event property registration [EVAL-3 §M7, XC-3] --------------------
def _prepare_analyze(ctx_dir: str) -> None:
    # lock the experiment so the render has a spec to analyze (one event, seeded
    # before the sweep snapshots the count).
    from ..ledger.events import EventContext
    from ..plan.lock import lock_experiment

    d = Path(ctx_dir)
    lock_experiment(
        d / "experiment.yaml", d / "ledger.ndjson",
        ctx=EventContext(experiment_id="prop"), n_sim=8, n_boot=40, deltas=[0.2, 0.4],
    )


def _analyze_entrypoint(ctx_dir: str) -> None:
    # An official render with no calibrated corpus fails closed to exactly one
    # cant_analyze event (the AN-3 path).
    run_analyze(Path(ctx_dir), mode="official", corpus=None, actor="prop")


def _selfcheck_entrypoint(ctx_dir: str) -> None:
    # With no trials the selfcheck is insufficient_data (n<2 clusters) and lands
    # exactly one additive `selfcheck` event — the one-event property [7I-1].
    run_selfcheck_cli(Path(ctx_dir), actor="prop", n_sim=8, n_boot=40)


def _register() -> None:
    from ..entrypoints import register_entrypoint

    register_entrypoint("analyze", _analyze_entrypoint, prepare=_prepare_analyze)
    register_entrypoint("selfcheck", _selfcheck_entrypoint, prepare=_prepare_analyze)


_register()
