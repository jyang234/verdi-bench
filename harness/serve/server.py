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
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from ..analyze.fence import official_fence_report
from ..analyze.timeline import trial_timeline
from ..http_guard import ForbiddenError, check_host
from ..ledger.query import TailOffsetError, tail_events, verify
from ..status.aggregate import compute_status
from ..status.trial import trial_detail
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


class _NotFound(Exception):
    """Route-level 404 with a message the observer can act on."""


class _ChainBroken(Exception):
    """Route-level 409: the ledger's chain does not verify, so no ledger-reading
    route may render its (tampered) content [PRA-M10]."""


class ObserverHandler(BaseHTTPRequestHandler):
    """GET-only routes over one experiment dir or a workspace root."""

    experiment_dir: Optional[Path]  # single mode (bound by make_server)
    workspace_root: Optional[Path]  # root mode (bound by make_server)
    corpus_manifest = None  # optional CorpusManifest for the fence items
    _verify_cache: dict = {}  # (path -> ((size, mtime_ns), ChainResult)) [PRA-M10]

    server_version = "verdi-bench-observer"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # -- routes ---------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        parsed = urlparse(self.path)
        q = parse_qs(parsed.query)
        try:
            check_host(self.headers, self.server.server_address)  # PRA-M16
            if parsed.path == "/":
                self._send(200, OPERATOR_PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif parsed.path == "/favicon.ico":
                # Browsers request this unprompted; a <link> tag would break the
                # page's no-external-references property, so answer empty here.
                self._send(204, b"", "image/x-icon")
            elif parsed.path == "/api/experiments":
                self._json(200, {"experiments": self._experiments()})
            elif parsed.path == "/api/status":
                self._json(200, compute_status(self._dir(q)))
            elif parsed.path == "/api/events":
                self._events(q)
            elif parsed.path == "/api/timeline":
                self._json(200, trial_timeline(self._verified_dir(q) / "ledger.ndjson"))
            elif parsed.path == "/api/trial":
                self._trial(q)
            elif parsed.path == "/api/compare":
                self._json(
                    200,
                    paired_comparisons(
                        self._verified_dir(q), corpus_manifest=self.corpus_manifest
                    ),
                )
            elif parsed.path == "/api/fence":
                self._json(
                    200,
                    official_fence_report(self._dir(q), corpus_manifest=self.corpus_manifest),
                )
            elif parsed.path == "/artifact":
                self._artifact(q)
            else:
                self._json(404, {"error": f"unknown path {parsed.path!r}"})
        except ForbiddenError as e:
            self._json(403, {"error": str(e)})
        except _ChainBroken as e:
            self._json(409, {"error": str(e)})
        except _NotFound as e:
            self._json(404, {"error": str(e)})
        except Exception as e:  # noqa: BLE001 — surfaced as a served 500, so one
            # failing read cannot take the observer down for other requests; the
            # message travels to the client instead of vanishing into a dropped
            # connection. Nothing is retried or defaulted.
            self._json(500, {"error": f"{type(e).__name__}: {e}"})

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
        is cached by (size, mtime) so the hot path stays O(1)."""
        d = self._dir(q)
        self._require_verified(d / "ledger.ndjson")
        return d

    def _require_verified(self, ledger: Path) -> None:
        if not ledger.exists():
            return  # absent ledger: nothing to be fooled by (matches assert_chain)
        st = ledger.stat()
        sig = (st.st_size, st.st_mtime_ns)
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
    def _method_not_allowed(self) -> None:
        body = json.dumps({"error": "read-only observer: GET only"}).encode("utf-8")
        self.send_response(405)
        self.send_header("Allow", "GET")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = _method_not_allowed  # noqa: N815 - BaseHTTPRequestHandler contract
    do_PUT = _method_not_allowed  # noqa: N815
    do_DELETE = _method_not_allowed  # noqa: N815
    do_PATCH = _method_not_allowed  # noqa: N815
    do_HEAD = _method_not_allowed  # noqa: N815 - 405+Allow, not stdlib's 501 [PRA-L10]
    do_OPTIONS = _method_not_allowed  # noqa: N815

    # -- plumbing ---------------------------------------------------------------
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")  # live data, never cached
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        self._send(
            status,
            json.dumps(payload, sort_keys=True).encode("utf-8"),
            "application/json",
        )

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib name
        """Quiet per-request stderr noise: the CLI prints the one line that
        matters (where the observer is), and every request is a read."""


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
    handler = type(
        "BoundObserverHandler",
        (ObserverHandler,),
        {
            "experiment_dir": Path(experiment_dir) if experiment_dir is not None else None,
            "workspace_root": Path(root) if root is not None else None,
            "corpus_manifest": corpus_manifest,
        },
    )
    return ThreadingHTTPServer((host, port), handler)
