"""Single blinding codepath [master plan §7.4].

One pattern-list scrub mechanism serves two *separate* lists:

* **identity** — arm ids, agent/model name patterns, transcript markers. Used to
  prove the judge (EVAL-2) and human review packet (EVAL-7) never see which arm
  produced an artifact.
* **secrets** — provider-key regexes (``sk-``/``AKIA``-style, ...). Used by
  EVAL-4's ``harness/run/redact.py`` at artifact capture. Secrets ≠ identity, so
  the lists are distinct even though the *mechanism* is shared.

``judge/packet.validate_identity_free`` and ``review/scrub.blind_scrub`` are thin
wrappers over :func:`identity_pattern_list`; ``run/redact.py`` wraps
:func:`secret_pattern_list`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Match:
    pattern: str
    text: str
    start: int
    end: int


@dataclass
class PatternList:
    """A named list of compiled regexes with scan/scrub over text."""

    name: str
    patterns: list[re.Pattern] = field(default_factory=list)

    def scan(self, text: str) -> list[Match]:
        if not text:
            return []
        found: list[Match] = []
        for pat in self.patterns:
            for m in pat.finditer(text):
                found.append(Match(pat.pattern, m.group(0), m.start(), m.end()))
        return found

    def contains(self, text: str) -> bool:
        return any(pat.search(text) for pat in self.patterns)

    def scrub(self, text: str, replacement: str = "[REDACTED]") -> tuple[str, int]:
        """Replace every match with ``replacement``; return (scrubbed, count)."""
        if not text:
            return text, 0
        count = 0

        def _sub(_m: re.Match) -> str:
            nonlocal count
            count += 1
            return replacement

        out = text
        for pat in self.patterns:
            out = pat.sub(_sub, out)
        return out, count


def _compile(raw: list[str], *, flags: int = re.IGNORECASE) -> list[re.Pattern]:
    return [re.compile(p, flags) for p in raw]


# ---------------------------------------------------------------------------
# Identity canary corpus — who produced an artifact.
# ---------------------------------------------------------------------------
_IDENTITY_NAME_PATTERNS = [
    r"claude[-_ ]?code",
    r"\bcodex\b",
    r"\bopencode\b",
    r"claude(?:-[a-z0-9.]+)?",
    r"\bgpt-[0-9][a-z0-9.\-]*",
    r"gemini(?:-[a-z0-9.]+)?",
    r"\banthropic\b",
    r"\bopenai\b",
    r"\bgoogle\b",
    # transcript role markers that would leak the harness/agent identity
    r"\bassistant:\s",
    r"\bhuman:\s",
    # EVAL-10 AC-2: contamination-canary values (markers embedded in task
    # content). A canary that reaches any published surface — judge packet,
    # review packet, render — is evidentially dead, so the shared scrub kills
    # the value everywhere; events and manifests carry its hash only.
    r"VBCANARY-[0-9a-f]{32}",
]


def arm_canaries(arms) -> list[str]:
    """The per-experiment identity literals of the *contestants*: each arm's
    name, platform, and every declared model id — primary and aux [EVAL-13
    AC-2], so a workflow's sub-model identities cannot pass the firewalls as
    undeclared literals would. The one place judge and review packets derive
    their spec-scoped canary set, so both firewalls scrub the same identities.
    Duck-typed on ``.name``/``.platform``/``.model``/``.aux_models`` (no schema
    import)."""
    out: list[str] = []
    seen: set = set()

    def _add(lit) -> None:
        if lit and lit not in seen:
            seen.add(lit)
            out.append(lit)

    for arm in arms:
        for lit in (arm.name, arm.platform, arm.model):
            _add(lit)
        for aux in getattr(arm, "aux_models", None) or []:
            # Duck-typed callers may carry aux entries as raw mappings; an
            # entry with no readable model id fails LOUDLY — silently omitting
            # an identity from the canary set would be a blinding breach.
            model = (
                aux.get("model") if isinstance(aux, Mapping)
                else getattr(aux, "model", None)
            )
            if not model:
                raise ValueError(
                    f"aux_models entry {aux!r} on arm {arm.name!r} has no readable "
                    "model id; refusing to silently omit an identity from the "
                    "blinding canary set [EVAL-13 AC-2]"
                )
            _add(model)
    return out


def identity_pattern_list(extra_literals: list[str] | None = None) -> PatternList:
    """Identity canaries plus any per-experiment literals (arm names, model ids).

    ``extra_literals`` are matched exactly (regex-escaped) — these are the arm
    ids and fully-versioned model ids drawn from the locked experiment, the
    surest tells of provenance.
    """
    raw = list(_IDENTITY_NAME_PATTERNS)
    for lit in extra_literals or []:
        if lit and lit.strip():
            raw.append(re.escape(lit.strip()))
    return PatternList("identity", _compile(raw))


# ---------------------------------------------------------------------------
# Secret redaction corpus — provider keys, NOT identity. [EVAL-4-D004]
# ---------------------------------------------------------------------------
_SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9_\-]{16,}",          # OpenAI / Anthropic style (covers sk-ant-…)
    r"AKIA[0-9A-Z]{16}",                # AWS access key id
    r"AIza[0-9A-Za-z_\-]{35}",          # Google API key
    r"gh[oprsu]_[A-Za-z0-9]{36,}",      # GitHub PAT/OAuth/app/user/refresh tokens
    r"github_pat_[A-Za-z0-9_]{22,}",    # GitHub fine-grained PAT
    r"glpat-[A-Za-z0-9_\-]{20,}",       # GitLab PAT
    r"xox[baprs]-[A-Za-z0-9\-]{10,}",   # Slack
    # Full PEM private-key block, header THROUGH footer — the key body must be
    # scrubbed too, not just the BEGIN marker [RN-8]. Non-greedy; ``[\s\S]``
    # spans newlines without needing a global DOTALL flag.
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    # Fallback for a TRUNCATED key (BEGIN with no matching END): still scrub the
    # header marker, as the pre-RN-8 pattern did — never leave it behind. Applied
    # after the full-block pattern, so a complete key is already fully redacted.
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
]


def secret_pattern_list(extra_patterns: list[str] | None = None) -> PatternList:
    """Provider-key patterns plus any configured extras (kept separate from
    identity — secrets are redacted, identity is blinded)."""
    raw = list(_SECRET_PATTERNS)
    raw.extend(extra_patterns or [])
    # secrets are case-sensitive tokens; do not fold case
    return PatternList("secrets", _compile(raw, flags=0))
