"""Sync — real source rows become real observations.

The vertical slice: connect to a customer's database, read a configured table,
normalise the values, and append canonical observations.

What this deliberately does not do: compute reality state, score confidence,
detect conflicts. Observations are recorded exactly as the source stated them.
Everything interpretive is a later phase reading this table.

Idempotency is a database property, not an application check. Every observation
carries a fingerprint over its identity-bearing parts, ``observations`` is
unique on ``(stream_id, fingerprint)``, and inserts use ``ON CONFLICT DO
NOTHING``. Re-running a sync inserts nothing; two concurrent syncs cannot both
insert the same row. A read-then-write check would have a race between the read
and the write, however narrow.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import DataConnector
from app.connectors.types import ConnectorError, ConnectorErrorCode, StreamSelector
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.ingestion.fingerprint import FINGERPRINT_VERSION, compute_fingerprint
from app.ingestion.locks import SyncAlreadyRunningError, source_sync_lock
from app.ingestion.normalization import normalize_row
from app.models.data_source import DataSource, SourceStatus
from app.models.observation import EntityMappingState, Observation
from app.models.source_stream import EventTimeSemantics, SourceStream
from app.models.sync_run import SyncRun, SyncStatus

logger = get_logger(__name__)

#: Rows buffered before an insert round trip.
_INSERT_BATCH = 500


@dataclass(slots=True)
class StreamSyncResult:
    stream_id: uuid.UUID
    qualified_name: str
    rows_seen: int = 0
    rows_created: int = 0
    rows_skipped: int = 0
    max_event_time: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "stream_id": str(self.stream_id),
            "table": self.qualified_name,
            "rows_seen": self.rows_seen,
            "rows_created": self.rows_created,
            "rows_skipped": self.rows_skipped,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


@dataclass(slots=True)
class SyncOutcome:
    run: SyncRun
    streams: list[StreamSyncResult] = field(default_factory=list)


def build_selector(stream: SourceStream, *, incremental: bool, limit: int) -> StreamSelector:
    """Translate a stored stream into a connector-facing selector.

    The connector never sees an ORM object, which is what keeps it independent
    of the application's persistence layer.
    """
    return StreamSelector(
        schema_name=stream.schema_name,
        table_name=stream.table_name,
        primary_key_columns=tuple(stream.primary_key_columns),
        event_time_column=stream.event_time_column,
        selected_columns=tuple(stream.selected_columns or ()),
        # Only meaningful with a real event-time column; an ingest_fallback
        # stream has nothing on the source to filter by.
        since_event_time=(
            stream.last_event_time
            if incremental and stream.event_time_column and stream.last_event_time
            else None
        ),
        limit=limit,
    )


def _resolve_event_time(
    *, record_event_time: datetime | None, semantics: EventTimeSemantics, ingested_at: datetime
) -> datetime:
    """Decide the observation's event time.

    Falling back to ingestion time is permitted **only** when the stream is
    configured as ``ingest_fallback`` — that configuration is the operator
    stating the table has no usable time column. Substituting ingestion time
    for a missing value on a stream that claims to have observed times would
    silently fabricate a timestamp, and nothing downstream could tell.
    """
    if record_event_time is not None:
        return record_event_time
    if semantics is EventTimeSemantics.INGEST_FALLBACK:
        return ingested_at
    raise ConnectorError(
        ConnectorErrorCode.QUERY_FAILED,
        "A row is missing its event-time value.",
        detail="null event_time on a stream not configured for ingest_fallback",
        remediation=(
            "The event-time column contains NULLs. Choose a column without them, "
            "or set the stream's event time to 'ingest_fallback'."
        ),
    )


async def sync_stream(
    db: AsyncSession,
    *,
    connector: DataConnector,
    source: DataSource,
    stream: SourceStream,
    run: SyncRun,
    settings: Settings | None = None,
    incremental: bool = True,
) -> StreamSyncResult:
    """Ingest one stream. Returns counts; does not commit."""
    settings = settings or get_settings()
    result = StreamSyncResult(stream_id=stream.id, qualified_name=stream.qualified_name)

    selector = build_selector(
        stream, incremental=incremental, limit=settings.connector_max_rows_per_sync
    )
    semantics = stream.semantics

    provenance_base = {
        "connector": connector.kind,
        "connector_version": connector.version,
        "schema": stream.schema_name,
        "table": stream.table_name,
        "primary_key": list(stream.primary_key_columns),
        "event_time_column": stream.event_time_column,
        "fingerprint_version": FINGERPRINT_VERSION,
        "sync_run_id": str(run.id),
    }

    pending: list[dict[str, object]] = []

    async def flush() -> None:
        """Insert the buffer, ignoring rows already present."""
        if not pending:
            return
        statement = (
            pg_insert(Observation)
            .values(pending)
            # The idempotency mechanism. The unique index does the work, so two
            # concurrent syncs cannot both insert the same observation.
            .on_conflict_do_nothing(constraint="uq_observations_stream_fingerprint")
            .returning(Observation.id)
        )
        inserted = (await db.execute(statement)).scalars().all()
        result.rows_created += len(inserted)
        result.rows_skipped += len(pending) - len(inserted)
        pending.clear()

    async for record in connector.fetch_data(selector):
        ingested_at = datetime.now(UTC)
        event_time = _resolve_event_time(
            record_event_time=record.event_time,
            semantics=semantics,
            ingested_at=ingested_at,
        )

        payload = normalize_row(record.values)
        fingerprint = compute_fingerprint(
            source_id=source.id,
            stream_id=stream.id,
            external_id=record.external_id,
            event_time=event_time,
            event_time_semantics=semantics.value,
            payload=payload,
        )

        pending.append(
            {
                "organization_id": source.organization_id,
                "source_id": source.id,
                "stream_id": stream.id,
                "external_id": record.external_id,
                # MVP entity resolution is deterministic and manual, so nothing
                # is mapped yet. Recorded honestly rather than guessed: an
                # invented identity would merge two real things irreversibly.
                "entity_id": None,
                "entity_mapping_state": EntityMappingState.UNMAPPED.value,
                "payload": payload,
                "event_time": event_time,
                "event_time_semantics": semantics.value,
                "ingested_at": ingested_at,
                "fingerprint": fingerprint,
                "provenance": {**provenance_base, "external_id": record.external_id},
            }
        )
        result.rows_seen += 1

        # Tracked across every row, including out-of-order ones: the high-water
        # mark must be the maximum seen, not the last seen, or a late arrival
        # would move the cursor backwards and re-read everything after it.
        if result.max_event_time is None or event_time > result.max_event_time:
            result.max_event_time = event_time

        if len(pending) >= _INSERT_BATCH:
            await flush()

    await flush()

    stream.last_synced_at = datetime.now(UTC)
    if result.max_event_time is not None:
        # Never moves backwards, so a late-arriving row cannot cause the next
        # incremental read to re-scan history.
        if stream.last_event_time is None or result.max_event_time > stream.last_event_time:
            stream.last_event_time = result.max_event_time

    logger.info(
        "sync.stream_completed",
        stream_id=str(stream.id),
        table=stream.qualified_name,
        rows_seen=result.rows_seen,
        rows_created=result.rows_created,
        rows_skipped=result.rows_skipped,
    )
    return result


async def run_sync(
    db: AsyncSession,
    *,
    source: DataSource,
    streams: list[SourceStream],
    connector_builder: object,
    idempotency_key: str,
    triggered_by_user_id: uuid.UUID | None = None,
    settings: Settings | None = None,
    incremental: bool = True,
) -> SyncOutcome:
    """Run a sync over `streams`, recording a sync run either way.

    `connector_builder` is an awaitable returning a connected DataConnector.
    Injected rather than constructed here so this function has no dependency on
    the connector registry, credential decryption or any particular source
    type — which is what makes it testable with a fake and extensible to new
    connectors without modification.
    """
    settings = settings or get_settings()

    # Reuse an existing run for the same key, so a client that retries after a
    # timeout observes the original rather than starting a second sync.
    existing = await db.scalar(
        select(SyncRun).where(
            SyncRun.source_id == source.id,
            SyncRun.organization_id == source.organization_id,
            SyncRun.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        return SyncOutcome(run=existing)

    run = SyncRun(
        organization_id=source.organization_id,
        source_id=source.id,
        stream_id=streams[0].id if len(streams) == 1 else None,
        status=SyncStatus.PENDING.value,
        idempotency_key=idempotency_key,
        triggered_by_user_id=triggered_by_user_id,
        started_at=datetime.now(UTC),
    )
    db.add(run)
    await db.flush()

    try:
        async with source_sync_lock(db, source.id):
            run.status = SyncStatus.RUNNING.value
            await db.flush()
            outcome = await _execute(
                db,
                source=source,
                streams=streams,
                run=run,
                connector_builder=connector_builder,
                settings=settings,
                incremental=incremental,
            )
    except SyncAlreadyRunningError:
        # Not a failure: another sync is doing this work right now.
        run.status = SyncStatus.SKIPPED.value
        run.completed_at = datetime.now(UTC)
        run.error_code = "already_running"
        run.error_message = "A sync is already running for this source."
        await db.flush()
        return SyncOutcome(run=run)

    return outcome


async def _execute(
    db: AsyncSession,
    *,
    source: DataSource,
    streams: list[SourceStream],
    run: SyncRun,
    connector_builder: object,
    settings: Settings,
    incremental: bool,
) -> SyncOutcome:
    """Run the streams with the lock held."""
    results: list[StreamSyncResult] = []
    connector: DataConnector | None = None

    try:
        connector = await connector_builder()  # type: ignore[operator]

        for stream in streams:
            try:
                results.append(
                    await sync_stream(
                        db,
                        connector=connector,
                        source=source,
                        stream=stream,
                        run=run,
                        settings=settings,
                        incremental=incremental,
                    )
                )
            except ConnectorError as exc:
                # One stream failing does not abandon the rest: a dropped table
                # should not stop every other table on the source from syncing.
                logger.warning(
                    "sync.stream_failed",
                    stream_id=str(stream.id),
                    code=exc.code.value,
                    detail=exc.detail,
                )
                results.append(
                    StreamSyncResult(
                        stream_id=stream.id,
                        qualified_name=stream.qualified_name,
                        error_code=exc.code.value,
                        error_message=exc.message,
                    )
                )

        _finalise(run, source, results)

    except ConnectorError as exc:
        # The connection itself failed, so no stream ran.
        logger.warning(
            "sync.failed", source_id=str(source.id), code=exc.code.value, detail=exc.detail
        )
        run.status = SyncStatus.FAILED.value
        run.completed_at = datetime.now(UTC)
        run.error_code = exc.code.value
        run.error_message = exc.message
        source.status = SourceStatus.ERROR.value
        source.last_error = exc.message
        source.last_error_at = datetime.now(UTC)
    finally:
        if connector is not None:
            await connector.disconnect()

    run.details = {"streams": [r.as_dict() for r in results]}
    await db.flush()
    return SyncOutcome(run=run, streams=results)


def _finalise(run: SyncRun, source: DataSource, results: list[StreamSyncResult]) -> None:
    """Set the run's terminal state from its stream results."""
    run.rows_seen = sum(r.rows_seen for r in results)
    run.rows_created = sum(r.rows_created for r in results)
    run.rows_skipped = sum(r.rows_skipped for r in results)
    run.completed_at = datetime.now(UTC)

    failed = [r for r in results if r.error_code]
    if failed and len(failed) == len(results):
        # Everything failed: the run failed.
        run.status = SyncStatus.FAILED.value
        run.error_code = failed[0].error_code
        run.error_message = failed[0].error_message
        source.status = SourceStatus.ERROR.value
        source.last_error = failed[0].error_message
        source.last_error_at = run.completed_at
        return

    run.status = SyncStatus.COMPLETED.value
    if failed:
        # Partial success. Recorded on the run so the interface can say which
        # streams failed rather than reporting a clean success.
        run.error_code = "partial_failure"
        run.error_message = f"{len(failed)} of {len(results)} streams failed."

    source.status = SourceStatus.CONNECTED.value
    source.last_synced_at = run.completed_at
    source.last_error = None
    source.last_error_at = None
