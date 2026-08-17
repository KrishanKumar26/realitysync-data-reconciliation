"""Mapping PostgreSQL driver failures to safe, actionable errors.

Two things must both be true of every error a connector produces:

* The **user** gets something they can act on — which database, what went
  wrong, what to change.
* The **user** gets nothing they should not have — no driver text, no
  connection string, no server internals.

Raw psycopg messages fail the second test routinely. A connection failure
embeds the full target including username; an authentication failure names the
role. Both go to the server log, where the redaction filter handles them, and
neither goes to the client.

Mapping is by SQLSTATE first, because those are stable across versions and
locales. Message-text matching is the fallback for pre-connection failures
(DNS, refused, TLS), which have no SQLSTATE.
"""

from __future__ import annotations

from app.connectors.types import ConnectorError, ConnectorErrorCode

#: SQLSTATE -> (code, message, remediation)
_SQLSTATE_MAP: dict[str, tuple[ConnectorErrorCode, str, str | None]] = {
    # Class 28 — invalid authorization
    "28P01": (
        ConnectorErrorCode.AUTHENTICATION_FAILED,
        "The database rejected the username or password.",
        "Check the credentials, then confirm the role can sign in from this network.",
    ),
    "28000": (
        ConnectorErrorCode.AUTHENTICATION_FAILED,
        "The database refused the connection for this role.",
        "Check pg_hba.conf allows this role to connect over TLS from RealitySync's address.",
    ),
    # Class 3D / 3F — missing database or schema
    "3D000": (
        ConnectorErrorCode.NOT_FOUND,
        "That database does not exist on the server.",
        "Check the database name.",
    ),
    "3F000": (
        ConnectorErrorCode.NOT_FOUND,
        "That schema does not exist.",
        "Run schema discovery again to see the available schemas.",
    ),
    # Class 42 — syntax or access rule violation
    "42501": (
        ConnectorErrorCode.PERMISSION_DENIED,
        "The database role does not have permission for this operation.",
        "Grant USAGE on the schema and SELECT on the table to the RealitySync role.",
    ),
    "42P01": (
        ConnectorErrorCode.NOT_FOUND,
        "That table no longer exists in the source database.",
        "Run schema discovery again, then reconfigure or remove the stream.",
    ),
    "42703": (
        ConnectorErrorCode.NOT_FOUND,
        "A configured column no longer exists in the source table.",
        "Run schema discovery again and update the stream's columns.",
    ),
    # Class 53 — insufficient resources
    "53300": (
        ConnectorErrorCode.UNREACHABLE,
        "The database has too many connections open.",
        "Retry shortly, or raise max_connections on the source.",
    ),
    # Class 57 — operator intervention
    "57014": (
        ConnectorErrorCode.TIMEOUT,
        "The query took longer than the allowed time and was cancelled.",
        "Narrow the stream, or add an index on the event-time column.",
    ),
    "57P01": (
        ConnectorErrorCode.UNREACHABLE,
        "The database server shut down the connection.",
        "Check whether the source is restarting, then retry.",
    ),
    "57P03": (
        ConnectorErrorCode.UNREACHABLE,
        "The database is starting up and not accepting connections yet.",
        "Retry in a few moments.",
    ),
}

