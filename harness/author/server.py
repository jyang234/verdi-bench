"""Authoring HTTP surface [EVAL-17 AC-1..AC-5, D001..D004].

Route posture, enforced by shape: every preview is a GET over the saved
draft's file bytes (validate / power / schedule / sha read what the last
save wrote — byte fidelity is a property of the flow, not a promise), and
the only mutating routes are the two enumerated ceremony endpoints:
``POST /api/draft`` (writes allowlisted files into an UNLOCKED draft
directory) and ``POST /api/lock`` (calls ``lock_experiment`` verbatim —
exactly one ledgered event). Nothing here re-implements validation, power,
scheduling, or locking; the endpoints are transport over the plan/schema
seams the CLI already trusts.

The actor binds at launch (resolve_actor — refused loudly, never
"unknown"); ``attested_by`` is an explicit ceremony field. Loopback by
default: this surface can mutate, so exposing it is a deliberate act.

[refactor 07 §4] The mechanical transport (``_send``/``_json``/``_read_json_object``,
the host+CSRF guards, route-table dispatch, the ``type("Bound…Handler", …)``
factory including this server's state injection) comes from the tier-neutral
:class:`harness.webkit.http.JsonRouteHandler`. The two-POST-only mutation
surface, the per-method error semantics (a ``/api/schedule`` refusal is a 500,
the ceremony refusals are typed 409s), and the preview parity stay here.
"""

from __future__ import annotations

import re
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

import yaml

from ..corpus.commit import TaskCommitmentError, load_task_dicts
from ..ledger.events import EventContext
from ..ledger.query import ChainIntegrityError, find_events
from ..plan.interleave import derive_schedule, enumerate_trials
from ..plan.lock import (
    AlreadyLockedError,
    RubricCommitmentError,
    UnderpoweredError,
    UnknownArmPlatformError,
    check_arm_platforms,
    commit_rubric,
    lock_experiment,
    spec_sha256,
)
from ..plan.power import AssumedVariance, mde_check
from ..schema.errors import SpecError
from ..schema.experiment import ExperimentSpec
from ..webkit.http import JsonRouteHandler, NotFound, Refused, bind_handler
from .page import AUTHOR_PAGE

DEFAULT_HOST = "127.0.0.1"  # a mutating surface: exposing it is a deliberate act
DEFAULT_AUTHOR_PORT = 8390

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# The only files a draft write may touch — the pre-registration inputs.
_DRAFT_FILE_RE = re.compile(r"^(experiment\.yaml|tasks\.yaml|rubrics/[A-Za-z0-9._-]+\.md)$")
# Quick-preview power settings; the lock recomputes at full fidelity and the
# response says so [AC-1 note].
_QUICK_POWER = {"n_sim": 8, "n_boot": 40, "deltas": [0.05, 0.1, 0.2, 0.3, 0.4, 0.5]}


def _rubric_commits(d: Path, spec: ExperimentSpec) -> bool:
    """True iff the lock's own ``commit_rubric`` preflight step would accept the
    draft's rubric — preview parity by composition, not re-implementation."""
    try:
        commit_rubric(d / "experiment.yaml", spec)
        return True
    except RubricCommitmentError:
        return False


class _NotFound(NotFound):
    """Route-level 404 with an actionable message."""


class _Refused(Refused):
    """Route-level 409: the operation is refused for a stated reason."""


def _get_error(exc: BaseException) -> Optional[tuple[int, dict]]:
    """Preview-read error map: a missing draft/file is a 404. Everything else —
    including a ``_Refused`` from ``/api/schedule`` on an invalid ``tasks.yaml`` —
    falls through to a served 500, exactly as the original ``do_GET`` ``except``
    ladder (which named only ``_NotFound``) did."""
    if isinstance(exc, _NotFound):
        return (404, {"error": str(exc)})
    return None


