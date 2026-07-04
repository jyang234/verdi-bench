"""``CorpusManifest`` — the versioned record of a task corpus [EVAL-8 §4.1].

A finding cites a corpus by ``(semver, task shas)``; this manifest is the
byte-reconstructible commitment behind that citation [AC-6]. Validation is
structural, not advisory:

* every task is in **Harbor task format** — a non-harbor ``format`` fails
  validation [D003, master plan §7.4/EVAL-1-D005];
* mutating task content without a semver bump fails validation; a bump
  re-triggers the flake baseline for the changed tasks [AC-6];
* an ``internal`` corpus ``boundary_path`` is realpath-resolved and refused if
  it resolves inside the instrument repo tree [AC-5, EVAL-1 invariant].

The admission gate lives in :mod:`harness.corpus.admit`; this module owns the
manifest shape and its self-consistency rules only (single responsibility).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
HARBOR_FORMAT = "harbor"

# Repo root of *this instrument* (…/harness/corpus/registry.py → parents[2]).
INSTRUMENT_ROOT = Path(__file__).resolve().parents[2]


class CorpusError(ValueError):
    """Base for corpus-manifest failures."""


class CorpusMutationError(CorpusError):
    """Task content changed without a semver bump [AC-6]."""


class BoundaryViolationError(CorpusError):
    """An internal corpus targeted a path inside the instrument repo [AC-5]."""


class UnsafeTaskIdError(CorpusError):
    """A registry-supplied task_id would traverse outside the task cache [CO-6]."""


def _assert_safe_task_id(task_id: str) -> str:
    """Reject a task_id that could escape the cache dir when used as a filename.

    The id becomes ``<cache>/tasks/<task_id>.json``; a separator, ``..`` segment,
    absolute path, or empty id could write outside the cache, so it is refused at
    the manifest boundary — a traversal id is unrepresentable [CO-6]."""
    if not task_id or task_id in (".", ".."):
        raise UnsafeTaskIdError(f"empty or dotted task_id {task_id!r}")
    if "/" in task_id or "\\" in task_id or "\x00" in task_id:
        raise UnsafeTaskIdError(f"task_id {task_id!r} contains a path separator")
    if ".." in Path(task_id).parts or Path(task_id).is_absolute():
        raise UnsafeTaskIdError(f"task_id {task_id!r} traverses outside the cache")
    return task_id


def assert_outside_instrument(path) -> None:
    """Refuse a write destination inside the instrument repo tree [CO-1].

    Internal corpora (candidate JSON, internal manifests) carry holdout content
    and must never be written into the instrument repo. Enforced on the actual
    destination path, not just a declared boundary string."""
    resolved = Path(path).resolve()
    if resolved == INSTRUMENT_ROOT or INSTRUMENT_ROOT in resolved.parents:
        raise BoundaryViolationError(
            f"refusing to write {resolved} inside the instrument repo "
            f"{INSTRUMENT_ROOT}; internal corpora never enter the instrument repo [AC-5]"
        )


class TaskEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    sha: str
    format: Literal["harbor"] = HARBOR_FORMAT
    status: Literal["admitted", "pending-curation", "quarantined"] = "pending-curation"
    baseline_ref: Optional[str] = None
    plugins: list[str] = []
    # dataset-provided metadata used for stratification (category/difficulty/…).
    metadata: dict = {}
    # the miner who staged this candidate — the approver must NOT be the miner
    # (self-approval bar, enforced at admission) [CO-7, D-P4-3].
    miner: Optional[str] = None
    # EVAL-10 AC-1: when the task's source material entered the world (RFC 3339,
    # from the merge request's merged_at — input data, never a wall-clock read).
    # Absent ⇒ the dating channel yields an honest `unknown`, never clean.
    created_at: Optional[str] = None
    # EVAL-10 AC-2: sha256 of the derived contamination canary. The canary VALUE
    # is a secret of the instrument and never enters the manifest — hash only.
    canary_sha256: Optional[str] = None
    # ``format`` is a Literal ⇒ a non-harbor task fails schema by construction,
    # the strongest form of the D003 rule (master plan §7.4 / EVAL-1-D005).

    @field_validator("task_id")
    @classmethod
    def _task_id_is_safe(cls, v: str) -> str:
        return _assert_safe_task_id(v)

    @field_validator("created_at")
    @classmethod
    def _created_at_parses(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        # Same parser the dating channel runs, so load-time acceptance is
        # analysis-time acceptance [EVAL-10 AC-1].
        from ..schema.dates import parse_rfc3339

        parse_rfc3339(v, field="task created_at")
        return v


class Dataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    version: str


class CalibrationSubset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seed: int
    strata: dict  # {stratum_key, allocation: {stratum: count}} — audit trail
    task_ids: list[str]


class Calibration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subset: Optional[CalibrationSubset] = None
    status: Literal["none", "subset-validated", "full-run-validated"] = "none"
    runs: list[dict] = []


class CorpusManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corpus_id: str
    semver: str
    kind: Literal["public", "internal"]
    dataset: Optional[Dataset] = None
    tasks: list[TaskEntry] = []
    calibration: Calibration = Calibration()
    boundary_path: Optional[str] = None

    @field_validator("semver")
    @classmethod
    def _semver_shape(cls, v: str) -> str:
        if not _SEMVER_RE.match(v):
            raise CorpusError(f"semver {v!r} is not MAJOR.MINOR.PATCH")
        return v

    # --- helpers -----------------------------------------------------------
    def task(self, task_id: str) -> Optional[TaskEntry]:
        for t in self.tasks:
            if t.task_id == task_id:
                return t
        return None

    def task_shas(self) -> dict[str, str]:
        """``{task_id: sha}`` in sorted order — the provenance the finding cites."""
        return {t.task_id: t.sha for t in sorted(self.tasks, key=lambda t: t.task_id)}

    def provenance_ref(self) -> dict:
        """Corpus provenance for the EVAL-6 findings block [AC-6]."""
        return {
            "corpus_id": self.corpus_id,
            "semver": self.semver,
            "kind": self.kind,
            "task_shas": self.task_shas(),
            "calibration_status": self.calibration.status,
        }

    # --- boundary enforcement [AC-5] --------------------------------------
    def assert_boundary(self) -> None:
        """Refuse an internal corpus whose boundary resolves inside this repo.

        Public corpora cache locally and need no boundary. An internal corpus
        must declare a ``boundary_path`` outside the instrument tree — the
        instrument repo is a structurally-refused target.
        """
        if self.kind != "internal":
            return
        if not self.boundary_path:
            raise BoundaryViolationError(
                "an internal corpus must declare a boundary_path outside the "
                "instrument repo [AC-5]"
            )
        # One containment rule, one implementation — the declared boundary must be
        # outside the instrument repo, same check the write destination gets.
        assert_outside_instrument(self.boundary_path)

    # --- versioning / mutation rule [AC-6] --------------------------------
    def assert_valid_successor(self, previous: "CorpusManifest") -> None:
        """Reject content mutation that skipped a semver bump.

        Same semver ⇒ the task (id, sha) set must be *identical*. Any content
        change — a changed sha, an added or removed task — requires a bump.
        """
        if self.corpus_id != previous.corpus_id:
            raise CorpusError("cannot compare manifests of different corpora")
        if self.semver != previous.semver:
            return  # a bump is exactly what a mutation requires; allowed
        prev = previous.task_shas()
        cur = self.task_shas()
        if prev != cur:
            changed = sorted(
                tid
                for tid in set(prev) | set(cur)
                if prev.get(tid) != cur.get(tid)
            )
            raise CorpusMutationError(
                f"tasks {changed} changed but semver stayed {self.semver}; "
                "mutating task content requires a semver bump [AC-6]"
            )

    def retrigger_baselines(self, previous: "CorpusManifest") -> None:
        """After a bump, changed tasks lose their stale baseline (must re-run).

        A semver bump re-triggers the flake baseline [AC-6]: a task whose sha
        changed cannot ride the previous version's baseline into ``admitted``.
        """
        prev = previous.task_shas()
        for t in self.tasks:
            if prev.get(t.task_id) not in (None, t.sha):
                t.baseline_ref = None
                if t.status == "admitted":
                    t.status = "pending-curation"

    def stage_candidate(
        self,
        task_id: str,
        *,
        sha: str,
        miner: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> TaskEntry:
        """Insert a mined candidate as a ``pending-curation`` task [CO-8].

        This is the missing mine→manifest link: ``admit_task`` requires the
        candidate to already be a manifest entry, so mining stages it here (with
        its content sha + miner, and the source MR's ``merged_at`` as
        ``created_at`` [EVAL-10 AC-1]) before curation. A duplicate task_id is
        refused — a silent overwrite would drop the prior candidate's
        provenance."""
        if self.task(task_id) is not None:
            raise CorpusError(f"task {task_id!r} already exists in manifest {self.corpus_id!r}")
        entry = TaskEntry(
            task_id=task_id, sha=sha, status="pending-curation", miner=miner,
            created_at=created_at,
        )
        self.tasks.append(entry)
        return entry

    def is_schedulable(self, task_id: str) -> bool:
        """A task is schedulable only once admitted (not pending/quarantined)."""
        t = self.task(task_id)
        return t is not None and t.status == "admitted"

    # --- calibration status lifecycle [AC-2] ------------------------------
    def record_calibration_run(self, run: dict, *, kind: Literal["subset", "full"]) -> None:
        """Record a calibration run and advance the status monotonically.

        ``none → subset-validated → full-run-validated``. A subset run advances
        to ``subset-validated``; a full run advances to ``full-run-validated``.
        The first official internal finding requires ``full-run-validated``
        (enforced on EVAL-6's official-render path).
        """
        self.calibration.runs.append(run)
        if kind == "full":
            self.calibration.status = "full-run-validated"
        elif self.calibration.status == "none":
            self.calibration.status = "subset-validated"

    @property
    def official_ready(self) -> bool:
        """Whether an official finding may cite this corpus [AC-2]."""
        return self.calibration.status == "full-run-validated"

    # --- file I/O ----------------------------------------------------------
    def to_json(self) -> str:
        """Deterministic serialization: sorted keys, stable separators."""
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    def save(self, path) -> Path:
        path = Path(path)
        # validate structural rules before persisting; an internal corpus is also
        # refused a destination inside the instrument repo [CO-1].
        self.assert_boundary()
        if self.kind == "internal":
            assert_outside_instrument(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json() + "\n", encoding="utf-8")
        return path

    @classmethod
    def load(cls, path) -> "CorpusManifest":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)
