"""Credential encryption."""

from __future__ import annotations

import base64
import os

import pytest

from app.core.config import Settings
from app.core.encryption import (
    ALGORITHM,
    CredentialCipher,
    DecryptionError,
    EncryptionKeyError,
    decode_key,
    generate_key,
)
from app.services.credentials import build_cipher, validate_encryption_at_startup


def make_cipher(**kwargs: object) -> CredentialCipher:
    return CredentialCipher(active_key=os.urandom(32), **kwargs)  # type: ignore[arg-type]


AAD = CredentialCipher.build_aad(organization_id="org-1", data_source_id="src-1")


# --- Round trip ------------------------------------------------------------


def test_encrypt_then_decrypt_returns_the_original() -> None:
    cipher = make_cipher()
    secret = b'{"password":"hunter2-and-then-some"}'

    encrypted = cipher.encrypt(secret, aad=AAD)

    assert encrypted.algorithm == ALGORITHM
    assert cipher.decrypt(encrypted, aad=AAD) == secret


def test_ciphertext_does_not_contain_the_plaintext() -> None:
    cipher = make_cipher()
    secret = b"correct-horse-battery-staple"

    encrypted = cipher.encrypt(secret, aad=AAD)

    assert secret not in encrypted.ciphertext


def test_same_plaintext_encrypts_differently_each_time() -> None:
    """A fresh nonce per encryption.

    Identical ciphertexts would leak that two sources share a password.
    """
    cipher = make_cipher()

    first = cipher.encrypt(b"same-secret", aad=AAD)
    second = cipher.encrypt(b"same-secret", aad=AAD)

    assert first.ciphertext != second.ciphertext
    assert first.nonce != second.nonce


# --- Authentication --------------------------------------------------------


def test_tampered_ciphertext_is_rejected() -> None:
    """GCM's tag is what makes this authenticated encryption."""
    cipher = make_cipher()
    encrypted = cipher.encrypt(b"a-real-secret", aad=AAD)

    corrupted = bytearray(encrypted.ciphertext)
    corrupted[0] ^= 0x01
    tampered = type(encrypted)(
        ciphertext=bytes(corrupted),
        nonce=encrypted.nonce,
        key_version=encrypted.key_version,
    )

    with pytest.raises(DecryptionError):
        cipher.decrypt(tampered, aad=AAD)


def test_a_credential_cannot_be_moved_to_another_source() -> None:
    """The associated data binds a ciphertext to one row of one tenant.

    Without this, someone with write access to the credentials table could
    point their own data source at another tenant's stored credential and have
    the connector use it on their behalf. Confidentiality alone would not stop
    that — the blob never has to be read to be misused.
    """
    cipher = make_cipher()
    encrypted = cipher.encrypt(b"tenant-a-password", aad=AAD)

    other_source = CredentialCipher.build_aad(organization_id="org-1", data_source_id="src-2")
    other_tenant = CredentialCipher.build_aad(organization_id="org-2", data_source_id="src-1")

    with pytest.raises(DecryptionError):
        cipher.decrypt(encrypted, aad=other_source)
    with pytest.raises(DecryptionError):
        cipher.decrypt(encrypted, aad=other_tenant)


def test_a_different_key_cannot_decrypt() -> None:
    encrypted = make_cipher().encrypt(b"secret", aad=AAD)

    with pytest.raises(DecryptionError):
        make_cipher().decrypt(encrypted, aad=AAD)


# --- Key handling ----------------------------------------------------------


def test_generated_keys_are_valid() -> None:
    assert len(decode_key(generate_key(), label="test")) == 32


@pytest.mark.parametrize(
    "material",
    [
        "",
        "not-base64!!!",
        base64.urlsafe_b64encode(os.urandom(16)).decode(),  # too short for AES-256
        base64.urlsafe_b64encode(os.urandom(64)).decode(),  # too long
    ],
)
def test_invalid_key_material_is_rejected(material: str) -> None:
    with pytest.raises(EncryptionKeyError):
        decode_key(material, label="TEST_KEY")


def test_key_length_error_names_the_setting() -> None:
    """A misconfigured key must say which one, or debugging is guesswork."""
    with pytest.raises(EncryptionKeyError, match="CREDENTIAL_ENCRYPTION_KEY"):
        decode_key(
            base64.urlsafe_b64encode(b"too-short").decode(),
            label="CREDENTIAL_ENCRYPTION_KEY",
        )


def test_rotation_keeps_old_records_readable() -> None:
    """The point of key versioning: rotate without a data migration."""
    old_key, new_key = os.urandom(32), os.urandom(32)

    old_cipher = CredentialCipher(active_key=old_key, active_version=1)
    encrypted = old_cipher.encrypt(b"stored-under-v1", aad=AAD)

    rotated = CredentialCipher(active_key=new_key, active_version=2, previous_keys={1: old_key})

    assert rotated.decrypt(encrypted, aad=AAD) == b"stored-under-v1"
    # New values use the new key.
    assert rotated.encrypt(b"fresh", aad=AAD).key_version == 2


def test_a_record_with_an_unavailable_key_version_fails_cleanly() -> None:
    encrypted = CredentialCipher(active_key=os.urandom(32), active_version=7).encrypt(
        b"secret", aad=AAD
    )

    with pytest.raises(DecryptionError):
        CredentialCipher(active_key=os.urandom(32), active_version=1).decrypt(encrypted, aad=AAD)


# --- Startup validation ----------------------------------------------------


def test_startup_validation_passes_with_a_valid_key() -> None:
    validate_encryption_at_startup(Settings(credential_encryption_key=generate_key()))


def test_startup_validation_rejects_a_malformed_key() -> None:
    """A process that cannot decrypt credentials must not start."""
    with pytest.raises(EncryptionKeyError):
        validate_encryption_at_startup(Settings(credential_encryption_key="nonsense"))


def test_previous_keys_must_be_version_prefixed() -> None:
    with pytest.raises(EncryptionKeyError, match="version:base64"):
        build_cipher(
            Settings(
                credential_encryption_key=generate_key(),
                credential_encryption_previous_keys=[generate_key()],
            )
        )


def test_production_refuses_the_published_development_key() -> None:
    """The default key is in the source code; production must not use it."""
    with pytest.raises(ValueError, match="CREDENTIAL_ENCRYPTION_KEY"):
        Settings(
            environment="production",
            secret_key="a-real-production-secret",
            cookie_secure=True,
            cors_origins=["https://app.example.com"],
            # credential_encryption_key left at its default
        )


def test_cipher_repr_reveals_nothing() -> None:
    """Reprs reach tracebacks and log lines."""
    assert repr(make_cipher()) == "<CredentialCipher>"


def test_self_test_detects_associated_data_not_being_enforced() -> None:
    """Guards against a refactor that stops passing AAD.

    Round-trip tests would all still pass in that case, while the row-binding
    protection was silently gone.
    """

    class BrokenCipher(CredentialCipher):
        def decrypt(self, value: object, *, aad: bytes) -> bytes:  # type: ignore[override]
            # Simulates ignoring the associated data.
            return super().decrypt(
                value, aad=self.build_aad(organization_id="startup", data_source_id="self-test")
            )  # type: ignore[arg-type]

    with pytest.raises(EncryptionKeyError, match="associated data"):
        BrokenCipher(active_key=os.urandom(32)).self_test()
