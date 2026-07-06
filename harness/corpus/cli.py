"""``bench corpus …`` [EVAL-8 §M6] — thin shells over :mod:`harness.corpus.api`.

``import`` (public dataset → cache + manifest), ``materialize``, ``validate-tasks``,
``subset`` (stratified calibration selection), ``mine`` (MR → pending candidate),
``review`` (curator surface — kept here as pure display), ``approve``,
``calibrate``, ``admit``, and ``baseline``. The verbs parse flags, map the
enumerated refusals to exit codes, and echo; the logic lives in the stage API
[refactor 02 §3].
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from ..cli_common import refusal_exit
from .api import (
    AdmitDestinationError,
    AdmitInputError,
    CalibrateKindError,
    CandidateStagingError,
    UnknownBenchmarkError,
    ValidateTasksFileError,
    corpus_admit,
    corpus_approve,
    corpus_baseline,
    corpus_calibrate,
    corpus_import,
    corpus_materialize,
    corpus_mine,
    corpus_subset,
    validate_tasks,
)
from .benchmarks import importer_help
from .ledger_ops import NoGradedTrialsError


def register(app: typer.Typer) -> None:
    corpus_app = typer.Typer(help="Task corpus tooling [EVAL-8].", no_args_is_help=True)

    @corpus_app.command("import")
    def import_cmd(
        source: Path = typer.Argument(
            ..., help="Harbor-task dir (--benchmark dir) or a benchmark export file"
        ),
        cache: Path = typer.Option(..., "--cache", help="Local cache dir"),
        benchmark: str = typer.Option(
            "dir", "--benchmark",
            help=importer_help(),  # derived from the importer registry [07 §3]
        ),
        corpus_id: str = typer.Option(None, "--corpus-id", help="Default: the benchmark name"),
        semver: str = typer.Option("1.0.0", "--semver"),
        dataset_version: str = typer.Option(
            None, "--dataset-version",
            help="Dataset version label recorded on the manifest/card; "
            "default is benchmark-specific",
        ),
        image_template: str = typer.Option(
            None, "--image-template",
            help="swebench: per-instance image ref template ({instance_id}, {repo})",
        ),
    ) -> None:
        """Import a standardized public dataset into the local cache (idempotent).

        ``--benchmark dir`` imports a directory of harbor-format task json (the
        terminal-bench path). ``--benchmark swebench`` maps a SWE-bench instances
        export (JSON array or JSONL) into citable, admitted corpus tasks — the
        problem statement is agent-visible, the tests become the grading holdout.
        """
        try:
            outcome = corpus_import(
                source, cache=cache, benchmark=benchmark, corpus_id=corpus_id,
                semver=semver, dataset_version=dataset_version,
                image_template=image_template,
            )
        except UnknownBenchmarkError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)
        except Exception as e:  # noqa: BLE001 — a malformed export is a user error, named
            typer.echo(f"{type(e).__name__}: {e}", err=True)
            raise typer.Exit(code=2)
        typer.echo(f"imported {outcome.n_tasks} task(s) → {cache}/manifest.json")

    @corpus_app.command("materialize")
    def materialize_cmd(
        manifest_path: Path = typer.Argument(..., help="manifest.json from a prior import"),
        cache: Path = typer.Option(..., "--cache", help="Cache dir the import wrote"),
        out: Path = typer.Option(..., "--out", help="Experiment dir to write tasks.yaml + holdouts/"),
        all_tasks: bool = typer.Option(
            False, "--all", help="Materialize every cached task, not just admitted ones"
        ),
    ) -> None:
        """Write a runnable experiment (tasks.yaml + read-only holdouts) from an
        imported corpus, so `plan → run → grade` can use a standardized task set."""
        try:
            outcome = corpus_materialize(manifest_path, cache=cache, out=out, all_tasks=all_tasks)
        except Exception as e:  # noqa: BLE001 — cache/manifest mismatch is named, not swallowed
            typer.echo(f"{type(e).__name__}: {e}", err=True)
            raise typer.Exit(code=2)
        typer.echo(f"materialized → {outcome.dest}/tasks.yaml (+ holdouts/)")

    @corpus_app.command("validate-tasks")
    def validate_tasks_cmd(
        experiment_dir: Path = typer.Argument(
            ..., help="Experiment dir whose tasks.yaml to lint"
        ),
    ) -> None:
        """Strict-lint tasks.yaml through the write-side TaskSpec [decision A9].

        The run/grade reader is deliberately lenient (it feeds the lock hash), so
        an unknown key is silently ignored there. This verb refuses unknown keys —
        with a did-you-mean for the known drift traps (holdout_dir→holdouts_dir,
        plugins→plugin_ids) — before a lock is ever taken. Pure read: nothing is
        ledgered. Exit 0 clean, 2 on any problem or a missing/mis-shaped file.
        """
        from .commit import TaskCommitmentError

        with refusal_exit(ValidateTasksFileError, TaskCommitmentError):
            outcome = validate_tasks(experiment_dir)
        if outcome.problems:
            for p in outcome.problems:
                typer.echo(p, err=True)
            typer.echo(
                f"validate-tasks: {len(outcome.problems)} problem(s) in tasks.yaml", err=True
            )
            raise typer.Exit(code=2)
        typer.echo(f"validate-tasks: {outcome.n_tasks} task(s) OK")

    @corpus_app.command("subset")
    def subset_cmd(
        manifest_path: Path = typer.Argument(..., help="manifest.json"),
        seed: int = typer.Option(..., "--seed"),
        size: int = typer.Option(30, "--size"),
        stratum_key: str = typer.Option("category", "--stratum-key"),
        ledger: Path = typer.Option(
            None, "--ledger", help="Ledger to record the subset_draw event [CO-9]"
        ),
        actor: str = typer.Option(None, "--actor", help="Actor recorded on the draw [GR-12]"),
    ) -> None:
        """Select and record a stratified calibration subset."""
        from ..ledger.actor import ActorResolutionError

        with refusal_exit(ActorResolutionError):
            outcome = corpus_subset(
                manifest_path, seed=seed, size=size, stratum_key=stratum_key,
                ledger=ledger, actor=actor,
            )
        typer.echo(f"subset: {outcome.n_tasks} task(s) over {outcome.n_strata} strata")

    @corpus_app.command("mine")
    def mine_cmd(
        mr_json: Path = typer.Argument(..., help="MR json {parent_sha, files:[...]}"),
        ticket: Path = typer.Option(..., "--ticket", help="Ticket text file"),
        out: Path = typer.Option(..., "--out", help="Candidate json output"),
        miner: str = typer.Option(None, "--miner", help="Miner identity [default: current user]"),
        manifest_path: Path = typer.Option(
            None, "--manifest", help="Manifest to stage the candidate into [CO-8]"
        ),
        task_id: str = typer.Option(None, "--task-id", help="Manifest task id [default: --out stem]"),
    ) -> None:
        """Mine a merged MR into a pending candidate; optionally stage it in a manifest."""
        from ..ledger.actor import ActorResolutionError

        with refusal_exit(ActorResolutionError, CandidateStagingError):
            outcome = corpus_mine(
                mr_json, ticket=ticket, out=out, miner=miner,
                manifest_path=manifest_path, task_id=task_id,
            )
        typer.echo(
            f"candidate: parent={outcome.workspace_ref[:12]}… sha={outcome.sha[:12]}… "
            f"miner={outcome.miner} holdouts={outcome.n_holdouts} status={outcome.status}"
        )

    @corpus_app.command("review")
    def review_cmd(
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
        typer.echo(f"\n-- miner: {c.get('miner')}")
        typer.echo("\n-- holdouts (shipped test additions) --")
        # CO-7: show holdout CONTENT, not just paths — the curator's whole job is
        # to check the shipped tests for solution leakage, impossible from a path.
        for h in c["holdouts"]:
            typer.echo(f"\n  === {h['path']} ===")
            typer.echo(h.get("content", ""))

    @corpus_app.command("approve")
    def approve_cmd(
        experiment_dir: Path = typer.Argument(..., help="Dir with ledger.ndjson"),
        candidate_id: str = typer.Option(..., "--candidate-id"),
        task_sha: str = typer.Option(..., "--task-sha"),
        signing_key: Path = typer.Option(..., "--signing-key", help="Approver Ed25519 private key (hex)"),
        approver: str = typer.Option(
            ..., "--approver",
            help="Approver identity (REQUIRED — never defaulted from the environment "
                 "because approver identity is security-relevant) [D-P7-7]",
        ),
        notes: str = typer.Option("", "--notes"),
    ) -> None:
        """Sign + record a curation_approval — the approver attests with their key."""
        corpus_approve(
            experiment_dir, candidate_id=candidate_id, task_sha=task_sha,
            signing_key=signing_key, approver=approver, notes=notes,
        )
        typer.echo(f"approved {candidate_id} (sha={task_sha[:12]}…) signed by {approver}")

    @corpus_app.command("calibrate")
    def calibrate_cmd(
        experiment_dir: Path = typer.Argument(..., help="Dir with a completed run's ledger"),
        manifest_path: Path = typer.Option(..., "--manifest", help="manifest.json to advance"),
        kind: str = typer.Option("full", "--kind", help="subset | full"),
        rho: float = typer.Option(0.3, "--rho", help="within-task correlation [recorded assumption]"),
        actor: str = typer.Option(None, "--actor", help="Actor recorded on the calibration run [GR-12]"),
    ) -> None:
        """Record a calibration run from a completed run's realized variance [CO-4].

        Derives ``p`` (mean holdout pass rate) and ``n_tasks`` from the ledger's
        grades — the run-path hook that finally invokes ``ledger_calibration_run``
        so a calibration run is chain-anchored and feeds ``bench plan``'s power
        gate [PL-5]. ``rho`` is a recorded assumption (full estimation is Phase 5).
        """
        from ..ledger.actor import ActorResolutionError

        with refusal_exit(CalibrateKindError, NoGradedTrialsError, ActorResolutionError):
            outcome = corpus_calibrate(
                experiment_dir, manifest_path=manifest_path, kind=kind, rho=rho, actor=actor
            )
        typer.echo(
            f"calibration ({outcome.kind}): p={outcome.p:.3f} n_tasks={outcome.n_tasks} "
            f"→ {outcome.status}"
        )

    @corpus_app.command("admit")
    def admit_cmd(
        experiment_dir: Path = typer.Argument(..., help="Dir with ledger.ndjson"),
        manifest_path: Path = typer.Option(..., "--manifest", help="manifest.json"),
        candidate_id: str = typer.Option(..., "--candidate-id"),
        task_sha: str = typer.Option(..., "--task-sha"),
        baseline_ref: str = typer.Option(..., "--baseline-ref"),
        keyring: Path = typer.Option(
            ..., "--keyring",
            help="Authorized curators (JSON object: approver id -> public-key hex) [D-P7-3]",
        ),
        candidate_json: Path = typer.Option(
            None, "--candidate-json",
            help="Stored candidate content to embed the contamination canary into "
                 "on admission. The reviewed file is never rewritten (its bytes "
                 "are what the approval sha signs); the embedded copy is written "
                 "alongside as <name>.embedded.json [EVAL-10 AC-2]",
        ),
        actor: str = typer.Option(None, "--actor", help="Actor recorded on the admission [GR-12]"),
    ) -> None:
        """Admit a curated candidate — verifies the signed approval + clean baseline."""
        from ..contamination.canary import CanaryError
        from ..ledger.actor import ActorResolutionError
        from .attestation import KeyringFormatError
        from .registry import CorpusError

        with refusal_exit(
            AdmitInputError, ActorResolutionError, KeyringFormatError,
            AdmitDestinationError, CorpusError, CanaryError,
        ):
            outcome = corpus_admit(
                experiment_dir, manifest_path=manifest_path, candidate_id=candidate_id,
                task_sha=task_sha, baseline_ref=baseline_ref, keyring=keyring,
                candidate_json=candidate_json, actor=actor,
            )
        if outcome.embedded_path is not None:
            typer.echo(f"canary-embedded content: {outcome.embedded_path}")
        if outcome.persist_error is not None:
            typer.echo(outcome.persist_error, err=True)
            raise typer.Exit(code=1)
        typer.echo(f"admitted {candidate_id} (sha={task_sha[:12]}…)")

    @corpus_app.command("baseline")
    def baseline_cmd(
        experiment_dir: Path = typer.Argument(..., help="Dir with ledger.ndjson"),
        task_id: str = typer.Option(..., "--task-id"),
        task_sha: str = typer.Option(..., "--task-sha"),
        workspace: Path = typer.Option(
            ..., "--workspace",
            help="The task's REFERENCE-SOLUTION tree (the contract: holdouts "
            "must pass deterministically when the task is truly solved — a "
            "fail-to-pass task's pre-fix tree would always quarantine) [F-H2]",
        ),
        holdouts_dir: Path = typer.Option(..., "--holdouts-dir"),
        k: int = typer.Option(None, "--k", help="Zero-tolerance runs (default 5); "
                              "raise for stronger flake detection — miss rate is (1-p)^k"),
        runner: str = typer.Option(
            "docker", "--runner", help="docker (real container) | local (no-daemon fake/test)"
        ),
        actor: str = typer.Option(None, "--actor", help="Actor recorded on the baseline [GR-12]"),
    ) -> None:
        """Run the flake baseline a candidate needs for admission [F-H2].

        Ledgers exactly one flake_baseline event (verdict clean|quarantined) on
        completion; a transient grader outage is inconclusive — non-zero exit,
        nothing ledgered, no quarantine.
        """
        from ..grade.container import GraderUnavailableError
        from ..ledger.actor import ActorResolutionError

        if runner not in ("docker", "local"):
            raise typer.BadParameter("--runner must be docker or local")
        try:
            outcome = corpus_baseline(
                experiment_dir, task_id=task_id, task_sha=task_sha,
                workspace=workspace, holdouts_dir=holdouts_dir, k=k,
                runner=runner, actor=actor,
            )
        except GraderUnavailableError as e:
            typer.echo(
                f"baseline inconclusive (nothing ledgered): grader unavailable — {e}",
                err=True,
            )
            raise typer.Exit(code=2)
        except (ValueError, ActorResolutionError) as e:  # k < 1 [GR-10] / actor
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)
        typer.echo(
            f"baseline {outcome.verdict}: {task_id} (sha={task_sha[:12]}…) "
            f"over k={outcome.k} run(s)"
        )
        if outcome.verdict != "clean":
            raise typer.Exit(code=1)

    app.add_typer(corpus_app, name="corpus")
