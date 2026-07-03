"""Curation-approval attestation [EVAL-8 §M4, D-P4-3].

A curation approval is **signed** with the approver's Ed25519 key so admission can
verify three things: the signature is valid over the exact
``{candidate_id, task_sha, approver}`` it approves; the signer is an *authorized
curator* (the public key is in a pinned keyring); and the approver is not the task's
miner. Ed25519 signatures are deterministic (RFC 8032), so signing introduces no
unseeded randomness — key *generation* is an out-of-band operational step, never on
the pipeline path.
"""

from __future__ import annotations

import json
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def canonical_payload(candidate_id: str, task_sha: str, approver: str) -> bytes:
    """The exact bytes an approval signs — deterministic, key-sorted JSON. The
    approver identity is *in* the payload, so a signature binds the identity to
    the key: a signer cannot claim an approver they did not sign as."""
    return json.dumps(
        {"approver": approver, "candidate_id": candidate_id, "task_sha": task_sha},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sign_approval(
    private_key_hex: str, *, candidate_id: str, task_sha: str, approver: str
) -> tuple[str, str]:
    """Sign an approval; return ``(signature_hex, signer_public_key_hex)``."""
    sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    sig = sk.sign(canonical_payload(candidate_id, task_sha, approver))
    return sig.hex(), sk.public_key().public_bytes_raw().hex()


def verify_approval(
    signature_hex: str,
    public_key_hex: str,
    *,
    candidate_id: str,
    task_sha: str,
    approver: str,
) -> bool:
    """True iff ``signature_hex`` is a valid signature by ``public_key_hex`` over
    the canonical approval payload. Any malformed input verifies False (never
    raises) — a bad signature is a fail-closed refusal, not a crash."""
    try:
        pk = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        pk.verify(bytes.fromhex(signature_hex), canonical_payload(candidate_id, task_sha, approver))
        return True
    except (InvalidSignature, ValueError):
        return False


def load_keyring(path) -> set[str]:
    """The set of authorized curator public keys (hex) from a JSON list file — the
    trust root admission verifies a signer against [D-P4-3]."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"keyring {path} must be a JSON list of public-key hex strings")
    return {str(k) for k in data}
