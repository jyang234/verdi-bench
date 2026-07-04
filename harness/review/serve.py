"""Reviewer surface — blinded capture-then-reveal over HTTP [EVAL-18].

The structural counterpart to the operator observer: its own process, its own
route table, and no import of ``harness.serve`` or ``harness.status``
(contract-enforced), so an unblinded byte has no path into this server's
output. The packet served is the built ``review_packet.html`` verbatim
(D004 — the same bytes a CLI reviewer opens), re-scanned against the identity
canary list before any response leaves the process; a packet that would leak
is refused with the reason, never served.

The recording path necessarily *holds* the ledgered ``response_map`` in
memory — exactly as the CLI record verb does — to translate the reviewer's
Response-1/2 choice into the judge's A/B frame; no route ever includes it in
a response. Capture and reveal reuse ``record_human_verdict`` /
``reveal_comparison`` verbatim: the record-layer gates (single verdict,
pre-reveal refusal, one-unblinding) stay beneath the transport.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from ..blind.core import arm_canaries
from ..judge.schema import Evidence, Verdict, VerdictProvenance, Winner
from ..ledger.events import EventContext
from ..ledger.query import find_events
from ..schema.experiment import ExperimentSpec
from .record import (
    ReviewError,
    RevealError,
    _reveal_exists,
    record_human_verdict,
    reveal_comparison,
    review_packet_built_for,
)
from .scrub import ScrubError, assert_identity_free
from .serve_page import REVIEWER_PAGE

DEFAULT_HOST = "127.0.0.1"
DEFAULT_REVIEW_PORT = 8395

_WINNERS = ("1", "2", "TIE", "CANT_JUDGE")


class _NotFound(Exception):
    """Route-level 404."""


class _Refused(Exception):
    """Route-level 409 with the stated reason."""


class ReviewerHandler(BaseHTTPRequestHandler):
    """Queue + packet + capture + reveal — and nothing else."""

    experiment_dir: Path
    reviewer: str

    server_version = "verdi-bench-reviewer"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # -- GET ------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        path = urlparse(self.path).path
        try:
            if path == "/":
                self._send(200, REVIEWER_PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
            elif path == "/api/queue":
                self._json(200, self._queue())
            elif path == "/packet":
                self._packet()
            else:
                # deliberately no status/events/timeline/compare/trial/artifact
                # routes: the operator tier does not exist on this server
                self._json(404, {"error": f"no such route {path!r} on the reviewer surface"})
        except _NotFound as e:
            self._json(404, {"error": str(e)})
        except _Refused as e:
            self._json(409, {"error": str(e)})
        except Exception as e:  # noqa: BLE001 — served, never dropped
            self._json(500, {"error": f"{type(e).__name__}: {e}"})

    # -- POST: the two ledgered operations, via the record layer verbatim --------
    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            body = self._body()
            if path == "/api/verdict":
                self._json(200, self._verdict(body))
            elif path == "/api/reveal":
                self._json(200, self._reveal(body))
            else:
                self._json(404, {"error": f"no such capture endpoint {path!r}"})
        except _NotFound as e:
            self._json(404, {"error": str(e)})
        except (_Refused, ReviewError, RevealError, ValueError) as e:
            self._json(409, {"error_class": type(e).__name__, "error": str(e)})
        except Exception as e:  # noqa: BLE001
            self._json(500, {"error": f"{type(e).__name__}: {e}"})

    # -- bodies ------------------------------------------------------------------
    def _ledger(self) -> Path:
        return self.experiment_dir / "ledger.ndjson"

    def _queue(self) -> dict:
        """Pending vs captured comparisons — ids and task metadata only; the
        response carries no arm names, no response_map, no judge winners."""
        built = find_events(self._ledger(), "review_packet_built")
        done_ids = {
            (ev.get("verdict") or {}).get("comparison_id")
            for ev in find_events(self._ledger(), "human_verdict")
        }
        pending, done = [], []
        for ev in built:
            item = {
                "comparison_id": ev["comparison_id"],
                "task_id": ev.get("task_id"),
                "task_class": ev.get("task_class"),
            }
            if ev["comparison_id"] in done_ids:
                # the record layer owns the reveal↔comparison join (reveals key
                # off the verdict event's line hash) — reuse it, don't re-derive
                item["revealed"] = bool(_reveal_exists(self._ledger(), ev["comparison_id"]))
                done.append(item)
            else:
                pending.append(item)
        return {
            "reviewer": self.reviewer,
            "packet_built": bool(built),
            "pending": pending,
            "done": done,
            "total": len(built),
        }

    def _packet(self) -> None:
        packet_path = self.experiment_dir / "review_packet.html"
        if not packet_path.is_file():
            raise _NotFound(
                "no review_packet.html — run `bench review build` first [RV-6]"
            )
        text = packet_path.read_text(encoding="utf-8")
        spec = ExperimentSpec.from_yaml(self.experiment_dir / "experiment.yaml")
        try:
            # belt-and-suspenders (the packet-build precedent): a packet that
            # would leak identity is refused with the reason, never served
            assert_identity_free(text, arm_canaries(spec.arms))
        except ScrubError as e:
            raise _Refused(f"refusing to serve a leaking packet: {e}") from e
        self._send(200, text.encode("utf-8"), "text/html; charset=utf-8")

    def _verdict(self, body: dict) -> dict:
        cid = body.get("comparison_id")
        winner = body.get("winner")
        if not cid:
            raise _NotFound("pass comparison_id")
        if winner not in _WINNERS:
            raise _Refused(f"winner must be one of {' | '.join(_WINNERS)}")
        if not isinstance(body.get("arm_recognized"), bool):
            raise _Refused(
                "the blinding-integrity answer is required: arm_recognized true/false"
            )
        built = review_packet_built_for(self._ledger(), cid)
        if built is None:
            raise _Refused(
                f"comparison {cid!r} has no review_packet_built event; build the "
                "packet before recording [RV-6]"
            )
        # translate the response frame to the judge's A/B frame — internal only,
        # exactly the CLI record verb's arithmetic; nothing below is echoed back
        response_map = built["response_map"]
        spec = ExperimentSpec.from_yaml(self.experiment_dir / "experiment.yaml")
        arm_a_name = spec.arms[0].name
        evidence = []
        if winner in ("1", "2"):
            letter = "A" if response_map[winner] == arm_a_name else "B"
            evidence = [Evidence(kind="diff", response=letter, hunk="reviewer-cited")]
        else:
            letter = winner
        ctx = EventContext(experiment_id=self.experiment_dir.name, actor=self.reviewer)
        prov = VerdictProvenance(
            judge_model="human", rubric_sha256="human", packet_sha256="human",
            call_ids=["human"], orders="single", temperature=0.0, ts=ctx.clock(),
        )
        verdict = Verdict(
            winner=Winner(letter),
            reason=body.get("reason") or winner,
            evidence=evidence,
            provenance=prov,
            source="human",
            comparison_id=cid,
            task_class=built.get("task_class"),
        )
        record_human_verdict(
            self._ledger(), ctx,
            verdict=verdict,
            arm_recognized=body["arm_recognized"],
            arm_guess=body.get("arm_guess"),
            actual_arm=response_map["1"],
        )
        return {"recorded": True, "comparison_id": cid}

    def _reveal(self, body: dict) -> dict:
        cid = body.get("comparison_id")
        if not cid:
            raise _NotFound("pass comparison_id")
        ctx = EventContext(experiment_id=self.experiment_dir.name, actor=self.reviewer)
        rec = reveal_comparison(self._ledger(), ctx, comparison_id=cid)
        # post-verdict unblinding IS the payload — the ledgered reveal happened
        return {"revealed": rec["revealed"], "comparison_id": cid}

    # -- refuse everything else ----------------------------------------------------
    def _method_not_allowed(self) -> None:
        body = json.dumps(
            {"error": "reviewer surface: the queue, the packet, capture, and reveal"}
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

    # -- plumbing -------------------------------------------------------------------
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
        self._send(status, json.dumps(payload, sort_keys=True).encode("utf-8"),
                   "application/json")

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib name
        """Quiet: the CLI prints the one line that matters."""


def make_review_server(
    experiment_dir,
    *,
    reviewer: str,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_REVIEW_PORT,
) -> ThreadingHTTPServer:
    """Bind the reviewer surface to one experiment. ``reviewer`` is the
    launch-bound identity (resolved by the CLI via ``resolve_actor``) recorded
    on every verdict and reveal this process ledgers."""
    handler = type(
        "BoundReviewerHandler",
        (ReviewerHandler,),
        {"experiment_dir": Path(experiment_dir), "reviewer": reviewer},
    )
    return ThreadingHTTPServer((host, port), handler)
