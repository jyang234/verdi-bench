"""Process-scoring packet [EVAL-9 §M2, AC-3, AC-4, AC-7].

``build_process_packet(transcript, rubric, telemetry=None)`` — the signature **is**
the allowlist, exactly as EVAL-2's judge packet. There is **no parameter for
outcome-verdict content**, so a process packet that carries a verdict is
unrepresentable by construction (property-tested). The judge process call is a
separate model call sharing no context with outcome verdicts.

Redaction is **upstream** (EVAL-4 artifact capture); as defense in depth this
builder re-scans the transcript against the shared **secret** canary corpus and
fails closed if a secret survived — a redaction canary must never reach the
scorer payload [AC-4]. Identity is *not* scrubbed: this tier is openly unblinded.

The human-facing form juxtaposes deterministic telemetry correlates beside each
dimension, anchoring human scoring in data [AC-7]; the judge form omits telemetry
(the judge scores the transcript, not the meter).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..blind.core import secret_pattern_list
from .rubric import ProcessRubric


class RedactionLeakError(RuntimeError):
    """A secret canary survived upstream redaction — payload blocked [AC-4]."""


@dataclass
class ProcessPacket:
    transcript: str
    rubric: ProcessRubric
    telemetry: Optional[dict] = None  # human packets only

    def render_judge(self) -> list[dict]:
        """Judge messages: rubric + transcript only, no telemetry, no verdict."""
        body = (
            f"{self.rubric.render()}\n\n"
            "# Transcript (post-redaction, full)\n"
            f"{self.transcript}\n\n"
            "Score each dimension on its 1..5 anchored scale. Reply as JSON: "
            '{\"scores\": {\"<dim_id>\": <1-5>, ...}}. If you cannot score a '
            'dimension, use \"CANT_SCORE\" for that dimension.'
        )
        return [
            {"role": "system", "content": "You score how the work was done, not who did it."},
            {"role": "user", "content": body},
        ]

    def render_human(self) -> str:
        """Human-facing packet: each dimension juxtaposed with its telemetry correlates."""
        lines = [self.rubric.render(), "", "# Deterministic telemetry (juxtaposed)"]
        tel = self.telemetry or {}
        for d in self.rubric.dimensions:
            corr = {c: tel.get(c) for c in d.telemetry_correlates}
            lines.append(f"- {d.name} ({d.id}): {corr}")
        lines += ["", "# Transcript (post-redaction, full)", self.transcript]
        return "\n".join(lines)


def build_process_packet(
    transcript: str,
    rubric: ProcessRubric,
    telemetry: Optional[dict] = None,
) -> ProcessPacket:
    """Assemble a process packet. Parameters are the entire allowlist [AC-3].

    Note there is deliberately no ``verdict`` / ``winner`` / ``judge_*`` parameter:
    outcome-verdict content is unreachable by construction.
    """
    # Defense in depth: redaction is upstream, but a surviving secret canary must
    # never reach the scorer payload [AC-4].
    if secret_pattern_list().contains(transcript):
        raise RedactionLeakError(
            "transcript contains an un-redacted secret canary; upstream redaction "
            "(EVAL-4) must run before any scorer sees the transcript [AC-4]"
        )
    return ProcessPacket(transcript=transcript, rubric=rubric, telemetry=telemetry)
