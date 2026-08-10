"""The vertical slice, against a real PostgreSQL.

    REAL TABLE -> DISCOVERY -> SYNC -> OBSERVATIONS

Every observation asserted here was produced by the connector reading an actual
row over an actual TLS connection. None are fabricated by a fixture — that is
the whole point of this file.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.registry import build_connector
from app.connectors.types import ConnectorError, ConnectorErrorCode, StreamSelector
from app.ingestion.sync import run_sync
from app.models.data_source import DataSource, SourceStatus
from app.models.observation import Observation
from app.models.organization import Organization
from app.models.source_stream import SourceStream
from app.models.sync_run import SyncStatus
from tests.source_db import (
    SourceTable,
    execute_on_source,
    insert_rows,
    reader_config,
    reader_credentials,
)

pytestmark = pytest.mark.integration

T0 = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)


# --- Helpers ---------------------------------------------------------------


async def make_organization(db: AsyncSession, name: str = "Connector Org") -> Organization:
    """A tenant. Not business data — the container business data lives in."""
    organization = Organization(name=name, slug=f"conn-{uuid.uuid4().hex[:10]}")
    db.add(organization)
    await db.flush()
    return organization


async def make_source(
    db: AsyncSession, organization: Organization, *, name: str | None = None
) -> DataSource:
    source = DataSource(
        organization_id=organization.id,
        name=name or f"Source {uuid.uuid4().hex[:6]}",
        kind="postgresql",
        config=reader_config(),
    )
    db.add(source)
    await db.flush()
    return source


async def make_stream(
    db: AsyncSession,
    organization: Organization,
    source: DataSource,
    table: SourceTable,
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
    """Build and connect a real connector against the source database."""

    async def build():  # type: ignore[no-untyped-def]
        connector = build_connector(
            kind="postgresql", config=reader_config(), credentials=reader_credentials()
        )
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


# --- Connection ------------------------------------------------------------


async def test_connects_over_tls_to_a_real_database(require_source_db: None) -> None:
    connector = build_connector(
        kind="postgresql", config=reader_config(), credentials=reader_credentials()
    )

    async with connector:
        result = await connector.test_connection()

    assert result.status == "connected"
    # Read from pg_stat_ssl, so this is the negotiated session, not the
    # requested mode.
    assert result.tls_version is not None
    assert result.tls_version.startswith("TLS")
    assert result.server_version is not None
    assert result.can_discover_schema is True


async def test_wrong_credentials_produce_a_safe_error(require_source_db: None) -> None:
    connector = build_connector(
        kind="postgresql",
        config=reader_config(),
        credentials={"password": "definitely-not-the-password"},
    )

    with pytest.raises(ConnectorError) as exc_info:
        await connector.connect()

    assert exc_info.value.code is ConnectorErrorCode.AUTHENTICATION_FAILED
    assert "definitely-not-the-password" not in exc_info.value.message


# --- Discovery -------------------------------------------------------------


async def test_discovery_finds_the_real_table(
    require_source_db: None, source_table: SourceTable
) -> None:
    connector = build_connector(
        kind="postgresql", config=reader_config(), credentials=reader_credentials()
    )

    async with connector:
        discovered = await connector.discover_schema()

    table = next(t for t in discovered.tables if t.table_name == source_table.table_name)

    assert table.primary_key_columns == ("record_id",)
    assert "observed_at" in table.temporal_columns
    assert {c.name for c in table.columns} == {
        "record_id",
        "reference",
        "status",
        "amount",
        "observed_at",
        "notes",
    }
    nullable = {c.name: c.nullable for c in table.columns}
    assert nullable["reference"] is False
    assert nullable["notes"] is True


async def test_discovery_excludes_system_schemas(
    require_source_db: None, source_table: SourceTable
) -> None:
    connector = build_connector(
        kind="postgresql", config=reader_config(), credentials=reader_credentials()
    )

    async with connector:
        discovered = await connector.discover_schema()

    assert "pg_catalog" not in discovered.schemas
    assert "information_schema" not in discovered.schemas


async def test_discovery_does_not_read_table_data(
    require_source_db: None, source_table: SourceTable
) -> None:
    """Row counts are planner estimates.

    A freshly created table that has never been analysed reports 0 or None,
    even though rows exist — proof the number came from the catalog rather
    than a scan. Scanning every table to describe it would turn a
    configuration screen into an outage on a large database.
    """
    await insert_rows(
        source_table,
        [
            {"record_id": i, "reference": f"REF-{i}", "status": "new", "observed_at": T0}
            for i in range(1, 51)
        ],
    )

    connector = build_connector(
        kind="postgresql", config=reader_config(), credentials=reader_credentials()
    )
    async with connector:
        discovered = await connector.discover_schema()

    table = next(t for t in discovered.tables if t.table_name == source_table.table_name)
    assert table.approximate_row_count in (0, None)


# --- The slice -------------------------------------------------------------


async def test_real_rows_become_real_observations(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    """REAL TABLE -> SYNC -> OBSERVATIONS."""
    await insert_rows(
        source_table,
        [
            {
                "record_id": 1,
                "reference": "REF-001",
                "status": "in_transit",
                "amount": "12.500",
                "observed_at": T0,
            },
            {
                "record_id": 2,
                "reference": "REF-002",
                "status": "delivered",
                "amount": "3.250",
                "observed_at": T0 + timedelta(hours=1),
            },
        ],
    )

    organization = await make_organization(db)
    source = await make_source(db, organization)
    stream = await make_stream(db, organization, source, source_table)

    outcome = await run_sync(
        db,
        source=source,
        streams=[stream],
        connector_builder=connector_builder(),
        idempotency_key="slice-1",
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
    # Scale preserved: not the float 12.5.
    assert first.payload["amount"] == "12.500"
    assert first.event_time == T0
    assert first.event_time_semantics == "observed"
    assert first.entity_mapping_state == "unmapped"
    assert len(first.fingerprint) == 64
    assert first.provenance["table"] == source_table.table_name
    assert first.provenance["connector"] == "postgresql"


async def test_event_time_and_ingestion_time_stay_separate(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    """The distinction the whole product rests on."""
    await insert_rows(
        source_table,
        [{"record_id": 1, "reference": "R", "status": "s", "observed_at": T0}],
    )

    organization = await make_organization(db)
    source = await make_source(db, organization)
    stream = await make_stream(db, organization, source, source_table)

    await run_sync(
        db,
        source=source,
        streams=[stream],
        connector_builder=connector_builder(),
        idempotency_key="times",
        incremental=False,
    )

    observation = (await observations_for(db, organization))[0]

    assert observation.event_time == T0
    # Ingested now, which is years after the source says the fact was true.
    assert observation.ingested_at > T0
    assert observation.ingested_at != observation.event_time


# --- Idempotency -----------------------------------------------------------


async def test_repeated_sync_creates_no_duplicates(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    await insert_rows(
        source_table,
        [
            {"record_id": i, "reference": f"REF-{i}", "status": "new", "observed_at": T0}
            for i in range(1, 4)
        ],
    )

    organization = await make_organization(db)
    source = await make_source(db, organization)
    stream = await make_stream(db, organization, source, source_table)

    first = await run_sync(
        db,
        source=source,
        streams=[stream],
        connector_builder=connector_builder(),
        idempotency_key="idem-1",
        incremental=False,
    )
    second = await run_sync(
        db,
        source=source,
        streams=[stream],
        connector_builder=connector_builder(),
        idempotency_key="idem-2",
        incremental=False,
    )

    assert first.run.rows_created == 3
    assert second.run.rows_seen == 3
    assert second.run.rows_created == 0
    assert second.run.rows_skipped == 3

    total = await db.scalar(
        select(func.count())
        .select_from(Observation)
        .where(Observation.organization_id == organization.id)
    )
    assert total == 3


async def test_the_same_idempotency_key_returns_the_original_run(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    """A retried request must not start a second sync."""
    await insert_rows(
        source_table,
        [{"record_id": 1, "reference": "R", "status": "s", "observed_at": T0}],
    )

    organization = await make_organization(db)
    source = await make_source(db, organization)
    stream = await make_stream(db, organization, source, source_table)

    first = await run_sync(
        db,
        source=source,
        streams=[stream],
        connector_builder=connector_builder(),
        idempotency_key="same-key",
        incremental=False,
    )
    second = await run_sync(
        db,
        source=source,
        streams=[stream],
        connector_builder=connector_builder(),
        idempotency_key="same-key",
        incremental=False,
    )

    assert first.run.id == second.run.id


async def test_a_changed_row_produces_a_new_observation(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    """Append-only: the original observation survives the change.

    It was true when it was made, and discarding it would erase the history
    that makes a later explanation possible.
    """
    await insert_rows(
        source_table,
        [
            {
                "record_id": 1,
                "reference": "REF-001",
                "status": "in_transit",
                "observed_at": T0,
            }
        ],
    )

    organization = await make_organization(db)
    source = await make_source(db, organization)
    stream = await make_stream(db, organization, source, source_table)

    await run_sync(
        db,
        source=source,
        streams=[stream],
        connector_builder=connector_builder(),
        idempotency_key="change-1",
        incremental=False,
    )

    await execute_on_source(
        source_table,
        "UPDATE {table} SET status = %(status)s, observed_at = %(t)s WHERE record_id = 1",
        {"status": "delivered", "t": T0 + timedelta(hours=2)},
    )

    second = await run_sync(
        db,
        source=source,
        streams=[stream],
        connector_builder=connector_builder(),
        idempotency_key="change-2",
        incremental=False,
    )

    assert second.run.rows_created == 1

    observations = await observations_for(db, organization)
    assert len(observations) == 2
    statuses = {o.payload["status"] for o in observations}
    assert statuses == {"in_transit", "delivered"}


async def test_a_deleted_row_leaves_its_observations_intact(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    """A filtered read cannot see deletes, and observations are not retracted.

    Documented behaviour rather than an oversight: the observation was a true
    statement when it was made. Detecting deletions needs a change feed, which
    the MVP does not use.
    """
    await insert_rows(
        source_table,
        [
            {"record_id": 1, "reference": "A", "status": "open", "observed_at": T0},
            {"record_id": 2, "reference": "B", "status": "open", "observed_at": T0},
        ],
    )

    organization = await make_organization(db)
    source = await make_source(db, organization)
    stream = await make_stream(db, organization, source, source_table)

    await run_sync(
        db,
        source=source,
        streams=[stream],
        connector_builder=connector_builder(),
        idempotency_key="del-1",
        incremental=False,
    )

    await execute_on_source(source_table, "DELETE FROM {table} WHERE record_id = 2")

    second = await run_sync(
        db,
        source=source,
        streams=[stream],
        connector_builder=connector_builder(),
        idempotency_key="del-2",
        incremental=False,
    )

    assert second.run.rows_seen == 1  # only the surviving row is read
    assert second.run.rows_created == 0

    observations = await observations_for(db, organization)
    assert len(observations) == 2
    assert {o.external_id for o in observations} == {"record_id=1", "record_id=2"}


# --- Out-of-order arrival --------------------------------------------------


async def test_a_late_arriving_row_keeps_its_own_event_time(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    """Out-of-order arrival is normal and must not be rewritten.

    A row inserted now but describing something from before the last sync
    keeps its earlier event time, and the high-water mark does not move
    backwards to accommodate it.
    """
    await insert_rows(
        source_table,
        [{"record_id": 1, "reference": "recent", "status": "s", "observed_at": T0}],
    )

    organization = await make_organization(db)
    source = await make_source(db, organization)
    stream = await make_stream(db, organization, source, source_table)

    await run_sync(
        db,
        source=source,
        streams=[stream],
        connector_builder=connector_builder(),
        idempotency_key="ooo-1",
        incremental=True,
    )
    assert stream.last_event_time == T0

    # Arrives now, but happened an hour before the row already ingested.
    earlier = T0 - timedelta(hours=1)
    await insert_rows(
        source_table,
        [{"record_id": 2, "reference": "late", "status": "s", "observed_at": earlier}],
    )

    await run_sync(
        db,
        source=source,
        streams=[stream],
        connector_builder=connector_builder(),
        idempotency_key="ooo-2",
        incremental=False,
    )

    observations = {o.external_id: o for o in await observations_for(db, organization)}

    assert observations["record_id=2"].event_time == earlier
    assert observations["record_id=1"].event_time == T0
    # The cursor tracks the maximum seen, not the last seen.
    assert stream.last_event_time == T0


async def test_incremental_sync_reads_only_new_rows(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    await insert_rows(
        source_table,
        [{"record_id": 1, "reference": "first", "status": "s", "observed_at": T0}],
    )

    organization = await make_organization(db)
    source = await make_source(db, organization)
    stream = await make_stream(db, organization, source, source_table)

    await run_sync(
        db,
        source=source,
        streams=[stream],
        connector_builder=connector_builder(),
        idempotency_key="inc-1",
        incremental=True,
    )

    await insert_rows(
        source_table,
        [
            {
                "record_id": 2,
                "reference": "second",
                "status": "s",
                "observed_at": T0 + timedelta(hours=1),
            }
        ],
    )

    second = await run_sync(
        db,
        source=source,
        streams=[stream],
        connector_builder=connector_builder(),
        idempotency_key="inc-2",
        incremental=True,
    )

    # The boundary row is re-read (the filter is >=) and skipped by
    # fingerprint; only the genuinely new row is created.
    assert second.run.rows_created == 1


# --- Event-time semantics --------------------------------------------------


async def test_ingest_fallback_uses_ingestion_time(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    """Permitted only when the stream declares it has no usable time column."""
    await insert_rows(
        source_table,
        [{"record_id": 1, "reference": "R", "status": "s", "observed_at": T0}],
    )

    organization = await make_organization(db)
    source = await make_source(db, organization)
    stream = await make_stream(
        db,
        organization,
        source,
        source_table,
        event_time_column=None,
        semantics="ingest_fallback",
    )

    await run_sync(
        db,
        source=source,
        streams=[stream],
        connector_builder=connector_builder(),
        idempotency_key="fallback",
        incremental=False,
    )

    observation = (await observations_for(db, organization))[0]

    assert observation.event_time_semantics == "ingest_fallback"
    # Ingestion time stands in, and is recorded as such rather than pretending
    # to be an observed time.
    assert observation.event_time == observation.ingested_at


# --- Multi-tenancy ---------------------------------------------------------


async def test_two_organizations_reading_the_same_table_stay_separate(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    """Same source table, two tenants, no bleed in either direction."""
    await insert_rows(
        source_table,
        [{"record_id": 1, "reference": "shared", "status": "s", "observed_at": T0}],
    )

    org_a = await make_organization(db, "Tenant A")
    org_b = await make_organization(db, "Tenant B")
    source_a = await make_source(db, org_a)
    source_b = await make_source(db, org_b)
    stream_a = await make_stream(db, org_a, source_a, source_table)
    stream_b = await make_stream(db, org_b, source_b, source_table)

    for source, stream, key in ((source_a, stream_a, "a"), (source_b, stream_b, "b")):
        await run_sync(
            db,
            source=source,
            streams=[stream],
            connector_builder=connector_builder(),
            idempotency_key=key,
            incremental=False,
        )

    a_observations = await observations_for(db, org_a)
    b_observations = await observations_for(db, org_b)

    assert len(a_observations) == 1
    assert len(b_observations) == 1
    assert a_observations[0].organization_id == org_a.id
    assert b_observations[0].organization_id == org_b.id
    # Same source row, but different stream ids, so different fingerprints —
    # neither tenant's idempotency can suppress the other's ingestion.
    assert a_observations[0].fingerprint != b_observations[0].fingerprint


# --- Concurrency -----------------------------------------------------------


async def test_a_second_sync_is_refused_while_one_is_running(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    """The advisory lock, held for real.

    Uses a separate session because the lock is session-scoped: two syncs on
    one connection would both hold it trivially.
    """
    from app.db.session import get_sessionmaker
    from app.ingestion.locks import SyncAlreadyRunningError, source_sync_lock

    await insert_rows(
        source_table,
        [{"record_id": 1, "reference": "R", "status": "s", "observed_at": T0}],
    )

    organization = await make_organization(db)
    source = await make_source(db, organization)
    await make_stream(db, organization, source, source_table)
    await db.commit()

    async with get_sessionmaker()() as holder:
        async with source_sync_lock(holder, source.id):
            # A different session must not be able to take the same lock.
            async with get_sessionmaker()() as contender:
                with pytest.raises(SyncAlreadyRunningError):
                    async with source_sync_lock(contender, source.id):
                        pass  # pragma: no cover - unreachable if the lock works


async def test_a_contended_sync_is_recorded_as_skipped(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    """Skipped, not failed — nothing went wrong."""
    from app.db.session import get_sessionmaker
    from app.ingestion.locks import source_sync_lock

    await insert_rows(
        source_table,
        [{"record_id": 1, "reference": "R", "status": "s", "observed_at": T0}],
    )

    organization = await make_organization(db)
    source = await make_source(db, organization)
    stream = await make_stream(db, organization, source, source_table)
    await db.commit()

    async with get_sessionmaker()() as holder:
        async with source_sync_lock(holder, source.id):
            outcome = await run_sync(
                db,
                source=source,
                streams=[stream],
                connector_builder=connector_builder(),
                idempotency_key="contended",
                incremental=False,
            )

    assert outcome.run.status == SyncStatus.SKIPPED.value
    assert outcome.run.error_code == "already_running"


async def test_concurrent_syncs_never_duplicate_observations(
    require_source_db: None, source_table: SourceTable
) -> None:
    """Even if the lock were bypassed, the unique index holds the line.

    Two syncs run at once on separate sessions; whatever interleaving occurs,
    the fingerprint constraint means each row is stored exactly once.

    Deliberately does not use the `db` fixture. That fixture wraps the test in
    a transaction it rolls back, so nothing it writes is visible to another
    connection — and this test needs genuinely separate sessions to be
    concurrent at all. It therefore commits, and cleans up after itself.
    """
    from app.db.session import get_sessionmaker

    await insert_rows(
        source_table,
        [
            {"record_id": i, "reference": f"R{i}", "status": "s", "observed_at": T0}
            for i in range(1, 6)
        ],
    )

    async with get_sessionmaker()() as setup:
        organization = await make_organization(setup, "Concurrency Org")
        source = await make_source(setup, organization)
        stream = await make_stream(setup, organization, source, source_table)
        await setup.commit()
        organization_id, source_id, stream_id = organization.id, source.id, stream.id

    async def sync_once(key: str) -> None:
        async with get_sessionmaker()() as session:
            # Scoped selects, not session.get(): a primary-key lookup does not
            # constrain organization_id, and the tenancy guard rejects it —
            # correctly, even here.
            local_source = await session.scalar(
                select(DataSource).where(
                    DataSource.organization_id == organization_id,
                    DataSource.id == source_id,
                )
            )
            local_stream = await session.scalar(
                select(SourceStream).where(
                    SourceStream.organization_id == organization_id,
                    SourceStream.id == stream_id,
                )
            )
            assert local_source is not None and local_stream is not None
            await run_sync(
                session,
                source=local_source,
                streams=[local_stream],
                connector_builder=connector_builder(),
                idempotency_key=key,
                incremental=False,
            )
            await session.commit()

    try:
        await asyncio.gather(sync_once("concurrent-a"), sync_once("concurrent-b"))

        async with get_sessionmaker()() as verify:
            total = await verify.scalar(
                select(func.count())
                .select_from(Observation)
                .where(Observation.organization_id == organization_id)
            )
        assert total == 5
    finally:
        async with get_sessionmaker()() as cleanup:
            await cleanup.execute(delete(Organization).where(Organization.id == organization_id))
            await cleanup.commit()


# --- Error handling --------------------------------------------------------


async def test_a_dropped_table_fails_the_run_without_crashing(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    organization = await make_organization(db)
    source = await make_source(db, organization)
    stream = SourceStream(
        organization_id=organization.id,
        data_source_id=source.id,
        schema_name="public",
        table_name="table_that_does_not_exist",
        primary_key_columns=["id"],
        event_time_column=None,
        event_time_semantics="ingest_fallback",
    )
    db.add(stream)
    await db.flush()

    outcome = await run_sync(
        db,
        source=source,
        streams=[stream],
        connector_builder=connector_builder(),
        idempotency_key="missing-table",
        incremental=False,
    )

    assert outcome.run.status == SyncStatus.FAILED.value
    assert outcome.run.error_code == ConnectorErrorCode.NOT_FOUND.value
    assert source.status == SourceStatus.ERROR.value
    # Sanitised: no driver text.
    assert "psycopg" not in (outcome.run.error_message or "")


async def test_selecting_an_unreadable_table_is_a_permission_error(
    require_source_db: None, source_table: SourceTable
) -> None:
    """The reader role's limits are real, not simulated."""
    await execute_on_source(source_table, "REVOKE SELECT ON {table} FROM realitysync_reader")

    connector = build_connector(
        kind="postgresql", config=reader_config(), credentials=reader_credentials()
    )
    selector = StreamSelector(
        schema_name=source_table.schema_name,
        table_name=source_table.table_name,
        primary_key_columns=("record_id",),
    )

    with pytest.raises(ConnectorError) as exc_info:
        async with connector:
            async for _ in connector.fetch_data(selector):
                pass  # pragma: no cover

    assert exc_info.value.code is ConnectorErrorCode.PERMISSION_DENIED
