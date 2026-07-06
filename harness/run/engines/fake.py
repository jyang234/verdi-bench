"""Fake engine [EVAL-4 §M1] — the fixture backbone for every downstream story.

Deterministic, no Docker. Scripted entirely by ``request.fake_behavior`` so tests
can drive completed/timeout/infra_failed outcomes, egress attempts, telemetry
logs, and artifact contents without a container runtime. It passes the *same*
contract suite as the Harbor engine, which is the whole point of the seam.

Now an :class:`~harness.run.engines.base.EngineBase` subclass [refactor 04 §2]: it
fills ``_resolve_image`` (the scripted digest — the fake never refuses) and
``_execute`` (write the artifacts, simulate the metering proxy), and inherits the
shared post-run ladder. **A10:** inheriting the shared ``_scan_proxy_log`` gives the
fake the SAME fail-closed ``proxy_log_missing`` semantics as Harbor *when a proxy is
configured* — a configured proxy whose log never appears now fails the trial closed
instead of being silently read as zero egress. Unconfigured-proxy fakes (no
``proxy.log_path``) are unchanged, so the hermetic suite stays green.
"""

from __future__ import annotations

import json
from pathlib import Path

from ...adapters.base import Outcome
from ..types import TrialRequest
from .base import EgressObservation, EngineBase, ExecOutcome, ResolvedImage


class FakeEngine(EngineBase):
    name = "fake"

    def _resolve_image(self, req: TrialRequest) -> ResolvedImage:
        # The fake never refuses: its digest is scripted (or split from the ref),
        # standing in for Harbor's resolve_pinned so provenance carries a digest.
        b = req.fake_behavior or {}
        digest = b.get("image_digest", req.image.split("@")[-1])
        return ResolvedImage(ref=req.image, digest=digest)

    def _execute(self, req: TrialRequest, resolved: ResolvedImage) -> ExecOutcome:
        b = req.fake_behavior or {}
        artifacts = self._artifacts_dir(req)

        # Write a transcript. If the script asks, echo a secret to exercise
        # redaction; the prompt is written so insulation tests can scan it.
        transcript = artifacts / "transcript.txt"
        lines = [f"PROMPT: {req.prompt}"]
        if b.get("echo_secret"):
            lines.append(f"exported OPENAI_API_KEY={b['echo_secret']}")
        lines.extend(b.get("transcript_extra", []))
        transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # The native telemetry log — always valid JSON on disk, so the shared
        # _read_native_log reads back exactly what was written (the dual-source
        # invariant: in-memory telemetry == on-disk pre-redaction bytes).
        native_log = b.get("native_log", {})
        (artifacts / "agent_log.json").write_text(
            json.dumps(native_log), encoding="utf-8"
        )

        # FAKE-ENGINE ONLY: simulate the agent writing files into its workspace
        # OUTSIDE artifacts/ (its solution, scratch configs, …). Lets insulation
        # and redaction tests exercise the whole-workspace surface a real trial
        # exposes, not just the captured transcript.
        for rel, content in (b.get("workspace_files") or {}).items():
            wf = Path(req.workspace) / rel
            wf.parent.mkdir(parents=True, exist_ok=True)
            wf.write_text(content, encoding="utf-8")

        outcome = Outcome(b.get("outcome", "completed"))
        # Simulate the metering proxy: the scripted egress attempts are reported
        # directly (a fake with a proxy but no log_path — e.g. an attestation test —
        # still surfaces its attempts), and each is written to the proxy log as an
        # ALIVE proxy would, keyed to the trial [RN-11]. The shared _scan_proxy_log
        # then fails closed if a configured log never appears (A10 parity).
        egress_attempts = list(b.get("egress_attempts", []))
        egress_violation = False
        if req.proxy is not None:
            for host in egress_attempts:
                allowed = req.proxy.is_allowed(host)
                if not allowed:
                    egress_violation = True
                if req.proxy.log_path:
                    # structured JSONL keyed to the trial — the same format a real
                    # metering proxy emits, so one parser serves both engines [RN-11].
                    with open(req.proxy.log_path, "a", encoding="utf-8") as fh:
                        fh.write(json.dumps({
                            "trial": req.trial_id,
                            "host": host,
                            "decision": "allow" if allowed else "deny",
                        }) + "\n")

        return ExecOutcome(
            outcome=outcome,
            exit_status=b.get("exit_status", 0 if outcome == Outcome.completed else 1),
            agent_binary_version=b.get("agent_binary_version", "fake-1.0.0"),
            engine_version=b.get("harbor_version", "fake-harbor-0"),
            failure_reason=b.get("infra_reason"),  # scripted reason [RN-14]
            # in-memory telemetry, decoupled from the on-disk log (a fixture may
            # overwrite agent_log.json to drive the seam's trajectory_corrupt path
            # while telemetry stays valid — the dual-source invariant).
            native_log=native_log,
            # The fake reports its own (scripted) egress + cost; the shared scan is
            # consulted only for the fail-closed proxy_log_missing check [A10].
            egress=EgressObservation(
                attempts=egress_attempts,
                violation=egress_violation,
                metered_cost=b.get("proxy_metered_cost"),
            ),
        )
