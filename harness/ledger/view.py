"""One read facade over a ledger file [refactor 06 §1].

``LedgerView`` parses a ledger **once** and exposes typed, memoized, read-only
projections — the joins that ``find_events`` call sites re-implement across the
codebase (trial indexes, latest-grade-by-trial, quarantine sets, verdict maps,
and the full per-trial "trial story"). It is strictly additive: the
``harness.ledger.query`` functions (``find_events`` &c.) stay, and consumers
migrate to the facade opportunistically.

**Read path only.** This module imports nothing that appends — no
``ledger.chain``, no ``ledger.events`` write funnel — so it cannot mutate a
chain. The only ``ledger.events`` use is the read-side event-name constants.

**Snapshot semantics.** One ``LedgerView`` instance is one snapshot of one
file: it reads the ledger at first access and memoizes every projection over
that single parse. It never re-reads. A view therefore does **not** see events
appended after it was constructed — for incremental/live tailing use
``query.tail_events`` (which this module deliberately does not touch); for a
fresh snapshot construct a new ``LedgerView``. Memoization is confined to the
instance, so it can never leak across files or go stale within its own life.

**The sha-hoist reader rule.** ``trials()`` reads ``trajectory_sha`` /
``flight_recorder_sha`` from the *event*, never from the embedded
``trial_record`` (whose copies are transport-only and ``None`` after a
round-trip) — the documented rule on ``events.record_trial``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import events
from .query import assert_chain, read_events


@dataclass(frozen=True)
class TrialEventView:
    """A ``trial`` event's record plus the two top-level shas hoisted onto it.

    ``trajectory_sha`` / ``flight_recorder_sha`` come from the EVENT — the
    single source of truth per the ``record_trial`` reader rule — so a consumer
    verifying the persisted artifact against the chain never has to reach into
    the embedded ``trial_record`` (whose sha fields are transport-only).
    """

    record: dict
    trajectory_sha: Optional[str]
    flight_recorder_sha: Optional[str]

    @property
    def trial_id(self) -> str:
        return self.record["trial_id"]


@dataclass(frozen=True)
class TrialStory:
    """The full per-trial join — everything the instrument knows about one trial.

    The six correlations the per-trial drill-down hand-rolls: the ledgered
    ``record`` and its sha-verified ``trajectory`` / ``flight_recorder``
    (status always stated, steps/entries only when ``verified``), the
    grade/cant-grade history, the judge ``verdicts`` of its ``(task,
    repetition)`` comparison (joined on the deterministic ``comparison_id``,
    never guessed), the latest forensic report's view of it, any quarantine
    disposition, and its egress flags. Nulls stay null [EVAL-4-D004].
    """

    trial_id: str
    record: dict
    comparison_id: str
    trajectory_status: str
    trajectory_steps: Optional[list]
    flight_recorder_status: str
    flight_recorder_entries: Optional[list]
    grades: list[dict]
    cant_grades: list[dict]
    verdicts: list[dict]
    forensics_metrics: Optional[dict]
    forensics_flags: list[dict]
    quarantine: Optional[dict]


class LedgerView:
    """Parse once; typed, memoized projections. Read-only by construction.

    Construct with ``verify=True`` to run the chain assertion before any read,
    so a tampered chain refuses the whole view instead of yielding evidence
    line by line [PL-6/CO-5].
    """

    def __init__(self, ledger: Path | str, *, verify: bool = False) -> None:
        self.path = Path(ledger)
        if verify:
            # Reuse the read-side gate; an absent/empty ledger is "nothing
            # recorded yet", not tampering (query.assert_chain's contract).
            assert_chain(self.path)
        self._events: Optional[list[dict]] = None
        self._by_kind: dict[str, list[dict]] = {}
        self._trials: Optional[list[TrialEventView]] = None
        self._grades_by_trial: Optional[dict[str, list[dict]]] = None
        self._latest_grade_by_trial: Optional[dict[str, dict]] = None
        self._verdicts_by_comparison: Optional[dict[str, dict]] = None
        self._quarantined: Optional[set[str]] = None

    # --- the single parse -----------------------------------------------------
    @property
    def events(self) -> list[dict]:
        """Every event, in ledger order — parsed once, then memoized."""
        if self._events is None:
            self._events = read_events(self.path)
        return self._events

    def by_kind(self, kind: str) -> list[dict]:
        """Events of one ``event`` type, in ledger order (memoized per kind).

        Parity with ``query.find_events``: same filter, same order. The
        returned list is the memoized projection — a read-only view; callers
        that need to mutate must copy (``list(...)``) as they did before.
        """
        cached = self._by_kind.get(kind)
        if cached is None:
            cached = [e for e in self.events if e.get("event") == kind]
            self._by_kind[kind] = cached
        return cached

    def latest(self, kind: str) -> Optional[dict]:
        """The last event of ``kind`` (ledger order), or ``None`` — parity with
        ``query.latest_event``."""
        found = self.by_kind(kind)
        return found[-1] if found else None

    # --- typed projections ----------------------------------------------------
    def trials(self) -> list[TrialEventView]:
        """The trial events as :class:`TrialEventView`, in ledger order.

        Each carries the ``trial_record`` and the two hoisted event-level shas.
        """
        if self._trials is None:
            self._trials = [
                TrialEventView(
                    record=e["trial_record"],
                    trajectory_sha=e.get("trajectory_sha"),
                    flight_recorder_sha=e.get("flight_recorder_sha"),
                )
                for e in self.by_kind(events.TRIAL)
            ]
        return self._trials

    def grades_by_trial(self) -> dict[str, list[dict]]:
        """``trial_id -> [grade event, ...]`` in ledger order (every grade)."""
        if self._grades_by_trial is None:
            acc: dict[str, list[dict]] = {}
            for e in self.by_kind(events.GRADE):
                acc.setdefault(e["trial_id"], []).append(e)
            self._grades_by_trial = acc
        return self._grades_by_trial

    def latest_grade_by_trial(self) -> dict[str, dict]:
        """``trial_id -> latest grade event`` — latest-wins in ledger order.

        Identical semantics to the ``{e["trial_id"]: e for e in
        find_events(..., GRADE)}`` idiom the grade/forensics/contamination
        consumers hand-roll (a later grade for a trial overwrites an earlier).
        """
        if self._latest_grade_by_trial is None:
            self._latest_grade_by_trial = {
                trial_id: grades[-1]
                for trial_id, grades in self.grades_by_trial().items()
            }
        return self._latest_grade_by_trial

    def verdicts_by_comparison(self) -> dict[str, dict]:
        """``comparison_id -> latest judge verdict`` (latest-wins, ledger order).

        Verdicts without a ``comparison_id`` are excluded (they cannot key a
        comparison). For the *list* of every verdict on a comparison, filter
        ``by_kind(JUDGE_VERDICT)`` — this map is the last-write-wins index.
        """
        if self._verdicts_by_comparison is None:
            acc: dict[str, dict] = {}
            for e in self.by_kind(events.JUDGE_VERDICT):
                verdict = e.get("verdict") or {}
                cid = verdict.get("comparison_id")
                if cid is not None:
                    acc[cid] = verdict
            self._verdicts_by_comparison = acc
        return self._verdicts_by_comparison

    def quarantined_trial_ids(self) -> set[str]:
        """Trial ids with a ledgered operator quarantine disposition [D007]."""
        if self._quarantined is None:
            self._quarantined = {
                e["forensic_quarantine"]["trial_id"]
                for e in self.by_kind(events.FORENSIC_QUARANTINE)
            }
        return self._quarantined

    @staticmethod
    def provenance_ts(event: dict) -> Optional[str]:
        """The ``provenance.ts`` of an event, defensively — ``None`` if either
        the provenance block or its timestamp is absent (the shape consumers
        dig for by hand everywhere)."""
        return (event.get("provenance") or {}).get("ts")

    # --- the full per-trial join ---------------------------------------------
    def trial_story(self, trial_id: str) -> Optional[TrialStory]:
        """Assemble the full :class:`TrialStory` for ``trial_id``, or ``None``
        if the ledger has no trial record with that id (the caller's 404).

        Reaches the trajectory/flight-recorder verifiers (``harness.run``) and
        the deterministic ``comparison_id`` (``harness.judge``) through
        function-local imports: those subsystems import ``harness.ledger``, so
        importing them at module load would form a load-time cycle. Reading is
        not writing — the verifiers only read and hash persisted artifacts.
        """
        from ..judge.assemble import comparison_id_for
        from ..run.flight_recorder import resolve_flight_recorder
        from ..run.trajectory import resolve_trajectory

        # Latest trial event carrying this id wins (a plain assignment in the
        # hand-rolled scan) — take the shas from the EVENT, never the record.
        record: Optional[dict] = None
        trajectory_sha = None
        flight_recorder_sha = None
        for ev in self.by_kind(events.TRIAL):
            rec = ev.get("trial_record") or {}
            if rec.get("trial_id") == trial_id:
                record = rec
                trajectory_sha = ev.get("trajectory_sha")
                flight_recorder_sha = ev.get("flight_recorder_sha")
        if record is None:
            return None

        grades = [
            {
                "task_sha": ev.get("task_sha"),
                "assertions": ev.get("assertions"),
                "binary_score": ev.get("binary_score"),
                "fractional_score": ev.get("fractional_score"),
                "grader": ev.get("grader"),
                "override_of": ev.get("override_of"),
                "ts": self.provenance_ts(ev),
            }
            for ev in self.by_kind(events.GRADE)
            if ev.get("trial_id") == trial_id
        ]
        cant_grades = [
            {
                "reason": ev.get("reason"),
                "override_of": ev.get("override_of"),
                "ts": self.provenance_ts(ev),
            }
            for ev in self.by_kind(events.CANT_GRADE)
            if ev.get("trial_id") == trial_id
        ]

        # Judge verdicts join on the deterministic comparison id of this trial's
        # (task, repetition) cell — exactly what the judge stamped [D-P4-1].
        cmp_id = comparison_id_for(record.get("task_id"), record.get("repetition", 0))
        verdicts = [
            v
            for ev in self.by_kind(events.JUDGE_VERDICT)
            if (v := ev.get("verdict") or {}).get("comparison_id") == cmp_id
        ]

        # Forensics: the LATEST report's view of this trial (latest-wins); a
        # quarantine keeps the last matching disposition.
        quarantine: Optional[dict] = None
        for ev in self.by_kind(events.FORENSIC_QUARANTINE):
            fq = ev.get("forensic_quarantine") or {}
            if fq.get("trial_id") == trial_id:
                quarantine = {"reason": fq.get("reason")}
        forensics_metrics: Optional[dict] = None
        forensics_flags: list[dict] = []
        report_ev = self.latest(events.FORENSICS_REPORT)
        if report_ev is not None:
            fr = report_ev.get("forensics_report") or {}
            forensics_metrics = (fr.get("metrics") or {}).get(trial_id)
            forensics_flags = [
                f for f in (fr.get("flags") or []) if f.get("trial_id") == trial_id
            ]

        artifacts_path = record.get("artifacts_path")
        status, trajectory = resolve_trajectory(artifacts_path, trajectory_sha)
        steps = (
            [s.model_dump(mode="json") for s in trajectory.steps]
            if trajectory is not None
            else None
        )
        fr_status, fr_record = resolve_flight_recorder(artifacts_path, flight_recorder_sha)
        fr_entries = (
            [e.model_dump(mode="json") for e in fr_record.entries]
            if fr_record is not None
            else None
        )

        return TrialStory(
            trial_id=trial_id,
            record=record,
            comparison_id=cmp_id,
            trajectory_status=status,
            trajectory_steps=steps,
            flight_recorder_status=fr_status,
            flight_recorder_entries=fr_entries,
            grades=grades,
            cant_grades=cant_grades,
            verdicts=verdicts,
            forensics_metrics=forensics_metrics,
            forensics_flags=forensics_flags,
            quarantine=quarantine,
        )
