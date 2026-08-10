"""Source credential storage.

The single place credentials are written and read. Everything else in the
application deals in *references* to a credential, never the value.

The read path is deliberately narrow: :func:`load_credentials` is called by the
connector factory and by nothing else. There is no route, no schema and no
serialiser anywhere that can emit a decrypted credential, which is what makes
"credentials never reach the frontend" a structural property rather than a rule
each new endpoint has to remember.
"""

from __future__ import annotations

import json
import uuid
from functools import lru_cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.encryption import (
    CredentialCipher,
    DecryptionError,
    EncryptionKeyError,
    decode_key,
)
from app.core.logging import get_logger
from app.models.data_source import DataSource, SourceCredential

logger = get_logger(__name__)


def _parse_previous_keys(entries: list[str]) -> dict[int, bytes]:
    """Parse "version:base64" pairs into a decrypt-only keyring."""
    keys: dict[int, bytes] = {}
    for entry in entries:
        version, _, material = entry.partition(":")
        if not material:
            raise EncryptionKeyError(
                "CREDENTIAL_ENCRYPTION_PREVIOUS_KEYS entries must be 'version:base64'"
            )
        try:
            parsed_version = int(version)
        except ValueError as exc:
            raise EncryptionKeyError(
                f"CREDENTIAL_ENCRYPTION_PREVIOUS_KEYS has a non-numeric version: {version!r}"
            ) from exc
        keys[parsed_version] = decode_key(
            material, label=f"CREDENTIAL_ENCRYPTION_PREVIOUS_KEYS[{parsed_version}]"
        )
    return keys


def build_cipher(settings: Settings) -> CredentialCipher:
    """Construct the cipher from settings, validating all key material."""
    return CredentialCipher(
        active_key=decode_key(
            settings.credential_encryption_key, label="CREDENTIAL_ENCRYPTION_KEY"
        ),
        active_version=settings.credential_encryption_key_version,
        previous_keys=_parse_previous_keys(settings.credential_encryption_previous_keys),
    )


@lru_cache(maxsize=1)
def get_cipher() -> CredentialCipher:
    """Return the process-wide cipher."""
    return build_cipher(get_settings())


def validate_encryption_at_startup(settings: Settings | None = None) -> None:
    """Fail fast if credential encryption is not usable.

    Called from the application lifespan. A process that cannot decrypt
    credentials should refuse to start: the alternative is discovering the
    problem one failed sync at a time, in production, with no obvious cause.
    """
    settings = settings or get_settings()
    cipher = build_cipher(settings)
    cipher.self_test()

    logger.info(
        "encryption.ready",
        algorithm="AES-256-GCM",
        active_key_version=cipher.active_version,
        previous_key_versions=len(settings.credential_encryption_previous_keys),
        # Never the key, and never anything derived from it.
    )


async def store_credentials(
    db: AsyncSession,
    *,
    data_source: DataSource,
    payload: dict[str, Any],
) -> SourceCredential:
    """Encrypt and persist the credentials for a data source.

    Replaces any existing credential for the source, so rotating a password is
    an update rather than an accumulation of old secrets.
    """
    cipher = get_cipher()
    aad = cipher.build_aad(
        organization_id=data_source.organization_id, data_source_id=data_source.id
    )
    # separators + sort_keys so the plaintext is deterministic; not required for
    # correctness, but it keeps ciphertext length from varying with dict order.
    plaintext = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    encrypted = cipher.encrypt(plaintext, aad=aad)

    existing = await db.scalar(
        select(SourceCredential).where(SourceCredential.data_source_id == data_source.id)
    )
    if existing is None:
        existing = SourceCredential(data_source_id=data_source.id)
        db.add(existing)

    existing.ciphertext = encrypted.ciphertext
    existing.nonce = encrypted.nonce
    existing.key_version = encrypted.key_version
    existing.algorithm = encrypted.algorithm
    await db.flush()

    logger.info(
        "credentials.stored",
        data_source_id=str(data_source.id),
        organization_id=str(data_source.organization_id),
        key_version=encrypted.key_version,
        # The payload is not logged, not even its keys.
    )
    return existing


async def load_credentials(db: AsyncSession, *, data_source: DataSource) -> dict[str, Any]:
    """Decrypt the credentials for a data source.

    The only function in the codebase that returns credential plaintext.
    Callers must pass it straight to a connector and never place it in a
    response model, a log field or an exception message.
    """
    record = await db.scalar(
        select(SourceCredential).where(SourceCredential.data_source_id == data_source.id)
    )
    if record is None:
        raise DecryptionError("No credentials are stored for this data source")

    cipher = get_cipher()
    aad = cipher.build_aad(
        organization_id=data_source.organization_id, data_source_id=data_source.id
    )
    plaintext = cipher.decrypt(record.to_encrypted_value(), aad=aad)

    decoded: dict[str, Any] = json.loads(plaintext)
    return decoded


async def delete_credentials(db: AsyncSession, *, data_source_id: uuid.UUID) -> None:
    """Remove stored credentials.

    Also happens automatically when a data source is deleted, through the
    foreign key's ON DELETE CASCADE.
    """
    record = await db.scalar(
        select(SourceCredential).where(SourceCredential.data_source_id == data_source_id)
    )
    if record is not None:
        await db.delete(record)
