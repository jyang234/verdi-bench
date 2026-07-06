"""Fixture construction for the one-event stage entrypoints [refactor 06 §6].

The ``register_entrypoint`` calls stay in their owning stage modules (the
property test pins their names + import-time firing), but the inline fixture
*construction* they used to carry — copy-pasted example-spec dicts (several
naming retired model ids) and a YAML-rewriting helper — moves here so it stops
duplicating the one canonical starter template.

This is a harness-internal home, NOT the SDK: the sdk-is-a-leaf contract forbids
any harness module from importing ``harness.sdk``, so — exactly as
``harness.cli``'s ``init`` verb does — the starter spec is read from the shared
template DATA FILE (a ``Path().read_text``), never by importing the sdk package.
Nothing here writes the ledger; the entrypoints themselves append their single
event, and these helpers only prepare the fixture the sweep runs against.
"""

from __future__ import annotations

from pathlib import Path

# The one canonical starter template, read as data (never an sdk import) — the
# same file `bench init` and the test builders derive from [refactor 02 §2].
_TEMPLATES_DIR = Path(__file__).resolve().parent / "sdk" / "templates"
_STARTER_EXPERIMENT = _TEMPLATES_DIR / "starter-experiment.yaml"


def starter_experiment_spec():
    """Parse the canonical starter ``experiment.yaml`` into an ``ExperimentSpec``.

    The single valid example spec (two arms on registered platforms, a fully
    date-versioned judge model, a real cost ceiling), replacing the retired-model
    spec dict the process entrypoint embedded. Consumers that only need *a* valid
    spec (e.g. the process one-event fixture) use this rather than hand-rolling
    one that drifts from the template."""
    from .schema.experiment import ExperimentSpec

    return ExperimentSpec.from_yaml_text(
        _STARTER_EXPERIMENT.read_text(encoding="utf-8"), source=str(_STARTER_EXPERIMENT)
    )


def make_experiment_underpowered(ctx_dir: str) -> None:
    """Fixture prep for the acknowledged-underpowered lock entrypoint [PL-14].

    Drop the hypothesized effect below any reasonable MDE so the design is
    underpowered. Not a ledger write — just a YAML rewrite of the fixture the
    test harness already wrote, so the sweep still measures only the lock event
    the entrypoint fn appends."""
    import yaml

    p = Path(ctx_dir) / "experiment.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    data["hypothesized_effect"] = 0.001
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
