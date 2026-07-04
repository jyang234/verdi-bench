"""``bench forensics`` — scan, human spot-check, operator quarantine [EVAL-11].

``scan`` walks the ledger's trials, resolves each trajectory through the
EVAL-12 verifier (a record is never evidence unless its bytes matched the
chain), computes the vocabulary-v1 metrics, runs the gaming detectors over
assembled evidence, optionally runs the blinded advisory review, and appends
**exactly one** ``forensics_report`` event — partial coverage is disclosed in
the report with a per-trial reason, never silent [AC-6].

``record`` ledgers a human's per-detector spot-check [AC-4, D006]; and
``quarantine`` ledgers the operator disposition [D003, D007] — the only path
by which forensics affects a comparison, and it is a human act, never a
detector's.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from ..ledger.actor import ActorResolutionError, resolve_actor
from ..ledger.events import (
    EventContext,
    record_forensic_quarantine,
    record_forensic_spotcheck,
    record_forensics_report,
)
from .detectors import DETECTOR_IDS, TrialEvidence, extract_assertion_values, run_detectors
from .metrics import FORENSICS_VOCABULARY_VERSION, trajectory_metrics

_SKIP_DIRS = {"artifacts", ".git", "__pycache__"}


def _resolve_actor_or_exit(flag_value: Optional[str]) -> str:
    try:
        return resolve_actor(flag_value)
    except ActorResolutionError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=2)


def _read_text_files(root: Optional[Path]) -> dict[str, str]:
    """Deterministic relpath→text mapping; binary/undecodable files are not
    text evidence and a deleted workspace is a coverage fact, never a crash."""
    if root is None or not root.is_dir():
        return {}
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if any(part in _SKIP_DIRS for part in rel.split("/")):
            continue
        try:
            out[rel] = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
    return out


def _holdout_assertion_values(experiment_dir: Path, holdouts_dir: str) -> tuple[str, ...]:
    values: list[str] = []
    root = Path(holdouts_dir)
    if not root.is_absolute():
        root = experiment_dir / holdouts_dir
    for _, text in sorted(_read_text_files(root if root.is_dir() else None).items()):
        for v in extract_assertion_values(text):
            if v not in values:
                values.append(v)
    return tuple(values)


def _read_transcript(artifacts_path) -> str:
    """Post-redaction transcript, or "" if absent — the EVAL-9 convention: an
    empty transcript reviews fail-closed downstream, never crashes the scan."""
    if not artifacts_path:
        return ""
    p = Path(artifacts_path) / "transcript.txt"
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def run_forensics(
    experiment_dir: Path,
    *,
    ctx: EventContext,
    review: bool = True,
    provider=None,
    provider_model: Optional[str] = None,
) -> dict:
    """The scan core: assemble → detect → (review) → one ledgered report."""
    from ..blind.core import arm_canaries
    from ..corpus.commit import load_task_dicts
    from ..ledger import events
    from ..ledger.query import find_events
    from ..plan.lock import assert_lock
    from ..run.trajectory import resolve_trajectory
    from ..schema.experiment import ExperimentSpec
    from .review import forensic_review

    experiment_dir = Path(experiment_dir)
    spec_path = experiment_dir / "experiment.yaml"
    ledger_path = experiment_dir / "ledger.ndjson"
    assert_lock(spec_path, ledger_path)
    spec = ExperimentSpec.from_yaml(spec_path)
    tasks = {t["id"]: t for t in load_task_dicts(experiment_dir)}
    canaries = arm_canaries(spec.arms)

    passed_by_trial: dict[str, bool] = {
        ev["trial_id"]: bool(ev["binary_score"])
        for ev in find_events(ledger_path, events.GRADE)
    }

    metrics: dict[str, dict] = {}
    flags: list[dict] = []
    gaps: list[dict] = []
    reviews: dict[str, dict] = {}
    trial_events = find_events(ledger_path, events.TRIAL)
    for ev in trial_events:
        rec = ev["trial_record"]
        trial_id = rec["trial_id"]
        artifacts_path = rec.get("artifacts_path")
        status, record = resolve_trajectory(artifacts_path, ev.get("trajectory_sha"))
        if status == "verified":
            metrics[trial_id] = trajectory_metrics(record)
        else:
            # AC-6: partial coverage is data with the verifier's named reason
            gaps.append({"trial_id": trial_id, "reason": status})

        task = tasks.get(rec["task_id"], {})
        holdouts_dir = task.get("holdouts_dir") or ""
        workspace_root = Path(artifacts_path).parent if artifacts_path else None
        evidence = TrialEvidence(
            trial_id=trial_id,
            task_id=rec["task_id"],
            arm=rec["arm"],
            trajectory=record,
            passed=passed_by_trial.get(trial_id),
            holdout_paths=(holdouts_dir,) if holdouts_dir else (),
            workspace_files=_read_text_files(workspace_root),
            # tasks.yaml carries no pristine workspace content; detectors fall
            # back to trajectory-attributed edits and stay silent when neither
            # can attribute [plan §4.3]
            pristine_files={},
            holdout_assertion_values=(
                _holdout_assertion_values(experiment_dir, holdouts_dir)
                if holdouts_dir
                else ()
            ),
        )
        flags.extend(run_detectors(evidence))

        if review:
            reviews[trial_id] = forensic_review(
                trial_id,
                _read_transcript(artifacts_path),
                canaries=canaries,
                provider=provider,
                provider_model=provider_model or spec.judge.model,
            ).model_dump(mode="json")

    report = {
        "vocabulary_version": FORENSICS_VOCABULARY_VERSION,
        "metrics": metrics,
        "flags": flags,
        "coverage": {
            "trials": len(trial_events),
            "covered": len(metrics),
            "gaps": gaps,
        },
    }
    if review:
        report["reviews"] = reviews
    record_forensics_report(ledger_path, ctx, forensics_report=report)
    return report


def register(app: typer.Typer) -> None:
    forensics_app = typer.Typer(
        help="Transcript forensics: metrics, gaming detectors, advisory review [EVAL-11].",
        no_args_is_help=True,
    )

    @forensics_app.command("scan")
    def forensics_scan(
        experiment_dir: Path = typer.Argument(..., help="Dir with experiment.yaml"),
        review: bool = typer.Option(
            True, "--review/--no-review",
            help="Run the blinded advisory LLM pass (fails closed to CANT_REVIEW)",
        ),
        model: str = typer.Option(
            None, "--model", help="Provider model for the review (default: judge model)"
        ),
        actor: str = typer.Option(None, "--actor", help="Actor on the report event [GR-12]"),
    ) -> None:
        """Scan every trial; append exactly one forensics_report event."""
        ctx = EventContext(
            experiment_id=Path(experiment_dir).name, actor=_resolve_actor_or_exit(actor)
        )
        report = run_forensics(
            Path(experiment_dir), ctx=ctx, review=review, provider_model=model
        )
        cov = report["coverage"]
        typer.echo(
            f"forensics: {cov['covered']}/{cov['trials']} trial(s) covered, "
            f"{len(report['flags'])} flag(s), "
            f"{len(cov['gaps'])} coverage gap(s)"
        )

    @forensics_app.command("record")
    def forensics_record(
        experiment_dir: Path = typer.Argument(..., help="Dir with ledger.ndjson"),
        trial_id: str = typer.Option(..., "--trial-id"),
        labels_json: Path = typer.Option(
            ..., "--labels", help='JSON {"<detector_id>": true|false, ...}'
        ),
        stratum: str = typer.Option(
            "mandatory", "--stratum", help="EVAL-7 review stratum: mandatory | floor"
        ),
        actor: str = typer.Option(None, "--actor", help="Human reviewer identity [GR-12]"),
    ) -> None:
        """Record a human per-detector spot-check [AC-4, D006]."""
        labels = json.loads(labels_json.read_text(encoding="utf-8"))
        unknown = sorted(set(labels) - set(DETECTOR_IDS))
        if unknown or not labels or not all(isinstance(v, bool) for v in labels.values()):
            typer.echo(
                f"labels must map known detector ids {sorted(DETECTOR_IDS)} to booleans; "
                f"got unknown={unknown} in {labels}",
                err=True,
            )
            raise typer.Exit(code=2)
        ctx = EventContext(
            experiment_id=Path(experiment_dir).name, actor=_resolve_actor_or_exit(actor)
        )
        record_forensic_spotcheck(
            Path(experiment_dir) / "ledger.ndjson", ctx,
            trial_id=trial_id, labels=labels, stratum=stratum,
        )
        typer.echo(f"recorded forensic spot-check for {trial_id}")

    @forensics_app.command("quarantine")
    def forensics_quarantine(
        experiment_dir: Path = typer.Argument(..., help="Dir with ledger.ndjson"),
        trial_id: str = typer.Option(..., "--trial-id"),
        reason: str = typer.Option(..., "--reason", help="Why this trial is excluded"),
        actor: str = typer.Option(None, "--actor", help="Operator identity [GR-12]"),
    ) -> None:
        """Ledger the operator disposition: exclude a trial, disclosed [D007]."""
        ctx = EventContext(
            experiment_id=Path(experiment_dir).name, actor=_resolve_actor_or_exit(actor)
        )
        record_forensic_quarantine(
            Path(experiment_dir) / "ledger.ndjson", ctx, trial_id=trial_id, reason=reason
        )
        typer.echo(f"quarantined {trial_id} (excluded from comparisons, disclosed)")

    app.add_typer(forensics_app, name="forensics")


# --- one-event property registration [EVAL-3 §M7, XC-3] ----------------------
def _prepare_forensics(ctx_dir: str) -> None:
    from ..plan.lock import lock_experiment

    d = Path(ctx_dir)
    lock_experiment(
        d / "experiment.yaml", d / "ledger.ndjson",
        ctx=EventContext(experiment_id="prop"), n_sim=8, n_boot=40, deltas=[0.2, 0.4],
    )


def _forensics_entrypoint(ctx_dir: str) -> None:
    # A scan over a trial-less ledger is a full-coverage-of-nothing report —
    # still exactly one forensics_report event (deterministic tier only; the
    # advisory pass needs no provider when there is nothing to review).
    d = Path(ctx_dir)
    run_forensics(d, ctx=EventContext(experiment_id="prop"), review=False)


def _register() -> None:
    from ..entrypoints import register_entrypoint

    register_entrypoint("forensics", _forensics_entrypoint, prepare=_prepare_forensics)


_register()
