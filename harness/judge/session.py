"""One judging session, two verdict sinks [refactor 05 §4].

``bench judge`` runs the identical per-comparison loop over two comparison
sources — the native pairing (:mod:`harness.judge.api`) and the reused-control
pairing (:mod:`harness.judge.reuse`) — landing verdicts under two different
event kinds. That loop was duplicated: twin ``_is_transient`` copies, twin
token-ceiling checks, twin packet-build + ``judge_pair`` + usage-accumulation
bodies. :class:`JudgingSession` is the single copy, parameterized by the
comparison source (an argument to :meth:`run`) and the verdict sink
(:class:`VerdictSink`: the ledger kind read for idempotency and the writer
``judge_pair`` appends through). Event kinds, idempotency semantics, ordering,
and echoed CLI text are unchanged — only the duplication is gone; both callers
become thin.

Blinding by construction [refactor 05 §5]: the session takes ``canaries`` as a
*required* argument (the caller derives it once from the locked spec) and feeds
it to every ``judge_pair`` call, so a comparison can never be judged against the
generic corpus alone because a call site forgot the argument — the silent
degradation the low-level ``judge_pair(canaries=None)`` default leaves open for
unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Iterable, Optional

from ..ledger import events
from ..ledger.events import EventContext
from ..ledger.query import find_events
from .schema import TRANSIENT_CANT_JUDGE, Winner

if TYPE_CHECKING:
    from .assemble import Comparison


def _is_transient(v: dict) -> bool:
    """A verdict a re-run should re-attempt: a fail-closed CANT_JUDGE whose reason
    is transient (the judge could not *run* the comparison — timeout /
    provider_error / parse). A terminal CANT_JUDGE (deterministic for a fixed
    packet) stays skipped. The single copy both verdict sinks share [PRA-M13,
    refactor 05 §4]."""
    return v.get("winner") == "CANT_JUDGE" and v.get("reason") in TRANSIENT_CANT_JUDGE


def _usage_tokens(usage: Optional[dict]) -> int:
    """Total tokens on a verdict's provider-reported usage block, or 0 when the
    provider reported none (honest absence is not spend). The one summing rule the
    ceiling seed and the running accumulation share, so they cannot drift."""
    u = usage or {}
    return int(u.get("input_tokens") or 0) + int(u.get("output_tokens") or 0)


@dataclass(frozen=True)
class VerdictSink:
    """Where a session's verdicts land [refactor 05 §4]: ``kind`` is the ledger
    event kind read for idempotency (which comparisons already carry a
    non-transient verdict), and ``append_verdict_fn`` is the writer ``judge_pair``
    appends through. The native sink writes ``judge_verdict`` via ``judge_pair``'s
    default writer (``append_verdict_fn=None``); the reused sink writes
    ``reused_judge_verdict`` through a writer carrying the reuse provenance — the
    distinct kind the official judge_preference / calibration never read."""

    kind: str
    append_verdict_fn: Optional[Callable] = None


@dataclass(frozen=True)
class SessionResult:
    """One :meth:`JudgingSession.run` pass: how many comparisons were newly
    judged, the running token accumulation after them (seed for the next pass),
    and whether the pre-registered judge token ceiling stopped the pass before it
    finished [F-M-J3].

    ``judged`` splits into ``verdicts`` (substantive A/B/TIE) + the CANT_JUDGE
    comparisons tallied by reason in ``cant_judge_reasons`` [ux-friction AC-3], so
    the CLI can disclose a fail-closed pass instead of a success-shaped count.
    Additive with defaults — the reused-control caller reads only ``judged``."""

    judged: int
    accumulated: int
    stopped_ceiling: bool
    verdicts: int = 0
    cant_judge_reasons: dict = field(default_factory=dict)


# The native pairing lands under ``judge_verdict`` through ``judge_pair``'s default
# writer; no reuse provenance rides it, so ``append_verdict_fn`` stays None.
NATIVE_SINK = VerdictSink(kind=events.JUDGE_VERDICT)


class JudgingSession:
    """The shared per-comparison judging loop [refactor 05 §4].

    Constructed once per experiment with the locked judge config, the rubric, the
    per-task prompts, and the spec-derived ``canaries`` (required — never None);
    :meth:`run` drives one comparison source into one :class:`VerdictSink`, and
    both the native and reused-control callers stay thin.
    """

    def __init__(
        self,
        ledger_path,
        ctx: EventContext,
        *,
        config,
        rubric: str,
        prompts: dict,
        canaries: list,
        ceiling: Optional[int] = None,
    ) -> None:
        if canaries is None:
            # Blinding by construction [refactor 05 §5]: the generic identity
            # corpus alone does not scrub the *contestants'* declared identities
            # (arm names, model ids) — only the spec-derived canaries do. A session
            # built without them would judge un-blinded, so fail loud here rather
            # than silently degrade the way ``judge_pair(canaries=None)`` does.
            raise ValueError(
                "JudgingSession requires the spec-derived canaries; a None set "
                "would judge against the generic corpus alone [refactor 05 §5]"
            )
        self.ledger_path = ledger_path
        self.ctx = ctx
        self.config = config
        self.rubric = rubric
        self.prompts = prompts
        self.canaries = canaries
        self.ceiling = ceiling

    def seed_accumulated(self, kinds: Iterable[str]) -> int:
        """The judge token budget already spent by prior verdicts of ``kinds`` —
        the resume-aware seed so a re-run cannot reset the ceiling [F-M-J3]. The
        native path seeds from BOTH the native and reused verdict kinds, since
        reuse judging draws on the same locked budget."""
        return sum(
            _usage_tokens((ev["verdict"].get("provenance") or {}).get("usage"))
            for kind in kinds
            for ev in find_events(self.ledger_path, kind)
        )

    def _already_judged(self, kind: str) -> set:
        """Comparison ids that already carry a NON-transient verdict of ``kind`` —
        skipped on a re-run [7A-4]. A transient CANT_JUDGE is not counted as done,
        so it is re-attempted [PRA-M13]."""
        return {
            ev["verdict"]["comparison_id"]
            for ev in find_events(self.ledger_path, kind)
            if not _is_transient(ev["verdict"])
        }

    def run(
        self,
        comparisons: "Iterable[Comparison]",
        sink: VerdictSink,
        *,
        accumulated: int = 0,
    ) -> SessionResult:
        """Judge each comparison not already carrying a non-transient verdict,
        appending exactly one verdict per comparison through ``sink`` [AC-8].

        ``accumulated`` seeds the judge token budget from prior spend; every
        verdict's provider-reported usage counts against the locked ceiling, and
        the pass refuses to *start* a further comparison once the ceiling is
        reached, recording one typed ``judge_stopped_token_ceiling`` [F-M-J3]."""
        from .client import judge_pair
        from .packet import build_packet

        already = self._already_judged(sink.kind)
        judged = 0
        substantive = 0
        cant_reasons: dict[str, int] = {}
        stopped_ceiling = False
        for cmp in comparisons:
            if cmp.comparison_id in already:
                continue
            if self.ceiling is not None and accumulated >= self.ceiling:
                events.record_judge_stopped_token_ceiling(
                    self.ledger_path, self.ctx,
                    accumulated_tokens=accumulated, ceiling=self.ceiling,
                )
                stopped_ceiling = True
                break
            packet = build_packet(
                cmp.response_a, cmp.response_b,
                task_prompt=self.prompts.get(cmp.task_id, ""),
                rubric=self.rubric,
            )
            verdict = judge_pair(
                packet, self.config, self.ledger_path, self.ctx,
                ts=self.ctx.clock(), canaries=self.canaries,
                comparison_id=cmp.comparison_id, task_class=cmp.task_class,
                arm_map=cmp.arm_map, task_id=cmp.task_id,
                append_verdict_fn=sink.append_verdict_fn,
            )
            accumulated += _usage_tokens(verdict.provenance.usage)
            judged += 1
            # AC-3: split from the verdict actually appended — a CANT_JUDGE
            # carries its fail-closed reason on ``reason``; everything else is a
            # substantive verdict.
            if verdict.winner == Winner.CANT_JUDGE:
                cant_reasons[verdict.reason] = cant_reasons.get(verdict.reason, 0) + 1
            else:
                substantive += 1
        return SessionResult(
            judged=judged, accumulated=accumulated, stopped_ceiling=stopped_ceiling,
            verdicts=substantive, cant_judge_reasons=cant_reasons,
        )
