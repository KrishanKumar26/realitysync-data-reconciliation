"""Mapping MySQL driver failures to safe, actionable errors.

Same two requirements as the PostgreSQL mapper: the user gets something they
can act on, and nothing they should not have. Raw driver text fails the second
test routinely — a MySQL access-denied message names the account and the host
it connected from.

MySQL error numbers are the stable identifier, the equivalent of SQLSTATE, and
are matched first. Text matching is the fallback for failures that happen
before the server answers — DNS, refused connections, TLS — which carry no
error number at all.
"""

from __future__ import annotations

from app.connectors.types import ConnectorError, ConnectorErrorCode

#: MySQL error number -> (code, message, remediation)
#: https://dev.mysql.com/doc/mysql-errors/8.4/en/server-error-reference.html
_ERROR_NUMBER_MAP: dict[int, tuple[ConnectorErrorCode, str, str | None]] = {
    1044: (
        ConnectorErrorCode.PERMISSION_DENIED,
        "The account does not have access to that database.",
        "Grant SELECT on the database to the RealitySync account.",
    ),
    1045: (
        ConnectorErrorCode.AUTHENTICATION_FAILED,
        "The database rejected the username or password.",
        (
            "Check the credentials. If the account requires TLS, confirm it is "
            "allowed to connect from RealitySync's address."
        ),
    ),
    1049: (
        ConnectorErrorCode.NOT_FOUND,
        "That database does not exist on the server.",
        "Check the database name.",
    ),
    1142: (
        ConnectorErrorCode.PERMISSION_DENIED,
        "The account does not have permission for this operation.",
        "Grant SELECT on the tables you want to sync.",
    ),
    1146: (
        ConnectorErrorCode.NOT_FOUND,
        "That table no longer exists in the source database.",
        "Run schema discovery again, then reconfigure or remove the stream.",
    ),
    1054: (
        ConnectorErrorCode.NOT_FOUND,
        "A column RealitySync was reading is gone from that table.",
        "Run schema discovery again and reconfigure the stream.",
    ),
    1064: (
        ConnectorErrorCode.QUERY_FAILED,
        "The source rejected the query.",
        "This is a RealitySync bug rather than a configuration problem.",
    ),
    #: Server shutting down, or the connection was killed mid-query.
    1053: (
        ConnectorErrorCode.UNREACHABLE,
        "The source database closed the connection while the query was running.",
        "Retry. If it persists, check whether the server is restarting or overloaded.",
    ),
    2003: (
        ConnectorErrorCode.UNREACHABLE,
        "The database refused the connection.",
        "Check the host and port, and that the server accepts connections from this network.",
    ),
    2005: (
        ConnectorErrorCode.UNREACHABLE,
        "The database host could not be resolved.",
        "Check the hostname.",
    ),
    2013: (
        ConnectorErrorCode.UNREACHABLE,
        "The connection to the source database was lost.",
        "Retry. If it persists, check the network path and the server's timeout settings.",
    ),
    3159: (
        ConnectorErrorCode.TLS_FAILED,
        "The server requires TLS and the connection was not encrypted.",
        "RealitySync always requires TLS; this usually means a proxy stripped it.",
    ),
}

#: Substrings matched against the driver text for pre-connection failures,
#: which have no usable error number. Ordered: the first match wins, so the
#: specific cases must precede the general ones.
_TEXT_PATTERNS: tuple[tuple[tuple[str, ...], ConnectorErrorCode, str, str | None], ...] = (
    (
        ("certificate verify failed", "certificate_verify_failed"),
        ConnectorErrorCode.TLS_FAILED,
        "The server's TLS certificate could not be verified.",
        (
            "Use 'require' if the server presents a self-signed certificate, or "
            "install a certificate from a trusted CA on the server."
        ),
    ),
    (
        ("hostname mismatch", "does not match"),
        ConnectorErrorCode.TLS_FAILED,
        "The server's TLS certificate does not match the hostname.",
        "Use the hostname the certificate was issued for, or switch to 'verify-ca'.",
    ),
    (
        ("ssl", "tls"),
        ConnectorErrorCode.TLS_FAILED,
        "The encrypted connection could not be established.",
        "This database does not offer an encrypted connection. RealitySync will not connect "
        "without one.",
    ),
    (
        ("timed out", "timeout"),
        ConnectorErrorCode.TIMEOUT,
        "The source database did not respond in time.",
        "The database did not answer in time. It may be busy or unreachable.",
    ),
    (
        ("name or service not known", "nodename nor servname", "getaddrinfo", "name resolution"),
        ConnectorErrorCode.UNREACHABLE,
        "The database host could not be resolved.",
        "Check the hostname.",
    ),
    (
        ("connection refused", "can't connect", "cannot connect"),
        ConnectorErrorCode.UNREACHABLE,
        "The database refused the connection.",
        "Check the host and port, and that the server is running.",
    ),
    (
        ("no route to host", "network is unreachable"),
        ConnectorErrorCode.UNREACHABLE,
        "The database host is not reachable from RealitySync.",
        "Check firewall rules and allow RealitySync's address.",
    ),
)


def _error_number(exc: BaseException) -> int | None:
    """The MySQL error number, if the exception carries one.

    pymysql raises with ``args = (errno, message)``. Read defensively: an
    exception shape we did not anticipate must fall through to text matching
    rather than raise from inside the error mapper.
    """
    args = getattr(exc, "args", ())
    if args and isinstance(args[0], int):
        return int(args[0])
    return None


def map_exception(exc: BaseException, *, operation: str) -> ConnectorError:
    """Turn a driver exception into a safe ConnectorError.

    The original text is preserved in ``detail`` for the server log, where the
    redaction filter handles it. It is never serialised into a response.
    """
    if isinstance(exc, ConnectorError):
        return exc

    detail = f"{type(exc).__name__} during {operation}: {exc}"

    number = _error_number(exc)
    if number is not None and number in _ERROR_NUMBER_MAP:
        code, message, remediation = _ERROR_NUMBER_MAP[number]
        return ConnectorError(code, message, detail=detail, remediation=remediation)

    if isinstance(exc, TimeoutError):
        return ConnectorError(
            ConnectorErrorCode.TIMEOUT,
            "The source database did not respond in time.",
            detail=detail,
            remediation="The database did not answer in time. It may be busy or unreachable.",
        )

    text = str(exc).lower()
    for needles, code, message, remediation in _TEXT_PATTERNS:
        if any(needle in text for needle in needles):
            return ConnectorError(code, message, detail=detail, remediation=remediation)

    return ConnectorError(
        ConnectorErrorCode.UNKNOWN,
        "The source database could not be reached.",
        detail=detail,
        remediation="Check the address, database name, username and password, then try again.",
    )
