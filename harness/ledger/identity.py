"""Experiment-id provenance resolution [ux-friction AC-1].

Every ledgering stage stamps an ``experiment_id`` on its :class:`EventContext`,
and the old sites all derived it the same wrong way: ``Path(experiment_dir).name``
on the UNRESOLVED path the CLI was handed. A cd-in invocation — ``bench run .``,
``bench grade .``, the bare-relative ``bench plan experiment.yaml`` that
``bench init`` itself prints — then baked ``experiment_id=''`` into every event of
the permanent hash-chained ledger (the F1 friction), because the ``.name`` of an
unresolved ``.`` or bare relative path is empty.

This is the one seam every derivation site now shares — the experiment-id
analogue of :mod:`~harness.ledger.actor`'s actor resolution: resolve the path
first, take the directory name, and refuse a path that resolves to a nameless
directory rather than ledger an empty id. Resolving first makes the id
path-independent, so ``.``, a bare relative name, and the absolute path to the
same directory all stamp the identical provenance.
"""

from __future__ import annotations

from pathlib import Path

from ..errors import VerdiRefusal


class ExperimentIdResolutionError(VerdiRefusal, RuntimeError):
    """The experiment path resolves to a directory with no name to identify the
    experiment [ux-friction AC-1].

    Stages stamp ``provenance.experiment_id`` from the experiment *directory*
    name. A directory that resolves to the filesystem root has an empty name;
    rather than ledger ``experiment_id=''`` into the permanent hash-chained
    ledger (the F1 friction), refuse and name the offending path."""


def derive_experiment_id(experiment_dir: Path | str) -> str:
    """Resolve ``experiment_dir`` and return its directory name — the one seam
    every ledgering stage derives ``experiment_id`` from [ux-friction AC-1].

    Resolving first makes the id path-independent: ``.``, a bare relative name,
    and the absolute path to the same directory all yield the identical id. A
    path that resolves to a nameless directory (the filesystem root) refuses with
    a typed :class:`ExperimentIdResolutionError` naming the offending path, so an
    empty id is never ledgered."""
    resolved = Path(experiment_dir).resolve()
    experiment_id = resolved.name
    if not experiment_id:
        raise ExperimentIdResolutionError(
            f"cannot derive an experiment_id: the resolved experiment directory "
            f"{resolved} has no name (a directory at the filesystem root has no "
            "name to identify the experiment). Place the experiment in a named "
            "directory."
        )
    return experiment_id
