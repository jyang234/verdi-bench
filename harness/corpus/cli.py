"""``bench corpus …`` [EVAL-8 §M6].

Functional against fixtures: ``import`` (public dataset → cache + manifest),
``subset`` (stratified calibration selection), ``mine`` (MR → pending
candidate), ``review`` (surface prompt + holdouts + diff for the curator), and
``approve`` (ledger a ``curation_approval``). The review view exists to make
curation real — solution-leakage in the prompt is caught here, not by a machine
[risks §9].
"""

from __future__ import annotations

import getpass
import json
from pathlib import Path

import typer


def _actor() -> str:
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover - unusual environments
        return "unknown"


def register(app: typer.Typer) -> None:
    corpus_app = typer.Typer(help="Task corpus tooling [EVAL-8].", no_args_is_help=True)

    @corpus_app.command("import")
    def corpus_import(
        source_dir: Path = typer.Argument(..., help="Directory of harbor task json"),
        cache: Path = typer.Option(..., "--cache", help="Local cache dir"),
        corpus_id: str = typer.Option("terminal-bench", "--corpus-id"),
        semver: str = typer.Option("1.0.0", "--semver"),
        dataset_version: str = typer.Option("2.0", "--dataset-version"),
    ) -> None:
        """Import a public dataset into the local cache (idempotent)."""
        from .public import DirectorySource, import_terminal_bench

        manifest = import_terminal_bench(
            DirectorySource(source_dir),
            cache,
            corpus_id=corpus_id,
            semver=semver,
            dataset_version=dataset_version,
        )
        typer.echo(f"imported {len(manifest.tasks)} task(s) → {cache}/manifest.json")

    @corpus_app.command("subset")
    def corpus_subset(
        manifest_path: Path = typer.Argument(..., help="manifest.json"),
        seed: int = typer.Option(..., "--seed"),
        size: int = typer.Option(30, "--size"),
        stratum_key: str = typer.Option("category", "--stratum-key"),
        ledger: Path = typer.Option(
            None, "--ledger", help="Ledger to record the subset_draw event [CO-9]"
        ),
    ) -> None:
        """Select and record a stratified calibration subset."""
        from ..ledger.events import EventContext
        from .ledger_ops import ledger_subset_draw
        from .registry import CorpusManifest
        from .stratify import calibration_subset

        manifest = CorpusManifest.load(manifest_path)
        subset = calibration_subset(
            manifest, seed, target_size=size, stratum_key=stratum_key
        )
        manifest.save(manifest_path)
        # CO-9: ledger the draw so the seeded selection is auditable and
        # tamper-evident, not only in the mutable manifest JSON.
        if ledger is not None:
            ctx = EventContext(experiment_id=manifest.corpus_id, actor=_actor())
            ledger_subset_draw(ledger, ctx, manifest, subset)
        typer.echo(f"subset: {len(subset.task_ids)} task(s) over {len(subset.strata['sizes'])} strata")

    @corpus_app.command("mine")
    def corpus_mine(
        mr_json: Path = typer.Argument(..., help="MR json {parent_sha, files:[...]}"),
        ticket: Path = typer.Option(..., "--ticket", help="Ticket text file"),
        out: Path = typer.Option(..., "--out", help="Candidate json output"),
    ) -> None:
        """Mine a merged MR into a pending candidate."""
        from .mine import MergeRequest, MRFile, mine_mr

        data = json.loads(mr_json.read_text(encoding="utf-8"))
        mr = MergeRequest(
            parent_sha=data["parent_sha"],
            files=[MRFile(**f) for f in data.get("files", [])],
        )
        candidate = mine_mr(mr, ticket.read_text(encoding="utf-8"))
        # CO-1: a mined candidate carries ticket text + holdout contents — internal
        # corpus data that must never be written into the instrument repo.
        from .registry import assert_outside_instrument

        assert_outside_instrument(out)
        out.write_text(
            json.dumps(candidate.__dict__, sort_keys=True, indent=2), encoding="utf-8"
        )
        typer.echo(
            f"candidate: parent={candidate.workspace_ref[:12]}… "
            f"holdouts={len(candidate.holdouts)} status={candidate.status}"
        )

    @corpus_app.command("review")
    def corpus_review(
        candidate_json: Path = typer.Argument(..., help="Candidate json"),
    ) -> None:
        """Surface prompt + holdouts + diff so the curator can vet the candidate.

        Curation checklist: is the prompt free of solution leakage? Is the task
        unambiguous? Is the difficulty representative? [risks §9]
        """
        c = json.loads(candidate_json.read_text(encoding="utf-8"))
        typer.echo("=== CURATION REVIEW ===")
        typer.echo("Checklist: (1) prompt free of solution leakage? "
                   "(2) task unambiguous? (3) difficulty representative?")
        typer.echo(f"\n-- workspace_ref (parent sha): {c['workspace_ref']}")
        typer.echo("\n-- prompt (ticket text) --")
        typer.echo(c["prompt"])
        typer.echo("\n-- holdouts (shipped test additions) --")
        for h in c["holdouts"]:
            typer.echo(f"  {h['path']}")

    @corpus_app.command("approve")
    def corpus_approve(
        experiment_dir: Path = typer.Argument(..., help="Dir with ledger.ndjson"),
        candidate_id: str = typer.Option(..., "--candidate-id"),
        task_sha: str = typer.Option(..., "--task-sha"),
        notes: str = typer.Option("", "--notes"),
    ) -> None:
        """Record a human curation_approval event for a candidate."""
        from ..ledger.events import EventContext, record_curation_approval

        ledger_path = experiment_dir / "ledger.ndjson"
        ctx = EventContext(experiment_id=experiment_dir.name, actor=_actor())
        record_curation_approval(
            ledger_path,
            ctx,
            candidate_id=candidate_id,
            task_sha=task_sha,
            approver=_actor(),
            notes=notes,
        )
        typer.echo(f"approved {candidate_id} (sha={task_sha[:12]}…)")

    app.add_typer(corpus_app, name="corpus")
