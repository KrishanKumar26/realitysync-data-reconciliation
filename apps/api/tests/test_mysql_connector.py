"""The MySQL connector, against a real MySQL server.

Every test here connects over TLS to an actual server and reads actual rows.
Nothing is mocked. A connector test that passes without connecting to anything
is worse than no test, so the whole module skips when the server is absent
rather than quietly succeeding.

The module has a second purpose beyond "does MySQL work". It is the evidence
for a claim the architecture has made since Phase 3 — that a new source type
needs no changes downstream of ``DataConnector``. The final section asserts
that directly: the same ingestion path, unmodified, turns MySQL rows into
observations indistinguishable from PostgreSQL ones.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.mysql.config import (
    MysqlSslMode,
    parse_config,
    validate_password,
    validate_ssl_mode,
)
from app.connectors.mysql.connector import MysqlConnector, quote_identifier
from app.connectors.registry import build_connector, supported_kinds
from app.connectors.types import ConnectorError, ConnectorErrorCode
from app.ingestion.sync import run_sync
from app.models.data_source import DataSource, SourceKind
from app.models.observation import Observation
from app.models.organization import Organization
from app.models.source_stream import SourceStream
from app.models.sync_run import SyncStatus
from tests.source_mysql import (
    MYSQL_READER,
    MysqlSourceTable,
    execute_on_mysql,
    insert_mysql_rows,
    mysql_catalog_row_estimate,
    mysql_reader_config,
    mysql_reader_credentials,
)

T0 = datetime(2026, 2, 1, 9, 0, tzinfo=UTC)


def mysql_connector() -> MysqlConnector:
    connector = build_connector(
        kind="mysql", config=mysql_reader_config(), credentials=mysql_reader_credentials()
    )
    assert isinstance(connector, MysqlConnector)
    return connector


async def make_organization(db: AsyncSession, name: str = "MySQL Org") -> Organization:
    organization = Organization(name=name, slug=f"mysql-{uuid.uuid4().hex[:10]}")
    db.add(organization)
    await db.flush()
    return organization


async def make_source(db: AsyncSession, organization: Organization) -> DataSource:
    source = DataSource(
        organization_id=organization.id,
        name=f"MySQL {uuid.uuid4().hex[:6]}",
        kind=SourceKind.MYSQL.value,
        config=mysql_reader_config(),
    )
    db.add(source)
    await db.flush()
    return source


async def make_stream(
    db: AsyncSession,
    organization: Organization,
    source: DataSource,
    table: MysqlSourceTable,
    *,
    event_time_column: str | None = "observed_at",
    semantics: str = "observed",
) -> SourceStream:
    stream = SourceStream(
        organization_id=organization.id,
        data_source_id=source.id,
        schema_name=table.schema_name,
        table_name=table.table_name,
        primary_key_columns=["record_id"],
        event_time_column=event_time_column,
        event_time_semantics=semantics,
    )
    db.add(stream)
    await db.flush()
    return stream


def connector_builder():  # type: ignore[no-untyped-def]
    async def build():  # type: ignore[no-untyped-def]
        connector = mysql_connector()
        await connector.connect()
        return connector

    return build


async def observations_for(db: AsyncSession, organization: Organization) -> list[Observation]:
    rows = await db.scalars(
        select(Observation)
        .where(Observation.organization_id == organization.id)
        .order_by(Observation.external_id, Observation.ingested_at)
    )
    return list(rows)


# --- Configuration ----------------------------------------------------------


def test_plaintext_ssl_modes_are_refused() -> None:
    """The same policy as PostgreSQL, expressed in MySQL's vocabulary."""
    for mode in ("disable", "disabled", "prefer", "preferred", "allow"):
        with pytest.raises(ConnectorError) as raised:
            validate_ssl_mode(mode)
        assert raised.value.code is ConnectorErrorCode.INVALID_CONFIGURATION
        # The message says *why*, not only what is allowed.
        assert "plaintext" in raised.value.message.lower()


def test_encrypted_ssl_modes_are_accepted() -> None:
    assert validate_ssl_mode("require") is MysqlSslMode.REQUIRE
    assert validate_ssl_mode("verify-ca") is MysqlSslMode.VERIFY_CA
    assert validate_ssl_mode("VERIFY-FULL") is MysqlSslMode.VERIFY_FULL


def test_the_default_port_is_mysqls_not_postgresqls() -> None:
    config = parse_config(
        {"host": "db.example.com", "database": "app", "username": "reader", "ssl_mode": "require"}
    )
    assert config.port == 3306


