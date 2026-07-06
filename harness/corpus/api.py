"""``corpus`` stage API [refactor 02 §3].

The importable entry points behind ``bench corpus …`` [EVAL-8 §M6]: import a
public dataset, materialize a runnable experiment, lint tasks.yaml, draw a
calibration subset, mine/approve/admit curated candidates, record a calibration
run, and run the flake baseline. Each verb keeps argument handling + refusal
mapping; the domain work lives in the corpus modules (calibrate statistics in
``ledger_ops.realized_calibration_run``, admission's two-phase persistence in
``admit.admit_with_persistence``) [refactor 07 §3]. The typer verbs
(``harness/corpus/cli.py``) are thin shells that map the refusals to exit codes
and echo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# The admission refusal types + two-phase outcome live beside admit_task
# [refactor 07 §3]; re-exported here because this stage API is the seam the
# CLI and SDK import them through.
from .admit import AdmitDestinationError, AdmitInputError, AdmitOutcome

# Known tasks.yaml drift traps — singular/plural keys the lenient run/grade reader
# would silently ignore (decision A9). validate-tasks names them explicitly.
_TASK_DRIFT_TRAPS = {"holdout_dir": "holdouts_dir", "plugins": "plugin_ids"}


def _suggest_task_key(unknown: str) -> str | None:
    """The known-good TaskSpec field an unknown tasks.yaml key most likely meant:
    a hardcoded drift trap first, else the closest field by edit distance."""
    import difflib

    from ..schema.tasks import TaskSpec

    if unknown in _TASK_DRIFT_TRAPS:
        return _TASK_DRIFT_TRAPS[unknown]
    matches = difflib.get_close_matches(unknown, list(TaskSpec.model_fields), n=1)
    return matches[0] if matches else None


class UnknownBenchmarkError(RuntimeError):
    """``--benchmark`` was not one of the registered importer names [refactor 07 §3]."""


class ValidateTasksFileError(RuntimeError):
    """``bench corpus validate-tasks`` was pointed at a dir with no tasks.yaml."""


class CalibrateKindError(RuntimeError):
    """``bench corpus calibrate --kind`` was neither ``subset`` nor ``full``."""


class CandidateStagingError(RuntimeError):
    """Staging a mined candidate into a manifest was refused (bad entry)."""


@dataclass(frozen=True)
class ImportOutcome:
    n_tasks: int


@dataclass(frozen=True)
class MaterializeOutcome:
    dest: Path


@dataclass(frozen=True)
class ValidateTasksOutcome:
    n_tasks: int
    problems: list[str]


@dataclass(frozen=True)
class SubsetOutcome:
    n_tasks: int
    n_strata: int


@dataclass(frozen=True)
class MineOutcome:
    workspace_ref: str
    sha: str
    miner: str
    n_holdouts: int
    status: str


@dataclass(frozen=True)
class CalibrateOutcome:
    kind: str
    p: float
    n_tasks: int
    status: str


@dataclass(frozen=True)
class BaselineOutcome:
    verdict: str
    k: int


def corpus_import(
    source, *, cache, benchmark: str = "dir", corpus_id=None, semver: str = "1.0.0",
    dataset_version=None, image_template=None,
) -> ImportOutcome:
    """Import a standardized public dataset into the local cache (idempotent).

    Validates ``--benchmark`` against the importer registry (``UnknownBenchmarkError``
    with the derived valid set); a malformed export propagates (the CLI names it
    as ``<Type>: <msg>``) [refactor 07 §3]."""
    from .benchmarks import IMPORTERS, importer_names
    from .public import import_public_dataset

    if benchmark not in IMPORTERS:
        raise UnknownBenchmarkError(
            f"unknown --benchmark {benchmark!r} ({importer_names()})"
        )
    spec = IMPORTERS[benchmark]
    task_source = spec.source_factory(source, image_template=image_template)
    resolved_version = dataset_version or spec.default_dataset_version
    # A generic directory has no canonical dataset name, so --corpus-id names it;
    # a benchmark with a canonical identity keeps its dataset name fixed [07 §3].
    dataset_name = (
        (corpus_id or spec.dataset_name)
        if spec.corpus_id_names_dataset
        else spec.dataset_name
    )
    manifest = import_public_dataset(
        task_source,
        cache,
        corpus_id=corpus_id or dataset_name,
        semver=semver,
        dataset_name=dataset_name,
        dataset_version=resolved_version,
    )
    return ImportOutcome(n_tasks=len(manifest.tasks))


def corpus_materialize(manifest_path, *, cache, out, all_tasks: bool = False) -> MaterializeOutcome:
    """Write a runnable experiment (tasks.yaml + read-only holdouts) from an
    imported corpus. A cache/manifest mismatch propagates (named by the CLI)."""
    from .materialize import materialize_experiment
    from .registry import CorpusManifest

    manifest = CorpusManifest.load(manifest_path)
    dest = materialize_experiment(manifest, cache, out, only_admitted=not all_tasks)
    return MaterializeOutcome(dest=dest)


def validate_tasks(experiment_dir) -> ValidateTasksOutcome:
    """Strict-lint tasks.yaml through the write-side TaskSpec [decision A9].

    Raises ``ValidateTasksFileError`` (no tasks.yaml) / ``TaskCommitmentError``
    (structural load failure); otherwise returns the task count + the list of
    per-task problems (empty ⇒ clean). Pure read: nothing is ledgered."""
    from pydantic import ValidationError

    from ..schema.tasks import TaskSpec
    from .commit import load_task_dicts

    experiment_dir = Path(experiment_dir)
    if not (experiment_dir / "tasks.yaml").exists():
        raise ValidateTasksFileError(f"no tasks.yaml in {experiment_dir}")
    # The lenient reader's own structural refusals (missing/duplicate id) are lint
    # failures too — surface them (TaskCommitmentError), don't crash past them.
    task_dicts = load_task_dicts(experiment_dir)

    problems: list[str] = []
    for t in task_dicts:
        tid = t.get("id", "<no id>")
        try:
            TaskSpec(**t)
        except ValidationError as e:
            for err in e.errors():
                if err["type"] == "extra_forbidden":
                    key = str(err["loc"][-1])
                    suggestion = _suggest_task_key(key)
                    hint = f" — did you mean {suggestion!r}?" if suggestion else ""
                    problems.append(f"task {tid!r}: unknown key {key!r}{hint}")
                else:
                    loc = ".".join(str(p) for p in err["loc"]) or "<task>"
                    problems.append(f"task {tid!r}: {err['msg']} (at {loc})")
    return ValidateTasksOutcome(n_tasks=len(task_dicts), problems=problems)


def corpus_subset(
    manifest_path, *, seed: int, size: int = 30, stratum_key: str = "category",
    ledger=None, actor=None,
) -> SubsetOutcome:
    """Select and record a stratified calibration subset [CO-9].

    Resolves the actor only when a ``ledger`` is supplied (the draw is recorded
    under the corpus id); raises ``ActorResolutionError`` (mapped to exit 2)."""
    from ..ledger.actor import resolve_actor
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
        ctx = EventContext(experiment_id=manifest.corpus_id, actor=resolve_actor(actor))
        ledger_subset_draw(ledger, ctx, manifest, subset)
    manifest.save(manifest_path)
    return SubsetOutcome(
        n_tasks=len(subset.task_ids), n_strata=len(subset.strata["sizes"])
    )


def corpus_mine(
    mr_json, *, ticket, out, miner=None, manifest_path=None, task_id=None,
) -> MineOutcome:
    """Mine a merged MR into a pending candidate; optionally stage it in a manifest.

    Resolves the miner first (raises ``ActorResolutionError``); a candidate
    destination inside the instrument repo is a loud ``CorpusError`` (CO-1,
    traceback), while a staging failure is a ``CandidateStagingError`` (exit 2)."""
    from ..ledger.actor import resolve_actor
    from .mine import MergeRequest, MRFile, mine_mr
    from .registry import CorpusManifest, assert_outside_instrument

    who = resolve_actor(miner)
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

        from .registry import CorpusError

        manifest = CorpusManifest.load(manifest_path)
        try:
            # EVAL-10 AC-1: created_at comes from the MR's merged_at — input data,
            # not a wall-clock read; absent stays an honest unknown. A malformed
            # merged_at surfaces as a pydantic ValidationError (the created_at
            # validator runs inside TaskEntry).
            manifest.stage_candidate(
                task_id or out.stem, sha=sha, miner=who,
                created_at=data.get("merged_at"),
            )
        except (CorpusError, ValidationError) as e:
            raise CandidateStagingError(str(e)) from e
        manifest.save(manifest_path)
    return MineOutcome(
        workspace_ref=candidate.workspace_ref, sha=sha, miner=who,
        n_holdouts=len(candidate.holdouts), status=candidate.status,
    )


def corpus_approve(
    experiment_dir, *, candidate_id: str, task_sha: str, signing_key, approver: str,
    notes: str = "",
) -> None:
    """Sign + record a curation_approval — the approver attests with their key.

    D-P7-7: approver identity is security-relevant (D-P7-3 binds it to a key), so
    it is given explicitly, never resolved from the environment."""
    from ..ledger.events import EventContext, record_curation_approval
    from .attestation import sign_approval

    who = approver
    priv = signing_key.read_text(encoding="utf-8").strip()
    sig, pk = sign_approval(priv, candidate_id=candidate_id, task_sha=task_sha, approver=who)
    ledger_path = experiment_dir / "ledger.ndjson"
    ctx = EventContext(experiment_id=experiment_dir.name, actor=who)
    record_curation_approval(
        ledger_path, ctx, candidate_id=candidate_id, task_sha=task_sha,
        approver=who, signature=sig, signer_public_key=pk, notes=notes,
    )


def corpus_calibrate(
    experiment_dir, *, manifest_path, kind: str = "full", rho: float = 0.3, actor=None,
) -> CalibrateOutcome:
    """Record a calibration run from a completed run's realized variance [CO-4].

    Raises ``CalibrateKindError`` / ``NoGradedTrialsError`` / ``ActorResolutionError``
    (all mapped to exit 2). ``rho`` is a recorded assumption (full estimation is
    Phase 5)."""
    from ..ledger.actor import resolve_actor
    from ..ledger.events import EventContext
    from .ledger_ops import ledger_calibration_run, realized_calibration_run
    from .registry import CorpusManifest

    if kind not in ("subset", "full"):
        raise CalibrateKindError("--kind must be 'subset' or 'full'")
    ledger_path = experiment_dir / "ledger.ndjson"
    manifest = CorpusManifest.load(manifest_path)
    # The realized-variance statistics moved into a corpus function [07 §3];
    # this stays argument handling + refusal mapping + the ledger orchestration.
    run = realized_calibration_run(ledger_path, rho=rho, kind=kind)
    ctx = EventContext(experiment_id=experiment_dir.name, actor=resolve_actor(actor))
    ledger_calibration_run(ledger_path, ctx, manifest, run, kind=kind)
    manifest.save(manifest_path)
    return CalibrateOutcome(
        kind=run["kind"], p=run["p"], n_tasks=run["n_tasks"],
        status=manifest.calibration.status,
    )


def corpus_admit(
    experiment_dir, *, manifest_path, candidate_id: str, task_sha: str,
    baseline_ref: str, keyring, candidate_json=None, actor=None,
) -> AdmitOutcome:
    """Admit a curated candidate — verifies the signed approval + clean baseline.

    Raises the pre-ledger refusals (``AdmitInputError``/``ActorResolutionError``/
    ``KeyringFormatError``/``AdmitDestinationError``/``CorpusError``/``CanaryError``,
    all exit 2); a *post-ledger* persistence failure is returned as
    ``AdmitOutcome.persist_error`` (exit 1) so the admission on the chain is
    reported, not lost [PRA-M11]. The two-phase persistence orchestration lives
    beside ``admit_task`` (:func:`harness.corpus.admit.admit_with_persistence`);
    this verb keeps argument handling + refusal mapping [refactor 07 §3]."""
    from ..ledger.events import EventContext
    from ..ledger.actor import resolve_actor
    from .admit import admit_with_persistence
    from .attestation import load_keyring
    from .registry import CorpusError, CorpusManifest, assert_outside_instrument

    # EVAL-10 AC-2: read + validate the candidate content BEFORE admission, so a
    # bad path/JSON refuses cleanly with nothing ledgered.
    candidate_content = None
    if candidate_json is not None:
        try:
            assert_outside_instrument(candidate_json)
            candidate_content = json.loads(candidate_json.read_text(encoding="utf-8"))
        except (CorpusError, OSError, ValueError) as e:
            raise AdmitInputError(str(e)) from e

    ledger_path = experiment_dir / "ledger.ndjson"
    ctx = EventContext(experiment_id=experiment_dir.name, actor=resolve_actor(actor))
    manifest = CorpusManifest.load(manifest_path)
    # Load the keyring before the admit envelope: a legacy list-format keyring
    # raises KeyringFormatError (a ValueError) the CLI maps to a clean exit-2
    # migration refusal [D-P7-3].
    authorized = load_keyring(keyring)
    return admit_with_persistence(
        manifest, ledger_path, ctx, candidate_id=candidate_id, task_sha=task_sha,
        baseline_ref=baseline_ref, keyring=authorized, manifest_path=manifest_path,
        candidate_content=candidate_content, candidate_json=candidate_json,
    )


def corpus_baseline(
    experiment_dir, *, task_id: str, task_sha: str, workspace, holdouts_dir,
    k=None, runner: str = "docker", actor=None,
) -> BaselineOutcome:
    """Run the flake baseline a candidate needs for admission [F-H2].

    Ledgers exactly one flake_baseline event (verdict clean|quarantined) on
    completion. Raises ``GraderUnavailableError`` (transient outage) / ``ValueError``
    (k < 1) / ``ActorResolutionError`` — all mapped to exit 2 by the CLI."""
    from ..grade.baseline import DEFAULT_K, flake_baseline
    from ..grade.container import (
        DockerGradeRunner,
        GradingContainer,
        LocalGradeRunner,
    )
    from ..grade.types import GradeTask
    from ..ledger.actor import resolve_actor
    from ..ledger.events import EventContext

    ledger_path = experiment_dir / "ledger.ndjson"
    ctx = EventContext(experiment_id=experiment_dir.name, actor=resolve_actor(actor))
    container = GradingContainer(
        runner=LocalGradeRunner() if runner == "local" else DockerGradeRunner()
    )
    task = GradeTask(id=task_id, task_sha=task_sha, holdouts_dir=str(holdouts_dir))
    outcome = flake_baseline(
        task, ledger_path, ctx,
        workspace=workspace, container=container,
        k=k if k is not None else DEFAULT_K,
        workspace_basis="reference_solution",
    )
    return BaselineOutcome(verdict=outcome.verdict, k=outcome.event["k"])
