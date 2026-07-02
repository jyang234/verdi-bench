"""Merge-request mining [EVAL-8 §M3, AC-3, D002].

``mine_mr`` turns a merged MR into a *pending* task candidate: the workspace
resets to the MR's **parent sha**, the prompt is the ticket text, and the
holdouts are the MR's shipped test additions (its diff restricted to test
paths), optionally hardened with groundwork rules. A candidate is only ever
``pending-curation`` out of mining — auto-admission is unrepresentable [D002];
admission is a separate human gate (:mod:`harness.corpus.admit`).

Prompt-from-ticket is the leakage hot spot: curation review must look for
solution leakage in the prompt [risks §9]. Mining does not judge that — it
stages the candidate for the reviewer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Optional

# A file is a "test addition" if it is newly added AND lives on a test path.
_TEST_PATH_RE = re.compile(
    r"""
    (^|/)tests?/            # a tests/ or test/ directory segment
    | (^|/)test_[^/]+$      # test_foo.py
    | _test\.[a-z0-9]+$     # foo_test.go / foo_test.py
    | \.test\.[a-z0-9]+$    # foo.test.ts
    | (^|/)spec/            # a spec/ directory
    | _spec\.[a-z0-9]+$     # foo_spec.rb
    """,
    re.VERBOSE | re.IGNORECASE,
)


def is_test_path(path: str) -> bool:
    return bool(_TEST_PATH_RE.search(path))


@dataclass(frozen=True)
class MRFile:
    """One file in an MR diff. ``change`` is the git status letter's meaning."""

    path: str
    change: Literal["added", "modified", "deleted"]
    content: str = ""


@dataclass(frozen=True)
class MergeRequest:
    """The mining seam's input — a merged MR reduced to what mining needs."""

    parent_sha: str
    files: list[MRFile]


@dataclass
class Candidate:
    """A pending task candidate mined from an MR [§4.3]."""

    workspace_ref: str  # parent sha — the pre-change workspace state
    prompt: str  # ticket text
    holdouts: list[dict] = field(default_factory=list)  # shipped test additions
    groundwork_rules: Optional[list[dict]] = None
    status: Literal["pending-curation"] = "pending-curation"


def mine_mr(
    mr: MergeRequest,
    ticket_text: str,
    *,
    groundwork_rules: Optional[list[dict]] = None,
) -> Candidate:
    """Stage a pending candidate from ``mr`` and ``ticket_text``.

    Holdouts are the MR's **added** files on test paths — the shipped tests that
    encode the acceptance criteria, held out from the agent. Modified test files
    are excluded (they may already have existed in the parent workspace).
    """
    holdouts = [
        {"path": f.path, "content": f.content}
        for f in mr.files
        if f.change == "added" and is_test_path(f.path)
    ]
    return Candidate(
        workspace_ref=mr.parent_sha,
        prompt=ticket_text,
        holdouts=sorted(holdouts, key=lambda h: h["path"]),
        groundwork_rules=groundwork_rules,
        status="pending-curation",
    )