def test_a_url_is_rejected_with_a_useful_message() -> None:
    with pytest.raises(ConnectorError) as raised:
        parse_config(
            {
                "host": "mysql://db.example.com",
                "database": "app",
                "username": "reader",
                "ssl_mode": "require",
            }
        )
    assert "hostname, not a URL" in raised.value.message


def test_the_config_carries_no_password() -> None:
    """A config object must be safe to log without checking first."""
    config = parse_config(mysql_reader_config())
    serialised = str(config.to_public_dict()) + config.display_target + repr(config)
    assert mysql_reader_credentials()["password"] not in serialised


def test_an_empty_password_is_rejected() -> None:
    with pytest.raises(ConnectorError):
        validate_password("")


def test_verify_modes_build_the_contexts_they_promise() -> None:
    """The TLS posture is the config's meaning, not the driver's default."""
    import ssl as ssl_module

    require = parse_config({**mysql_reader_config(), "ssl_mode": "require"}).build_ssl_context()
    assert require.verify_mode is ssl_module.CERT_NONE
    assert require.check_hostname is False

    verify_ca = parse_config({**mysql_reader_config(), "ssl_mode": "verify-ca"}).build_ssl_context()
    assert verify_ca.verify_mode is ssl_module.CERT_REQUIRED
    assert verify_ca.check_hostname is False

    full = parse_config({**mysql_reader_config(), "ssl_mode": "verify-full"}).build_ssl_context()
    assert full.verify_mode is ssl_module.CERT_REQUIRED
    assert full.check_hostname is True


# --- Identifier quoting -----------------------------------------------------


def test_identifiers_are_backtick_quoted() -> None:
    assert quote_identifier("orders") == "`orders`"


def test_a_backtick_in_an_identifier_is_refused_not_escaped() -> None:
    """Refusing is a smaller surface than escaping, and it fails loudly."""
    with pytest.raises(ConnectorError) as raised:
        quote_identifier("orders`; DROP TABLE users; --")
    assert raised.value.code is ConnectorErrorCode.INVALID_CONFIGURATION
    # The rejected value stays in detail, out of the user-facing message.
    assert "DROP TABLE" not in raised.value.message


# --- Registry ---------------------------------------------------------------


def test_both_connector_types_are_registered() -> None:
    """The claim under test: a second type needed only a registry entry."""
    assert "postgresql" in supported_kinds()
    assert "mysql" in supported_kinds()


def test_an_unknown_kind_names_what_is_available() -> None:
    with pytest.raises(ConnectorError) as raised:
        build_connector(kind="oracle", config={}, credentials={})
    assert "mysql" in (raised.value.remediation or "")


# --- Connection -------------------------------------------------------------


async def test_connects_over_tls_to_a_real_database(require_source_mysql: None) -> None:
    async with mysql_connector() as connector:
        result = await connector.test_connection()

    assert result.status == "connected"
    # Read from the session's own status, so this is the negotiated session
    # rather than the requested mode.
    assert result.tls_version is not None
    assert result.tls_version.startswith("TLS")
    assert result.server_version is not None
    assert result.server_version.startswith("MySQL")
    assert MYSQL_READER in (result.connected_as or "")


async def test_wrong_credentials_produce_a_safe_error(require_source_mysql: None) -> None:
    """The user gets something actionable and nothing they should not have."""
    connector = build_connector(
        kind="mysql",
        config=mysql_reader_config(),
        credentials={"password": "definitely-not-the-password"},
    )
    with pytest.raises(ConnectorError) as raised:
        await connector.connect()

    error = raised.value
    assert error.code is ConnectorErrorCode.AUTHENTICATION_FAILED
    assert "definitely-not-the-password" not in error.message
    assert MYSQL_READER not in error.message


async def test_the_session_is_read_only(
    require_source_mysql: None, mysql_table: MysqlSourceTable
) -> None:
    """Read-only enforced by the server, not by our good intentions."""
    async with mysql_connector() as connector:
        connection = connector._require_connection()
        async with connection.cursor() as cursor:
            with pytest.raises(Exception) as raised:
                # Fixture-generated identifier, literal values; the point of the
                # statement is that the server refuses it.
                statement = f"INSERT INTO {mysql_table.qualified} (record_id, reference, status, observed_at) VALUES (999, 'X', 'x', '2026-01-01 00:00:00')"  # noqa: E501, S608
                await cursor.execute(statement)
    # Either the read-only session or the reader's missing INSERT grant stops
    # it. Both are correct; what matters is that the write did not happen.
    assert raised.value is not None


