"""Shared fixture builders [master plan §7.6].

Fabricate a miniature experiment (valid yaml, deterministic ledger context) so
each story's fixture ACs compose instead of hand-rolling ledgers. Fault
injection is via dependency-injected clock/actor.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import yaml

from harness.ledger.events import EventContext

_counter = itertools.count()


def fixed_ctx(experiment_id: str = "exp-fixture", actor: str = "tester") -> EventContext:
    """Deterministic EventContext: monotonic synthetic timestamps, fixed actor."""
    seq = itertools.count()

    def clock() -> str:
        return f"2026-01-01T00:00:{next(seq):02d}+00:00"

    return EventContext(experiment_id=experiment_id, actor=actor, clock=clock)


def valid_experiment_dict(**overrides) -> dict:
    base = {
        "arms": [
            {"name": "control", "platform": "claude_code", "model": "anthropic/claude-3-5-sonnet-20241022", "payload": {}},
            {"name": "treatment", "platform": "codex", "model": "openai/gpt-4o-2024-08-06", "payload": {}},
        ],
        "corpus": {"id": "public-mini", "version": "1.0.0"},
        "repetitions": 3,
        "primary_metric": "holdout_pass_rate",
        "decision_rule": "delta_holdout_pass_rate > 0",
        "judge": {
            "model": "google/gemini-1.5-pro-002",
            "rubric": "rubrics/code-task-v1.md",
            "orders": "both",
            "temperature": 0,
        },
        "seed": 1234,
        "cost_ceiling": {"amount": 25.0, "currency": "USD"},
    }
    base.update(overrides)
    return base


def write_experiment_yaml(path: Path, **overrides) -> Path:
    path = Path(path)
    path.write_text(yaml.safe_dump(valid_experiment_dict(**overrides)), encoding="utf-8")
    return path
