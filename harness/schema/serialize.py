"""``ExperimentSpec`` → ``experiment.yaml`` text [refactor 02 §2, write path].

The audit's single largest gap: the library can *validate* every spec
(:meth:`ExperimentSpec.from_dict`) but *author* none — there was no serializer,
so every "example spec" was a hand-maintained copy. :func:`spec_to_yaml` closes
that half: it emits through the **existing** pydantic model, so there is one
validation source and zero new rules (02 §2).

**Pre-lock writing ONLY — never rewrite a locked file.** The plan lock's
commitment is the sha256 of the experiment.yaml file's *raw bytes*
(``spec_sha256`` / the read-once-then-parse-and-hash discipline,
``harness/plan/lock.py:99-106``); ``assert_lock`` recomputes that hash on every
run/grade and refuses the moment the bytes drift (``lock.py:288-297``). A
canonicalize-on-save or re-serialize step over a locked spec would therefore
*invalidate its own lock*. This serializer exists to write experiment.yaml
*before* the lock is taken and for nothing else — there is deliberately no
"format" or "normalize" verb.
"""

from __future__ import annotations

import yaml

from .experiment import ExperimentSpec


def spec_to_yaml(spec: ExperimentSpec) -> str:
    """Serialize a validated :class:`ExperimentSpec` to ``experiment.yaml`` text.

    Round-trips: ``ExperimentSpec.from_yaml_text(spec_to_yaml(s))`` revalidates
    to a spec equal to ``s``. Emitted via ``model_dump(mode="json")`` so only
    JSON-native scalars reach the YAML dumper; ``parsed_rule`` is already
    ``exclude=True`` on the model (``experiment.py:314``) and so is never
    written — it is re-derived from ``decision_rule`` on the next load.

    Pre-lock only (see the module docstring): the output is meant to be written
    once, then hashed by the lock. Nothing may rewrite a spec after its lock.
    """
    data = spec.model_dump(mode="json")
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
