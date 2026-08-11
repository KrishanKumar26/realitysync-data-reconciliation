"""Scheduled background syncing.

``SourceStream.poll_interval_seconds`` has existed since Phase 3 with a
minimum of 30 and a default of 300, and nothing has ever read it. A schema
field that promises polling and delivers none is worse than no field: it tells
an operator their source refreshes every five minutes when in fact it refreshes
only when someone presses a button.

This is what reads it.

**Multi-instance safety.** Every API process runs this loop, so two instances
will regularly decide the same stream is due at the same moment. Two things
stop that becoming two syncs:

1. The PostgreSQL advisory lock in ``run_sync`` — a second attempt is told
   "already running" and records a SKIPPED run rather than queueing.
2. A deterministic idempotency key derived from the due window, so two
   instances computing "this source was due at T" produce the same key and the
   second reuses the first's run.

Neither is a substitute for the other. The lock handles overlap in time; the
key handles a retry after the first attempt has already finished.

**Failure is per-source.** One unreachable customer database must not stop
every other tenant's syncing, so each source is attempted independently and a
failure is recorded and logged rather than raised.

**The scheduler is not authoritative.** If it never runs, nothing is wrong —
data is merely staler than configured, manual sync still works, and no
observation is lost. That is why it degrades quietly and reports its state
rather than failing the process.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.db.tenancy import unscoped
from app.ingestion.connection import open_connector
from app.ingestion.sync import run_sync
from app.models.data_source import DataSource, SourceStatus
from app.models.source_stream import SourceStream

logger = get_logger(__name__)


@dataclass
class SchedulerState:
    """What the loop has actually done, for the status endpoint.

    Held in memory and deliberately not persisted: it describes this process,
    and a restart genuinely has done nothing yet.
    """

    running: bool = False
    started_at: datetime | None = None
    last_tick_at: datetime | None = None
    ticks: int = 0
    sources_synced: int = 0
    failures: int = 0
    last_error: str | None = None


_state = SchedulerState()


def scheduler_state() -> SchedulerState:
    return _state


#: Sentinel for "due since forever" — a stream that has never synced. Earlier
#: than any real timestamp, so it sorts first and yields a stable key.
_NEVER = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class DueSource:
    """A source with at least one stream past its poll interval.

    Deliberately identifiers rather than ORM objects. The scheduler discovers
    due work in one session and performs it in another, and an ORM instance
    carried across that boundary is *detached*: mutations to it — including
    ``last_synced_at``, which is what makes the next poll interval start —
    are silently dropped instead of persisted. Passing ids forces the working
    session to load its own rows, which is also what lets the sync be
    tenant-scoped even though the discovery query cannot be.
    """

    source_id: uuid.UUID
    organization_id: uuid.UUID
    stream_ids: tuple[uuid.UUID, ...]
    #: The earliest moment any of these streams became due. Diagnostic only —
    #: deliberately *not* the idempotency key. See attempt_window.
    due_since: datetime = _NEVER
    #: The scheduling window this attempt belongs to: the current time floored
    #: to the source's shortest poll interval. This is what the idempotency key
    #: is built from.
    #:
    #: Deriving the key from the *cursor* instead was a bug worth recording.
    #: An attempt that does not advance last_synced_at — a failure, a crash, a
    #: source that was unreachable — leaves the next attempt computing an
    #: identical key, which run_sync correctly treats as a retry of a request
    #: it has already answered. It returns the old run and does no work. The
    #: source then never syncs again, and every log line says "completed",
    #: because the completed run being reported is the old one.
    #:
    #: A floored window fixes it without giving up the property the key exists
    #: for: two instances ticking seconds apart land in the same window and
    #: still produce one run, while the window itself always advances, so a
    #: failure is retried on the next one.
    attempt_window: datetime = _NEVER


async def find_due_sources(db: AsyncSession, *, now: datetime) -> list[DueSource]:
    """Streams whose poll interval has elapsed, grouped by source.

    Cross-tenant by definition: the scheduler serves every organization, so
    there is no single organization to scope to. This is one of the rare
    legitimate uses of ``unscoped()`` — see app/db/tenancy.py. The query still
    only reads scheduling metadata, and the sync it leads to is scoped to the
    owning organization through the source itself.
    """
    with unscoped():
        rows = list(
            await db.execute(
                select(SourceStream, DataSource)
                .join(DataSource, DataSource.id == SourceStream.data_source_id)
                .where(
                    SourceStream.enabled.is_(True),
                    # A source that has never connected has nothing to poll,
                    # and a disabled one was switched off on purpose.
                    DataSource.status != SourceStatus.DISABLED.value,
                )
                .order_by(SourceStream.data_source_id, SourceStream.created_at)
            )
        )

    grouped: dict[uuid.UUID, _Grouped] = {}
    for stream, source in rows:
        due_at = _due_at(stream)
        if due_at > now:
            continue
        entry = grouped.get(source.id)
        if entry is None:
            grouped[source.id] = _Grouped(
                organization_id=source.organization_id,
                stream_ids=[stream.id],
                earliest_due=due_at,
                shortest_interval=stream.poll_interval_seconds,
            )
        else:
            entry.stream_ids.append(stream.id)
            entry.earliest_due = min(entry.earliest_due, due_at)
            # The shortest interval wins: a source with a 30-second stream and
            # an hourly one must be attemptable every 30 seconds.
            entry.shortest_interval = min(entry.shortest_interval, stream.poll_interval_seconds)

    return [
        DueSource(
            source_id=source_id,
            organization_id=entry.organization_id,
            stream_ids=tuple(entry.stream_ids),
            due_since=entry.earliest_due,
            attempt_window=floor_to_window(now, entry.shortest_interval),
        )
        for source_id, entry in grouped.items()
    ]


@dataclass
class _Grouped:
    """Accumulator while grouping streams onto their source."""

    organization_id: uuid.UUID
    stream_ids: list[uuid.UUID]
    earliest_due: datetime
    shortest_interval: int


def floor_to_window(moment: datetime, interval_seconds: int) -> datetime:
    """Floor `moment` to a multiple of `interval_seconds` since the epoch.

    Absolute rather than relative to anything stored, so two processes compute
    the same window from their own clocks without coordinating.
    """
    interval = max(interval_seconds, 1)
    epoch_seconds = int(moment.timestamp())
    return datetime.fromtimestamp(epoch_seconds - (epoch_seconds % interval), tz=UTC)


def _due_at(stream: SourceStream) -> datetime:
    """When this stream next becomes eligible.

    A stream that has never synced is due immediately: the interval measures
    time since the last sync, and there has not been one.
    """
    if stream.last_synced_at is None:
        return _NEVER
    last = stream.last_synced_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return last + timedelta(seconds=stream.poll_interval_seconds)


def scheduled_idempotency_key(source_id: uuid.UUID, attempt_window: datetime) -> str:
    """The key for one source's attempt in one scheduling window.

    Two instances ticking seconds apart fall in the same window and produce the
    same key, so only one of them does the work. The window always advances,
    so an attempt that failed is retried rather than mistaken for one already
    answered.
    """
    return f"scheduled:{source_id}:{attempt_window.isoformat()}"


async def sync_due_source(db: AsyncSession, due: DueSource) -> str:
    """Run one due source. Returns the resulting run status.

    Loads its own rows, scoped to the owning organization. The discovery query
    spans tenants because a background loop has no single one; the work itself
    does not, and is filtered exactly as a request-scoped query would be.
    """
    source = await db.scalar(
        select(DataSource).where(
            DataSource.organization_id == due.organization_id,
            DataSource.id == due.source_id,
        )
    )
    streams = list(
        await db.scalars(
            select(SourceStream)
            .where(
                SourceStream.organization_id == due.organization_id,
                SourceStream.id.in_(due.stream_ids),
            )
            .order_by(SourceStream.created_at)
        )
    )
    if source is None or not streams:
        # Deleted or disabled between discovery and now. Not an error: the
        # next tick will simply not find it.
        logger.info("scheduler.source_vanished", source_id=str(due.source_id))
        return "skipped"

    async def builder() -> object:
        return await open_connector(db, source)

    outcome = await run_sync(
        db,
        source=source,
        streams=streams,
        connector_builder=builder,
        idempotency_key=scheduled_idempotency_key(due.source_id, due.attempt_window),
        # No user: this run was not triggered by a person, and recording a
        # nominal one would put a false name in the audit trail.
        triggered_by_user_id=None,
        incremental=True,
    )
    await db.commit()
    return str(outcome.run.status)


async def tick(settings: Settings | None = None) -> int:
    """One scheduling pass. Returns the number of sources attempted.

    Each source gets its own database session. Sharing one would mean a single
    failed sync's rollback discarded the work of every source before it in the
    same pass.
    """
    settings = settings or get_settings()
    now = datetime.now(UTC)

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as db:
        due = await find_due_sources(db, now=now)

    if not due:
        return 0

    # Bounded, so a deployment with many due sources spreads the load across
    # ticks instead of opening every customer connection at once.
    limit = settings.sync_scheduler_max_sources_per_tick
    if len(due) > limit:
        logger.info("scheduler.deferred", due=len(due), attempting=limit)
        due = due[:limit]

    attempted = 0
    for item in due:
        attempted += 1
        async with sessionmaker() as db:
            try:
                status = await sync_due_source(db, item)
                _state.sources_synced += 1
                logger.info(
                    "scheduler.synced",
                    source_id=str(item.source_id),
                    streams=len(item.stream_ids),
                    status=status,
                )
            except Exception as exc:
                # Per-source isolation: one unreachable customer database must
                # not stop every other tenant from syncing.
                await db.rollback()
                _state.failures += 1
                _state.last_error = type(exc).__name__
                logger.warning(
                    "scheduler.source_failed",
                    source_id=str(item.source_id),
                    error_type=type(exc).__name__,
                )
    return attempted


async def run_scheduler(settings: Settings | None = None) -> None:
    """The loop. Runs until cancelled."""
    settings = settings or get_settings()
    _state.running = True
    _state.started_at = datetime.now(UTC)
    logger.info(
        "scheduler.started",
        tick_seconds=settings.sync_scheduler_tick_seconds,
        max_sources_per_tick=settings.sync_scheduler_max_sources_per_tick,
    )
    try:
        while True:
            try:
                await tick(settings)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # The loop itself must survive anything a single pass throws,
                # including a database outage. A scheduler that dies on the
                # first bad tick silently stops refreshing every source in the
                # deployment, and nothing would report it.
                _state.failures += 1
                _state.last_error = type(exc).__name__
                logger.warning("scheduler.tick_failed", error_type=type(exc).__name__)
            _state.ticks += 1
            _state.last_tick_at = datetime.now(UTC)
            await asyncio.sleep(settings.sync_scheduler_tick_seconds)
    except asyncio.CancelledError:
        logger.info("scheduler.stopped", ticks=_state.ticks, synced=_state.sources_synced)
        raise
    finally:
        _state.running = False


def start_scheduler(settings: Settings) -> asyncio.Task[None] | None:
    """Start the loop as a background task, if it is enabled."""
    if not settings.sync_scheduler_enabled:
        logger.info("scheduler.disabled")
        return None
    return asyncio.create_task(run_scheduler(settings), name="realitysync-sync-scheduler")


async def stop_scheduler(task: asyncio.Task[None] | None) -> None:
    """Cancel the loop and wait for it to finish.

    Awaited rather than fire-and-forget so a shutdown does not tear the event
    loop down underneath a sync that is mid-write.
    """
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
