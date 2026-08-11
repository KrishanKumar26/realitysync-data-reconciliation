"""The sync scheduler.

Two halves, tested differently.

The selection logic — which streams are due, how they group, what idempotency
key a due window produces — is pure and tested directly with real rows.

The end-to-end pass runs against the **real** source database through the real
connector, because "the scheduler syncs a source" is only worth asserting if
the rows it produces are real observations from a real table. There is no fake
connector in the sync path here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.ingestion.scheduler import (
    DueSource,
    find_due_sources,
    floor_to_window,
    scheduled_idempotency_key,
    scheduler_state,
    start_scheduler,
    stop_scheduler,
    sync_due_source,
)
from app.models.data_source import DataSource, SourceStatus
from app.models.observation import Observation
from app.models.organization import Organization
from app.models.source_stream import SourceStream
from app.models.sync_run import SyncRun, SyncStatus
from app.services.credentials import store_credentials
from tests.source_db import SourceTable, execute_on_source, reader_credentials
from tests.test_connector_integration import (
    T0,
    insert_rows,
    make_organization,
    make_stream,
    observations_for,
)
from tests.test_connector_integration import make_source as _make_bare_source


async def make_source(db: AsyncSession, organization, **kwargs):  # type: ignore[no-untyped-def]
    """A source with its credentials actually stored.

    The connector integration tests inject a pre-built connector, so they never
    need a stored credential. The scheduler has no caller to inject one — it
    decrypts what is on the row, exactly as production does — so these tests
    must go through the real encryption path too.
    """
    source = await _make_bare_source(db, organization, **kwargs)
    await store_credentials(db, data_source=source, payload=reader_credentials())
    await db.flush()
    return source


NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


async def _due(db: AsyncSession, *, now: datetime = NOW) -> list[DueSource]:
    return await find_due_sources(db, now=now)


async def _due_for(
    db: AsyncSession, organization: Organization, *, now: datetime = NOW
) -> list[DueSource]:
    """Due work belonging to one tenant.

    The scheduler's query is global on purpose, so a test asserting on the
    whole result would be asserting about every source in the database —
    including any a developer has genuinely configured. Scoping the assertion
    to the organization under test keeps it about the behaviour rather than
    about the machine it runs on.
    """
    return [d for d in await _due(db, now=now) if d.organization_id == organization.id]


# --- Which streams are due --------------------------------------------------


async def test_a_stream_that_never_synced_is_due_immediately(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    """The interval measures time since the last sync, and there was none."""
    organization = await make_organization(db)
    source = await make_source(db, organization)
    await make_stream(db, organization, source, source_table)
    await db.flush()

    due = await _due_for(db, organization)

    assert [d.source_id for d in due] == [source.id]


async def test_a_recently_synced_stream_is_not_due(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    organization = await make_organization(db)
    source = await make_source(db, organization)
    stream = await make_stream(db, organization, source, source_table)
    stream.poll_interval_seconds = 300
    # Synced one minute ago; the interval is five.
    stream.last_synced_at = NOW - timedelta(seconds=60)
    await db.flush()

    assert await _due_for(db, organization) == []


async def test_a_stream_past_its_interval_is_due(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    organization = await make_organization(db)
    source = await make_source(db, organization)
    stream = await make_stream(db, organization, source, source_table)
    stream.poll_interval_seconds = 300
    stream.last_synced_at = NOW - timedelta(seconds=301)
    await db.flush()

    assert [d.source_id for d in await _due_for(db, organization)] == [source.id]


async def test_the_interval_is_honoured_per_stream(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    """poll_interval_seconds is read, not assumed.

    The field has existed since Phase 3 with nothing consuming it. This is the
    assertion that keeps it from going back to being decorative.
    """
    organization = await make_organization(db)
    source = await make_source(db, organization)
    stream = await make_stream(db, organization, source, source_table)
    stream.last_synced_at = NOW - timedelta(seconds=120)

    stream.poll_interval_seconds = 300
    await db.flush()
    assert await _due_for(db, organization) == [], (
        "a 5-minute interval should not be due after 2 minutes"
    )

    stream.poll_interval_seconds = 60
    await db.flush()
    assert len(await _due_for(db, organization)) == 1, (
        "a 1-minute interval should be due after 2 minutes"
    )


async def test_disabled_streams_are_never_due(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    organization = await make_organization(db)
    source = await make_source(db, organization)
    stream = await make_stream(db, organization, source, source_table)
    stream.enabled = False
    await db.flush()

    assert await _due_for(db, organization) == []


async def test_disabled_sources_are_never_due(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    """Switched off on purpose; the scheduler must not switch it back on."""
    organization = await make_organization(db)
    source = await make_source(db, organization)
    await make_stream(db, organization, source, source_table)
    source.status = SourceStatus.DISABLED.value
    await db.flush()

    assert await _due_for(db, organization) == []


async def test_streams_of_one_source_are_grouped_into_a_single_sync(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    """One connection per source per pass, not one per stream.

    The advisory lock is per source, so two separate syncs of the same source
    would serialise anyway — the second recorded as SKIPPED and its stream left
    unsynced until the next tick.
    """
    organization = await make_organization(db)
    source = await make_source(db, organization)
    await make_stream(db, organization, source, source_table)
    # A second stream on a different table: UNIQUE(data_source_id, schema_name,
    # table_name) correctly forbids two streams on the same one. Grouping is
    # metadata-level and runs no sync, so this table need not exist in the
    # source database — nothing here reads it.
    other = SourceTable(schema_name="public", table_name=f"rs_test_{uuid.uuid4().hex[:12]}")
    await make_stream(db, organization, source, other)
    await db.flush()

    due = await _due_for(db, organization)

    assert len(due) == 1
    assert len(due[0].stream_ids) == 2


async def test_sources_across_organizations_are_all_found(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    """The scheduler serves every tenant.

    This is the query that requires ``unscoped()`` — there is no single
    organization to scope to when the caller is a background loop rather than
    a signed-in user.
    """
    first = await make_organization(db, "Scheduler Org A")
    second = await make_organization(db, "Scheduler Org B")
    source_a = await make_source(db, first)
    source_b = await make_source(db, second)
    await make_stream(db, first, source_a, source_table)
    await make_stream(db, second, source_b, source_table)
    await db.flush()

    found = {d.source_id for d in await _due(db)}

    assert {source_a.id, source_b.id} <= found


# --- Idempotency ------------------------------------------------------------


def test_two_instances_ticking_seconds_apart_share_a_window() -> None:
    """The property the key exists for: one run, not two."""
    interval = 300
    first = datetime(2026, 3, 1, 11, 57, 3, tzinfo=UTC)
    second = datetime(2026, 3, 1, 11, 59, 41, tzinfo=UTC)

    assert floor_to_window(first, interval) == floor_to_window(second, interval)


def test_the_window_advances() -> None:
    """So an attempt that failed is retried rather than deduplicated away."""
    interval = 300
    now = datetime(2026, 3, 1, 11, 57, tzinfo=UTC)

    assert floor_to_window(now, interval) != floor_to_window(
        now + timedelta(seconds=interval), interval
    )


def test_the_key_is_stable_within_a_window_and_differs_across_them() -> None:
    source_id = uuid.uuid4()
    window = datetime(2026, 3, 1, 11, 55, tzinfo=UTC)

    assert scheduled_idempotency_key(source_id, window) == scheduled_idempotency_key(
        source_id, window
    )
    assert scheduled_idempotency_key(source_id, window) != scheduled_idempotency_key(
        source_id, window + timedelta(seconds=300)
    )


def test_the_key_differs_per_source() -> None:
    window = datetime(2026, 3, 1, 11, 55, tzinfo=UTC)
    assert scheduled_idempotency_key(uuid.uuid4(), window) != scheduled_idempotency_key(
        uuid.uuid4(), window
    )


# --- Running a due source ---------------------------------------------------


async def test_a_due_source_produces_real_observations(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    """REAL TABLE -> SCHEDULER -> REAL OBSERVATIONS.

    The whole point of the phase: rows arrive without anyone pressing a button.
    """
    await insert_rows(
        source_table,
        [
            {
                "record_id": 1,
                "reference": "REF-101",
                "status": "in_transit",
                "amount": "9.750",
                "observed_at": T0,
            },
        ],
    )

    organization = await make_organization(db)
    source = await make_source(db, organization)
    stream = await make_stream(db, organization, source, source_table)
    await db.flush()

    item = next(d for d in await _due_for(db, organization) if d.source_id == source.id)
    status = await sync_due_source(db, item)

    assert status == SyncStatus.COMPLETED.value

    observations = await observations_for(db, organization)
    assert len(observations) == 1
    assert observations[0].payload["reference"] == "REF-101"
    assert observations[0].event_time == T0
    # Real provenance, not a scheduler-specific placeholder.
    assert observations[0].provenance["connector"] == "postgresql"
    assert observations[0].provenance["table"] == source_table.table_name

    _ = stream


async def test_a_scheduled_run_records_no_triggering_user(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    """Nobody triggered it, so the audit trail must not name anyone."""
    organization = await make_organization(db)
    source = await make_source(db, organization)
    await make_stream(db, organization, source, source_table)
    await db.flush()

    item = next(d for d in await _due_for(db, organization) if d.source_id == source.id)
    await sync_due_source(db, item)

    run = await db.scalar(
        select(SyncRun).where(
            SyncRun.organization_id == organization.id, SyncRun.source_id == source.id
        )
    )
    assert run is not None
    assert run.triggered_by_user_id is None
    assert run.idempotency_key.startswith("scheduled:")


async def test_the_same_due_window_does_not_sync_twice(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    """Two instances agreeing a source is due must produce one run.

    The advisory lock covers overlap in time; this covers the second instance
    arriving after the first has already finished.
    """
    await insert_rows(
        source_table,
        [{"record_id": 1, "reference": "REF-201", "status": "new", "observed_at": T0}],
    )

    organization = await make_organization(db)
    source = await make_source(db, organization)
    await make_stream(db, organization, source, source_table)
    await db.flush()

    item = next(d for d in await _due_for(db, organization) if d.source_id == source.id)
    await sync_due_source(db, item)
    # The same DueSource, exactly as a second process would have computed it.
    await sync_due_source(db, item)

    runs = list(
        await db.scalars(
            select(SyncRun).where(
                SyncRun.organization_id == organization.id, SyncRun.source_id == source.id
            )
        )
    )
    assert len(runs) == 1, "the due window's idempotency key should have been reused"
    assert len(await observations_for(db, organization)) == 1


async def test_syncing_advances_the_stream_so_it_stops_being_due(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    """Otherwise every tick would resync every source forever."""
    organization = await make_organization(db)
    source = await make_source(db, organization)
    stream = await make_stream(db, organization, source, source_table)
    stream.poll_interval_seconds = 300
    await db.flush()

    item = next(d for d in await _due_for(db, organization) if d.source_id == source.id)
    await sync_due_source(db, item)

    # Re-read scoped rather than db.refresh(): a bare refresh emits an
    # unfiltered SELECT that the tenancy guard rejects, which is the guard
    # working correctly.
    refreshed = await db.scalar(
        select(SourceStream).where(
            SourceStream.organization_id == organization.id, SourceStream.id == stream.id
        )
    )
    assert refreshed is not None and refreshed.last_synced_at is not None
    later = refreshed.last_synced_at + timedelta(seconds=60)
    still_due = [d.source_id for d in await find_due_sources(db, now=later)]
    assert source.id not in still_due


async def test_the_cursor_survives_the_session_that_wrote_it(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    """The regression test for the detached-instance bug.

    The scheduler discovers due work in one session and syncs in another. When
    DueSource carried ORM objects, ``stream.last_synced_at`` was written to a
    *detached* instance and never reached the database. Everything looked
    healthy — the loop ticked, runs completed, the log said so — while the due
    window never advanced, so the idempotency key never changed and every tick
    after the first reused the original run and read nothing. A source
    configured to poll every 30 seconds synced exactly once, forever.

    Asserting through a fresh session is the whole point: the earlier version
    of this test re-read through the same session and passed on an in-memory
    attribute that was never persisted.
    """
    organization = await make_organization(db)
    source = await make_source(db, organization)
    stream = await make_stream(db, organization, source, source_table)
    stream.poll_interval_seconds = 30
    await db.flush()

    item = next(d for d in await _due_for(db, organization) if d.source_id == source.id)
    await sync_due_source(db, item)

    # A column-only select: it reads the database directly instead of being
    # answered from the identity map, so it sees what was actually written
    # rather than what an in-memory object claims. That distinction is exactly
    # what the earlier version of this test missed.
    persisted_at = await db.scalar(
        select(SourceStream.last_synced_at).where(
            SourceStream.organization_id == organization.id,
            SourceStream.id == stream.id,
        )
    )
    assert persisted_at is not None, (
        "last_synced_at was not persisted. The due window will never advance, "
        "so every later tick reuses the first run's idempotency key and the "
        "source silently stops syncing."
    )

    # And the consequence that matters: the next due window is a different one,
    # so it produces a different idempotency key and therefore a new run.
    later = persisted_at + timedelta(seconds=31)
    next_due = next(d for d in await find_due_sources(db, now=later) if d.source_id == source.id)
    assert next_due.due_since != item.due_since
    assert scheduled_idempotency_key(
        next_due.source_id, next_due.due_since
    ) != scheduled_idempotency_key(item.source_id, item.due_since)


async def test_a_row_added_after_the_first_sync_is_picked_up(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    """Continuous, not once. The behaviour the whole phase exists to deliver."""
    await insert_rows(
        source_table,
        [{"record_id": 1, "reference": "REF-1", "status": "new", "observed_at": T0}],
    )

    organization = await make_organization(db)
    source = await make_source(db, organization)
    stream = await make_stream(db, organization, source, source_table)
    stream.poll_interval_seconds = 30
    await db.flush()

    first = next(d for d in await _due_for(db, organization) if d.source_id == source.id)
    await sync_due_source(db, first)
    assert len(await observations_for(db, organization)) == 1

    await insert_rows(
        source_table,
        [
            {
                "record_id": 2,
                "reference": "REF-2",
                "status": "new",
                "observed_at": T0 + timedelta(hours=1),
            }
        ],
    )

    persisted_at = await db.scalar(
        select(SourceStream.last_synced_at).where(
            SourceStream.organization_id == organization.id,
            SourceStream.id == stream.id,
        )
    )
    assert persisted_at is not None
    later = persisted_at + timedelta(seconds=31)

    second = next(d for d in await find_due_sources(db, now=later) if d.source_id == source.id)
    await sync_due_source(db, second)

    assert len(await observations_for(db, organization)) == 2


async def test_a_failed_attempt_is_retried_in_the_next_window(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    """The bug that made the scheduler silently stop.

    A failed sync advances nothing. When the idempotency key was derived from
    the cursor, the next attempt produced an identical key, run_sync returned
    the failed run as though the request had already been answered, and the
    source was never tried again. Every log line said "completed", because the
    run being reported was the old one.

    Here the source's table is missing, the attempt fails, the table comes
    back, and the next window must actually retry.
    """
    organization = await make_organization(db)
    source = await make_source(db, organization)
    stream = await make_stream(db, organization, source, source_table)
    stream.poll_interval_seconds = 30
    await db.flush()

    await execute_on_source(source_table, f"DROP TABLE {source_table.qualified}")

    first = next(d for d in await _due_for(db, organization) if d.source_id == source.id)
    assert await sync_due_source(db, first) == SyncStatus.FAILED.value

    # Nothing advanced, which is correct: no rows were read.
    persisted_at = await db.scalar(
        select(SourceStream.last_synced_at).where(
            SourceStream.organization_id == organization.id,
            SourceStream.id == stream.id,
        )
    )
    assert persisted_at is None

    await execute_on_source(
        source_table,
        f"CREATE TABLE {source_table.qualified} ("
        "record_id bigint PRIMARY KEY, reference text NOT NULL, status text NOT NULL, "
        "amount numeric(12,3), observed_at timestamptz NOT NULL, notes text)",
    )
    await execute_on_source(
        source_table, f"GRANT SELECT ON {source_table.qualified} TO realitysync_reader"
    )
    await insert_rows(
        source_table,
        [{"record_id": 1, "reference": "REF-RETRY", "status": "new", "observed_at": T0}],
    )

    later = NOW + timedelta(seconds=60)
    second = next(d for d in await find_due_sources(db, now=later) if d.source_id == source.id)
    assert second.attempt_window != first.attempt_window, (
        "the attempt window did not advance, so the retry would be deduplicated "
        "against the failed run and the source would never sync again"
    )

    assert await sync_due_source(db, second) == SyncStatus.COMPLETED.value
    assert len(await observations_for(db, organization)) == 1


# --- The loop ---------------------------------------------------------------


async def test_the_scheduler_can_be_started_and_stopped() -> None:
    """Shutdown is awaited, not fire-and-forget.

    A cancelled-but-unawaited task would let the event loop close underneath a
    sync that was mid-write.
    """
    settings = get_settings()
    task = start_scheduler(settings)
    assert task is not None
    try:
        assert not task.done()
    finally:
        await stop_scheduler(task)

    assert task.done()
    assert scheduler_state().running is False


async def test_the_scheduler_does_not_start_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings().model_copy(update={"sync_scheduler_enabled": False})
    assert start_scheduler(settings) is None


async def test_stopping_a_scheduler_that_never_started_is_safe() -> None:
    await stop_scheduler(None)


# --- Tenancy ----------------------------------------------------------------


async def test_the_scheduler_does_not_mix_observations_between_tenants(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    """Two organizations reading the same table stay separate.

    The scheduler's due query is cross-tenant by necessity; the sync it leads
    to must not be. Each source carries its own organization, and the
    observations must land under it.
    """
    await insert_rows(
        source_table,
        [{"record_id": 1, "reference": "REF-301", "status": "new", "observed_at": T0}],
    )

    first = await make_organization(db, "Tenant One")
    second = await make_organization(db, "Tenant Two")
    source_a = await make_source(db, first)
    source_b = await make_source(db, second)
    await make_stream(db, first, source_a, source_table)
    await make_stream(db, second, source_b, source_table)
    await db.flush()

    due = {d.source_id: d for d in await _due(db)}
    await sync_due_source(db, due[source_a.id])
    await sync_due_source(db, due[source_b.id])

    a_observations = await observations_for(db, first)
    b_observations = await observations_for(db, second)

    assert len(a_observations) == 1
    assert len(b_observations) == 1
    assert {o.organization_id for o in a_observations} == {first.id}
    assert {o.organization_id for o in b_observations} == {second.id}

    # And no observation belongs to a source of the other tenant.
    cross = await db.scalars(
        select(Observation).where(
            Observation.organization_id == first.id,
            Observation.source_id == source_b.id,
        )
    )
    assert list(cross) == []


async def test_a_stream_belongs_to_the_source_it_was_grouped_under(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    """Grouping must not attach one tenant's stream to another's source."""
    first = await make_organization(db, "Group One")
    second = await make_organization(db, "Group Two")
    source_a = await make_source(db, first)
    source_b = await make_source(db, second)
    stream_a = await make_stream(db, first, source_a, source_table)
    stream_b = await make_stream(db, second, source_b, source_table)
    await db.flush()

    due = {d.source_id: d for d in await _due(db)}

    assert due[source_a.id].organization_id == first.id
    assert due[source_a.id].stream_ids == (stream_a.id,)
    assert due[source_b.id].organization_id == second.id
    assert due[source_b.id].stream_ids == (stream_b.id,)


async def test_due_work_carries_identifiers_not_orm_objects(
    require_source_db: None, source_table: SourceTable, db: AsyncSession
) -> None:
    """The structural guard against the detached-instance bug.

    The scheduler discovers due work in one session and performs it in another.
    An ORM instance carried across that boundary is detached, and writing to it
    — ``last_synced_at`` above all — is silently discarded. Keeping DueSource
    free of ORM objects makes that mistake impossible to make again.
    """
    organization = await make_organization(db)
    source = await make_source(db, organization)
    stream = await make_stream(db, organization, source, source_table)
    await db.flush()

    item = next(d for d in await _due_for(db, organization) if d.source_id == source.id)

    assert item.stream_ids == (stream.id,)
    assert item.organization_id == organization.id
    assert not isinstance(item.source_id, DataSource)
    assert all(not isinstance(value, SourceStream) for value in item.stream_ids)
