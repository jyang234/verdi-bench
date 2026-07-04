"""``bench corpus …`` [EVAL-8 §M6].

Functional against fixtures: ``import`` (public dataset → cache + manifest),
``subset`` (stratified calibration selection), ``mine`` (MR → pending
candidate), ``review`` (surface prompt + holdouts + diff for the curator), and
``approve`` (ledger a ``curation_approval``). The review view exists to make
curation real — solution-leakage in the prompt is caught here, not by a machine
[risks §9].
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer

from ..ledger.actor import ActorResolutionError, resolve_actor


def _resolve_actor_or_exit(flag_value):
    """Resolve the ledgered actor or exit 2 with the named refusal [GR-12]."""
    try:
        return resolve_actor(flag_value)
    except ActorResolutionError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=2)


def register(app: typer.Typer) -> None:
    corpus_app = typer.Typer(help="Task corpus tooling [EVAL-8].", no_args_is_help=True)

    @corpus_app.command("import")
    def corpus_import(
        source: Path = typer.Argument(
            ..., help="Harbor-task dir (--benchmark dir) or a benchmark export file"
        ),
        cache: Path = typer.Option(..., "--cache", help="Local cache dir"),
        benchmark: str = typer.Option(
            "dir", "--benchmark",
            help="Source format: 'dir' (harbor json dir) | 'swebench' (SWE-bench export)",
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
        from .public import DirectorySource, import_public_dataset

        if benchmark == "dir":
            task_source = DirectorySource(source)
            dataset_name = corpus_id or "terminal-bench"
            resolved_version = dataset_version or "2.0"
        elif benchmark == "swebench":
            from .benchmarks import SWEBENCH, SweBenchSource

            task_source = SweBenchSource(source, image_template=image_template)
            dataset_name = SWEBENCH
            # do NOT inherit terminal-bench's "2.0"; the user labels the export
            resolved_version = dataset_version or "SWE-bench_Verified"
        else:
            typer.echo(f"unknown --benchmark {benchmark!r} (dir | swebench)", err=True)
            raise typer.Exit(code=2)

        try:
            manifest = import_public_dataset(
                task_source,
                cache,
                corpus_id=corpus_id or dataset_name,
                semver=semver,
                dataset_name=dataset_name,
                dataset_version=resolved_version,
            )
        except Exception as e:  # noqa: BLE001 — a malformed export is a user error, named
            typer.echo(f"{type(e).__name__}: {e}", err=True)
            raise typer.Exit(code=2)
        typer.echo(f"imported {len(manifest.tasks)} task(s) → {cache}/manifest.json")

    @corpus_app.command("materialize")
    def corpus_materialize(
        manifest_path: Path = typer.Argument(..., help="manifest.json from a prior import"),
        cache: Path = typer.Option(..., "--cache", help="Cache dir the import wrote"),
        out: Path = typer.Option(..., "--out", help="Experiment dir to write tasks.yaml + holdouts/"),
        all_tasks: bool = typer.Option(
            False, "--all", help="Materialize every cached task, not just admitted ones"
        ),
    ) -> None:
        """Write a runnable experiment (tasks.yaml + read-only holdouts) from an
        imported corpus, so `plan → run → grade` can use a standardized task set."""
        from .materialize import materialize_experiment
        from .registry import CorpusManifest

        manifest = CorpusManifest.load(manifest_path)
        try:
            dest = materialize_experiment(manifest, cache, out, only_admitted=not all_tasks)
        except Exception as e:  # noqa: BLE001 — cache/manifest mismatch is named, not swallowed
            typer.echo(f"{type(e).__name__}: {e}", err=True)
            raise typer.Exit(code=2)
        typer.echo(f"materialized → {dest}/tasks.yaml (+ holdouts/)")

    @corpus_app.command("subset")
    def corpus_subset(
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
        from ..ledger.events import EventContext
        from .ledger_ops import ledger_subset_draw
        from .registry import CorpusManifest
        from .stratify import calibration_subset

        manifest = CorpusManifest.load(manifest_path)
        subset = calibration_subset(
            manifest, seed, target_size=size, stratum_key=stratum_key
        )
        # CO-9: ledger the draw *before* persisting the mutable manifest, so an
        # interrupted run cannot leave the manifest showing a draw the chain never
        # recorded (the ledger is the auditable, tamper-evident source of truth).
        if ledger is not None:
            ctx = EventContext(experiment_id=manifest.corpus_id, actor=_resolve_actor_or_exit(actor))
            ledger_subset_draw(ledger, ctx, manifest, subset)
        manifest.save(manifest_path)
        typer.echo(f"subset: {len(subset.task_ids)} task(s) over {len(subset.strata['sizes'])} strata")

    @corpus_app.command("mine")
    def corpus_mine(
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
        from .mine import MergeRequest, MRFile, mine_mr
        from .registry import CorpusError, CorpusManifest, assert_outside_instrument

        who = _resolve_actor_or_exit(miner)
        data = json.loads(mr_json.read_text(encoding="utf-8"))
        mr = MergeRequest(
            parent_sha=data["parent_sha"],
            files=[MRFile(**f) for f in data.get("files", [])],
        )
        candidate = mine_mr(mr, ticket.read_text(encoding="utf-8"))
        candidate.miner = who
        # CO-1: a mined candidate carries ticket text + holdout contents — internal
        # corpus data that must never be written into the instrument repo.
        assert_outside_instrument(out)
        out.write_text(
            json.dumps(candidate.__dict__, sort_keys=True, indent=2), encoding="utf-8"
        )
        sha = candidate.content_sha()
        # CO-8: the mine→manifest link — stage the candidate as a pending task so
        # admission (which requires a manifest entry) is reachable.
        if manifest_path is not None:
            from pydantic import ValidationError

            manifest = CorpusManifest.load(manifest_path)
            try:
                # EVAL-10 AC-1: created_at comes from the MR's merged_at — input
                # data, not a wall-clock read; absent stays an honest unknown.
                # A malformed merged_at surfaces as a pydantic ValidationError
                # (the created_at validator runs inside TaskEntry), so it must
                # be caught here for the clean exit-2 refusal, not a traceback.
                manifest.stage_candidate(
                    task_id or out.stem, sha=sha, miner=who,
                    created_at=data.get("merged_at"),
                )
            except (CorpusError, ValidationError) as e:
                typer.echo(str(e), err=True)
                raise typer.Exit(code=2)
            manifest.save(manifest_path)
        typer.echo(
            f"candidate: parent={candidate.workspace_ref[:12]}… sha={sha[:12]}… "
            f"miner={who} holdouts={len(candidate.holdouts)} status={candidate.status}"
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
        typer.echo(f"\n-- miner: {c.get('miner')}")
        typer.echo("\n-- holdouts (shipped test additions) --")
        # CO-7: show holdout CONTENT, not just paths — the curator's whole job is
        # to check the shipped tests for solution leakage, impossible from a path.
        for h in c["holdouts"]:
            typer.echo(f"\n  === {h['path']} ===")
            typer.echo(h.get("content", ""))

    @corpus_app.command("approve")
    def corpus_approve(
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
        from ..ledger.events import EventContext, record_curation_approval
        from .attestation import sign_approval

        # D-P7-7: approver identity is security-relevant (D-P7-3 binds it to a key),
        # so it must be given explicitly — never defaulted from the environment.
        who = approver
        priv = signing_key.read_text(encoding="utf-8").strip()
        sig, pk = sign_approval(priv, candidate_id=candidate_id, task_sha=task_sha, approver=who)
        ledger_path = experiment_dir / "ledger.ndjson"
        ctx = EventContext(experiment_id=experiment_dir.name, actor=who)
        record_curation_approval(
            ledger_path, ctx, candidate_id=candidate_id, task_sha=task_sha,
            approver=who, signature=sig, signer_public_key=pk, notes=notes,
        )
        typer.echo(f"approved {candidate_id} (sha={task_sha[:12]}…) signed by {who}")

    @corpus_app.command("calibrate")
    def corpus_calibrate(
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
        from ..ledger import events
        from ..ledger.events import EventContext
        from ..ledger.query import find_events
        from .ledger_ops import ledger_calibration_run
        from .registry import CorpusManifest

        if kind not in ("subset", "full"):
            typer.echo("--kind must be 'subset' or 'full'", err=True)
            raise typer.Exit(code=2)
        ledger_path = experiment_dir / "ledger.ndjson"
        manifest = CorpusManifest.load(manifest_path)

        trial_task = {
            ev["trial_record"]["trial_id"]: ev["trial_record"]["task_id"]
            for ev in find_events(ledger_path, events.TRIAL)
        }
        by_task: dict[str, list[float]] = {}
        for ev in find_events(ledger_path, events.GRADE):
            task_id = trial_task.get(ev["trial_id"])
            if task_id is None:
                continue
            by_task.setdefault(task_id, []).append(1.0 if ev["binary_score"] else 0.0)
        if not by_task:
            typer.echo("no graded trials to calibrate from", err=True)
            raise typer.Exit(code=2)
        all_scores = [s for xs in by_task.values() for s in xs]
        p = sum(all_scores) / len(all_scores)
        n_tasks = len(by_task)
        run = {"p": round(p, 6), "rho": rho, "n_tasks": n_tasks, "kind": kind}

        ctx = EventContext(experiment_id=experiment_dir.name, actor=_resolve_actor_or_exit(actor))
        ledger_calibration_run(ledger_path, ctx, manifest, run, kind=kind)
        manifest.save(manifest_path)
        typer.echo(f"calibration ({kind}): p={p:.3f} n_tasks={n_tasks} → {manifest.calibration.status}")

    @corpus_app.command("admit")
    def corpus_admit(
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
        from ..contamination.canary import CanaryError, derive_canary, embed_canary
        from ..ledger.events import EventContext
        from .admit import admit_task
        from .attestation import KeyringFormatError, load_keyring
        from .registry import CorpusError, CorpusManifest, assert_outside_instrument

        # EVAL-10 AC-2: read + validate the candidate content BEFORE admission,
        # so a bad path/JSON refuses cleanly with nothing ledgered.
        candidate_content = None
        if candidate_json is not None:
            try:
                assert_outside_instrument(candidate_json)
                candidate_content = json.loads(
                    candidate_json.read_text(encoding="utf-8")
                )
            except (CorpusError, OSError, ValueError) as e:
                typer.echo(str(e), err=True)
                raise typer.Exit(code=2)

        ledger_path = experiment_dir / "ledger.ndjson"
        ctx = EventContext(experiment_id=experiment_dir.name, actor=_resolve_actor_or_exit(actor))
        manifest = CorpusManifest.load(manifest_path)
        # Load the keyring before the admit envelope: a legacy list-format keyring
        # raises KeyringFormatError (a ValueError, not CorpusError), so evaluating
        # it inside the `except CorpusError` block below would escape as a
        # traceback instead of the clean exit-2 migration refusal [D-P7-3].
        try:
            authorized = load_keyring(keyring)
        except KeyringFormatError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)
        # PRA-M11: validate the write destinations BEFORE ledgering, so a
        # non-writable manifest/embedded-copy path fails closed with nothing torn
        # rather than advancing the ledger and then tracebacking on save. The
        # manifest already exists (we loaded it); check its dir, and the embedded
        # sibling's dir, are writable up front.
        for dest in (manifest_path, candidate_json):
            if dest is not None and not os.access(dest.parent, os.W_OK):
                typer.echo(
                    f"admission destination {dest.parent} is not writable; refusing "
                    "before ledgering [PRA-M11]", err=True,
                )
                raise typer.Exit(code=2)
        try:
            # admit_task validates the canary embed BEFORE ledgering, so an
            # embed refusal (no prompt, double embed) leaves nothing torn.
            admit_task(
                manifest, ledger_path, ctx, candidate_id=candidate_id, task_sha=task_sha,
                baseline_ref=baseline_ref, keyring=authorized,
                candidate_content=candidate_content,
            )
        except (CorpusError, CanaryError) as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2)
        # EVAL-10 AC-2: persist the embedded copy ALONGSIDE the reviewed file —
        # never over it. The reviewed bytes are what the curation approval and
        # manifest sha are keyed to; destroying them would make every admitted
        # task look post-review-tampered. embed_canary is pure, so this repeats
        # the exact call admit_task already validated. A failure here (post-ledger)
        # is reported loudly with the recovery hint, not swallowed [PRA-M11].
        try:
            if candidate_content is not None:
                embedded = embed_canary(candidate_content, derive_canary(task_sha))
                embedded_path = candidate_json.with_suffix(".embedded.json")
                embedded_path.write_text(
                    json.dumps(embedded, sort_keys=True, indent=2), encoding="utf-8"
                )
                typer.echo(f"canary-embedded content: {embedded_path}")
            manifest.save(manifest_path)
        except OSError as e:
            typer.echo(
                f"task_admitted was ledgered but persisting the manifest/embedded "
                f"copy failed: {e}. The admission is on the chain; re-save the "
                f"manifest to {manifest_path} to reconcile [PRA-M11]", err=True,
            )
            raise typer.Exit(code=1)
        typer.echo(f"admitted {candidate_id} (sha={task_sha[:12]}…)")

    app.add_typer(corpus_app, name="corpus")
