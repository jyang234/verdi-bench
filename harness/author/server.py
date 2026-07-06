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
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

import yaml

from ..corpus.commit import TaskCommitmentError, load_task_dicts
from ..http_guard import ForbiddenError, check_csrf, check_host
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


class _NotFound(Exception):
    """Route-level 404 with an actionable message."""


class _Refused(Exception):
    """Route-level 409: the operation is refused for a stated reason."""


class AuthorHandler(BaseHTTPRequestHandler):
    """The authoring routes over one workspace root (bound by make_author_server)."""

    workspace_root: Path
    actor: str
    lock_kwargs: Optional[dict]  # operational MDE tuning (the bench-plan builders' knob)

    server_version = "verdi-bench-author"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # -- GET: page + pure preview reads over saved drafts -----------------------
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        parsed = urlparse(self.path)
        q = parse_qs(parsed.query)
        try:
            check_host(self.headers, self.server.server_address)  # PRA-M16
            if parsed.path == "/":
                self._send(200, AUTHOR_PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif parsed.path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
            elif parsed.path == "/api/drafts":
                self._json(200, {"actor": self.actor, "drafts": self._drafts()})
            elif parsed.path == "/api/draft":
                self._json(200, self._draft(q))
            elif parsed.path == "/api/validate":
                self._json(200, self._validate(q))
            elif parsed.path == "/api/power":
                self._json(200, self._power(q))
            elif parsed.path == "/api/schedule":
                self._json(200, self._schedule(q))
            elif parsed.path == "/api/sha":
                d = self._dir(q)
                self._json(200, {"spec_sha256": spec_sha256(d / "experiment.yaml")})
            else:
                self._json(404, {"error": f"unknown path {parsed.path!r}"})
        except ForbiddenError as e:
            self._json(403, {"error": str(e)})
        except _NotFound as e:
            self._json(404, {"error": str(e)})
        except Exception as e:  # noqa: BLE001 — served as 500, never a dropped
            # connection; nothing is retried or defaulted.
            self._json(500, {"error": f"{type(e).__name__}: {e}"})

    # -- POST: the two enumerated ceremony endpoints, nothing else -------------
    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            # PRA-H2: the ceremony endpoints mutate state (draft writes, and the
            # chain-anchored lock). Guard Host + Origin + Content-Type before
            # reading the body so a cross-site page cannot forge a lock.
            check_host(self.headers, self.server.server_address)
            check_csrf(self.headers, self.server.server_address)
            body = self._body()
            if parsed.path == "/api/draft":
                self._json(200, self._write_draft(body))
            elif parsed.path == "/api/lock":
                self._json(200, self._lock(body))
            else:
                self._json(404, {"error": f"no such ceremony endpoint {parsed.path!r}"})
        except ForbiddenError as e:
            self._json(403, {"error": str(e)})
        except _NotFound as e:
            self._json(404, {"error": str(e)})
        except _Refused as e:
            self._json(409, {"error": str(e)})
        except (
            UnderpoweredError,
            AlreadyLockedError,
            TaskCommitmentError,
            ChainIntegrityError,
            RubricCommitmentError,
            SpecError,
        ) as e:
            # the typed refusals, each rendered with its own message [AC-2]
            self._json(409, {"error_class": type(e).__name__, "error": str(e)})
        except Exception as e:  # noqa: BLE001
            self._json(500, {"error": f"{type(e).__name__}: {e}"})

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
    def _method_not_allowed(self) -> None:
        body = json.dumps(
            {"error": "authoring surface: GET previews + the two ceremony POSTs only"}
        ).encode("utf-8")
        self.send_response(405)
        self.send_header("Allow", "GET, POST")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_PUT = _method_not_allowed  # noqa: N815 - BaseHTTPRequestHandler contract
    do_DELETE = _method_not_allowed  # noqa: N815
    do_PATCH = _method_not_allowed  # noqa: N815

    # -- plumbing ---------------------------------------------------------------------
    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise _NotFound(f"request body is not JSON: {e}") from e
        if not isinstance(parsed, dict):
            raise _NotFound("request body must be a JSON object")
        return parsed

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        self._send(
            status,
            json.dumps(payload, sort_keys=True).encode("utf-8"),
            "application/json",
        )

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib name
        """Quiet: the CLI prints the one line that matters."""


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
    handler = type(
        "BoundAuthorHandler",
        (AuthorHandler,),
        {"workspace_root": root, "actor": actor, "lock_kwargs": lock_kwargs},
    )
    return ThreadingHTTPServer((host, port), handler)
