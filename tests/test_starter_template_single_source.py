"""The starter example spec lives in exactly ONE file [refactor 02 §2].

The audit found the "example spec" copy-pasted into >=5 places, several still
naming a retired model id. Phase 2 makes ``harness/sdk/templates/starter-
experiment.yaml`` the single source; this suite is the forcing function that
keeps every consumer provably derived from it, so they can never drift again:

- the template itself is a valid spec (the real pydantic validators);
- the test fixture builders parse it (byte-neutral);
- the author page embeds it verbatim as its template pane;
- the usage-guide §2.1 example is field-compatible with it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from harness.schema.experiment import ExperimentSpec
from harness.sdk import starter_spec_text

REPO = Path(__file__).resolve().parents[1]


def _template_dict() -> dict:
    return yaml.safe_load(starter_spec_text())


def _extract_docs_example() -> dict:
    """The first ```yaml fenced block under `### 2.1` in the usage guide."""
    text = (REPO / "docs" / "usage-guide.md").read_text(encoding="utf-8")
    after = text.split("### 2.1", 1)
    assert len(after) == 2, "usage-guide.md lost its §2.1 heading"
    m = re.search(r"```yaml\n(.*?)\n```", after[1], re.DOTALL)
    assert m, "no ```yaml example block under usage-guide §2.1"
    return yaml.safe_load(m.group(1))


def test_template_is_a_valid_spec():
    """The canonical starter must itself pass the real validators — a broken
    scaffold would ship a spec that `bench plan` refuses."""
    spec = ExperimentSpec.from_dict(_template_dict())
    assert len(spec.arms) >= 2
    # date-versioned, non-alias judge id (aliases are refused at plan time). The
    # scaffold ships the keyless fake/ deterministic judge (ux-friction D1-A) so
    # the default path runs end to end with no key; the id is still date-versioned,
    # the exact non-alias property D10's retired-id audit called out.
    assert spec.judge.model == "fake/deterministic-2026-01-01"


def test_builders_fixture_is_derived_from_the_template():
    """tests/fixtures/builders.valid_experiment_dict parses the template rather
    than keeping a copy — proven byte-equal so the many callers that pin the
    produced spec are unaffected."""
    from tests.fixtures.builders import valid_experiment_dict

    assert valid_experiment_dict() == _template_dict()
    # overrides still compose on top of the derived base.
    assert valid_experiment_dict(seed=7)["seed"] == 7


def test_author_template_pane_is_the_template_verbatim():
    """harness/author/page.py seeds its template pane from the same file — the
    JS embeds the exact bytes (json-encoded), so the author scaffold can't drift
    from the spec the docs and builders use."""
    from harness.author.page import AUTHOR_PAGE

    assert json.dumps(starter_spec_text()) in AUTHOR_PAGE


def test_usage_guide_example_is_field_compatible_with_the_template():
    """docs/usage-guide.md §2.1 parses to a valid spec AND shares the template's
    field structure (top-level + judge keys), so a field added/removed in one
    surface forces the other to follow [W2 rot forcing function, 02 §7]."""
    docs = _extract_docs_example()
    template = _template_dict()

    # parses through the real validators
    ExperimentSpec.from_dict(docs)
    # same top-level field set, same judge sub-keys, same arm keys
    assert set(docs) == set(template)
    assert set(docs["judge"]) == set(template["judge"])
    assert {k for a in docs["arms"] for k in a} == {
        k for a in template["arms"] for k in a
    }
