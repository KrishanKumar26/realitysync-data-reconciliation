"""Authenticated encryption for credentials at rest.

Source credentials are the most dangerous thing RealitySync stores: they open a
customer's production database. They are encrypted with **AES-256-GCM**, an
authenticated cipher — the tag detects tampering, so a modified ciphertext
fails to decrypt rather than yielding attacker-chosen plaintext.

Two properties are worth spelling out.

**Associated data binds a ciphertext to its row.** Every credential is
encrypted with AAD of ``organization_id:data_source_id``. A blob copied from
one source row to another — or from one tenant to another — will not decrypt.
Confidentiality alone would not prevent that: an attacker with write access to
the credentials table could otherwise point their own data source at another
tenant's stored credential and have the connector use it on their behalf.

**Rotation without downtime.** ``CREDENTIAL_ENCRYPTION_KEY`` encrypts new
values; ``CREDENTIAL_ENCRYPTION_PREVIOUS_KEYS`` decrypt old ones. Each stored
record carries the key version that produced it, so a rotation is: add the old
key to the previous list, set the new key active, re-encrypt in the background.

Nothing here is hand-rolled. Key derivation, nonce generation and the cipher
all come from ``cryptography``.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

#: AES-256 requires a 32-byte key.
KEY_BYTES = 32

#: 96 bits is the GCM-recommended nonce size — the only length for which the
#: standard's security analysis holds without extra derivation.
NONCE_BYTES = 12

#: Written into each record so a future algorithm change is distinguishable
#: rather than silently incompatible.
ALGORITHM = "AES-256-GCM"


class EncryptionError(Exception):
    """Base class for credential encryption failures."""


class EncryptionKeyError(EncryptionError):
    """The configured key material is missing or malformed.

    Raised at startup, never mid-request: a process that cannot decrypt
    credentials must refuse to start rather than fail one sync at a time.
    """


class DecryptionError(EncryptionError):
    """A ciphertext could not be decrypted.

    Deliberately does not distinguish "wrong key" from "tampered" from
    "corrupted". All three mean the value is unusable, and reporting which
    would help an attacker probe the store.
    """


@dataclass(frozen=True, slots=True)
class EncryptedValue:
    """A ciphertext and everything needed to decrypt it later.

    The nonce is stored alongside the ciphertext because it is not secret — it
    must merely never repeat under the same key, which random generation
    guarantees with overwhelming probability at 96 bits.
    """

    ciphertext: bytes
    nonce: bytes
    key_version: int
    algorithm: str = ALGORITHM


def decode_key(material: str, *, label: str) -> bytes:
    """Decode and validate a base64 key.

    Accepts standard and URL-safe base64, with or without padding, because
    secret managers differ in what they emit and a key that fails to load in
    production is an outage.
    """
    cleaned = material.strip()
    if not cleaned:
        raise EncryptionKeyError(f"{label} is empty")

    padding = "=" * (-len(cleaned) % 4)
    try:
        raw = base64.urlsafe_b64decode(cleaned.replace("+", "-").replace("/", "_") + padding)
    except Exception as exc:
        raise EncryptionKeyError(f"{label} is not valid base64") from exc

    if len(raw) != KEY_BYTES:
        raise EncryptionKeyError(
            f"{label} must decode to exactly {KEY_BYTES} bytes for AES-256; got {len(raw)}"
        )
    return raw


def generate_key() -> str:
    """Return a new base64 key, for operators to put in their environment."""
    return base64.urlsafe_b64encode(os.urandom(KEY_BYTES)).decode("ascii")


class CredentialCipher:
    """Encrypts and decrypts credential payloads.

    Built once at startup from settings. Holds key material in memory only —
    it is never logged, never serialised, and has no repr that could leak it.
    """

    def __init__(
        self,
        *,
        active_key: bytes,
        active_version: int = 1,
        previous_keys: dict[int, bytes] | None = None,
    ) -> None:
        if len(active_key) != KEY_BYTES:
            raise EncryptionKeyError(f"Active key must be {KEY_BYTES} bytes")

        self._active_version = active_version
        self._keys: dict[int, bytes] = {active_version: active_key}
        if previous_keys:
            for version, key in previous_keys.items():
                if version == active_version:
                    continue
                if len(key) != KEY_BYTES:
                    raise EncryptionKeyError(f"Key version {version} must be {KEY_BYTES} bytes")
                self._keys[version] = key

    def __repr__(self) -> str:
        # No key material, no key count that would hint at the configuration.
        return "<CredentialCipher>"

    @property
    def active_version(self) -> int:
        return self._active_version

    @staticmethod
    def build_aad(*, organization_id: object, data_source_id: object) -> bytes:
        """Associated data binding a ciphertext to one row of one tenant."""
        return f"rs:v1:org={organization_id}:source={data_source_id}".encode()

    def encrypt(self, plaintext: bytes, *, aad: bytes) -> EncryptedValue:
        """Encrypt with the active key."""
        nonce = os.urandom(NONCE_BYTES)
        cipher = AESGCM(self._keys[self._active_version])
        ciphertext = cipher.encrypt(nonce, plaintext, aad)
        return EncryptedValue(
            ciphertext=ciphertext,
            nonce=nonce,
            key_version=self._active_version,
            algorithm=ALGORITHM,
        )

    def decrypt(self, value: EncryptedValue, *, aad: bytes) -> bytes:
        """Decrypt, verifying both the tag and the associated data."""
        if value.algorithm != ALGORITHM:
            raise DecryptionError("Unsupported encryption algorithm")

        key = self._keys.get(value.key_version)
        if key is None:
            raise DecryptionError("No key available for this record")

        try:
            return AESGCM(key).decrypt(value.nonce, value.ciphertext, aad)
        except InvalidTag as exc:
            # Tampered ciphertext, wrong key, or a blob moved to another row.
            raise DecryptionError("Credential could not be decrypted") from exc
        except Exception as exc:  # pragma: no cover - defensive
            raise DecryptionError("Credential could not be decrypted") from exc

    def self_test(self) -> None:
        """Round-trip a probe value, and confirm AAD is actually enforced.

        Run at startup. Catches a key that decodes but does not work, and would
        catch a future refactor that quietly stopped passing associated data —
        which would silently remove the row-binding protection while every test
        that only checks round-tripping still passed.
        """
        aad = self.build_aad(organization_id="startup", data_source_id="self-test")
        probe = b"realitysync-encryption-self-test"

        encrypted = self.encrypt(probe, aad=aad)
        if self.decrypt(encrypted, aad=aad) != probe:
            raise EncryptionKeyError("Encryption self-test failed to round-trip")

        other = self.build_aad(organization_id="startup", data_source_id="different")
        try:
            self.decrypt(encrypted, aad=other)
        except DecryptionError:
            return
        raise EncryptionKeyError(
            "Encryption self-test failed: associated data is not being enforced"
        )
