"""Cutoff dating tri-state [EVAL-10 AC-1].

The dating channel is a pure function of manifest dates + spec cutoffs: every
date combination maps to a deterministic tri-state, an absent date is an honest
``unknown`` (never coerced to clean), and a positive detection outranks dating.
"""

from __future__ import annotations

import pytest

from harness.contamination.dating import (
    ContaminationStatus,
    DatingError,
    cutoff_status,
)
from pydantic import ValidationError

from harness.corpus.registry import CorpusManifest, TaskEntry
from harness.schema.experiment import ExperimentSpec
from tests.fixtures.builders import valid_experiment_dict


def test_ac1_cutoff_tristate():
    """Every date combination maps deterministically [AC-1]."""
    # created strictly after the cutoff — the one provably-clean case
    assert (
        cutoff_status("2026-07-01T00:00:00Z", "2026-01-01T00:00:00Z")
        is ContaminationStatus.CLEAN_BY_DATE
    )
    # created before or exactly at the cutoff: could have been in training
    assert (
        cutoff_status("2025-06-01T00:00:00Z", "2026-01-01T00:00:00Z")
        is ContaminationStatus.UNKNOWN
    )
    assert (
        cutoff_status("2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        is ContaminationStatus.UNKNOWN
    )
    # date-only and naive forms compare against Z-suffixed timestamps
    assert cutoff_status("2026-07-01", "2026-01-01") is ContaminationStatus.CLEAN_BY_DATE
    assert (
        cutoff_status("2026-07-01", "2026-01-01T00:00:00Z")
        is ContaminationStatus.CLEAN_BY_DATE
    )
    # a positive AC-3/AC-4 detection outranks dating — even post-cutoff dates
    # (a detection on a "clean by date" task means the dates are wrong, not the
    # evidence)
    assert (
        cutoff_status("2026-07-01", "2026-01-01", flagged=True)
        is ContaminationStatus.FLAGGED
    )
    assert cutoff_status(None, None, flagged=True) is ContaminationStatus.FLAGGED
    # pure: identical inputs, identical outputs
    assert cutoff_status("2026-07-01", "2026-01-01") is cutoff_status(
        "2026-07-01", "2026-01-01"
    )


def test_ac1_unknown_never_clean():
    """An absent date yields ``unknown``, never ``clean_by_date`` [AC-1]."""
    for created, cutoff in [
        (None, "2026-01-01T00:00:00Z"),  # task has no created_at
        ("2026-07-01T00:00:00Z", None),  # arm model publishes no cutoff
        (None, None),
    ]:
        status = cutoff_status(created, cutoff)
        assert status is ContaminationStatus.UNKNOWN
        assert status is not ContaminationStatus.CLEAN_BY_DATE


def test_malformed_date_refused_loudly():
    """A malformed date raises naming the field — it never silently degrades to
    ``unknown`` [fail-loudly]."""
    with pytest.raises(DatingError, match="created_at"):
        cutoff_status("not-a-date", "2026-01-01")
    with pytest.raises(DatingError, match="training_cutoff"):
        cutoff_status("2026-07-01", "junk")


def test_arm_training_cutoff_field():
    """The arm schema carries an optional RFC 3339 ``training_cutoff`` [AC-1]."""
    data = valid_experiment_dict()
    data["arms"][0]["training_cutoff"] = "2026-01-01T00:00:00Z"
    spec = ExperimentSpec.from_dict(data)
    assert spec.arms[0].training_cutoff == "2026-01-01T00:00:00Z"
    assert spec.arms[1].training_cutoff is None  # optional — absent stays legal

    data["arms"][0]["training_cutoff"] = "not-a-date"
    with pytest.raises(Exception, match="training_cutoff"):
        ExperimentSpec.from_dict(data)


def test_task_entry_created_at_field():
    """The manifest task entry carries an optional RFC 3339 ``created_at`` and
    ``stage_candidate`` plumbs it through [AC-1]."""
    entry = TaskEntry(task_id="t1", sha="a" * 64, created_at="2026-07-01T00:00:00Z")
    assert entry.created_at == "2026-07-01T00:00:00Z"
    # pydantic wraps the validator's CorpusError in a ValidationError (the same
    # surface the registry's other validators, e.g. semver, present)
    with pytest.raises(ValidationError, match="created_at"):
        TaskEntry(task_id="t1", sha="a" * 64, created_at="yesterday")

    manifest = CorpusManifest(corpus_id="c", semver="1.0.0", kind="public")
    staged = manifest.stage_candidate(
        "t2", sha="b" * 64, miner="m", created_at="2026-06-30T12:00:00Z"
    )
    assert staged.created_at == "2026-06-30T12:00:00Z"


def test_contamination_config_pre_registered():
    """The overlap threshold rides the locked spec bytes — pre-registered by
    construction; nonsense thresholds are refused at the schema [D003]."""
    data = valid_experiment_dict()
    data["contamination"] = {"overlap_threshold": 0.6}
    spec = ExperimentSpec.from_dict(data)
    assert spec.contamination is not None
    assert spec.contamination.overlap_threshold == 0.6
    # absent block stays legal (module default applies downstream)
    assert ExperimentSpec.from_dict(valid_experiment_dict()).contamination is None
    for bad in (0, -0.1, 1.5):
        data["contamination"] = {"overlap_threshold": bad}
        with pytest.raises(Exception):
            ExperimentSpec.from_dict(data)