#: Substrings in pre-connection failures, which carry no SQLSTATE. Ordered:
#: the first match wins, so more specific patterns must come first.
_TEXT_PATTERNS: tuple[tuple[tuple[str, ...], ConnectorErrorCode, str, str | None], ...] = (
    (
        (
            "server does not support ssl",
            "ssl is not enabled",
            "no pg_hba.conf entry",
            "sslmode value",
        ),
        ConnectorErrorCode.TLS_FAILED,
        "The database refused an encrypted connection.",
        "RealitySync requires TLS. Enable ssl on the source, or use a provider\n"
        "endpoint that supports it.",
    ),
    (
        (
            "certificate verify failed",
            "self-signed certificate",
            "self signed certificate",
            "certificate is not valid",
            "hostname mismatch",
            "ip address mismatch",
        ),
        ConnectorErrorCode.TLS_FAILED,
        "The database's TLS certificate could not be verified.",
        "Use sslmode 'require' if the certificate is self-signed, or supply the\n"
        "correct CA for 'verify-full'.",
    ),
    (
        ("ssl error", "sslv3", "tlsv1", "wrong version number"),
        ConnectorErrorCode.TLS_FAILED,
        "TLS negotiation with the database failed.",
        "Confirm the port speaks PostgreSQL over TLS.",
    ),
    (
        (
            "could not translate host name",
            "name or service not known",
            "nodename nor servname",
            "temporary failure in name resolution",
        ),
        ConnectorErrorCode.UNREACHABLE,
        "The database host name could not be resolved.",
        "Check the host name. RealitySync needs a publicly resolvable address.",
    ),
    (
        ("connection refused",),
        ConnectorErrorCode.UNREACHABLE,
        "The database refused the connection.",
        "Check the port, and that the server is accepting connections from\n"
        "RealitySync's IP addresses.",
    ),
    (
        ("timeout expired", "connection timed out", "timed out"),
        ConnectorErrorCode.TIMEOUT,
        "The database did not respond in time.",
        "Check the firewall allows RealitySync's IP addresses, and that the host\n"
        "and port are correct.",
    ),
    (
        ("network is unreachable", "no route to host", "host is unreachable"),
        ConnectorErrorCode.UNREACHABLE,
        "The database is not reachable from RealitySync.",
        "The MVP requires an endpoint reachable from the public internet.\n"
        "Private VPC addresses are not supported.",
    ),
    (
        # Connection poolers in transaction mode — Neon's `-pooler` endpoint,
        # PgBouncer, Supabase's pooler — reject libpq startup options. We send
        # `default_transaction_read_only`, which is what makes "RealitySync
        # cannot write to your database" enforced by the server rather than
        # promised by us. Dropping it to fit through the pooler would trade a
        # guarantee for a convenience, silently. So this fails, and says why.
        ("unsupported startup parameter",),
        ConnectorErrorCode.INVALID_CONFIGURATION,
        "This endpoint is a connection pooler, which cannot accept the "
        "read-only setting RealitySync requires.",
        "Use the direct endpoint instead of the pooled one. On Neon that means "
        "removing '-pooler' from the host name.\n"
        "RealitySync opens every connection read-only at the server, so it "
        "cannot write to your database. A pooler refuses that setting, and "
        "connecting without it would drop the guarantee.",
    ),
    (
        ("password authentication failed", "authentication failed"),
        ConnectorErrorCode.AUTHENTICATION_FAILED,
        "The database rejected the username or password.",
        "Check the credentials.",
    ),
    (
        ("permission denied",),
        ConnectorErrorCode.PERMISSION_DENIED,
        "The database role does not have permission for this operation.",
        "Grant USAGE on the schema and SELECT on the tables to the RealitySync role.",
    ),
)


def sqlstate_of(exc: BaseException) -> str | None:
    """Extract a SQLSTATE, if the driver attached one."""
    code = getattr(exc, "sqlstate", None)
    if isinstance(code, str) and code:
        return code
    diag = getattr(exc, "diag", None)
    candidate = getattr(diag, "sqlstate", None)
    return candidate if isinstance(candidate, str) and candidate else None


def map_exception(exc: BaseException, *, operation: str) -> ConnectorError:
    """Convert a driver exception into a safe ConnectorError.

    ``detail`` keeps the original text for the server log; it is never
    serialised into a response.
    """
    detail = f"{operation}: {type(exc).__name__}: {exc}"

    sqlstate = sqlstate_of(exc)
    if sqlstate and sqlstate in _SQLSTATE_MAP:
        code, message, remediation = _SQLSTATE_MAP[sqlstate]
        return ConnectorError(code, message, detail=detail, remediation=remediation)

    lowered = str(exc).lower()
    for needles, code, message, remediation in _TEXT_PATTERNS:
        if any(needle in lowered for needle in needles):
            return ConnectorError(code, message, detail=detail, remediation=remediation)

    # Class 42 covers a family of access-rule violations; treating an
    # unrecognised one as a permission problem is the useful default.
    if sqlstate and sqlstate.startswith("42"):
        return ConnectorError(
            ConnectorErrorCode.PERMISSION_DENIED,
            "The database rejected the query.",
            detail=detail,
            remediation="Check the RealitySync role's privileges on this schema and table.",
        )

    return ConnectorError(
        ConnectorErrorCode.UNKNOWN,
        "The database connection failed for an unexpected reason.",
        detail=detail,
        remediation="Check the source's logs. The full error is in RealitySync's server log.",
    )
