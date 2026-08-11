"""MySQL connection configuration and validation.

Mirrors the PostgreSQL module's shape deliberately, but the TLS policy needed
rethinking rather than copying, because MySQL's model is not libpq's.

MySQL has no ``sslmode=prefer``. A client either requires TLS or it does not,
and verification is a separate axis. So the three modes here are RealitySync's
own vocabulary, chosen to mean the same things the PostgreSQL modes mean:

``require``       encrypted, certificate not verified
``verify-ca``     encrypted, chain verified
``verify-full``   chain verified and hostname checked

The value ``disable`` is rejected for the same reason as in PostgreSQL: it
would carry a customer's production credentials across the network in
plaintext. There is no equivalent of ``prefer`` to reject, because the driver
is configured with an explicit SSL context and never negotiates downward.
"""

from __future__ import annotations

import enum
import ipaddress
import re
import ssl
from dataclasses import dataclass
from typing import Any

from app.connectors.types import ConnectorError, ConnectorErrorCode


class MysqlSslMode(enum.StrEnum):
    """The TLS postures RealitySync permits for MySQL."""

    REQUIRE = "require"
    VERIFY_CA = "verify-ca"
    VERIFY_FULL = "verify-full"


#: Named so the error can say *why*, not only what is allowed.
REJECTED_SSL_MODES: dict[str, str] = {
    "disable": "sends credentials and data in plaintext",
    "disabled": "sends credentials and data in plaintext",
    "preferred": "silently falls back to plaintext when TLS is unavailable",
    "prefer": "silently falls back to plaintext when TLS is unavailable",
    "allow": "only uses TLS if the server refuses plaintext first",
}

ALLOWED_SSL_MODES: tuple[str, ...] = tuple(m.value for m in MysqlSslMode)

DEFAULT_PORT = 3306

_HOST_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
#: MySQL identifiers are permissive, but these are database and user names
#: arriving from configuration. Restricting the character set keeps them from
#: carrying anything that would need escaping in a place we have not escaped.
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_$-]+$")

MAX_IDENTIFIER_LENGTH = 128


@dataclass(frozen=True, slots=True)
class MysqlConnectionConfig:
    """Validated, non-secret connection parameters.

    The password is deliberately absent, exactly as in the PostgreSQL config:
    it travels separately and is decrypted at the last moment, so this object
    can be logged or serialised without anyone having to check first.
    """

    host: str
    port: int
    database: str
    username: str
    ssl_mode: MysqlSslMode

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "username": self.username,
            "ssl_mode": self.ssl_mode.value,
        }

    @property
    def display_target(self) -> str:
        """Human-readable target for messages. Contains no secret."""
        return f"{self.host}:{self.port}/{self.database}"

    def build_ssl_context(self) -> ssl.SSLContext:
        """The TLS context this mode means.

        Built explicitly rather than letting the driver decide. aiomysql will
        happily connect without TLS if given no context, so handing it one is
        what makes "TLS is required" true at the client as well as the server.
        """
        context = ssl.create_default_context()
        if self.ssl_mode is MysqlSslMode.REQUIRE:
            # Encrypted, certificate not verified — the practical minimum, and
            # what a self-signed server certificate needs. Safe against passive
            # interception, not against an active machine-in-the-middle, which
            # is why verify-full is what production should use.
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        elif self.ssl_mode is MysqlSslMode.VERIFY_CA:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_REQUIRED
        else:
            context.check_hostname = True
            context.verify_mode = ssl.CERT_REQUIRED
        return context


def _invalid(message: str, *, remediation: str | None = None) -> ConnectorError:
    return ConnectorError(
        ConnectorErrorCode.INVALID_CONFIGURATION, message, remediation=remediation
    )


def validate_ssl_mode(value: object) -> MysqlSslMode:
    """Accept only genuinely-encrypted TLS modes."""
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
                "minimum, or 'verify-full' if the server presents a certificate "
                "from a trusted CA."
            ),
        )

    try:
        return MysqlSslMode(normalised)
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

    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass

    if "://" in value:
        raise _invalid(
            "Enter a hostname, not a URL.",
            remediation="For example 'db.example.com', not 'mysql://db.example.com'.",
        )
    if not _HOST_PATTERN.match(host):
        raise _invalid(
            "The host name contains characters that are not valid in a hostname.",
            remediation="Use a hostname or IP address, without a scheme or port.",
        )
    return host


def _validate_port(value: object) -> int:
    if value is None or value == "":
        return DEFAULT_PORT
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


def parse_config(raw: dict[str, Any]) -> MysqlConnectionConfig:
    """Validate raw connection parameters, or raise ConnectorError."""
    return MysqlConnectionConfig(
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
