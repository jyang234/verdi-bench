"""Shared ledger-tamper vectors [refactor 01 §2].

The adversarial toolkit the suites use to prove the tripwires fire: rewrite a
committed event line while keeping it canonically encoded (invisible to
everything except hash verification), substitute raw bytes in place, or forge
a lock line's recorded spec sha (the PL-6 attack). Modeled on the shakedown
tripwires' vectors so tests and scripts share one toolkit once the scripts
convert to the SDK (a later phase). Test-utils only — never the public SDK.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness.ledger.chain import canonical_line


def reencode_line(ledger: Path, index: int, mutate) -> None:
    """Apply ``mutate(event_dict)`` to ledger line ``index`` and rewrite it in
    the chain's own canonical encoding, so the tampered line stays well-formed
    JSON and only chain verification can catch the rewrite."""
    ledger = Path(ledger)
    lines = ledger.read_text(encoding="utf-8").splitlines()
    obj = json.loads(lines[index])
    mutate(obj)
    lines[index] = canonical_line(obj)
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")


def flip_bytes(ledger: Path, index: int, old: str, new: str) -> None:
    """Substitute raw bytes on ledger line ``index`` without re-encoding — the
    byte-level rewrite vector. Refuses loudly when ``old`` is absent so a
    drifted fixture cannot silently tamper nothing."""
    ledger = Path(ledger)
    lines = ledger.read_text(encoding="utf-8").splitlines()
    if old not in lines[index]:
        raise AssertionError(
            f"tamper target {old!r} not found on ledger line {index}"
        )
    lines[index] = lines[index].replace(old, new, 1)
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")


def forge_lock_sha(spec_path: Path, ledger: Path) -> str:
    """The PL-6 attack: mutate ``experiment.yaml`` *and* rewrite the genesis
    lock line's ``spec_sha256`` to match, canonically re-encoded — the naive
    sha-equality check passes; only hash-chain verification refuses. Returns
    the forged sha for the caller's own assertions."""
    from harness.plan.lock import spec_sha256

    spec_path = Path(spec_path)
    ledger = Path(ledger)
    spec_path.write_text(spec_path.read_text() + "\n# tampered\n")
    forged = spec_sha256(spec_path)
    lines = ledger.read_text(encoding="utf-8").splitlines()
    lock_obj = json.loads(lines[0])
    assert lock_obj["event"] == "experiment_locked"
    lock_obj["spec_sha256"] = forged
    lines[0] = canonical_line(lock_obj)
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return forged
