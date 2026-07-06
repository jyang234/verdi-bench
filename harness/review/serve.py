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

[refactor 07 §4] The mechanical transport (``_send``/``_json``/``_read_json_object``,
the host+CSRF guards, route-table dispatch, the ``type("Bound…Handler", …)``
factory) comes from the tier-neutral :class:`harness.webkit.http.JsonRouteHandler`
— which imports none of ``serve``/``status``/``author``, so the reviewer-isolation
property is preserved. The two-POST-only mutation surface, the pre-serve canary
re-scan, and the blinded error semantics stay here.
"""

from __future__ import annotations

from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from ..blind.core import arm_canaries
from ..judge.schema import Evidence, Verdict, VerdictProvenance, Winner
from ..ledger.events import EventContext
from ..ledger.query import find_events
from ..schema.experiment import ExperimentSpec
from ..webkit.http import (
    JsonRouteHandler,
    NotFound,
    Refused,
    bind_handler,
    default_error,
)
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


class _NotFound(NotFound):
    """Route-level 404."""


class _Refused(Refused):
    """Route-level 409 with the stated reason."""


def _post_error(exc: BaseException) -> Optional[tuple[int, dict]]:
    """The POST error map: a malformed route or resource is 404 (plain), while a
    refused capture/reveal — the record layer's typed gates — is 409 carrying its
    ``error_class`` so the page can label it [EVAL-18]. Anything else is a served
    500. Order preserves the original ``except`` ladder: ``_NotFound`` before the
    refusal set."""
    if isinstance(exc, _NotFound):
        return (404, {"error": str(exc)})
    if isinstance(exc, (_Refused, ReviewError, RevealError, ValueError)):
        return (409, {"error_class": type(exc).__name__, "error": str(exc)})
    return None


class ReviewerHandler(JsonRouteHandler):
    """Queue + packet + capture + reveal — and nothing else."""

    experiment_dir: Path
    reviewer: str

    server_version = "verdi-bench-reviewer"

    # -- GET ------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        table = {
            "/": lambda: self._send(200, REVIEWER_PAGE.encode("utf-8"), "text/html; charset=utf-8"),
            "/favicon.ico": lambda: self._send(204, b"", "image/x-icon"),
            "/api/queue": lambda: self._json(200, self._queue()),
            "/packet": lambda: self._packet(),
        }
        # deliberately no status/events/timeline/compare/trial/artifact routes:
        # the operator tier does not exist on this server. _NotFound → 404,
        # _Refused → 409 (default_error); any other read failure → served 500.
        self.dispatch(
            table,
            guard=self._guard_host,  # PRA-M16
            unknown=lambda p: (404, {"error": f"no such route {p!r} on the reviewer surface"}),
            error=default_error,
        )

    # -- POST: the two ledgered operations, via the record layer verbatim --------
    def do_POST(self) -> None:  # noqa: N802
        prepared: dict = {}

        def prepare() -> None:
            # PRA-H2: verdict/reveal are ledgered mutations — guard against a
            # cross-site POST forging a human_verdict or a premature reveal, then
            # read the body (a malformed body is a 404, before route dispatch).
            self._guard_host_and_csrf()
            prepared["body"] = self._read_json_object()

        table = {
            "/api/verdict": lambda: self._json(200, self._verdict(prepared["body"])),
            "/api/reveal": lambda: self._json(200, self._reveal(prepared["body"])),
        }
        self.dispatch(
            table,
            guard=prepare,
            unknown=lambda p: (404, {"error": f"no such capture endpoint {p!r}"}),
            error=_post_error,
        )

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
    def _refuse_method(self) -> None:
        self._method_not_allowed(
            "GET, POST",
            "reviewer surface: the queue, the packet, capture, and reveal",
        )

    do_PUT = _refuse_method  # noqa: N815 - BaseHTTPRequestHandler contract
    do_DELETE = _refuse_method  # noqa: N815
    do_PATCH = _refuse_method  # noqa: N815


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
    handler = bind_handler(
        ReviewerHandler,
        "BoundReviewerHandler",
        experiment_dir=Path(experiment_dir),
        reviewer=reviewer,
    )
    return ThreadingHTTPServer((host, port), handler)