# --- Discovery --------------------------------------------------------------


async def test_discovery_finds_the_real_table(
    require_source_mysql: None, mysql_table: MysqlSourceTable
) -> None:
    async with mysql_connector() as connector:
        discovered = await connector.discover_schema()

    table = next(t for t in discovered.tables if t.table_name == mysql_table.table_name)
    columns = {c.name: c for c in table.columns}

    assert set(columns) == {
        "record_id",
        "reference",
        "status",
        "amount",
        "observed_at",
        "notes",
    }
    # Precision preserved in the described type, so an operator choosing
    # identity columns sees what the source actually declared.
    assert columns["amount"].data_type == "decimal(12,3)"
    assert columns["record_id"].primary_key_position == 1
    assert columns["observed_at"].is_temporal is True
    assert columns["reference"].is_temporal is False
    assert columns["notes"].nullable is True
    assert columns["reference"].nullable is False


async def test_discovery_excludes_system_schemas(
    require_source_mysql: None, mysql_table: MysqlSourceTable
) -> None:
    async with mysql_connector() as connector:
        discovered = await connector.discover_schema()

    assert "information_schema" not in discovered.schemas
    assert "performance_schema" not in discovered.schemas
    assert "mysql" not in discovered.schemas
    assert all(t.schema_name != "mysql" for t in discovered.tables)


async def test_discovery_does_not_read_table_data(
    require_source_mysql: None, mysql_table: MysqlSourceTable
) -> None:
    """Row counts are the engine's estimate, never COUNT(*).

    Discovery runs against a customer's production database; scanning every
    table to describe it would be an outage caused by a configuration screen.
    """
    await insert_mysql_rows(
        mysql_table,
        [
            {"record_id": i, "reference": f"REF-{i}", "status": "new", "observed_at": T0}
            for i in range(1, 51)
        ],
    )

    async with mysql_connector() as connector:
        discovered = await connector.discover_schema()

    table = next(t for t in discovered.tables if t.table_name == mysql_table.table_name)

    # Asserted against the catalog's own estimate rather than against the true
    # count. InnoDB's estimate sometimes happens to equal the real number for a
    # small table, so "the value differs from 50" would be a flaky proxy for
    # the actual claim. Matching information_schema exactly proves where the
    # number came from, which is the claim: the estimate, never COUNT(*).
    estimate = await mysql_catalog_row_estimate(mysql_table)
    assert table.approximate_row_count == estimate


# --- The slice --------------------------------------------------------------


async def test_real_mysql_rows_become_real_observations(
    require_source_mysql: None, mysql_table: MysqlSourceTable, db: AsyncSession
) -> None:
    """REAL MYSQL TABLE -> SYNC -> OBSERVATIONS."""
    await insert_mysql_rows(
        mysql_table,
        [
            {
                "record_id": 1,
                "reference": "REF-001",
                "status": "in_transit",
                "amount": Decimal("12.500"),
                "observed_at": T0,
            },
            {
                "record_id": 2,
                "reference": "REF-002",
                "status": "delivered",
                "amount": Decimal("3.250"),
                "observed_at": T0 + timedelta(hours=1),
            },
        ],
    )

    organization = await make_organization(db)
    source = await make_source(db, organization)
    stream = await make_stream(db, organization, source, mysql_table)

    outcome = await run_sync(
        db,
        source=source,
        streams=[stream],
        connector_builder=connector_builder(),
        idempotency_key="mysql-slice-1",
        incremental=False,
    )

    assert outcome.run.status == SyncStatus.COMPLETED.value
    assert (outcome.run.rows_seen, outcome.run.rows_created, outcome.run.rows_skipped) == (2, 2, 0)

    observations = await observations_for(db, organization)
    assert len(observations) == 2

    first = observations[0]
    assert first.external_id == "record_id=1"
    assert first.payload["reference"] == "REF-001"
    assert first.payload["status"] == "in_transit"
    # Scale preserved: not the float 12.5. The same assertion the PostgreSQL
    # slice makes, because the ingestion layer normalises both identically.
    assert first.payload["amount"] == "12.500"
    assert first.event_time == T0
    assert first.event_time_semantics == "observed"
    assert first.entity_mapping_state == "unmapped"
    assert len(first.fingerprint) == 64
    assert first.provenance["connector"] == "mysql"
    assert first.provenance["table"] == mysql_table.table_name


