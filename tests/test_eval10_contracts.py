"""Import hygiene: the deterministic detectors are LLM-free [EVAL-10 AC-6]."""

from __future__ import annotations

from tests.test_import_contracts import _REPO, _run_lint


def test_ac6_detectors_llm_free():
    """The contract is load-bearing: lint-imports is green with the detectors
    clean, and a planted provider import in ``overlap.py`` breaks it [AC-6].

    The probe module is deliberately outside the contract — it is the story's
    only LLM-touching module, importing the provider seam like ``harness.process``
    does; the deterministic tier (dating, canary, overlap, summary) may not.
    """
    assert _run_lint().returncode == 0, "contracts must be green before planting"
    path = _REPO / "harness/contamination/overlap.py"
    original = path.read_text(encoding="utf-8")
    injected = (
        original
        + "\n\ndef _planted_contract_violation():  # test-injected, restored below\n"
        + "    import harness.judge.client  # noqa\n"
    )
    try:
        path.write_text(injected, encoding="utf-8")
        result = _run_lint()
        assert result.returncode != 0, (
            "planting an LLM-client import into the overlap detector did not "
            f"break any contract:\n{result.stdout}"
        )
        assert "BROKEN" in result.stdout, result.stdout
        assert "judge.client" in result.stdout, result.stdout
    finally:
        path.write_text(original, encoding="utf-8")
    assert _run_lint().returncode == 0, "restoration must leave contracts green"
