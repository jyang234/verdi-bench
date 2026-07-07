"""Curation-approval attestation [EVAL-8 §M4, D-P4-3].

A curation approval is **signed** with the approver's Ed25519 key so admission can
verify three things: the signature is valid over the exact
``{candidate_id, task_sha, approver}`` it approves; the signer is an *authorized
curator* (the public key is in a pinned keyring); and the approver is not the task's
miner. Ed25519 signatures are deterministic (RFC 8032), so signing introduces no
unseeded randomness — key *generation* is an out-of-band operational step, never on
the pipeline path.

Accepted limitation [PRA-L10]: the signed payload has no nonce, expiry, or
ledger binding, so an approval is *content-bound* (it verifies only for its exact
``{candidate_id, task_sha, approver}``) but not *revocable* — a once-valid
approval can be re-ledgered on a fresh ledger forever, and there is no
representation of a withdrawn approval. Because admission also requires a clean
flake baseline and refuses an already-admitted candidate, the practical blast
radius is small; a full revocation model (approval epochs / a revocation list) is
deferred to the TRUSTED tier and is a deliberate v1 scope choice, not an
oversight.
"""

from __future__ import annotations

import json
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from ..errors import VerdiRefusal


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


class KeyringFormatError(VerdiRefusal, ValueError):
    """The keyring is in the pre-Phase-7 list format [D-P7-3]."""


def load_keyring(path) -> dict[str, str]:
    """Authorized curators as ``{approver_id: public_key_hex}`` [D-P7-3].

    Binding the approver identity to a key (not just a set of authorized keys) is
    what closes CO-7: admission verifies each approval against the *named
    approver's own* key, so an authorized-key holder cannot relabel the approver
    to launder a self-approval.

    A legacy JSON **list** is refused with a loud migration error — the keyring is
    local operator state, not a hash-chained artifact, so there is no
    compatibility shim; it is re-issued in the new format.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        raise KeyringFormatError(
            f"keyring {path} is a JSON list (pre-Phase-7 format); it must now be a "
            'JSON object mapping approver id -> public-key hex, e.g. '
            '{"alice": "<hex>"}. This binds approver identity to a key so a '
            "relabeled self-approval is refused [D-P7-3]. Re-issue the keyring."
        )
    if not isinstance(data, dict):
        raise KeyringFormatError(
            f"keyring {path} must be a JSON object mapping approver id -> "
            "public-key hex [D-P7-3]"
        )
    return {str(k): str(v) for k, v in data.items()}
