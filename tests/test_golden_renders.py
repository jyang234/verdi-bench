"""Render byte-fixtures for the golden scenario [refactor 01 §1 item 4].

The committed ``findings.exploratory.md`` / ``findings.official.md``, both
dossier HTMLs, and the card JSON must be byte-identical to what the current
code renders from the committed golden ledger + seed. These fixtures gate the
``report.py`` decomposition ([refactor 07]): every split step must leave them
byte-identical (the shipped determinism tests only did within-process double
renders).

Renders are recomputed from the exact ledger *prefix* each committed render
was produced at (findings are head-bound: ``_assert_head_hash`` refuses a
render whose head moved), derived deterministically from the committed ledger
by slicing before the first/second ``findings_rendered`` event.

Git-sha leak, documented and pinned around [refactor 01 §1 item 4]:
``compute_findings`` stamps ``findings.provenance.instrument_version`` /
``instrument_git_sha`` from the LIVE ``harness.version.instrument_identity()``
(rendered in the provenance section and embedded in the card). Every
recomputation therefore runs under ``goldens.pin_instrument()``; no harness
code was changed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.ledger.chain import split_ledger_lines
from tests.fixtures import goldens

_DATA = Path(__file__).parent / "fixtures" / "data"
_LEDGER = _DATA / "golden_ledger.ndjson"


def _fixture_bytes(name: str) -> bytes:
    return (_DATA / name).read_bytes()


@pytest.fixture(scope="module")
def golden_dir(tmp_path_factory) -> Path:
    """The committed golden experiment materialized on disk: the lock-verified
    spec bytes + the committed ledger (assert_lock recomputes the spec sha)."""
    d = tmp_path_factory.mktemp("golden-exp")
    (d / "experiment.yaml").write_bytes(_fixture_bytes("golden_experiment.yaml"))
    (d / "ledger.ndjson").write_bytes(_fixture_bytes("golden_ledger.ndjson"))
    return d


@pytest.fixture(scope="module")
def spec(golden_dir: Path):
    from harness.plan.lock import assert_lock

    return assert_lock(golden_dir / "experiment.yaml", golden_dir / "ledger.ndjson").spec


def _prefix_before_render(golden_dir: Path, nth: int) -> Path:
    """The committed ledger truncated just before its ``nth`` (1-based)
    ``findings_rendered`` event — the exact head state that render saw."""
    lines = split_ledger_lines(_LEDGER.read_bytes())
    render_idxs = [
        i for i, line in enumerate(lines)
        if json.loads(line)["event"] == "findings_rendered"
    ]
    prefix = golden_dir / f"ledger.prefix{nth}.ndjson"
    prefix.write_bytes(b"\n".join(lines[: render_idxs[nth - 1]]) + b"\n")
    return prefix


def _compute(ledger: Path, spec):
    from harness.analyze.report import compute_findings

    with goldens.pin_instrument():
        return compute_findings(
            ledger,
            spec,
            spec.seed,
            corpus_manifest=goldens.golden_manifest(),
            coverage_n_sim=goldens.COVERAGE_N_SIM,
            n_boot=goldens.N_BOOT,
        )


@pytest.fixture(scope="module")
def exploratory_state(golden_dir: Path, spec):
    ledger = _prefix_before_render(golden_dir, 1)
    return ledger, _compute(ledger, spec)


@pytest.fixture(scope="module")
def official_state(golden_dir: Path, spec):
    ledger = _prefix_before_render(golden_dir, 2)
    return ledger, _compute(ledger, spec)


def test_committed_spec_and_rubric_match_the_builder_constants():
    """The committed input fixtures and the builder's literal constants are the
    same bytes — the lock's spec sha and rubric sha stay reproducible."""
    assert _fixture_bytes("golden_experiment.yaml") == goldens.EXPERIMENT_YAML.encode("utf-8")
    assert _fixture_bytes("golden_rubric.md") == goldens.RUBRIC_MD.encode("utf-8")


def test_exploratory_markdown_bytes(exploratory_state, spec):
    from harness.analyze.report import render_markdown

    ledger, findings = exploratory_state
    rendered = render_markdown(
        findings, ledger, "exploratory", corpus_manifest=goldens.golden_manifest()
    )
    assert rendered.encode("utf-8") == _fixture_bytes("golden_findings.exploratory.md")


def test_exploratory_dossier_bytes(exploratory_state):
    from harness.analyze.dossier import render_dossier

    ledger, findings = exploratory_state
    rendered = render_dossier(
        findings, ledger, "exploratory", corpus_manifest=goldens.golden_manifest()
    )
    assert rendered.encode("utf-8") == _fixture_bytes(
        "golden_findings.exploratory.dossier.html"
    )


def test_official_markdown_bytes(official_state):
    """The official fence genuinely clears on the golden scenario (ledgered
    full-run-validated calibration + genuinely passing selfcheck) — the fence
    was not weakened to force this render [refactor 01 §1 item 4]."""
    from harness.analyze.report import render_markdown

    ledger, findings = official_state
    rendered = render_markdown(
        findings, ledger, "official", corpus_manifest=goldens.golden_manifest()
    )
    assert rendered.encode("utf-8") == _fixture_bytes("golden_findings.official.md")


def test_official_dossier_bytes(official_state):
    from harness.analyze.dossier import render_dossier

    ledger, findings = official_state
    rendered = render_dossier(
        findings, ledger, "official", corpus_manifest=goldens.golden_manifest()
    )
    assert rendered.encode("utf-8") == _fixture_bytes(
        "golden_findings.official.dossier.html"
    )


def test_card_json_bytes(golden_dir: Path, spec):
    from harness.analyze.card import build_card, serialize_card

    with goldens.pin_instrument():
        card = build_card(
            golden_dir / "ledger.ndjson",
            spec,
            task_ids=[t["id"] for t in goldens.GOLDEN_TASKS],
            corpus_manifest=goldens.golden_manifest(),
        )
    assert serialize_card(card).encode("utf-8") == _fixture_bytes("golden_card.json")


def test_renders_carry_pinned_identity_never_the_live_sha():
    """The documented leak field, pinned around: findings provenance stamps
    instrument identity into the render/card bytes. The committed fixtures
    must carry the pinned identity and never the live checkout sha."""
    import re

    from harness.version import git_sha

    official = _fixture_bytes("golden_findings.official.md").decode("utf-8")
    assert (
        f"- instrument: {goldens.PINNED_INSTRUMENT_VERSION} "
        f"@ {goldens.PINNED_INSTRUMENT_GIT_SHA[:12]}"
    ) in official

    card = json.loads(_fixture_bytes("golden_card.json"))
    assert card["instrument"]["version"] == goldens.PINNED_INSTRUMENT_VERSION
    assert card["instrument"]["git_sha"] == goldens.PINNED_INSTRUMENT_GIT_SHA

    live = git_sha()
    if re.fullmatch(r"[0-9a-f]{40}", live) and live != goldens.PINNED_INSTRUMENT_GIT_SHA:
        for name in (
            "golden_findings.exploratory.md",
            "golden_findings.official.md",
            "golden_findings.exploratory.dossier.html",
            "golden_findings.official.dossier.html",
            "golden_card.json",
            "golden_ledger.ndjson",
        ):
            assert live.encode("ascii") not in _fixture_bytes(name), name
