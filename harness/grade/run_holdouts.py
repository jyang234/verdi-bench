"""Container entrypoint: execute a declared holdout network-less [refactor 05 §1].

The generic, harness-shipped counterpart to a bespoke benchmark grader — the
sibling of :mod:`harness.grade.run_plugin`. Runs INSIDE the shipped grader image
(which bundles the harness, refactor 03 §3) as that image's entrypoint,
``python -m harness.grade.run_holdouts``, launched by
``GradingContainer.build_grade_command`` in a ``--network none`` container: the
host mounts the declared holdout read-only at ``/holdouts``, mounts a copy of the
trial workspace at ``/workspace``, and injects the per-grade ``VERDI_FENCE_NONCE``
— exactly the shape that already drives ``run_plugin``. This entrypoint loads the
declared :class:`~harness.grade.holdouts.Holdout`, executes it against
``/workspace``, and prints the assertion list on stdout inside the
nonce-authenticated V2 holdout fence [F-H1] — never into the agent-writable
workspace, where in-run agent code could rewrite it.

Nonce discipline [F-H1 follow-up / deep-dive §2.4]. Unlike ``run_plugin`` — which
runs TRUSTED verdi graders and only shallow-drops the nonce from ``os.environ`` —
this entrypoint runs AGENT CODE (the holdout imports the trial's solution). The
genuine protection is therefore in :meth:`Holdout.execute`, which launches that
code in a subprocess with a scrubbed ``env=`` (``VERDI_FENCE_NONCE`` removed), so
the child's exec-time environment never held the nonce and agent code cannot read
it to forge a competing valid fence. The ``pop`` below is the same shallow
defense-in-depth ``run_plugin`` documents; the subprocess scrub is the guarantee.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .container import NONCE_ENV, holdout_fence
from .holdouts import HOLDOUT_SPEC_FILENAME, assertions_to_raw, load_declared_holdout

_HOLDOUTS_MOUNT = Path("/holdouts")
_WORKSPACE = Path("/workspace")


def main(argv: list[str]) -> int:
    # Capture the injected nonce and drop it from os.environ (shallow
    # defense-in-depth; the real scrub is the per-subprocess env= inside
    # Holdout.execute — deep-dive §2.4). The captured value stamps the fence.
    nonce = os.environ.pop(NONCE_ENV, None)
    holdout = load_declared_holdout(_HOLDOUTS_MOUNT)
    if holdout is None:
        # This generic entrypoint has nothing to execute (no declared kind, or no
        # spec mounted). Emit no fence and exit nonzero → the host reads the
        # channel absent and records a terminal container_failure. An opaque or
        # bespoke holdout must ship its own grader image, not this one.
        print(
            f"no declared holdout ({HOLDOUT_SPEC_FILENAME} with a 'kind') mounted at "
            f"{_HOLDOUTS_MOUNT}; a bespoke holdout needs a benchmark grader image",
            file=sys.stderr,
        )
        return 1
    assertions = holdout.execute(_WORKSPACE)
    begin, end = holdout_fence(nonce)
    print(f"{begin}\n{json.dumps(assertions_to_raw(assertions))}\n{end}", flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised in the grader image
    sys.exit(main(sys.argv))
