"""Judge configuration block, validated at plan time [EVAL-3 §4.1, EVAL-2 §M4].

EVAL-3 owns the *shape*; EVAL-2 consumes it. The alias-id rejection lives here
(not in EVAL-2) so that plan-time validation refuses un-versioned judge ids
before a lock is ever written [EVAL-2 AC-5]. There is **no vendor allow/deny
list** — any provider is legal [EVAL-2-D001]; only version specificity is
required.
"""

from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator

from .errors import AliasJudgeIdError

# An id segment counts as "explicitly versioned" only if it carries a date or a
# long numeric build stamp. JD-6: a bare dotted version (``1.5``, ``4.1``) names a
# mutable *family*, not a pinned build — ``google/gemini-1.5-pro`` and
# ``openai/gpt-4.1`` are aliases and must be rejected; the same family with a
# date/build suffix (``gpt-4.1-2025-04-14``, ``gemini-1.5-pro-002``) is pinned.
# Bare single-number suffixes (``gpt-5``) and word-only ids (``gemini-pro``) are
# also aliases.
_VERSIONED = re.compile(
    r"""
    \d{4}-\d{2}-\d{2}      # 2024-08-06
    | \d{8}               # 20241022
    | \d{6}\b             # 202410 / build stamp
    | -\d{3,}\b           # -002, -1106
    """,
    re.VERBOSE,
)


def is_alias_model_id(model: str) -> bool:
    """True if ``model`` is an un-versioned alias that must be rejected at plan.

    Requires the ``<provider>/<id>`` shape and an explicitly versioned id.
    """
    if "/" not in model:
        return True
    provider, _, ident = model.partition("/")
    if not provider.strip() or not ident.strip():
        return True
    return _VERSIONED.search(ident) is None


class EscalationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Defaults pending EVAL-2-D006; strictly config so resolution is a yaml edit.
    kappa_threshold: float = 0.6
    min_human_verdicts: int = 20


class JudgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    rubric: str
    orders: Literal["both", "single"] = "both"
    temperature: float = 0.0
    panel: Optional[dict] = None  # v2; schema stubbed only
    escalation: EscalationConfig = EscalationConfig()

    @field_validator("model")
    @classmethod
    def _reject_alias(cls, v: str) -> str:
        if is_alias_model_id(v):
            raise AliasJudgeIdError(
                f"judge.model {v!r} is not a fully-versioned id "
                "(expected '<provider>/<versioned-id>', e.g. "
                "'google/gemini-1.5-pro-002'); alias ids are rejected at plan time"
            )
        return v
