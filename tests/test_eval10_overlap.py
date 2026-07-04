"""Solution-overlap detector: winnowing fingerprints [EVAL-10 AC-4, D003]."""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from harness.contamination.overlap import (
    DEFAULT_OVERLAP_THRESHOLD,
    OverlapError,
    solution_overlap,
)
from harness.run.seam import HoldoutLeakError

_ORACLE = """
def parse_config(path):
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if "version" not in data:
        raise ValueError("config file is missing the required version key")
    entries = data.get("entries", [])
    return Config(version=data["version"], entries=entries)
"""

# the same solution modulo whitespace, case, and formatting cosmetics — the
# exact evasions D003 chose winnowing to survive
_NEAR_VERBATIM = """
def parse_config( path ):
        with open(path,   encoding = "UTF-8") as handle:
            data = json.load( handle )
        if "version" not in data:
                raise ValueError("config file is missing the required version key")
        entries = data.get("entries", [])
        return Config(version=data["version"], entries=entries)
"""

# solves the same problem, independently written: different structure,
# different names, different library
_INDEPENDENT = """
class SettingsLoader:
    def load(self, filename):
        raw = Path(filename).read_text()
        parsed = yaml.safe_load(raw) or {}
        self._require_schema_field(parsed)
        return Settings.from_mapping(parsed)

    def _require_schema_field(self, mapping):
        if "schema" not in mapping:
            raise SettingsError("no schema field present")
"""

_HOLDOUT = """
def test_parse_config_rejects_missing_version(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"entries": [1, 2, 3]}))
    with pytest.raises(ValueError, match="version"):
        parse_config(cfg)
"""


def test_ac4_overlap_flags_verbatim():
    """Planted verbatim and near-verbatim solutions flag; an independently
    written solution does not [AC-4]."""
    verbatim = solution_overlap(_ORACLE, oracle=_ORACLE)
    assert verbatim.flagged
    assert verbatim.oracle_score == 1.0

    near = solution_overlap(_NEAR_VERBATIM, oracle=_ORACLE)
    assert near.flagged
    assert near.oracle_score >= DEFAULT_OVERLAP_THRESHOLD

    independent = solution_overlap(_INDEPENDENT, oracle=_ORACLE)
    assert not independent.flagged
    assert independent.oracle_score < DEFAULT_OVERLAP_THRESHOLD

    # verbatim copy buried inside a larger solution still flags
    padded = "import json\n\n# helpers first\n" + _INDEPENDENT + "\n" + _ORACLE
    assert solution_overlap(padded, oracle=_ORACLE).flagged


def test_ac4_holdout_overlap_alarms():
    """Holdout overlap raises the EVAL-4 insulation alarm alongside the flag —
    the agent should never have seen that content at all [AC-4]."""
    solution = "# my tests\n" + _HOLDOUT
    with pytest.raises(HoldoutLeakError, match="holdout") as exc:
        solution_overlap(solution, oracle=_ORACLE, holdouts=[_HOLDOUT])
    result = exc.value.result
    assert result.flagged
    assert result.holdout_scores[0] >= DEFAULT_OVERLAP_THRESHOLD

    # an independent solution raises no alarm and stays unflagged
    clean = solution_overlap(_INDEPENDENT, oracle=_ORACLE, holdouts=[_HOLDOUT])
    assert not clean.flagged
    assert clean.holdout_scores[0] < DEFAULT_OVERLAP_THRESHOLD


def test_overlap_deterministic_bytes():
    """Byte-identical output for fixed inputs — hashlib fingerprints, no salted
    ``hash()``, no ordering dependence [AC-4 vc]."""
    a = solution_overlap(_NEAR_VERBATIM, oracle=_ORACLE, holdouts=[_HOLDOUT])
    b = solution_overlap(_NEAR_VERBATIM, oracle=_ORACLE, holdouts=[_HOLDOUT])
    assert json.dumps(asdict(a), sort_keys=True) == json.dumps(asdict(b), sort_keys=True)


def test_degenerate_reference_refused():
    """A reference too short to fingerprint is refused loudly, never silently
    scored 0.0 [fail-loudly]."""
    with pytest.raises(OverlapError, match="oracle"):
        solution_overlap(_INDEPENDENT, oracle="tiny")
    with pytest.raises(OverlapError, match="holdout #0"):
        solution_overlap(_INDEPENDENT, oracle=_ORACLE, holdouts=["also tiny"])
    with pytest.raises(OverlapError, match="threshold"):
        solution_overlap(_INDEPENDENT, oracle=_ORACLE, threshold=0.0)
