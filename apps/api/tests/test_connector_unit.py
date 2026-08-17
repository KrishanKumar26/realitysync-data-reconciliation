"""Connector logic that needs no database.

Configuration validation, error mapping, normalisation and fingerprinting are
pure functions. Testing them without I/O keeps the feedback fast and means a
failure here points at logic rather than at the environment.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import psycopg
import pytest

from app.connectors.postgres.config import (
    REJECTED_SSL_MODES,
    SslMode,
    parse_config,
    validate_ssl_mode,
)
from app.connectors.postgres.connector import build_external_id
from app.connectors.postgres.errors import map_exception
from app.connectors.registry import build_connector, supported_kinds
from app.connectors.types import ConnectorError, ConnectorErrorCode
from app.ingestion.fingerprint import canonical_json, compute_fingerprint
from app.ingestion.normalization import normalize_row, normalize_value

VALID_CONFIG = {
    "host": "db.example.com",
    "port": 5432,
    "database": "warehouse",
    "username": "reader",
    "ssl_mode": "require",
}


# --- SSL policy ------------------------------------------------------------


@pytest.mark.parametrize("mode", sorted(REJECTED_SSL_MODES))
def test_insecure_ssl_modes_are_rejected(mode: str) -> None:
    """'prefer' matters most here: it looks safe and silently downgrades."""
    with pytest.raises(ConnectorError) as exc_info:
        validate_ssl_mode(mode)

    assert exc_info.value.code is ConnectorErrorCode.INVALID_CONFIGURATION
    assert mode in exc_info.value.message


@pytest.mark.parametrize("mode", ["require", "verify-ca", "verify-full"])
def test_encrypted_ssl_modes_are_accepted(mode: str) -> None:
    assert validate_ssl_mode(mode) is SslMode(mode)


def test_ssl_mode_is_case_insensitive() -> None:
    assert validate_ssl_mode("REQUIRE") is SslMode.REQUIRE


def test_rejection_explains_why_rather_than_only_listing_options() -> None:
    with pytest.raises(ConnectorError) as exc_info:
        validate_ssl_mode("disable")

    assert "plaintext" in exc_info.value.message
    assert exc_info.value.remediation is not None


# --- Configuration validation ---------------------------------------------


def test_valid_configuration_parses() -> None:
    config = parse_config(VALID_CONFIG)

    assert config.host == "db.example.com"
    assert config.port == 5432
    assert config.ssl_mode is SslMode.REQUIRE
    # Never carries a password.
    assert "password" not in config.to_public_dict()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("host", ""),
        ("host", "db.example.com sslmode=disable"),  # parameter smuggling
        ("host", "postgres://db.example.com"),
        ("host", "db example.com"),
        ("port", 0),
        ("port", 70000),
        ("port", "not-a-number"),
        ("database", ""),
        ("database", "war ehouse"),
        ("database", "warehouse; DROP TABLE users"),
        ("username", ""),
        ("username", "reader'--"),
    ],
)
def test_malformed_parameters_are_rejected(field: str, value: object) -> None:
    """Rejected before any connection is attempted.

    The host cases matter beyond tidiness: the value is passed to libpq, and
    whitespace or '=' would let extra connection keywords be smuggled in —
    including one that turns TLS off.
    """
    with pytest.raises(ConnectorError) as exc_info:
        parse_config({**VALID_CONFIG, field: value})

    assert exc_info.value.code is ConnectorErrorCode.INVALID_CONFIGURATION


def test_ip_addresses_are_valid_hosts() -> None:
    assert parse_config({**VALID_CONFIG, "host": "10.0.0.5"}).host == "10.0.0.5"


def test_port_defaults_when_omitted() -> None:
    config = parse_config({k: v for k, v in VALID_CONFIG.items() if k != "port"})
    assert config.port == 5432


# --- Error mapping ---------------------------------------------------------


@pytest.mark.parametrize(
    ("sqlstate", "expected"),
    [
        ("28P01", ConnectorErrorCode.AUTHENTICATION_FAILED),
        ("3D000", ConnectorErrorCode.NOT_FOUND),
        ("42501", ConnectorErrorCode.PERMISSION_DENIED),
        ("42P01", ConnectorErrorCode.NOT_FOUND),
        ("57014", ConnectorErrorCode.TIMEOUT),
    ],
)
def test_sqlstates_map_to_stable_codes(sqlstate: str, expected: ConnectorErrorCode) -> None:
    class FakeError(Exception):
        pass

    error = FakeError("driver detail")
    error.sqlstate = sqlstate  # type: ignore[attr-defined]

    assert map_exception(error, operation="test").code is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("could not translate host name to address", ConnectorErrorCode.UNREACHABLE),
        ("connection refused", ConnectorErrorCode.UNREACHABLE),
        ("server does not support SSL", ConnectorErrorCode.TLS_FAILED),
        ("certificate verify failed", ConnectorErrorCode.TLS_FAILED),
        ("connection timed out", ConnectorErrorCode.TIMEOUT),
        ("network is unreachable", ConnectorErrorCode.UNREACHABLE),
    ],
)
def test_pre_connection_failures_map_by_message(text: str, expected: ConnectorErrorCode) -> None:
    """DNS, refusal and TLS failures carry no SQLSTATE."""
    assert map_exception(psycopg.OperationalError(text), operation="connect").code is expected


def test_driver_text_never_reaches_the_user_message() -> None:
    """The raw message routinely contains the host and username."""
    raw = (
        'connection to server at "db.internal" port 5432 failed: '
        'FATAL: password authentication failed for user "admin"'
    )

    error = map_exception(psycopg.OperationalError(raw), operation="connect")

    assert "db.internal" not in error.message
    assert "admin" not in error.message
    # Preserved for the server log, where redaction applies.
    assert error.detail is not None and "db.internal" in error.detail


def test_an_unrecognised_failure_is_generic_rather_than_leaky() -> None:
    error = map_exception(RuntimeError("host=secret.internal password=hunter2"), operation="x")

    assert error.code is ConnectorErrorCode.UNKNOWN
    assert "hunter2" not in error.message
    assert "secret.internal" not in error.message


# --- Registry --------------------------------------------------------------


def test_postgresql_is_registered() -> None:
    assert "postgresql" in supported_kinds()


def test_an_unknown_source_kind_fails_clearly() -> None:
    with pytest.raises(ConnectorError) as exc_info:
        build_connector(kind="oracle", config=VALID_CONFIG, credentials={"password": "x"})

    assert exc_info.value.code is ConnectorErrorCode.INVALID_CONFIGURATION
    assert "postgresql" in (exc_info.value.remediation or "")


def test_a_built_connector_never_reveals_its_password() -> None:
    connector = build_connector(
        kind="postgresql", config=VALID_CONFIG, credentials={"password": "hunter2-secret"}
    )

    assert "hunter2-secret" not in repr(connector)


def test_missing_password_is_rejected_at_build_time() -> None:
    with pytest.raises(ConnectorError):
        build_connector(kind="postgresql", config=VALID_CONFIG, credentials={})


# --- Normalisation ---------------------------------------------------------


def test_decimals_keep_their_scale() -> None:
    """A NUMERIC(10,3) of 12.500 must not become the float 12.5.

    Scale is information — the source is claiming three decimal places of
    precision — and a float cannot represent every decimal exactly.
    """
    assert normalize_value(Decimal("12.500")) == "12.500"
    assert normalize_value(Decimal("0.1")) == "0.1"
    assert normalize_value(Decimal("-999999999.999")) == "-999999999.999"


def test_timestamps_normalise_to_utc_iso() -> None:
    aware = dt.datetime(2026, 8, 1, 9, 0, tzinfo=dt.timezone(dt.timedelta(hours=2)))

    assert normalize_value(aware) == "2026-08-01T07:00:00+00:00"


def test_naive_timestamps_are_treated_as_utc() -> None:
    """Guessing a local zone would silently shift every event by hours."""
    assert normalize_value(dt.datetime(2026, 8, 1, 9, 0)) == "2026-08-01T09:00:00+00:00"


def test_booleans_do_not_become_integers() -> None:
    """bool is a subclass of int; the distinction must survive."""
    assert normalize_value(True) is True
    assert normalize_value(1) == 1


def test_bytes_become_base64() -> None:
    assert normalize_value(b"\x00\xff") == "AP8="


def test_uuids_and_nested_structures_normalise() -> None:
    value = normalize_value(
        {"b": uuid.UUID("00000000-0000-0000-0000-000000000001"), "a": [Decimal("1.10")]}
    )

    assert value == {"a": ["1.10"], "b": "00000000-0000-0000-0000-000000000001"}


def test_rows_normalise_deterministically_regardless_of_key_order() -> None:
    first = normalize_row({"b": 2, "a": 1})
    second = normalize_row({"a": 1, "b": 2})

    assert canonical_json(first) == canonical_json(second)


# --- Fingerprinting --------------------------------------------------------

BASE = {
    "source_id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
    "stream_id": uuid.UUID("22222222-2222-2222-2222-222222222222"),
    "external_id": "id=1",
    "event_time": dt.datetime(2026, 8, 1, tzinfo=dt.UTC),
    "event_time_semantics": "observed",
    "payload": {"status": "in_transit"},
}


def test_the_same_input_always_produces_the_same_fingerprint() -> None:
    assert compute_fingerprint(**BASE) == compute_fingerprint(**BASE)  # type: ignore[arg-type]


def test_fingerprints_are_sha256_hex() -> None:
    fingerprint = compute_fingerprint(**BASE)  # type: ignore[arg-type]

    assert len(fingerprint) == 64
    assert all(c in "0123456789abcdef" for c in fingerprint)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("external_id", "id=2"),
        ("payload", {"status": "delivered"}),
        ("event_time", dt.datetime(2026, 8, 2, tzinfo=dt.UTC)),
        ("event_time_semantics", "recorded"),
        ("stream_id", uuid.UUID("33333333-3333-3333-3333-333333333333")),
        ("source_id", uuid.UUID("44444444-4444-4444-4444-444444444444")),
    ],
)
def test_every_identity_bearing_field_changes_the_fingerprint(field: str, value: object) -> None:
    assert compute_fingerprint(**{**BASE, field: value}) != compute_fingerprint(**BASE)  # type: ignore[arg-type]


def test_payload_key_order_does_not_change_the_fingerprint() -> None:
    """Otherwise a driver returning columns in a different order would make
    every row look changed."""
    a = compute_fingerprint(**{**BASE, "payload": {"x": 1, "y": 2}})  # type: ignore[arg-type]
    b = compute_fingerprint(**{**BASE, "payload": {"y": 2, "x": 1}})  # type: ignore[arg-type]

    assert a == b


def test_fingerprint_input_excludes_ingestion_time() -> None:
    """The exclusion that idempotency depends on.

    If wall-clock entered the fingerprint, every sync would duplicate every
    row. compute_fingerprint takes no such parameter, which is the point —
    this test documents that the signature itself is the guarantee.
    """
    import inspect

    parameters = set(inspect.signature(compute_fingerprint).parameters)

    assert "ingested_at" not in parameters
    assert "sync_run_id" not in parameters
    assert parameters == {
        "source_id",
        "stream_id",
        "external_id",
        "event_time",
        "event_time_semantics",
        "payload",
    }


# --- External ids ----------------------------------------------------------


def test_composite_keys_are_unambiguous() -> None:
    """Without a delimiter, ('ab','c') and ('a','bc') would collide."""
    first = build_external_id({"a": "ab", "b": "c"})
    second = build_external_id({"a": "a", "b": "bc"})

    assert first != second


def test_external_ids_are_stable_regardless_of_column_order() -> None:
    assert build_external_id({"b": 2, "a": 1}) == build_external_id({"a": 1, "b": 2})


# --- TLS detection, and the hop it asks about -------------------------------


class _FakePgConn:
    def __init__(self, ssl_in_use: object) -> None:
        self.ssl_in_use = ssl_in_use


class _FakeConnection:
    def __init__(self, pgconn: object) -> None:
        self.pgconn = pgconn


def test_tls_is_detected_from_libpq_not_the_server() -> None:
    """BUG FOUND ON A REAL DEPLOYMENT, FIXED HERE.

    The TLS check used to ask the *server* - `pg_stat_ssl` - whether the
    session was encrypted. That describes the backend's own connection, which
    is the wrong hop. Every managed provider that terminates TLS at a proxy
    (Neon, Supabase's pooler, PgBouncer, RDS Proxy) reports ssl=false there
    while the client's connection is fully encrypted.

    The result was a refusal to connect to a genuinely encrypted database:
    Neon failed with "established without encryption" while libpq reported
    ssl_in_use=True and the same server refused a plaintext connection
    outright.

    libpq knows whether it negotiated TLS on its own socket. That is the only
    hop that matters, and no proxy can misreport it.
    """
    from app.connectors.postgres.connector import _tls_in_use

    assert _tls_in_use(_FakeConnection(_FakePgConn(True))) is True
    assert _tls_in_use(_FakeConnection(_FakePgConn(False))) is False


def test_tls_detection_does_not_fail_a_working_connection() -> None:
    """Absent attribute must not be read as "unencrypted".

    The connection was opened with sslmode=require, and libpq refuses to
    complete such a connection unencrypted - so reaching this point already
    implies TLS. Failing here would reject a working, encrypted connection
    because of a driver version difference.
    """
    from app.connectors.postgres.connector import _tls_in_use

    assert _tls_in_use(_FakeConnection(_FakePgConn(None))) is True
    assert _tls_in_use(_FakeConnection(None)) is True
    assert _tls_in_use(_FakeConnection(object())) is True