async def test_event_time_and_ingestion_time_stay_separate(
    require_source_mysql: None, mysql_table: MysqlSourceTable, db: AsyncSession
) -> None:
    """The distinction the whole product rests on, for the second source type."""
    old = datetime(2020, 1, 1, 0, 0, tzinfo=UTC)
    await insert_mysql_rows(
        mysql_table,
        [{"record_id": 1, "reference": "REF-OLD", "status": "new", "observed_at": old}],
    )

    organization = await make_organization(db)
    source = await make_source(db, organization)
    stream = await make_stream(db, organization, source, mysql_table)

    await run_sync(
        db,
        source=source,
        streams=[stream],
        connector_builder=connector_builder(),
        idempotency_key="mysql-bitemporal",
        incremental=False,
    )

    observation = (await observations_for(db, organization))[0]
    assert observation.event_time == old
    # Learned now, true in 2020. Conflating them would erase the distinction.
    assert observation.ingested_at > old
    assert observation.ingested_at.year >= 2026


async def test_repeated_sync_creates_no_duplicates(
    require_source_mysql: None, mysql_table: MysqlSourceTable, db: AsyncSession
) -> None:
    """The fingerprint is over source values, so it is connector-independent."""
    await insert_mysql_rows(
        mysql_table,
        [{"record_id": 1, "reference": "REF-001", "status": "new", "observed_at": T0}],
    )

    organization = await make_organization(db)
    source = await make_source(db, organization)
    stream = await make_stream(db, organization, source, mysql_table)

    for key in ("mysql-run-1", "mysql-run-2"):
        await run_sync(
            db,
            source=source,
            streams=[stream],
            connector_builder=connector_builder(),
            idempotency_key=key,
            incremental=False,
        )

    assert len(await observations_for(db, organization)) == 1


async def test_a_changed_row_produces_a_new_observation(
    require_source_mysql: None, mysql_table: MysqlSourceTable, db: AsyncSession
) -> None:
    """A new state is a new observation, not an overwrite.

    Overwriting would destroy the history the timeline is built from.
    """
    await insert_mysql_rows(
        mysql_table,
        [{"record_id": 1, "reference": "REF-001", "status": "new", "observed_at": T0}],
    )

    organization = await make_organization(db)
    source = await make_source(db, organization)
    stream = await make_stream(db, organization, source, mysql_table)

    await run_sync(
        db,
        source=source,
        streams=[stream],
        connector_builder=connector_builder(),
        idempotency_key="mysql-change-1",
        incremental=False,
    )

    await execute_on_mysql(
        f"UPDATE {mysql_table.qualified} SET status = %s, observed_at = %s WHERE record_id = 1",  # noqa: S608
        ("delivered", T0 + timedelta(hours=2)),
    )

    await run_sync(
        db,
        source=source,
        streams=[stream],
        connector_builder=connector_builder(),
        idempotency_key="mysql-change-2",
        incremental=False,
    )

    observations = await observations_for(db, organization)
    assert len(observations) == 2
    assert {o.payload["status"] for o in observations} == {"new", "delivered"}


async def test_incremental_sync_reads_only_new_rows(
    require_source_mysql: None, mysql_table: MysqlSourceTable, db: AsyncSession
) -> None:
    await insert_mysql_rows(
        mysql_table,
        [{"record_id": 1, "reference": "REF-001", "status": "new", "observed_at": T0}],
    )

    organization = await make_organization(db)
    source = await make_source(db, organization)
    stream = await make_stream(db, organization, source, mysql_table)

    await run_sync(
        db,
        source=source,
        streams=[stream],
        connector_builder=connector_builder(),
        idempotency_key="mysql-inc-1",
        incremental=True,
    )

    await insert_mysql_rows(
        mysql_table,
        [
            {
                "record_id": 2,
                "reference": "REF-002",
                "status": "new",
                "observed_at": T0 + timedelta(hours=5),
            }
        ],
    )

    second = await run_sync(
        db,
        source=source,
        streams=[stream],
        connector_builder=connector_builder(),
        idempotency_key="mysql-inc-2",
        incremental=True,
    )

    # The first row is re-read (>= the high-water mark, so a same-instant row
    # is never skipped) and deduplicated by fingerprint rather than skipped by
    # the query.
    assert second.run.rows_created == 1
    assert len(await observations_for(db, organization)) == 2


