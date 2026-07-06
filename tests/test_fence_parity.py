"""Observer fence ↔ render fence parity on correction consistency [refactor 01 §4 D8].

``render_markdown --official`` refuses a render whose applied multi-arm
correction differs from a prior official render's recorded one
(``_assert_correction_consistent``, F-H7). The observer projection
(``official_fence_report``, feeding ``/api/fence`` and the compare screen's
``official_ready``) omitted that check, so the screen could show ready while
``bench analyze --official`` refuses with CorrectionMismatchError. Interim fix
until the Phase-5 structural fence unification: the checklist carries the item,
evaluated through the render fence's own helper.
"""

from __future__ import annotations

import pytest

from harness.analyze.fence import official_fence_report
from harness.analyze.report import (
    CorrectionMismatchError,
    _assert_correction_consistent,
    effective_multi_arm_correction,
)
from harness.ledger import events
from tests.fixtures.builders import fixed_ctx, locked_experiment


def _correction_item(report: dict) -> dict:
    matches = [i for i in report["items"] if i["id"] == "correction"]
    assert matches, (
        "official_fence_report carries no correction-consistency item — the "
        "observer fence can show ready while the official render refuses "
        "CorrectionMismatchError [refactor 01 §4 D8]"
    )
    return matches[0]


def test_fence_report_fails_correction_item_when_render_fence_refuses(tmp_path):
    """A chain carrying a prior official render under a DIFFERENT multi-arm
    correction policy: the render fence refuses — the checklist must show a
    failing item too, not ok/absent."""
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path, ctx=ctx)
    # a prior official render recorded under a different correction policy
    events.record_findings_rendered(
        ledger, ctx, mode="official", primary_metric="holdout_pass_rate",
        ledger_head_hash="h", findings_sha256="s", multi_arm_correction="holm",
    )
    current = effective_multi_arm_correction(spec)
    assert current == "none"  # 2-arm spec: a single pre-registered pair
    # the render fence refuses …
    with pytest.raises(CorrectionMismatchError, match="one pre-registered decision"):
        _assert_correction_consistent(current, ledger)
    # … so the observer fence must fail the same requirement, wording included
    report = official_fence_report(tmp_path)
    item = _correction_item(report)
    assert item["state"] == "failed"
    assert "holm" in item["detail"] and "one pre-registered decision" in item["detail"]
    assert report["official_ready"] is False


def test_fence_report_correction_item_ok_when_consistent(tmp_path):
    """No prior official render, and a matching prior one, both pass the item —
    exactly the cases the render fence lets through."""
    ctx = fixed_ctx()
    spec, _, ledger = locked_experiment(tmp_path, ctx=ctx)
    assert _correction_item(official_fence_report(tmp_path))["state"] == "ok"

    events.record_findings_rendered(
        ledger, ctx, mode="official", primary_metric="holdout_pass_rate",
        ledger_head_hash="h", findings_sha256="s", multi_arm_correction="none",
    )
    _assert_correction_consistent(effective_multi_arm_correction(spec), ledger)
    assert _correction_item(official_fence_report(tmp_path))["state"] == "ok"
