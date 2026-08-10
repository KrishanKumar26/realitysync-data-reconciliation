"""PostgreSQL connection configuration and validation.

Validation happens before any connection attempt, so a malformed configuration
produces an immediate, specific message instead of a driver error thirty
seconds later.

The SSL policy is the important part. RealitySync refuses ``disable``,
``allow`` and ``prefer``. Only ``require`` and stronger are accepted, because
the first three can all result in an unencrypted session carrying a customer's
production credentials across the network — ``prefer`` silently downgrades,
which is worse than refusing outright.
"""

from __future__ import annotations

import enum
import ipaddress
import re
from dataclasses import dataclass
from typing import Any

from app.connectors.types import ConnectorError, ConnectorErrorCode


class SslMode(enum.StrEnum):
    """The subset of libpq SSL modes RealitySync permits.

    ``require``
        Encrypted, certificate not verified. Vulnerable to an active
        machine-in-the-middle, but safe against passive interception. The
        practical minimum, and what a self-signed certificate needs.

    ``verify-ca``
        Encrypted, certificate chain verified against a trusted CA.

    ``verify-full``
        Chain verified *and* the hostname checked. The only mode that defends
        against an active attacker. Recommended for production.
    """

    REQUIRE = "require"
    VERIFY_CA = "verify-ca"
    VERIFY_FULL = "verify-full"


#: Explicitly named so the error message can say *why* rather than only listing
#: what is allowed. "prefer" in particular looks safe and is not: it falls back
#: to plaintext without telling anyone.
REJECTED_SSL_MODES: dict[str, str] = {
    "disable": "sends credentials and data in plaintext",
    "allow": "only uses TLS if the server refuses plaintext first",
    "prefer": "silently falls back to plaintext when TLS is unavailable",
}

ALLOWED_SSL_MODES: tuple[str, ...] = tuple(m.value for m in SslMode)

#: Hostname or IP. Deliberately strict: this string is passed to libpq, and
#: accepting arbitrary text would allow smuggling extra connection parameters.
_HOST_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_$-]+$")

MAX_IDENTIFIER_LENGTH = 128


@dataclass(frozen=True, slots=True)
class PostgresConnectionConfig:
    """Validated, non-secret connection parameters.

    The password is deliberately absent. It travels separately, decrypted at
    the last moment, so a config object can be logged or serialised without
    anyone having to check whether this particular one is safe.
    """

    host: str
    port: int
    database: str
    username: str
    ssl_mode: SslMode

    def to_public_dict(self) -> dict[str, Any]:
        """The form stored on the data source and returned by the API."""
        return {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "username": self.username,
            "ssl_mode": self.ssl_mode.value,
        }

    @property
    def display_target(self) -> str:
        """Human-readable target, for messages. Contains no secret."""
        return f"{self.host}:{self.port}/{self.database}"


def _invalid(message: str, *, remediation: str | None = None) -> ConnectorError:
    return ConnectorError(
        ConnectorErrorCode.INVALID_CONFIGURATION, message, remediation=remediation
    )


def validate_ssl_mode(value: object) -> SslMode:
    """Accept only genuinely-encrypted SSL modes."""
    if not isinstance(value, str) or not value.strip():
        raise _invalid(
            "An SSL mode is required.",
            remediation=f"Use one of: {', '.join(ALLOWED_SSL_MODES)}.",
        )

    normalised = value.strip().lower()

    if normalised in REJECTED_SSL_MODES:
        raise _invalid(
            f"SSL mode '{normalised}' is not permitted because it "
            f"{REJECTED_SSL_MODES[normalised]}.",
            remediation=(
                "RealitySync requires an encrypted connection. Use 'require' at "
                "minimum, or 'verify-full' if you can provide the server's CA."
            ),
        )

    try:
        return SslMode(normalised)
    except ValueError:
        raise _invalid(
            f"Unknown SSL mode '{normalised}'.",
            remediation=f"Use one of: {', '.join(ALLOWED_SSL_MODES)}.",
        ) from None


def _validate_host(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid("A database host is required.")

    host = value.strip()
    if len(host) > 253:
        raise _invalid("The host name is too long.")

    # An IP literal is fine; otherwise it must look like a hostname. This also
    # blocks whitespace, '=' and quotes, which is what would let someone append
    # extra libpq keywords through this field.
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass

    if not _HOST_PATTERN.match(host):
        raise _invalid(
            "The host name contains characters that are not valid in a hostname.",
            remediation="Use a hostname or IP address, without a scheme or port.",
        )
    if "://" in value:
        raise _invalid(
            "Enter a hostname, not a URL.",
            remediation="For example 'db.example.com', not 'postgres://db.example.com'.",
        )
    return host


def _validate_port(value: object) -> int:
    if value is None or value == "":
        return 5432
    if not isinstance(value, (int, str)) or isinstance(value, bool):
        raise _invalid("The port must be a number.")
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise _invalid("The port must be a number.") from None
    if not 1 <= port <= 65535:
        raise _invalid("The port must be between 1 and 65535.")
    return port


def _validate_identifier(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid(f"A {field} is required.")

    identifier = value.strip()
    if len(identifier) > MAX_IDENTIFIER_LENGTH:
        raise _invalid(f"The {field} is too long.")
    if not _IDENTIFIER_PATTERN.match(identifier):
        raise _invalid(
            f"The {field} contains characters that are not permitted.",
            remediation="Use letters, digits, underscores, dollar signs or hyphens.",
        )
    return identifier


def parse_config(raw: dict[str, Any]) -> PostgresConnectionConfig:
    """Validate raw connection parameters, or raise ConnectorError."""
    return PostgresConnectionConfig(
        host=_validate_host(raw.get("host")),
        port=_validate_port(raw.get("port")),
        database=_validate_identifier(raw.get("database"), field="database name"),
        username=_validate_identifier(raw.get("username"), field="username"),
        ssl_mode=validate_ssl_mode(raw.get("ssl_mode")),
    )


def validate_password(raw: object) -> str:
    """Validate the credential payload's password field."""
    if not isinstance(raw, str) or raw == "":
        raise _invalid("A database password is required.")
    if len(raw) > 1024:
        raise _invalid("The password is too long.")
    return raw
