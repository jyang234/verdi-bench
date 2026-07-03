"""Fake engine [EVAL-4 §M1] — the fixture backbone for every downstream story.

Deterministic, no Docker. Scripted entirely by ``request.fake_behavior`` so tests
can drive completed/timeout/infra_failed outcomes, egress attempts, telemetry
logs, and artifact contents without a container runtime. It passes the *same*
contract suite as the Harbor engine, which is the whole point of the seam.
"""

from __future__ import annotations

import json
from pathlib import Path

from ...adapters.base import Outcome, Quotas
from ..types import EngineResult, TrialRequest


class FakeEngine:
    name = "fake"

    def run(self, request: TrialRequest) -> EngineResult:
        b = request.fake_behavior or {}
        artifacts = Path(request.workspace) / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)

        # Write a transcript. If the script asks, echo a secret to exercise
        # redaction; the prompt is written so insulation tests can scan it.
        transcript = artifacts / "transcript.txt"
        lines = [f"PROMPT: {request.prompt}"]
        if b.get("echo_secret"):
            lines.append(f"exported OPENAI_API_KEY={b['echo_secret']}")
        lines.extend(b.get("transcript_extra", []))
        transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")

        native_log = b.get("native_log", {})
        (artifacts / "agent_log.json").write_text(
            json.dumps(native_log), encoding="utf-8"
        )

        # FAKE-ENGINE ONLY: simulate the agent writing files into its workspace
        # OUTSIDE artifacts/ (its solution, scratch configs, …). Lets insulation
        # and redaction tests exercise the whole-workspace surface a real trial
        # exposes, not just the captured transcript.
        for rel, content in (b.get("workspace_files") or {}).items():
            wf = Path(request.workspace) / rel
            wf.parent.mkdir(parents=True, exist_ok=True)
            wf.write_text(content, encoding="utf-8")

        outcome = Outcome(b.get("outcome", "completed"))
        egress_attempts = list(b.get("egress_attempts", []))
        egress_violation = False
        if request.proxy is not None:
            for host in egress_attempts:
                if not request.proxy.is_allowed(host):
                    egress_violation = True
                    if request.proxy.log_path:
                        with open(request.proxy.log_path, "a", encoding="utf-8") as fh:
                            fh.write(f"DENY {host} trial={request.trial_id}\n")

        return EngineResult(
            outcome=outcome,
            native_log=native_log,
            artifacts_dir=artifacts,
            exit_status=b.get("exit_status", 0 if outcome == Outcome.completed else 1),
            image_digest=b.get("image_digest", request.image.split("@")[-1]),
            agent_binary_version=b.get("agent_binary_version", "fake-1.0.0"),
            harbor_version=b.get("harbor_version", "fake-harbor-0"),
            engine=self.name,
            quotas=request.quotas or Quotas(),
            egress_violation=egress_violation,
            egress_attempts=egress_attempts,
            executed_at=request.ts,
            proxy_metered_cost=b.get("proxy_metered_cost"),
            failure_reason=b.get("infra_reason"),  # scripted reason [RN-14]
        )
