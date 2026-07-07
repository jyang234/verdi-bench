"""Regenerate the Phase-0 golden serialization fixtures [refactor 01 §1].

    uv run python tests/fixtures/data/regen_goldens.py

WHEN REGENERATION IS LEGITIMATE — read before running. These goldens ARE the
serialization contract: the committed bytes pin the ledger canonicalization,
the event envelope and every constructor's payload key set, the anchor-record
serialization, and the findings/dossier/card render output. A guard test
failing against them means the CODE drifted, and the fix is almost always to
fix the code, not to regenerate. Regenerating is legitimate ONLY when a
contract change was explicitly approved by a human with a migration story
(CLAUDE.md "Public seams are contracts"; refactor master plan §5 approval
register), or when the fixtures are being deliberately extended to cover new
additive events/fields. Never regenerate to make a red test green.

The script is deterministic: it pins instrument identity, uses a synthetic
clock, and seeds everything from the spec seed, so two runs on any machine and
any git HEAD produce byte-identical fixtures. After a legitimate regeneration,
``GOLDEN_HEAD_HASH`` in ``tests/fixtures/goldens.py`` must be updated BY HAND
to the printed value — the script deliberately refuses to update the pinned
constant itself, so a changed contract always requires a conscious edit that
shows up in review.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

_DATA = Path(__file__).resolve().parent
_ROOT = _DATA.parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.fixtures import goldens  # noqa: E402  (needs _ROOT on sys.path)

_OFFICIAL_FIXTURES = (
    "golden_findings.official.md",
    "golden_findings.official.dossier.html",
)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="verdi-golden-regen-") as td:
        scenario = goldens.build_golden_scenario(Path(td) / "exp")
        replay = Path(td) / "golden_constructors.ndjson"
        replayed = goldens.build_constructor_replay(replay)

        from harness.ledger.events import REGISTERED_EVENTS

        missing = REGISTERED_EVENTS - replayed
        if missing:
            print(f"REFUSING: constructor replay misses event type(s) {sorted(missing)}")
            print("extend build_constructor_replay before regenerating")
            return 1

        for name, src in {**scenario.artifacts, "golden_constructors.ndjson": replay}.items():
            shutil.copyfile(src, _DATA / name)
            print(f"wrote tests/fixtures/data/{name}")

        print(f"selfcheck passed: {scenario.selfcheck_passed}")
        if scenario.official_refusal is not None:
            # The fence refused the official render; the exploratory fixtures
            # were still written. Stale official fixtures would be dangling
            # (nothing regenerates them), so drop them loudly [refactor 01 §1].
            print(f"OFFICIAL RENDER REFUSED: {scenario.official_refusal}")
            for name in _OFFICIAL_FIXTURES:
                stale = _DATA / name
                if stale.exists():
                    stale.unlink()
                    print(f"removed stale tests/fixtures/data/{name}")

        print(f"golden ledger head hash: {scenario.head_hash}")
        if scenario.head_hash != goldens.GOLDEN_HEAD_HASH:
            print(
                "HEAD HASH CHANGED — the serialization contract moved. Update "
                "GOLDEN_HEAD_HASH in tests/fixtures/goldens.py to the value above "
                "(a deliberate, reviewable edit), then rerun this script to "
                "confirm it reproduces byte-identically."
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
