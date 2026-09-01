"""Token encryption at rest (§51)."""

import pytest

from app.services.meta.tokens import needs_refresh, token_fields
from app.services.security.encryption import EncryptionError, decrypt, derive_key, encrypt

KEY = "a" * 64  # 32 bytes of hex
OTHER_KEY = "b" * 64
TOKEN = "IGQVJYb2hkTWpuYVdrZAmVlaGF0Za1234567890abcdef"


def test_round_trip():
    assert decrypt(encrypt(TOKEN, KEY), KEY) == TOKEN


def test_ciphertext_does_not_contain_the_plaintext():
    assert TOKEN not in encrypt(TOKEN, KEY)


def test_same_plaintext_encrypts_differently_each_time():
    """A fresh nonce per encryption; identical ciphertexts would leak equality."""
    assert encrypt(TOKEN, KEY) != encrypt(TOKEN, KEY)


def test_the_wrong_key_cannot_decrypt():
    with pytest.raises(EncryptionError):
        decrypt(encrypt(TOKEN, KEY), OTHER_KEY)


def test_tampered_ciphertext_is_rejected():
    """AES-GCM's tag makes this detectable, so a corrupted token is never sent."""
    blob = encrypt(TOKEN, KEY)
    tampered = blob[:-6] + ("A" if blob[-6] != "A" else "B") + blob[-5:]
    with pytest.raises(EncryptionError):
        decrypt(tampered, KEY)


@pytest.mark.parametrize("bad", ["", "not-encrypted", "v2:abc", "v1:", "v1:!!!not-base64"])
def test_malformed_ciphertext_raises_cleanly(bad):
    with pytest.raises(EncryptionError):
        decrypt(bad, KEY)


def test_versioned_prefix_is_present():
    assert encrypt(TOKEN, KEY).startswith("v1:")


def test_a_passphrase_key_is_accepted():
    """A non-hex ENCRYPTION_KEY must still produce a valid AES-256 key."""
    assert decrypt(encrypt(TOKEN, "short-passphrase"), "short-passphrase") == TOKEN


def test_hex_key_is_used_directly():
    assert derive_key(KEY) == bytes.fromhex(KEY)
    assert len(derive_key("anything else")) == 32


def test_an_empty_key_is_rejected():
    with pytest.raises(EncryptionError):
        encrypt(TOKEN, "")


def test_token_fields_store_only_ciphertext():
    fields = token_fields(TOKEN, None, KEY)
    assert "access_token" not in fields
    assert TOKEN not in str(fields)
    assert decrypt(fields["access_token_encrypted"], KEY) == TOKEN


def test_a_freshly_issued_token_is_not_refreshed():
    """Meta refuses to refresh a token under 24 hours old."""
    assert needs_refresh(token_fields(TOKEN, None, KEY)) is False
