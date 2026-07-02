"""Judge packet [EVAL-2 AC-2, D002].

``build_packet(response_a, response_b, task_prompt, rubric)`` — the function
signature **is** the allowlist. The judge sees only: task prompt, final workspace
diff per response, holdout results per response, rubric. Transcripts, telemetry,
arm labels, agent/model names, and job paths are not parameters, so they are
unreachable by construction. Response labels "Response 1/2" are assigned per call
(both orders are exercised by the client, cancelling position bias).

``validate_identity_free`` delegates to ``harness/blind/core.py`` (the single
blinding codepath, master plan §7.4). A packet that trips a canary is **never
sent**.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from ..blind.core import identity_pattern_list


class IdentityLeakError(RuntimeError):
    """A packet contained an identity canary — it must not be sent [AC-2]."""


@dataclass
class ResponseArtifacts:
    """The allowlisted view of one response: outcomes only, no identity."""

    diff: str
    holdout_results: list = field(default_factory=list)


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass
class Packet:
    task_prompt: str
    rubric: str
    rubric_sha256: str
    response_a: ResponseArtifacts
    response_b: ResponseArtifacts
    packet_sha256: str

    def render(self, order: str) -> list[dict]:
        """Render messages with Response 1/2 assigned by ``order`` ('AB'|'BA')."""
        if order == "AB":
            first, second = self.response_a, self.response_b
        elif order == "BA":
            first, second = self.response_b, self.response_a
        else:  # pragma: no cover - guarded by caller
            raise ValueError(f"order must be 'AB' or 'BA', got {order!r}")
        body = (
            f"# Task\n{self.task_prompt}\n\n"
            f"# Rubric\n{self.rubric}\n\n"
            f"# Response 1\n## Diff\n{first.diff}\n"
            f"## Holdout results\n{_canonical(first.holdout_results)}\n\n"
            f"# Response 2\n## Diff\n{second.diff}\n"
            f"## Holdout results\n{_canonical(second.holdout_results)}\n"
        )
        return [
            {"role": "system", "content": "You judge results, never the contestants."},
            {"role": "user", "content": body},
        ]


def build_packet(
    response_a: ResponseArtifacts,
    response_b: ResponseArtifacts,
    task_prompt: str,
    rubric: str,
) -> Packet:
    """Assemble a blind packet. The parameters are the *entire* allowlist."""
    rubric_sha = hashlib.sha256(rubric.encode("utf-8")).hexdigest()
    # packet hash is order-independent: hash the sorted content of both responses
    content = _canonical(
        {
            "task_prompt": task_prompt,
            "rubric_sha256": rubric_sha,
            "responses": sorted(
                [
                    {"diff": response_a.diff, "holdout": response_a.holdout_results},
                    {"diff": response_b.diff, "holdout": response_b.holdout_results},
                ],
                key=_canonical,
            ),
        }
    )
    packet_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return Packet(
        task_prompt=task_prompt,
        rubric=rubric,
        rubric_sha256=rubric_sha,
        response_a=response_a,
        response_b=response_b,
        packet_sha256=packet_sha,
    )


def validate_identity_free(packet: Packet, canaries: list[str] | None = None) -> None:
    """Scan every packet field against the identity canary corpus.

    Raises :class:`IdentityLeakError` on any hit, so a leaking packet is never
    sent. ``canaries`` are per-experiment literals (arm ids, model ids).
    """
    patterns = identity_pattern_list(extra_literals=canaries)
    # Scan every field render() emits to the judge — including the rubric, which
    # is embedded verbatim in the message body and is otherwise an unscanned
    # provenance channel if a rubric author references an arm/model.
    blobs = [packet.task_prompt, packet.rubric, packet.response_a.diff, packet.response_b.diff]
    blobs.append(_canonical(packet.response_a.holdout_results))
    blobs.append(_canonical(packet.response_b.holdout_results))
    for blob in blobs:
        hits = patterns.scan(blob)
        if hits:
            raise IdentityLeakError(
                f"packet contains identity marker {hits[0].text!r}; not sent [AC-2]"
            )
