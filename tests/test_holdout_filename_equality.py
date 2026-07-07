"""The grader-output filename agrees across the three subsystems that name it
[refactor 13 OI-A, closeout item 6].

``grade`` writes the deterministic grader's output to ``holdout_results.json``;
both ``run.workspace`` (hashing a trial's solution bytes) and
``contamination.scan`` (walking that solution for holdout overlap) must EXCLUDE
that file from their workspace walk, so each names it. The three constants are
deliberately NOT single-sourced: ``run`` and ``contamination`` are peer
subsystems to ``grade`` and keep the workspace-walk exclusion local rather than
importing ``grade``'s grader-runner module for a single filename string — the
sanctioned layering call recorded in closeout item 6. Left unchecked, three
independent literals can silently drift; this meta-test is the forcing function
that keeps them equal, mirroring ``test_starter_template_single_source``.

The test is the sanctioned cross-layer reader: it imports the private
``_GRADER_OUTPUT`` copies precisely to prove they equal the public canonical.
"""

from __future__ import annotations

from harness.contamination.scan import _GRADER_OUTPUT as SCAN_GRADER_OUTPUT
from harness.grade.runners import HOLDOUT_RESULTS
from harness.run.workspace import _GRADER_OUTPUT as WORKSPACE_GRADER_OUTPUT


def test_holdout_filename_agrees_across_subsystems():
    """``grade.runners.HOLDOUT_RESULTS`` is the canonical grader-output filename;
    ``run.workspace._GRADER_OUTPUT`` and ``contamination.scan._GRADER_OUTPUT``
    are its two deliberate copies. All three must be byte-identical, or a
    workspace walk would hash/scan the grader's own output against the holdouts
    that output derives from."""
    assert HOLDOUT_RESULTS == WORKSPACE_GRADER_OUTPUT == SCAN_GRADER_OUTPUT
