"""Password hashing and token generation.

Two different problems that are easy to conflate:

* A **password** is low-entropy and attacker-guessable, so it needs a hash that
  is deliberately expensive — Argon2id, memory-hard, tuned in seconds-per-guess.
* A **session token** is 256 bits of CSPRNG output, so there is nothing to
  guess. It needs a *fast* hash (SHA-256), because every authenticated request
  looks one up, and an expensive hash there would be a self-inflicted denial of
  service with no security benefit.

Using Argon2 for session tokens is a common and costly mistake. Using SHA-256
for passwords is a catastrophic one. They are not interchangeable.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings, get_settings

#: Bytes of entropy per generated token. 32 bytes = 256 bits, well beyond
#: brute-force reach, and urlsafe-base64 encodes to 43 characters.
_TOKEN_BYTES = 32

#: Argon2id rejects excessively long inputs late; bounding early keeps a
#: multi-megabyte "password" from becoming a memory amplification vector.
_MAX_PASSWORD_BYTES = 1024


def _build_hasher(settings: Settings) -> PasswordHasher:
    """Argon2id hasher configured from settings.

    Defaults follow the OWASP Password Storage Cheat Sheet (19 MiB, t=2, p=1).
    Tests override them downward: correctness does not depend on the cost, and
    running the real parameters on every fixture would make the suite crawl.
    """
    return PasswordHasher(
        time_cost=settings.argon2_time_cost,
        memory_cost=settings.argon2_memory_cost_kib,
        parallelism=settings.argon2_parallelism,
        hash_len=32,
        salt_len=16,
    )


_hasher: PasswordHasher | None = None
_hasher_signature: tuple[int, int, int] | None = None


def get_password_hasher(settings: Settings | None = None) -> PasswordHasher:
    """Return the process-wide hasher, rebuilding it if the cost changed."""
    global _hasher, _hasher_signature
    settings = settings or get_settings()
    signature = (
        settings.argon2_time_cost,
        settings.argon2_memory_cost_kib,
        settings.argon2_parallelism,
    )
    if _hasher is None or _hasher_signature != signature:
        _hasher = _build_hasher(settings)
        _hasher_signature = signature
    return _hasher


def hash_password(password: str, settings: Settings | None = None) -> str:
    """Return an Argon2id PHC string for `password`."""
    if len(password.encode("utf-8")) > _MAX_PASSWORD_BYTES:
        raise ValueError("Password exceeds the maximum supported length")
    return get_password_hasher(settings).hash(password)


def verify_password(password: str, password_hash: str, settings: Settings | None = None) -> bool:
    """Check `password` against `password_hash`, returning False on any mismatch.

    Never raises for a wrong password, an unparseable hash or a corrupted
    record — all of those mean "not authenticated", and letting them surface as
    different exceptions would leak which case occurred.
    """
    if len(password.encode("utf-8")) > _MAX_PASSWORD_BYTES:
        return False
    try:
        return get_password_hasher(settings).verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str, settings: Settings | None = None) -> bool:
    """True when `password_hash` was made with weaker parameters than current.

    Lets the cost be raised over time: on a successful login the hash is
    silently upgraded, so a parameter change reaches existing accounts without
    a password reset.
    """
    try:
        return get_password_hasher(settings).check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def generate_token() -> str:
    """Return a new URL-safe random token (256 bits of entropy)."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(token: str) -> str:
    """Return the hex SHA-256 of `token`, for storage and lookup.

    Unsalted by design: the value must be reproducible from the token alone so
    a session can be found with a single indexed equality lookup. A salt would
    make that impossible, and buys nothing against a 256-bit random input.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_equal(left: str, right: str) -> bool:
    """Constant-time comparison for secret values."""
    return hmac.compare_digest(left, right)