async def test_a_missing_table_fails_the_run_without_crashing(
    require_source_mysql: None, mysql_table: MysqlSourceTable, db: AsyncSession
) -> None:
    organization = await make_organization(db)
    source = await make_source(db, organization)
    stream = await make_stream(db, organization, source, mysql_table)

    await execute_on_mysql(f"DROP TABLE {mysql_table.qualified}")

    outcome = await run_sync(
        db,
        source=source,
        streams=[stream],
        connector_builder=connector_builder(),
        idempotency_key="mysql-missing",
        incremental=False,
    )

    assert outcome.run.status == SyncStatus.FAILED.value
    assert outcome.run.error_code == ConnectorErrorCode.NOT_FOUND.value
    # Recreated so the fixture's teardown has something to drop.
    await execute_on_mysql(
        f"CREATE TABLE {mysql_table.qualified} ("
        "record_id BIGINT PRIMARY KEY, reference VARCHAR(64) NOT NULL, "
        "status VARCHAR(32) NOT NULL, amount DECIMAL(12,3), "
        "observed_at DATETIME NOT NULL, notes TEXT)"
    )


# --- The architectural claim ------------------------------------------------


async def test_two_source_types_produce_equivalent_observations(
    require_source_mysql: None,
    require_source_db: None,
    mysql_table: MysqlSourceTable,
    source_table,  # type: ignore[no-untyped-def]
    db: AsyncSession,
) -> None:
    """The same values from MySQL and PostgreSQL become the same observation.

    This is the payoff of the whole connector abstraction. If the two paths
    produced different payloads for identical source values, every downstream
    comparison — conflict detection above all — would report differences that
    exist only because the systems are different, which is precisely the noise
    this product must not generate.
    """
    from tests.source_db import insert_rows as insert_pg_rows
    from tests.test_connector_integration import connector_builder as pg_builder
    from tests.test_connector_integration import make_source as make_pg_source
    from tests.test_connector_integration import make_stream as make_pg_stream

    values = {
        "record_id": 7,
        "reference": "REF-SAME",
        "status": "in_transit",
        "amount": Decimal("42.125"),
        "observed_at": T0,
    }
    await insert_mysql_rows(mysql_table, [values])
    await insert_pg_rows(source_table, [dict(values)])

    organization = await make_organization(db, "Two Types")

    mysql_source = await make_source(db, organization)
    mysql_stream = await make_stream(db, organization, mysql_source, mysql_table)
    await run_sync(
        db,
        source=mysql_source,
        streams=[mysql_stream],
        connector_builder=connector_builder(),
        idempotency_key="equiv-mysql",
        incremental=False,
    )

    pg_source = await make_pg_source(db, organization)
    pg_stream = await make_pg_stream(db, organization, pg_source, source_table)
    await run_sync(
        db,
        source=pg_source,
        streams=[pg_stream],
        connector_builder=pg_builder(),
        idempotency_key="equiv-pg",
        incremental=False,
    )

    observations = await observations_for(db, organization)
    assert len(observations) == 2

    from_mysql = next(o for o in observations if o.source_id == mysql_source.id)
    from_postgres = next(o for o in observations if o.source_id == pg_source.id)

    assert from_mysql.external_id == from_postgres.external_id == "record_id=7"
    assert from_mysql.event_time == from_postgres.event_time == T0
    for field in ("reference", "status", "amount"):
        assert from_mysql.payload[field] == from_postgres.payload[field], (
            f"{field} differs between source types: "
            f"{from_mysql.payload[field]!r} vs {from_postgres.payload[field]!r}"
        )

    # Fingerprints must NOT match, and that is the correct behaviour rather
    # than a wart. The fingerprint deliberately includes source_id and
    # stream_id (see app/ingestion/fingerprint.py): two systems asserting the
    # same thing are two separate observations, and collapsing them into one
    # would destroy exactly the corroboration — or disagreement — the Reality
    # Engine exists to weigh.
    assert from_mysql.fingerprint != from_postgres.fingerprint

    # What must match is the normalised payload, asserted field by field above.
    # That is the real equivalence: the connectors differ, the values do not.
    assert from_mysql.payload == from_postgres.payload

    # Provenance is the one thing that must differ: it records where the
    # observation came from.
    assert from_mysql.provenance["connector"] == "mysql"
    assert from_postgres.provenance["connector"] == "postgresql"
