"""Authenticated encryption for Meta access tokens at rest (§51).

AES-256-GCM: the tag makes tampering detectable, so a modified ciphertext fails
to decrypt rather than yielding a corrupted token that would be sent to Meta.

The key comes from ENCRYPTION_KEY and is never stored in MongoDB. Ciphertext is
versioned so the scheme can be rotated without ambiguity about what a stored
value means.
"""

import base64
import hashlib
import os
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.errors import InstaFlowError

VERSION: Final = "v1"
NONCE_BYTES: Final = 12  # 96 bits, the size AES-GCM is specified for.


class EncryptionError(InstaFlowError):
    code = "encryption_error"


def derive_key(secret: str) -> bytes:
    """Accept either 64 hex characters or an arbitrary passphrase.

    A 32-byte hex key is used directly; anything else is hashed to 32 bytes so a
    short or oddly-sized ENCRYPTION_KEY still produces a valid AES-256 key rather
    than failing at the first encrypt.
    """
    if not secret:
        raise EncryptionError("ENCRYPTION_KEY is not configured.")
    try:
        raw = bytes.fromhex(secret)
        if len(raw) == 32:
            return raw
    except ValueError:
        pass
    return hashlib.sha256(secret.encode()).digest()


def encrypt(plaintext: str, secret: str) -> str:
    if plaintext is None:
        raise EncryptionError("Nothing to encrypt.")
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(derive_key(secret)).encrypt(nonce, plaintext.encode(), None)
    return f"{VERSION}:{base64.urlsafe_b64encode(nonce + ciphertext).decode()}"


def decrypt(token: str, secret: str) -> str:
    if not token:
        raise EncryptionError("Nothing to decrypt.")

    version, _, payload = token.partition(":")
    if version != VERSION or not payload:
        raise EncryptionError("Unrecognised ciphertext format.")

    try:
        blob = base64.urlsafe_b64decode(payload.encode())
        nonce, ciphertext = blob[:NONCE_BYTES], blob[NONCE_BYTES:]
        return AESGCM(derive_key(secret)).decrypt(nonce, ciphertext, None).decode()
    except (InvalidTag, ValueError, IndexError) as exc:
        # Wrong key, tampered ciphertext, or corrupt storage all land here.
        raise EncryptionError("Could not decrypt stored token.") from exc