def _post_error(exc: BaseException) -> Optional[tuple[int, dict]]:
    """Ceremony error map: a bad name/file is a 404 (plain); a ``_Refused``
    precondition is a 409 (plain); the plan/schema typed refusals are 409s
    rendered with their own ``error_class`` [AC-2]. Anything else → served 500.
    Order preserves the original ``except`` ladder."""
    if isinstance(exc, _NotFound):
        return (404, {"error": str(exc)})
    if isinstance(exc, _Refused):
        return (409, {"error": str(exc)})
    if isinstance(exc, (
        UnderpoweredError,
        AlreadyLockedError,
        TaskCommitmentError,
        ChainIntegrityError,
        RubricCommitmentError,
        SpecError,
    )):
        return (409, {"error_class": type(exc).__name__, "error": str(exc)})
    return None


class AuthorHandler(JsonRouteHandler):
    """The authoring routes over one workspace root (bound by make_author_server)."""

    workspace_root: Path
    actor: str
    lock_kwargs: Optional[dict]  # operational MDE tuning (the bench-plan builders' knob)

    server_version = "verdi-bench-author"

    # -- GET: page + pure preview reads over saved drafts -----------------------
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        q = parse_qs(urlparse(self.path).query)
        table = {
            "/": lambda: self._send(200, AUTHOR_PAGE.encode("utf-8"), "text/html; charset=utf-8"),
            "/favicon.ico": lambda: self._send(204, b"", "image/x-icon"),
            "/api/drafts": lambda: self._json(200, {"actor": self.actor, "drafts": self._drafts()}),
            "/api/draft": lambda: self._json(200, self._draft(q)),
            "/api/validate": lambda: self._json(200, self._validate(q)),
            "/api/power": lambda: self._json(200, self._power(q)),
            "/api/schedule": lambda: self._json(200, self._schedule(q)),
            "/api/sha": lambda: self._json(
                200, {"spec_sha256": spec_sha256(self._dir(q) / "experiment.yaml")}
            ),
        }
        self.dispatch(
            table,
            guard=self._guard_host,  # PRA-M16
            unknown=lambda p: (404, {"error": f"unknown path {p!r}"}),
            error=_get_error,
        )

    # -- POST: the two enumerated ceremony endpoints, nothing else -------------
    def do_POST(self) -> None:  # noqa: N802
        prepared: dict = {}

        def prepare() -> None:
            # PRA-H2: the ceremony endpoints mutate state (draft writes, and the
            # chain-anchored lock). Guard Host + Origin + Content-Type, then read
            # the body (a malformed body is a 404) before dispatch, so a
            # cross-site page cannot forge a lock.
            self._guard_host_and_csrf()
            prepared["body"] = self._read_json_object()

        table = {
            "/api/draft": lambda: self._json(200, self._write_draft(prepared["body"])),
            "/api/lock": lambda: self._json(200, self._lock(prepared["body"])),
        }
        self.dispatch(
            table,
            guard=prepare,
            unknown=lambda p: (404, {"error": f"no such ceremony endpoint {p!r}"}),
            error=_post_error,
        )

    # -- draft resolution --------------------------------------------------------
    def _named(self, name: Optional[str]) -> Path:
        if not name:
            raise _NotFound("pass name=<draft-name>")
        if not _NAME_RE.match(name):
            raise _NotFound(f"invalid draft name {name!r}")
        return self.workspace_root / name

    def _dir(self, q: dict) -> Path:
        d = self._named(q.get("name", [None])[0])
        if not (d / "experiment.yaml").exists():
            raise _NotFound(f"draft {d.name!r} has no experiment.yaml yet")
        return d

    def _is_locked(self, d: Path) -> bool:
        ledger = d / "ledger.ndjson"
        return ledger.exists() and bool(find_events(ledger, "experiment_locked"))

    def _drafts(self) -> list[dict]:
        out = []
        for child in sorted(self.workspace_root.iterdir()):
            if child.is_dir() and (
                (child / "experiment.yaml").exists() or (child / "ledger.ndjson").exists()
            ):
                out.append(
                    {
                        "name": child.name,
                        "locked": self._is_locked(child),
                        "has_spec": (child / "experiment.yaml").exists(),
                        "has_tasks": (child / "tasks.yaml").exists(),
                    }
                )
        return out

    def _draft(self, q: dict) -> dict:
        d = self._named(q.get("name", [None])[0])
        if not d.is_dir():
            raise _NotFound(f"no draft directory {d.name!r}")
        files: dict[str, str] = {}
        for rel in ["experiment.yaml", "tasks.yaml"]:
            p = d / rel
            if p.exists():
                files[rel] = p.read_text(encoding="utf-8")
        rubrics = d / "rubrics"
        if rubrics.is_dir():
            for p in sorted(rubrics.glob("*.md")):
                files[f"rubrics/{p.name}"] = p.read_text(encoding="utf-8")
        doc: dict = {"name": d.name, "locked": self._is_locked(d), "files": files}
        if doc["locked"]:
            lock = find_events(d / "ledger.ndjson", "experiment_locked")[0]
            doc["lock"] = {
                "spec_sha256": lock.get("spec_sha256"),
                "seed": lock.get("seed"),
                "ts": (lock.get("provenance") or {}).get("ts"),
                "attested_by": (lock.get("attestation") or {}).get("attested_by"),
            }
        return doc

    # -- previews (pure reads of the saved draft) ---------------------------------
    def _spec(self, d: Path) -> ExperimentSpec:
        text = (d / "experiment.yaml").read_text(encoding="utf-8")
        return ExperimentSpec.from_yaml_text(text, source=str(d / "experiment.yaml"))

    def _validate(self, q: dict) -> dict:
        """Preview parity with the lock's preflight steps [refactor 02 §4].

        Composes the same steps ``lock_experiment`` runs that can refuse a *fresh
        draft*: spec-parse+hash, arm-platform capability, rubric presence, and
        task validity — so a green preview cannot then refuse at lock. The lock's
        chain-state steps (chain integrity, single-lock) are legitimately skipped:
        a draft under preview has no ledger to verify and is unlocked by
        definition. The power gate is served by ``/api/power`` (a heavy sim and a
        soft, acknowledgeable gate, not a structural refusal). All a pure read.
        """
        d = self._dir(q)
        out: dict = {"name": d.name, "spec_sha256": spec_sha256(d / "experiment.yaml")}
        try:
            spec = self._spec(d)
            out["spec"] = {
                "ok": True,
                "arms": [a.name for a in spec.arms],
                "repetitions": spec.repetitions,
                "primary_metric": spec.primary_metric.value,
                "decision_rule": spec.decision_rule,
                "seed": spec.seed,
                "cost_ceiling": spec.cost_ceiling.amount,
                "hypothesized_effect": spec.hypothesized_effect,
                "rubric": spec.judge.rubric,
                # Rubric parity: the SAME commit_rubric preflight the lock runs
                # (is_file + readable), not a parallel exists() re-implementation
                # that previews a directory rubric green [P1 review F1].
                "rubric_present": _rubric_commits(d, spec),
            }
            # Platform-capability parity: the SAME preflight step the lock runs,
            # closing the audited gap where an unregistered platform previewed
            # green then refused at lock with UnknownArmPlatformError.
            try:
                check_arm_platforms(spec)
                out["platform"] = {"ok": True}
            except UnknownArmPlatformError as e:
                out["platform"] = {"ok": False, "error_class": type(e).__name__,
                                   "error": str(e)}
        except (SpecError, yaml.YAMLError) as e:
            out["spec"] = {"ok": False, "error_class": type(e).__name__, "error": str(e)}
        try:
            tasks = load_task_dicts(d)
            out["tasks"] = {"ok": True, "count": len(tasks),
                            "ids": [t.get("id") for t in tasks]}
        except TaskCommitmentError as e:
            out["tasks"] = {"ok": False, "error_class": type(e).__name__, "error": str(e)}
        return out

    def _power(self, q: dict) -> dict:
        d = self._dir(q)
        spec = self._spec(d)
        quick = q.get("quick", ["0"])[0] == "1"
        kwargs = dict(_QUICK_POWER) if quick else {}
        try:
            n_tasks = len(load_task_dicts(d)) or None
        except TaskCommitmentError:
            n_tasks = None
        mde = mde_check(spec, AssumedVariance(), n_tasks=n_tasks, **kwargs)
        return {
            "quick": quick,
            "note": "quick estimate — the lock recomputes at full fidelity" if quick else "",
            "mde": mde.to_event_payload(),
        }

    def _schedule(self, q: dict) -> dict:
        d = self._dir(q)
        spec = self._spec(d)
        try:
            task_ids = [t["id"] for t in load_task_dicts(d)]
        except TaskCommitmentError as e:
            raise _Refused(f"cannot derive a schedule from invalid tasks.yaml: {e}") from e
        limit = int(q.get("limit", ["50"])[0])
        trials = enumerate_trials(task_ids, [a.name for a in spec.arms], spec.repetitions)
        order = derive_schedule(spec.seed, trials)
        return {
            "total": len(order),
            "order": [
                {"task_id": t.task_id, "arm": t.arm, "repetition": t.repetition}
                for t in order[:limit]
            ],
        }

    # -- the ceremony endpoints ----------------------------------------------------
    def _write_draft(self, body: dict) -> dict:
        d = self._named(body.get("name"))
        if self._is_locked(d):
            raise _Refused(
                f"{d.name!r} is locked: its pre-registered files are immutable — "
                "re-planning means a new draft directory [EVAL-17 AC-3]"
            )
        files = body.get("files")
        if not isinstance(files, dict) or not files:
            raise _NotFound("pass files={relative-path: text} to save")
        written: list[str] = []
        for rel, text in files.items():
            if not (isinstance(rel, str) and _DRAFT_FILE_RE.match(rel)):
                raise _Refused(
                    f"{rel!r} is not an authorable draft file (experiment.yaml, "
                    "tasks.yaml, or rubrics/<name>.md)"
                )
            if not isinstance(text, str):
                raise _Refused(f"{rel!r} content must be text")
            target = d / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            written.append(rel)
        # A draft may be saved before its experiment.yaml exists (e.g. tasks
        # first), so the sha is None until there are bytes to hash.
        exp = d / "experiment.yaml"
        sha = spec_sha256(exp) if exp.exists() else None
        return {"name": d.name, "saved": sorted(written), "spec_sha256": sha}

    def _lock(self, body: dict) -> dict:
        d = self._named(body.get("name"))
        if not (d / "experiment.yaml").exists():
            raise _NotFound(f"draft {d.name!r} has no experiment.yaml to lock")
        attested_by = body.get("attested_by")
        if not attested_by or not isinstance(attested_by, str):
            raise _Refused("the ceremony requires an explicit attested_by [D008]")
        ctx = EventContext(experiment_id=d.name, actor=self.actor)
        task_dicts = load_task_dicts(d)  # TaskCommitmentError → typed 409
        outcome = lock_experiment(
            d / "experiment.yaml",
            d / "ledger.ndjson",
            ctx=ctx,
            acknowledge_underpowered=bool(body.get("acknowledge_underpowered")),
            attested_by=attested_by,
            task_dicts=task_dicts,
            **(self.lock_kwargs or {}),
        )
        return {
            "locked": True,
            "spec_sha256": outcome.spec_sha256,
            "seed": outcome.spec.seed,
            "mde": outcome.mde,
        }

    # -- refuse everything else ------------------------------------------------------
    def _refuse_method(self) -> None:
        self._method_not_allowed(
            "GET, POST",
            "authoring surface: GET previews + the two ceremony POSTs only",
        )

    do_PUT = _refuse_method  # noqa: N815 - BaseHTTPRequestHandler contract
    do_DELETE = _refuse_method  # noqa: N815
    do_PATCH = _refuse_method  # noqa: N815


def make_author_server(
    root,
    *,
    actor: str,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_AUTHOR_PORT,
    lock_kwargs: Optional[dict] = None,
) -> ThreadingHTTPServer:
    """Bind the authoring surface to a workspace root.

    ``actor`` is the launch-bound identity every ceremony event records (the
    CLI resolves it via ``resolve_actor`` — never "unknown"). ``lock_kwargs``
    is the operational MDE tuning ``lock_experiment`` already accepts
    (n_sim/n_boot/deltas — the bench-plan test-builders' knob); omitted means
    the lock's full-fidelity defaults.
    """
    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(f"workspace root {root} is not a directory")
    handler = bind_handler(
        AuthorHandler,
        "BoundAuthorHandler",
        workspace_root=root,
        actor=actor,
        lock_kwargs=lock_kwargs,
    )
    return ThreadingHTTPServer((host, port), handler)
