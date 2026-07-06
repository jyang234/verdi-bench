"""Read-only HTTP observer [EVAL-13 AC-5; EVAL-14 AC-1..AC-8].

stdlib ``ThreadingHTTPServer`` wrapping the read seams — no new runtime
dependency for a loopback GET-only tool. The handler is the allowlist:
fixed routes, GET only; nothing here appends an event, writes a file, or
triggers execution. A UI-triggered mutation path is a different story with
its own actor and one-event obligations (spec: out of scope).

Two modes [EVAL-14 D003]: a single experiment directory, or a workspace
root (``--root``) scanned one level for ``ledger.ndjson`` directories. In
root mode every experiment-scoped route takes ``exp=<dirname>``, validated
against a conservative name shape and the scanned set — never joined into
a path from raw input. Artifacts are served from a fixed-name allowlist
(the analyze render outputs), so no route can read an arbitrary file.

Route errors are *served*, not dropped: a failing read returns its message
as a 500 JSON body — surfaced to the observer while the server keeps
answering other requests. An invalid tail cursor (the ledger shrank —
rewrite evidence) is 409, distinct from a merely malformed offset (400).

[refactor 07 §4] The mechanical transport — the ``_send``/``_json`` plumbing,
the host guard, route-table dispatch, the ``RouteError`` → status mapping, and
the ``type("Bound…Handler", …)`` factory — comes from the tier-neutral
:class:`harness.webkit.http.JsonRouteHandler`. This module keeps its own route
table, GET-only posture, artifact allowlist, and unblinded-operator semantics.
"""

from __future__ import annotations

import hashlib
import re
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from ..analyze.fence import official_fence_report
from ..analyze.timeline import trial_timeline
from ..ledger.query import TailOffsetError, tail_events, verify
from ..status.aggregate import compute_status
from ..status.trial import trial_detail
from ..webkit.http import ChainBroken, JsonRouteHandler, NotFound, bind_handler, default_error
from .compare import paired_comparisons
from .page import OPERATOR_PAGE
from .workspace import scan_workspace

DEFAULT_HOST = "127.0.0.1"  # loopback by default: an operator tool, not a service
DEFAULT_PORT = 8383

# Experiment names come from directory scans; a name used in a query string
# must have the same conservative shape — no separators, no dot-dot, nothing
# a path join could be steered with.
_EXP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# The only files /artifact will ever serve: the analyze render outputs.
_ARTIFACT_RE = re.compile(
    r"^findings\.(json|(official|exploratory)\.(md|html|dossier\.html))$"
)
_ARTIFACT_TYPES = {
    ".json": "application/json",
    ".html": "text/html; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
}


class _NotFound(NotFound):
    """Route-level 404 with a message the observer can act on."""


class _ChainBroken(ChainBroken):
    """Route-level 409: the ledger's chain does not verify, so no ledger-reading
    route may render its (tampered) content [PRA-M10]."""


