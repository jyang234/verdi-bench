"""Container entrypoint: run grader plugins network-less [PRA-M6].

Runs INSIDE the grader image (which bundles the harness), launched by
``GradingContainer.build_plugin_command`` as
``python -m harness.grade.run_plugin <plugin_id>...`` in a ``--network none``
container. Reads the ``GradeTask`` from the read-only ``/verdi/task.json`` mount,
runs each plugin against ``/workspace``, and prints the assertion list on
stdout inside the nonce-authenticated V2 plugin fence [F-H1 A.4] — never into
the agent-writable workspace, where in-run agent code could rewrite it. It has
no network and no host access — the container flags enforce that; this module
just does the work.

Nonce discipline [F-H1 follow-up]: the host injects a per-grade
``VERDI_FENCE_NONCE`` that must be stamped into the fence marker. This entrypoint
reads it and immediately ``del``etes it from the environment BEFORE running any
plugin, so plugin code (which may import agent-controlled workspace content)
cannot read the nonce and forge a competing fence. The stronger form of this
discipline — running agent-executing code in a separate subprocess whose
environment is scrubbed — is the grader-image contract documented in
docs/deep-dive.md §2.4; this reference emitter models the env-scrub half.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .container import NONCE_ENV, plugin_fence
from .plugins import get_plugin
from .types import GradeTask

_TASK_MOUNT = Path("/verdi/task.json")
_WORKSPACE = Path("/workspace")


def main(argv: list[str]) -> int:
    plugin_ids = argv[1:]
    # Read and scrub the nonce before any plugin (hence any agent-controlled
    # code) runs, so it cannot be read back out of the environment.
    nonce = os.environ.pop(NONCE_ENV, None)
    data = json.loads(_TASK_MOUNT.read_text(encoding="utf-8")) if _TASK_MOUNT.exists() else {}
    task = GradeTask(
        id=data.get("id", "t"),
        task_sha=data.get("task_sha", ""),
        holdouts_dir=data.get("holdouts_dir", ""),
        plugin_ids=plugin_ids,
        fake_plugin_output=data.get("fake_plugin_output") or {},
    )
    out: list = []
    for pid in plugin_ids:
        out.extend(a.model_dump(mode="json") for a in get_plugin(pid).grade(_WORKSPACE, task))
    begin, end = plugin_fence(nonce)
    print(f"{begin}\n{json.dumps(out)}\n{end}", flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised in the grader image
    sys.exit(main(sys.argv))
