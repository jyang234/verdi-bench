"""``contamination`` stage API [refactor 02 §3].

The importable entry point behind ``bench contamination probe`` [EVAL-10 AC-3]:
load each task's references once, run the deterministic AC-4 overlap scan over
the run's trial artifacts, then the AC-3 memory probes per arm model, and ledger
the merged measurement as one ``contamination_probe`` event. Probes run here,
never inside trial containers [constraint]. The typer verb is a thin shell that
resolves the actor, maps the refusals, and echoes the scan alarms + per-arm
outcomes from the returned :class:`ContaminationProbeOutcome`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class ContaminationProbeOutcome:
    """What the probe computed, for the shell to render in the body's order.

    ``alarms``/``skipped`` are the scan's insulation-breach + unscanned notices
    (echoed to stderr before the probe outcome); ``probe`` is the ledgered
    ``contamination_probe`` payload (``None`` only when the probe itself refused
    before ledgering, carried instead as ``probe_error`` — exit 2)."""

    alarms: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    probe: dict | None = None
    probe_error: str | None = None


def contamination_probe(
    experiment_dir, *, ctx, manifest_path=None, oracle_dir=None,
    scan_artifacts: bool = True,
) -> ContaminationProbeOutcome:
    """Probe every arm model for training-set membership [AC-3, D002].

    Raises ``OverlapError`` (the CLI maps to exit 2) for a broken holdout layout
    or an unscannable artifact set; a probe-time refusal (``ProbeError``/
    ``OverlapError`` from the memory probe) is returned as ``probe_error`` so the
    scan's alarms still precede it, exactly as the inline body ordered them."""
    from ..corpus.commit import load_task_dicts
    from ..corpus.registry import CorpusManifest
    from ..plan.lock import assert_lock
    from .overlap import OverlapError
    from .probe import ProbeError, ProbeTask, run_memory_probe
    from .scan import TaskReferences, scan_trials

    experiment_dir = Path(experiment_dir)
    ledger_path = experiment_dir / "ledger.ndjson"
    # PRA-M2: contamination is a ledgered stage, so it must gate on the lock like
    # every other stage — assert_lock chain-verifies and returns the spec parsed
    # from the locked bytes (no second read; PRA-M1).
    spec = assert_lock(experiment_dir / "experiment.yaml", ledger_path).spec
    task_dicts = load_task_dicts(experiment_dir)
    manifest = (
        CorpusManifest.load(manifest_path) if manifest_path is not None else None
    )

    def _oracle_for(task_id: str) -> Optional[str]:
        if oracle_dir is None:
            return None
        p = oracle_dir / f"{task_id}.txt"
        if not p.exists():
            return None
        return p.read_text(encoding="utf-8", errors="replace")

    def _holdouts_for(task: dict) -> tuple[str, ...]:
        rel = task.get("holdouts_dir")
        if not rel:
            return ()
        root = experiment_dir / rel
        if not root.is_dir():
            # a DECLARED holdout dir that is gone is a broken experiment layout —
            # refusing beats silently disabling the leak channel
            raise OverlapError(
                f"task {task['id']!r} declares holdouts_dir {rel!r} but "
                f"{root} is not a directory; refusing a scan that would "
                "silently skip its holdout references"
            )
        return tuple(
            p.read_text(encoding="utf-8", errors="replace")
            for p in sorted(root.rglob("*"))
            if p.is_file()
        )

    # Per-task inputs, loaded exactly once: the probe and the scan must see the
    # same oracle/holdout content.
    tasks: list[ProbeTask] = []
    references: dict[str, TaskReferences] = {}
    for t in task_dicts:
        entry = manifest.task(t["id"]) if manifest is not None else None
        oracle = _oracle_for(t["id"])
        references[t["id"]] = TaskReferences(oracle=oracle, holdouts=_holdouts_for(t))
        tasks.append(
            ProbeTask(
                task_id=t["id"],
                task_sha=(entry.sha if entry is not None else ""),
                prompt=t.get("prompt", ""),
                oracle=oracle,
                has_canary=entry is not None and entry.canary_sha256 is not None,
            )
        )
    threshold = (
        spec.contamination.overlap_threshold
        if spec.contamination is not None
        else None
    )

    overlap_flags: dict[str, dict[str, bool]] = {}
    scan_alarms: list[str] = []
    scan_skipped: list[str] = []
    if scan_artifacts:
        report = scan_trials(ledger_path, references, threshold=threshold)
        overlap_flags = report.overlap_flags
        scan_alarms = report.alarms
        scan_skipped = report.skipped

    try:
        event = run_memory_probe(
            ledger_path, ctx,
            arms=spec.arms, tasks=tasks,
            threshold=threshold, overlap_flags=overlap_flags,
            alarms=scan_alarms, skipped=scan_skipped,
        )
    except (ProbeError, OverlapError) as e:
        return ContaminationProbeOutcome(
            alarms=scan_alarms, skipped=scan_skipped, probe=None, probe_error=str(e)
        )
    return ContaminationProbeOutcome(
        alarms=scan_alarms, skipped=scan_skipped, probe=event["probe"],
    )