class ObserverHandler(JsonRouteHandler):
    """GET-only routes over one experiment dir or a workspace root."""

    experiment_dir: Optional[Path]  # single mode (bound by make_server)
    workspace_root: Optional[Path]  # root mode (bound by make_server)
    corpus_manifest = None  # optional CorpusManifest for the fence items
    _verify_cache: dict = {}  # (path -> (content_sha256, ChainResult)) [PRA-M10/F-M-I1]

    server_version = "verdi-bench-observer"

    # -- routes ---------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        q = parse_qs(urlparse(self.path).query)
        table = {
            "/": lambda: self._send(200, OPERATOR_PAGE.encode("utf-8"), "text/html; charset=utf-8"),
            # Browsers request /favicon.ico unprompted; a <link> tag would break
            # the page's no-external-references property, so answer empty here.
            "/favicon.ico": lambda: self._send(204, b"", "image/x-icon"),
            "/api/experiments": lambda: self._json(200, {"experiments": self._experiments()}),
            "/api/status": lambda: self._json(200, compute_status(self._dir(q))),
            "/api/events": lambda: self._events(q),
            "/api/timeline": lambda: self._json(
                200, trial_timeline(self._verified_dir(q) / "ledger.ndjson")
            ),
            "/api/trial": lambda: self._trial(q),
            "/api/compare": lambda: self._json(
                200,
                paired_comparisons(self._verified_dir(q), corpus_manifest=self.corpus_manifest),
            ),
            "/api/fence": lambda: self._json(
                200,
                official_fence_report(self._dir(q), corpus_manifest=self.corpus_manifest),
            ),
            "/artifact": lambda: self._artifact(q),
        }
        # ForbiddenError → 403 (host guard); _ChainBroken → 409, _NotFound → 404
        # (default_error); any other read failure → a served 500 [PRA-M16].
        self.dispatch(
            table,
            guard=self._guard_host,
            unknown=lambda p: (404, {"error": f"unknown path {p!r}"}),
            error=default_error,
        )

    # -- experiment resolution --------------------------------------------------
    def _experiments(self) -> list[dict]:
        if self.workspace_root is not None:
            return scan_workspace(self.workspace_root)
        from .workspace import _summary_row  # single mode: a list of one

        assert self.experiment_dir is not None
        return [
            _summary_row(self.experiment_dir.name, compute_status(self.experiment_dir))
        ]

    def _dir(self, q: dict) -> Path:
        """The experiment directory a request targets — validated, never joined
        from raw input. Root mode requires ``exp``; single mode accepts only its
        own directory's name (or no ``exp`` at all)."""
        exp = q.get("exp", [None])[0]
        if self.workspace_root is not None:
            if not exp:
                raise _NotFound("root mode: pass exp=<experiment-name>")
            if not _EXP_NAME_RE.match(exp):
                raise _NotFound(f"invalid experiment name {exp!r}")
            candidate = self.workspace_root / exp
            if not (candidate.is_dir() and (candidate / "ledger.ndjson").exists()):
                raise _NotFound(f"unknown experiment {exp!r}")
            return candidate
        assert self.experiment_dir is not None
        if exp and exp != self.experiment_dir.name:
            raise _NotFound(f"unknown experiment {exp!r} (serving {self.experiment_dir.name!r})")
        return self.experiment_dir

    def _verified_dir(self, q: dict) -> Path:
        """Resolve the target dir AND fail closed on a broken chain, so no
        ledger-reading route renders tampered events [PRA-M10]. The verify verdict
        is cached by CONTENT hash [F-M-I1], so the hot path pays one file read +
        hash instead of a full chain re-verification."""
        d = self._dir(q)
        self._require_verified(d / "ledger.ndjson")
        return d

    def _require_verified(self, ledger: Path) -> None:
        if not ledger.exists():
            return  # absent ledger: nothing to be fooled by (matches assert_chain)
        # F-M-I1: key the cached verdict on the ledger BYTES. The old
        # (st_size, st_mtime_ns) signature was defeated by a same-size rewrite
        # plus os.utime(), serving tampered events from a stale ok verdict; a
        # byte-identical file is by definition the one that verified.
        sig = hashlib.sha256(ledger.read_bytes()).hexdigest()
        key = str(ledger)
        entry = ObserverHandler._verify_cache.get(key)
        if entry is None or entry[0] != sig:
            entry = (sig, verify(ledger))
            ObserverHandler._verify_cache[key] = entry
        result = entry[1]
        if not result:
            raise _ChainBroken(
                f"chain BROKEN at line {result.line_number}: {result.detail} "
                "— withholding ledger content [PRA-M10]"
            )

    # -- endpoint bodies ----------------------------------------------------------
    def _events(self, q: dict) -> None:
        raw = q.get("offset", ["0"])[0]
        try:
            offset = int(raw)
        except ValueError:
            self._json(400, {"error": f"offset must be an integer, got {raw!r}"})
            return
        if offset < 0:
            # A negative cursor is malformed input (400), distinct from a cursor
            # past EOF (409, rewrite evidence). The read seam refuses both, but the
            # HTTP status must tell them apart [PRA-L10].
            self._json(400, {"error": f"offset must be >= 0, got {offset}"})
            return
        d = self._verified_dir(q)
        try:
            events, next_offset = tail_events(d / "ledger.ndjson", offset)
        except TailOffsetError as e:
            self._json(409, {"error": str(e)})  # cursor invalid: rewrite evidence
            return
        self._json(200, {"events": events, "next_offset": next_offset})

    def _trial(self, q: dict) -> None:
        trial_id = q.get("id", [None])[0]
        if not trial_id:
            self._json(400, {"error": "pass id=<trial-id>"})
            return
        detail = trial_detail(self._verified_dir(q), trial_id)
        if detail is None:
            self._json(404, {"error": f"no trial {trial_id!r} on this ledger"})
            return
        self._json(200, detail)

    def _artifact(self, q: dict) -> None:
        name = q.get("name", [None])[0] or ""
        if not _ARTIFACT_RE.match(name):
            raise _NotFound(f"{name!r} is not a servable artifact (analyze outputs only)")
        path = self._dir(q) / name
        if not path.is_file():
            raise _NotFound(f"artifact {name!r} has not been rendered for this experiment")
        content_type = _ARTIFACT_TYPES["".join(path.suffixes[-1:])]
        self._send(200, path.read_bytes(), content_type)

    # -- non-GET methods are refused, naming the one allowed method ------------
    def _refuse_method(self) -> None:
        self._method_not_allowed("GET", "read-only observer: GET only")

    do_POST = _refuse_method  # noqa: N815 - BaseHTTPRequestHandler contract
    do_PUT = _refuse_method  # noqa: N815
    do_DELETE = _refuse_method  # noqa: N815
    do_PATCH = _refuse_method  # noqa: N815
    do_HEAD = _refuse_method  # noqa: N815 - 405+Allow, not stdlib's 501 [PRA-L10]
    do_OPTIONS = _refuse_method  # noqa: N815


def make_server(
    experiment_dir=None,
    *,
    root=None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    corpus_manifest=None,
) -> ThreadingHTTPServer:
    """Bind a threaded observer to one experiment directory OR a workspace root.

    Exactly one of ``experiment_dir``/``root`` must be given. ``port=0`` asks
    the OS for an ephemeral port (tests); the caller reads the realized address
    from ``server_address`` and owns the lifecycle (``serve_forever`` /
    ``shutdown`` / ``server_close``).
    """
    if (experiment_dir is None) == (root is None):
        raise ValueError("pass exactly one of experiment_dir or root")
    handler = bind_handler(
        ObserverHandler,
        "BoundObserverHandler",
        experiment_dir=Path(experiment_dir) if experiment_dir is not None else None,
        workspace_root=Path(root) if root is not None else None,
        corpus_manifest=corpus_manifest,
    )
    return ThreadingHTTPServer((host, port), handler)
