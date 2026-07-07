"""Engine base — the template-method contract every engine inherits [refactor 04 §2].

Before this module the ``Engine`` contract was folklore reverse-engineered from the
one containerizing engine: write ``artifacts/agent_log.json``, resolve an immutable
image digest, confirm kill-on-timeout, honour the closed ``failure_reason``
vocabulary, and keep the in-memory ``native_log`` consistent with the on-disk
pre-redaction bytes. Those obligations now live here as a shared post-run ladder so a
new engine gets them by inheritance instead of by imitation. The normative statement
of the contract is ``docs/engines.md``.

:class:`EngineBase.run` is the FINAL template: ``_resolve_image`` (digest pin or
refuse) → ``_execute`` (the only subclass-owned step) → the shared fail-closed
readers ``_read_native_log`` (RN-17), ``_scan_proxy_log`` (PRA-H4), and
``_read_span_log`` (refactor 09 §4) → ``_assemble`` (the outcome-downgrade
precedence ``kill_failed > daemon_error > timeout > telemetry_corrupt >
proxy_log_missing > span_log_missing``). The first three of that precedence are
determined by the engine inside ``_execute`` (a containerizing engine from the
container result, a scripted engine from its script); the last three are the shared
downgrades applied here, each only against a would-be-``completed`` trial so a more
specific reason is never masked — egress evidence (proxy_log_missing) outranks
telemetry evidence (span_log_missing).

This module names no engine: the engine modules import :class:`EngineBase`, never the
reverse [EVAL-4 AC-1, the engine-confinement contract + the AST seam sweep].
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Optional

from ...adapters.base import Outcome, Quotas
from ..environment import stage_files
from ..types import EngineResult, TrialRequest

# The closed engine ``failure_reason`` vocabulary [refactor 04 §2/§6, A12]. Every
# reason a REAL engine (a containerizing engine + the shared downgrades) may stamp
# onto an infra failure is a member — the scheduler ledgers these verbatim and
# ``proxy_log_missing`` additionally aborts the run in ``interleave.py``. A scripted
# engine may script an ARBITRARY ``infra_reason`` placeholder (a fixture affordance,
# not part of the contract).
#
# ``span_log_missing`` (A12): a configured OTLP collector whose envelope log vanished
# is infrastructure breakage, never "zero spans" — the shared ``_read_span_log``
# fails the trial closed with it, after ``proxy_log_missing`` in the precedence.
# ``spans_corrupt`` (A12) is RESERVED for spec 10's span→trajectory normalizer, which
# wires its raise path when an OTLP span structure cannot be normalized; it is added
# to the closed vocabulary now (so both A12 values land together) but is NOT raised by
# this spec — do not treat its absence of a raise site as a gap.
ENGINE_FAILURE_REASONS: frozenset[str] = frozenset(
    {
        "unpinned_image", "kill_failed", "daemon_error", "telemetry_corrupt",
        "proxy_log_missing", "span_log_missing", "spans_corrupt",
    }
)


class TelemetryCorruptError(RuntimeError):
    """The agent's native telemetry log was present but not valid JSON [RN-17].

    Distinct from an absent log (legitimately no telemetry): corruption must fail the
    trial closed, never silently become "no telemetry"."""


class ProxyLogMissingError(RuntimeError):
    """A configured metering-proxy log file is absent [PRA-H4].

    The proxy is dead or misconfigured; treating this as "no egress, no cost, no
    violation" is a silent fail-open of the cost guard and the egress fence, so the
    scan raises and the trial fails ``infra_failed(proxy_log_missing)``."""


class SpanLogMissingError(RuntimeError):
    """A configured OTLP collector's envelope log is absent [refactor 09 §4, A12].

    The collector is dead or misconfigured; treating this as "zero spans" would
    launder infrastructure breakage into honest emptiness and lose the datapoint
    silently, so ``_read_span_log`` raises and the trial fails
    ``infra_failed(span_log_missing)`` — the ``proxy_log_missing`` discipline for
    telemetry evidence. A configured collector means telemetry is REQUIRED (A12)."""


@dataclass
class ResolvedImage:
    """Outcome of :meth:`EngineBase._resolve_image` — a digest-pinned image, or a
    refusal. ``refusal_reason`` (a member of :data:`ENGINE_FAILURE_REASONS`, e.g.
    ``unpinned_image``) short-circuits the template before ``_execute`` runs, so an
    unrunnable image never launches a container [RN-12, D005]."""

    ref: Optional[str] = None
    digest: Optional[str] = None
    refusal_reason: Optional[str] = None

    @property
    def refused(self) -> bool:
        return self.refusal_reason is not None


@dataclass
class EgressObservation:
    """The per-trial egress a metering proxy attributed to a trial [RN-11]: the hosts
    attempted, whether any was denied, and any metered cost. A containerizing engine
    derives this from the shared proxy-log scan; a scripted engine reports its own."""

    attempts: list[str] = field(default_factory=list)
    violation: bool = False
    metered_cost: Optional[float] = None


@dataclass
class ExecOutcome:
    """What a subclass's :meth:`EngineBase._execute` produces — the container/agent
    result BEFORE the shared fail-closed post-run ladder runs. ``outcome`` already
    carries the engine-determined head of the precedence (``kill_failed >
    daemon_error > timeout`` for a containerizing engine; the scripted outcome for a
    scripted engine); the shared ``_assemble`` layers ``telemetry_corrupt`` then
    ``proxy_log_missing`` on top.

    ``native_log`` is the engine's IN-MEMORY telemetry, set ONLY by a scripted engine
    so its telemetry stays decoupled from the on-disk log a fixture may corrupt to
    exercise the seam's trajectory fail-closed path — the dual-source invariant
    (telemetry from memory, trajectory from redacted disk). Left ``None``, the shared
    ``_read_native_log`` reads the on-disk log with fail-closed RN-17 semantics (a
    containerizing engine's only telemetry source). ``egress`` is set ONLY by an
    engine that reports its own egress (a scripted engine); left ``None``, the shared
    proxy-log scan is the egress source (a containerizing engine)."""

    outcome: Outcome
    exit_status: Optional[int] = None
    agent_binary_version: Optional[str] = None
    engine_version: Optional[str] = None
    failure_reason: Optional[str] = None
    native_log: Optional[dict] = None
    egress: Optional[EgressObservation] = None


class EngineBase(ABC):
    """The engine contract as a template method [refactor 04 §2].

    Subclasses fill :meth:`_resolve_image` and :meth:`_execute`; every shared
    obligation — workspace staging, fail-closed telemetry/egress reads, and the
    outcome-downgrade precedence — is inherited, so all engines fail closed
    identically and a new engine is contract-correct by construction."""

    name: ClassVar[str]

    # Whether this engine manages real infrastructure (docker containers) that the
    # managed metering proxy / OTLP collector must wrap [refactor 11 §G5c]. Declared
    # per engine (no default, like ``name``) so infra gating derives from the engine
    # rather than a ``engine == "fake"`` string a new offline engine would have to
    # know to imitate; a scripted, hermetic-by-fiat engine sets ``False`` and the
    # managed sidecars no-op for it.
    manages_real_infra: ClassVar[bool]

    def run(self, req: TrialRequest) -> EngineResult:
        """FINAL template [refactor 04 §2]: stage → resolve → execute → read
        telemetry (fail-closed RN-17) → scan egress (fail-closed PRA-H4) → assemble
        (shared precedence). Not meant to be overridden — an engine that needs
        different mechanics overrides ``_execute``, not this ladder."""
        self._prepare_workspace(req)
        resolved = self._resolve_image(req)
        if resolved.refused:
            return self._refused_result(req, resolved)
        exec_ = self._execute(req, resolved)
        # Telemetry source (dual-source invariant): a scripted engine supplies its
        # own in-memory native_log, decoupled from the on-disk log a fixture may
        # corrupt to drive the seam's trajectory fail-closed path, so it never has
        # engine-level telemetry corruption. Otherwise the shared fail-closed reader
        # is the source, and a raise is a precedence SIGNAL the assembler downgrades
        # a would-be-completed trial on — never a swallowed failure.
        if exec_.native_log is not None:
            native_log, telemetry_corrupt = exec_.native_log, False
        else:
            try:
                native_log, telemetry_corrupt = self._read_native_log(req), False
            except TelemetryCorruptError:
                native_log, telemetry_corrupt = {}, True
        try:
            attempts, violation, metered_cost = self._scan_proxy_log(req)
            proxy_log_missing = False
        except ProxyLogMissingError:
            attempts, violation, metered_cost, proxy_log_missing = [], False, None, True
        # In-trial OTLP span capture [refactor 09 §4]: extract + persist this trial's
        # slice of the shared collector log. A configured-but-absent log fails closed
        # (span_log_missing), the same precedence SIGNAL the assembler downgrades on —
        # slotted AFTER proxy_log_missing (egress evidence outranks telemetry).
        try:
            spans_sha = self._read_span_log(req)
            span_log_missing = False
        except SpanLogMissingError:
            spans_sha, span_log_missing = None, True
        return self._assemble(
            req,
            resolved,
            exec_,
            native_log=native_log,
            telemetry_corrupt=telemetry_corrupt,
            scanned=EgressObservation(attempts, violation, metered_cost),
            proxy_log_missing=proxy_log_missing,
            spans_sha=spans_sha,
            span_log_missing=span_log_missing,
        )

    # --- subclass seams ----------------------------------------------------
    @abstractmethod
    def _resolve_image(self, req: TrialRequest) -> ResolvedImage:
        """Resolve the image to an immutable digest-pinned ref, or refuse [D005]."""

    @abstractmethod
    def _execute(self, req: TrialRequest, resolved: ResolvedImage) -> ExecOutcome:
        """Run the agent and map its result to an :class:`ExecOutcome` (the only
        engine-specific step)."""

    # --- shared workspace/artifacts ----------------------------------------
    @staticmethod
    def _artifacts_dir(req: TrialRequest) -> Path:
        """The trial's captured-artifacts directory ``<workspace>/artifacts`` — the
        one location every engine writes ``agent_log.json`` and the transcript to,
        and the only tree redaction and grading read [refactor 04 §2]."""
        return Path(req.workspace) / "artifacts"

    def _prepare_workspace(self, req: TrialRequest) -> None:
        """Make the artifacts dir and stage the task's declared fixture files into
        ``/workspace`` before the agent runs [A3, refactor 03 §5] — shared so a
        scripted engine materializes the same tree a real container would see."""
        self._artifacts_dir(req).mkdir(parents=True, exist_ok=True)
        stage_files(req.workspace, req.files or {})

    # --- shared fail-closed readers ----------------------------------------
    @staticmethod
    def _read_native_log(req: TrialRequest) -> dict:
        """Parse the agent's native telemetry log from ``<workspace>/artifacts``.

        An **absent** log is legitimate (the arm may emit none) and reads as ``{}``.
        A **present but corrupt** log is not — silently mapping it to ``{}`` would
        launder corrupt telemetry into "no telemetry", so raise
        :class:`TelemetryCorruptError` and let the template fail the trial closed
        [RN-17]."""
        log = EngineBase._artifacts_dir(req) / "agent_log.json"
        if not log.exists():
            return {}
        try:
            return json.loads(log.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise TelemetryCorruptError(f"{log}: {e}") from e

    @staticmethod
    def _scan_proxy_log(request: TrialRequest) -> tuple[list[str], bool, Optional[float]]:
        """Parse the metering proxy's structured JSONL, keyed on trial [RN-11].

        Each line is ``{"trial","host","decision":"allow|deny"[,"cost"]}``; only lines
        for this trial count (per-trial attribution via the injected proxy
        credential). Any ``deny`` is an egress violation, and a per-line ``cost`` (when
        the proxy meters it) sums into the trial's metered cost so a null-telemetry-cost
        arm is still enforceable on the real path [RN-2].

        A line that is not a JSON object is skipped without crashing (a bare
        ``42``/``null``/``[...]`` must not abort the whole run); unparseable lines are
        skipped — the metering proxy is expected to emit valid JSONL, so a malformed
        line is an operational fault of the proxy, not this trial's.

        PRA-H4: a *configured but absent* log is NOT treated as "no egress, no cost, no
        violation" — that silent fail-open let a null-telemetry arm spend invisibly
        against the ceiling and shed egress-violation evidence when the proxy was dead
        or its path was wrong. A configured proxy whose log file is missing raises
        :class:`ProxyLogMissingError`, and the trial fails closed
        ``infra_failed(proxy_log_missing)``."""
        if request.proxy is None or not request.proxy.log_path:
            return [], False, None
        p = Path(request.proxy.log_path)
        if not p.exists():
            raise ProxyLogMissingError(
                f"proxy log {p} is configured but absent — the metering proxy is "
                "dead or misconfigured; refusing to treat this as zero egress/cost "
                "[PRA-H4]"
            )
        attempts: list[str] = []
        violation = False
        metered: Optional[float] = None
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict) or rec.get("trial") != request.trial_id:
                continue
            host = rec.get("host")
            if host:
                attempts.append(host)
            if rec.get("decision") == "deny":
                violation = True
            cost = rec.get("cost")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                metered = (metered or 0.0) + float(cost)
        return attempts, violation, metered

    @staticmethod
    def _read_span_log(request: TrialRequest) -> Optional[str]:
        """Extract + persist this trial's OTLP span slice; return its sha [refactor 09 §4].

        The shared sibling of :meth:`_scan_proxy_log`, running for every engine:

        1. ``request.otlp is None`` (or no ``log_path``) → not configured: no
           artifact, no sha (the ``_scan_proxy_log`` unconfigured return).
        2. Configured but the envelope log is absent → :class:`SpanLogMissingError`
           — fail-closed (A12); a configured collector whose output vanished is
           infrastructure breakage, never "zero spans".
        3. Filter the shared log by ``rec["trial"] == request.trial_id`` (the
           ``_scan_proxy_log`` selection rule), decode (§5), and persist
           ``artifacts/otlp_spans.json`` — BEFORE the seam's whole-workspace
           redaction, so span payloads pass the same scrub as every other artifact.
        4. Zero matching lines → an empty-``batches`` artifact with a sha: honest
           emptiness ("collector ran, this trial emitted nothing"), distinct from
           absence ("no collector configured").

        The provider-key literals scrub as ``extra_patterns`` (RN-9), matching the
        seam's redaction so the second (workspace) scrub is a byte-identical no-op
        and the ledgered ``spans_sha`` still matches the on-disk artifact.
        """
        if request.otlp is None or not request.otlp.log_path:
            return None
        # Lazy import: keeps the opentelemetry-proto chain (and hermetic) out of the
        # engine's import surface until a trial actually has a collector configured.
        from ...hermetic.otlp_decode import decode_envelope_lines, persist_spans

        p = Path(request.otlp.log_path)
        if not p.exists():
            raise SpanLogMissingError(
                f"otlp collector log {p} is configured but absent — the collector is "
                "dead or misconfigured; refusing to treat this as zero spans "
                "[refactor 09 §4, A12]"
            )
        lines = p.read_text(encoding="utf-8").splitlines()
        record = decode_envelope_lines(lines, request.trial_id)
        extra_patterns = [
            re.escape(v) for v in (request.provider_keys or {}).values() if v
        ]
        return persist_spans(record, EngineBase._artifacts_dir(request), extra_patterns)

    # --- shared assembly / precedence --------------------------------------
    def _refused_result(self, req: TrialRequest, resolved: ResolvedImage) -> EngineResult:
        """The infra-failed record for a refused image [D005/RN-12] — no container
        ran, so there is no telemetry, no egress, and no digest to record."""
        return EngineResult(
            outcome=Outcome.infra_failed,
            native_log={},
            artifacts_dir=self._artifacts_dir(req),
            image_digest=None,
            engine=self.name,
            quotas=req.quotas or Quotas(),
            executed_at=req.ts,
            failure_reason=resolved.refusal_reason,
        )

    def _assemble(
        self,
        req: TrialRequest,
        resolved: ResolvedImage,
        exec_: ExecOutcome,
        *,
        native_log: dict,
        telemetry_corrupt: bool,
        scanned: EgressObservation,
        proxy_log_missing: bool,
        spans_sha: Optional[str] = None,
        span_log_missing: bool = False,
    ) -> EngineResult:
        """Fold the execution result and the three fail-closed signals into the
        record with the shared downgrade precedence [byte-preserved from the
        pre-refactor containerizing engine's ``run`` body, extended for spans].

        ``telemetry_corrupt`` (RN-17), then ``proxy_log_missing`` (PRA-H4), then
        ``span_log_missing`` (refactor 09 §4, A12) each downgrade ONLY a
        would-be-``completed`` trial, so an engine-determined
        ``kill_failed``/``daemon_error``/``timeout`` keeps its more specific reason.
        The order is the frozen precedence: telemetry corruption outranks a missing
        proxy log, and egress evidence (proxy) outranks telemetry evidence (spans)."""
        outcome = exec_.outcome
        failure_reason = exec_.failure_reason
        if telemetry_corrupt:
            native_log = {}
            if outcome == Outcome.completed:
                outcome = Outcome.infra_failed
                failure_reason = "telemetry_corrupt"
        if proxy_log_missing:
            # The egress evidence is emptied whether or not this downgrades the
            # outcome — a missing log is never read as a trustworthy zero.
            egress = EgressObservation()
            if outcome == Outcome.completed:
                outcome = Outcome.infra_failed
                failure_reason = "proxy_log_missing"
        elif exec_.egress is not None:
            egress = exec_.egress  # a scripted engine reports its own egress
        else:
            egress = scanned  # the metering proxy log is the egress source
        if span_log_missing:
            # The span sha is emptied whether or not this downgrades the outcome —
            # a missing collector log is never read as a trustworthy zero-span sha.
            spans_sha = None
            if outcome == Outcome.completed:
                outcome = Outcome.infra_failed
                failure_reason = "span_log_missing"
        return EngineResult(
            outcome=outcome,
            native_log=native_log,
            artifacts_dir=self._artifacts_dir(req),
            exit_status=exec_.exit_status,
            image_digest=resolved.digest,
            agent_binary_version=exec_.agent_binary_version,
            harbor_version=exec_.engine_version,
            engine=self.name,
            quotas=req.quotas or Quotas(),
            egress_violation=egress.violation,
            egress_attempts=egress.attempts,
            executed_at=req.ts,
            failure_reason=failure_reason,
            proxy_metered_cost=egress.metered_cost,
            spans_sha=spans_sha,
        )
