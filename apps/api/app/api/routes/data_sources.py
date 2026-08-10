"""Data source, stream and sync routes.

Every route takes :data:`~app.api.deps.CurrentOrganization`, so the tenant id
is part of the handler's signature and comes from the session rather than the
request. Combined with the tenancy guard, a query that forgets to scope raises
instead of returning another tenant's rows.

No route in this module can return a credential: the response models have no
field for one, and the only function that decrypts is called from the connector
factory path.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select

from app.api.deps import (
    AppSettings,
    CurrentOrganization,
    DbSession,
    RequireAdmin,
    enforce_csrf,
)
from app.connectors.base import DataConnector
from app.connectors.registry import build_connector
from app.connectors.types import ConnectorError, DiscoveredSchema
from app.core.logging import get_logger
from app.ingestion.sync import run_sync
from app.models.data_source import DataSource, SourceKind, SourceStatus
from app.models.observation import Observation
from app.models.source_stream import EventTimeSemantics, SourceStream
from app.models.sync_run import SyncRun, SyncStatus
from app.schemas.data_source import (
    ColumnResponse,
    ConnectionSummary,
    ConnectionTestResponse,
    ConnectorHealthResponse,
    CreateDataSourceRequest,
    CreateStreamRequest,
    DataSourceResponse,
    ObservationResponse,
    SchemaDiscoveryResponse,
    StreamResponse,
    SyncRequest,
    SyncRunResponse,
    SyncStreamDetail,
    TableResponse,
    UpdateStreamRequest,
)
from app.services import audit
from app.services.credentials import load_credentials, store_credentials

logger = get_logger(__name__)

router = APIRouter(prefix="/data-sources", tags=["data sources"])


# --- Helpers ---------------------------------------------------------------


def _connector_error_to_http(exc: ConnectorError) -> HTTPException:
    """Translate a connector failure into an HTTP error.

    ``exc.message`` and ``exc.remediation`` are written for the operator and
    are safe to return. ``exc.detail`` holds driver text and stays server-side.
    """
    status_by_code = {
        "invalid_configuration": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "not_found": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "authentication_failed": status.HTTP_400_BAD_REQUEST,
        "permission_denied": status.HTTP_400_BAD_REQUEST,
        "tls_failed": status.HTTP_400_BAD_REQUEST,
        "unreachable": status.HTTP_502_BAD_GATEWAY,
        "timeout": status.HTTP_504_GATEWAY_TIMEOUT,
    }
    message = exc.message
    if exc.remediation:
        message = f"{message} {exc.remediation}"
    return HTTPException(
        status_code=status_by_code.get(exc.code.value, status.HTTP_502_BAD_GATEWAY),
        detail=message,
    )


async def _load_source(
    db: DbSession, *, context: CurrentOrganization, source_id: uuid.UUID
) -> DataSource:
    """Fetch a source within the caller's organization, or 404.

    404 rather than 403 for a source in another organization: whether an id
    exists elsewhere is not something a caller should be able to probe.
    """
    source = await db.scalar(
        select(DataSource).where(
            DataSource.organization_id == context.organization_id,
            DataSource.id == source_id,
        )
    )
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data source not found.")
    return source


async def _load_stream(
    db: DbSession,
    *,
    context: CurrentOrganization,
    source: DataSource,
    stream_id: uuid.UUID,
) -> SourceStream:
    stream = await db.scalar(
        select(SourceStream).where(
            SourceStream.organization_id == context.organization_id,
            SourceStream.data_source_id == source.id,
            SourceStream.id == stream_id,
        )
    )
    if stream is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stream not found.")
    return stream


async def _open_connector(db: DbSession, source: DataSource) -> DataConnector:
    """Build and connect a connector for `source`.

    The only path that decrypts a credential. The plaintext exists as a local
    for the duration of one call and is handed straight to the factory.
    """
    credentials = await load_credentials(db, data_source=source)
    connector = build_connector(kind=source.kind, config=source.config, credentials=credentials)
    await connector.connect()
    return connector


def _connection_summary(source: DataSource) -> ConnectionSummary:
    config: dict[str, Any] = source.config or {}
    return ConnectionSummary(
        host=str(config.get("host", "")),
        port=int(config.get("port", 5432)),
        database=str(config.get("database", "")),
        username=str(config.get("username", "")),
        ssl_mode=str(config.get("ssl_mode", "require")),
        password_set=True,
    )


async def _source_response(
    db: DbSession, source: DataSource, *, organization_id: uuid.UUID
) -> DataSourceResponse:
    stream_count = await db.scalar(
        select(func.count())
        .select_from(SourceStream)
        .where(
            SourceStream.organization_id == organization_id,
            SourceStream.data_source_id == source.id,
        )
    )
    observation_count = await db.scalar(
        select(func.count())
        .select_from(Observation)
        .where(
            Observation.organization_id == organization_id,
            Observation.source_id == source.id,
        )
    )
    return DataSourceResponse(
        id=source.id,
        name=source.name,
        kind=SourceKind(source.kind),
        status=SourceStatus(source.status),
        connection=_connection_summary(source),
        last_connected_at=source.last_connected_at,
        last_connection_latency_ms=source.last_connection_latency_ms,
        last_synced_at=source.last_synced_at,
        last_error=source.last_error,
        last_error_at=source.last_error_at,
        stream_count=int(stream_count or 0),
        observation_count=int(observation_count or 0),
        created_at=source.created_at,
    )


def _stream_response(stream: SourceStream, *, observation_count: int = 0) -> StreamResponse:
    return StreamResponse(
        id=stream.id,
        data_source_id=stream.data_source_id,
        schema_name=stream.schema_name,
        table_name=stream.table_name,
        qualified_name=stream.qualified_name,
        primary_key_columns=list(stream.primary_key_columns),
        event_time_column=stream.event_time_column,
        event_time_semantics=EventTimeSemantics(stream.event_time_semantics),
        selected_columns=list(stream.selected_columns or []),
        enabled=stream.enabled,
        poll_interval_seconds=stream.poll_interval_seconds,
        last_synced_at=stream.last_synced_at,
        last_event_time=stream.last_event_time,
        observation_count=observation_count,
        created_at=stream.created_at,
    )


def _run_response(run: SyncRun) -> SyncRunResponse:
    details = run.details or {}
    streams = [SyncStreamDetail(**entry) for entry in details.get("streams", [])]
    return SyncRunResponse(
        id=run.id,
        source_id=run.source_id,
        stream_id=run.stream_id,
        status=SyncStatus(run.status),
        started_at=run.started_at,
        completed_at=run.completed_at,
        duration_ms=run.duration_ms,
        rows_seen=run.rows_seen,
        rows_created=run.rows_created,
        rows_skipped=run.rows_skipped,
        error_code=run.error_code,
        error_message=run.error_message,
        streams=streams,
    )


# --- Data sources ----------------------------------------------------------


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=DataSourceResponse,
    summary="Create a data source",
)
async def create_data_source(
    payload: CreateDataSourceRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
    context: RequireAdmin,
) -> DataSourceResponse:
    """Store a source and its encrypted credentials.

    Does **not** test the connection. The source is created with status
    ``configured``, meaning "credentials stored, never verified" — a distinct
    state from ``connected``, because claiming a connection that has never been
    made is exactly the sort of unverified assertion this product exists to
    eliminate. Testing is a separate, explicit step.
    """
    await enforce_csrf(request, context.auth, settings)

    existing = await db.scalar(
        select(DataSource.id).where(
            DataSource.organization_id == context.organization_id,
            DataSource.name == payload.name,
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A data source with that name already exists in this workspace.",
        )

    connection = payload.connection
    source = DataSource(
        organization_id=context.organization_id,
        name=payload.name,
        kind=payload.kind,
        status=SourceStatus.CONFIGURED.value,
        # The password is deliberately absent: config holds only what is safe
        # to display, and is returned to the client verbatim.
        config={
            "host": connection.host,
            "port": connection.port,
            "database": connection.database,
            "username": connection.username,
            "ssl_mode": connection.ssl_mode,
        },
        created_by_user_id=context.user.id,
    )
    db.add(source)
    await db.flush()

    await store_credentials(db, data_source=source, payload={"password": connection.password})

    await audit.record(
        db,
        action="data_source.created",
        organization_id=context.organization_id,
        actor_user_id=context.user.id,
        resource_type="data_source",
        resource_id=source.id,
        # Target only — never the username, never the password.
        details={"kind": payload.kind, "host": connection.host, "database": connection.database},
        request=request,
    )
    await db.commit()

    logger.info(
        "data_source.created",
        data_source_id=str(source.id),
        organization_id=str(context.organization_id),
        kind=payload.kind,
    )
    return await _source_response(db, source, organization_id=context.organization_id)


@router.get("", response_model=list[DataSourceResponse], summary="List data sources")
async def list_data_sources(
    db: DbSession, context: CurrentOrganization
) -> list[DataSourceResponse]:
    sources = await db.scalars(
        select(DataSource)
        .where(DataSource.organization_id == context.organization_id)
        .order_by(DataSource.created_at.desc())
    )
    return [
        await _source_response(db, source, organization_id=context.organization_id)
        for source in sources
    ]


@router.get("/{source_id}", response_model=DataSourceResponse, summary="Get a data source")
async def get_data_source(
    source_id: uuid.UUID, db: DbSession, context: CurrentOrganization
) -> DataSourceResponse:
    source = await _load_source(db, context=context, source_id=source_id)
    return await _source_response(db, source, organization_id=context.organization_id)


@router.get(
    "/{source_id}/health",
    response_model=ConnectorHealthResponse,
    summary="Connector health",
)
async def get_source_health(
    source_id: uuid.UUID, db: DbSession, context: CurrentOrganization
) -> ConnectorHealthResponse:
    """Last known health, from stored state.

    Deliberately does not open a connection: this is read on every page load,
    and dialling a customer's production database to render a status dot would
    be both slow and rude.
    """
    source = await _load_source(db, context=context, source_id=source_id)
    return ConnectorHealthResponse(
        status=SourceStatus(source.status),
        connected=source.status == SourceStatus.CONNECTED.value,
        last_connected_at=source.last_connected_at,
        last_synced_at=source.last_synced_at,
        last_error=source.last_error,
        last_error_at=source.last_error_at,
        latency_ms=source.last_connection_latency_ms,
    )


@router.delete(
    "/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a data source",
)
async def delete_data_source(
    source_id: uuid.UUID,
    request: Request,
    db: DbSession,
    settings: AppSettings,
    context: RequireAdmin,
) -> Response:
    """Delete a source, its credentials, streams and observations.

    Cascades all the way down. Observations are removed because they are only
    meaningful with the source that produced them — an observation whose
    provenance points at a deleted source cannot be explained, and an
    unexplainable record is worse than no record here.
    """
    await enforce_csrf(request, context.auth, settings)
    source = await _load_source(db, context=context, source_id=source_id)

    await audit.record(
        db,
        action="data_source.deleted",
        organization_id=context.organization_id,
        actor_user_id=context.user.id,
        resource_type="data_source",
        resource_id=source.id,
        details={"name": source.name},
        request=request,
    )
    await db.delete(source)
    await db.commit()

    logger.info("data_source.deleted", data_source_id=str(source_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Connection ------------------------------------------------------------


@router.post(
    "/{source_id}/test-connection",
    response_model=ConnectionTestResponse,
    summary="Test the connection",
)
async def test_connection(
    source_id: uuid.UUID,
    request: Request,
    db: DbSession,
    settings: AppSettings,
    context: RequireAdmin,
) -> ConnectionTestResponse:
    """Verify reachability, TLS, authentication and discovery permissions.

    Returns 200 with ``status: "failed"`` for a connection problem rather than
    an HTTP error. The request succeeded — it tested the connection and
    reported the answer. A 502 here would conflate "RealitySync is broken" with
    "your database refused us", and the interface needs to say the second.
    """
    await enforce_csrf(request, context.auth, settings)
    source = await _load_source(db, context=context, source_id=source_id)

    connector: DataConnector | None = None
    try:
        connector = await _open_connector(db, source)
        result = await connector.test_connection()

        source.status = SourceStatus.CONNECTED.value
        source.last_connected_at = datetime.now(UTC)
        source.last_connection_latency_ms = result.latency_ms
        source.last_error = None
        source.last_error_at = None
        await db.commit()

        return ConnectionTestResponse(
            status="connected",
            database=result.database,
            server_version=result.server_version,
            latency_ms=result.latency_ms,
            tls_version=result.tls_version,
            connected_as=result.connected_as,
            can_discover_schema=result.can_discover_schema,
            warnings=list(result.warnings),
        )

    except ConnectorError as exc:
        source.status = SourceStatus.ERROR.value
        source.last_error = exc.message
        source.last_error_at = datetime.now(UTC)
        await db.commit()

        logger.info(
            "data_source.test_failed",
            data_source_id=str(source_id),
            code=exc.code.value,
            detail=exc.detail,
        )
        return ConnectionTestResponse(
            status="failed",
            error_code=exc.code.value,
            error_message=exc.message,
            remediation=exc.remediation,
        )
    finally:
        if connector is not None:
            await connector.disconnect()


# --- Schema ----------------------------------------------------------------


@router.post(
    "/{source_id}/discover-schema",
    response_model=SchemaDiscoveryResponse,
    summary="Discover the source schema",
)
async def discover_schema(
    source_id: uuid.UUID,
    request: Request,
    db: DbSession,
    settings: AppSettings,
    context: RequireAdmin,
    include_system_schemas: bool = Query(default=False),
) -> SchemaDiscoveryResponse:
    """Read the source's catalog.

    Metadata only — no table data is read, and row counts are planner
    estimates. Results are returned rather than stored: a cached schema goes
    stale silently, and a stale schema is how a stream ends up configured
    against a column that no longer exists.
    """
    await enforce_csrf(request, context.auth, settings)
    source = await _load_source(db, context=context, source_id=source_id)

    connector: DataConnector | None = None
    try:
        connector = await _open_connector(db, source)
        discovered = await connector.discover_schema(include_system_schemas=include_system_schemas)
    except ConnectorError as exc:
        source.status = SourceStatus.ERROR.value
        source.last_error = exc.message
        source.last_error_at = datetime.now(UTC)
        await db.commit()
        raise _connector_error_to_http(exc) from exc
    finally:
        if connector is not None:
            await connector.disconnect()

    source.status = SourceStatus.CONNECTED.value
    source.last_connected_at = datetime.now(UTC)
    source.last_error = None
    await db.commit()

    configured = {
        (s.schema_name, s.table_name)
        for s in await db.scalars(
            select(SourceStream).where(
                SourceStream.organization_id == context.organization_id,
                SourceStream.data_source_id == source.id,
            )
        )
    }
    return _discovery_response(discovered, configured=configured)


def _discovery_response(
    discovered: DiscoveredSchema, *, configured: set[tuple[str, str]]
) -> SchemaDiscoveryResponse:
    return SchemaDiscoveryResponse(
        schemas=list(discovered.schemas),
        inaccessible_schemas=list(discovered.inaccessible_schemas),
        discovered_at=discovered.discovered_at,
        tables=[
            TableResponse(
                schema_name=table.schema_name,
                table_name=table.table_name,
                qualified_name=table.qualified_name,
                kind=table.kind,
                approximate_row_count=table.approximate_row_count,
                primary_key_columns=list(table.primary_key_columns),
                temporal_columns=list(table.temporal_columns),
                configured=(table.schema_name, table.table_name) in configured,
                columns=[
                    ColumnResponse(
                        name=column.name,
                        data_type=column.data_type,
                        nullable=column.nullable,
                        is_primary_key=column.is_primary_key,
                        is_temporal=column.is_temporal,
                    )
                    for column in table.columns
                ],
            )
            for table in discovered.tables
        ],
    )


# --- Streams ---------------------------------------------------------------


@router.post(
    "/{source_id}/streams",
    status_code=status.HTTP_201_CREATED,
    response_model=StreamResponse,
    summary="Configure a table as a stream",
)
async def create_stream(
    source_id: uuid.UUID,
    payload: CreateStreamRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
    context: RequireAdmin,
) -> StreamResponse:
    await enforce_csrf(request, context.auth, settings)
    source = await _load_source(db, context=context, source_id=source_id)

    try:
        payload.validate_time_configuration()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    duplicate = await db.scalar(
        select(SourceStream.id).where(
            SourceStream.organization_id == context.organization_id,
            SourceStream.data_source_id == source.id,
            SourceStream.schema_name == payload.schema_name,
            SourceStream.table_name == payload.table_name,
        )
    )
    if duplicate is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That table is already configured as a stream.",
        )

    stream = SourceStream(
        organization_id=context.organization_id,
        data_source_id=source.id,
        schema_name=payload.schema_name,
        table_name=payload.table_name,
        primary_key_columns=list(payload.primary_key_columns),
        event_time_column=payload.event_time_column,
        event_time_semantics=payload.event_time_semantics.value,
        selected_columns=list(payload.selected_columns),
        enabled=payload.enabled,
        poll_interval_seconds=payload.poll_interval_seconds,
    )
    db.add(stream)
    await audit.record(
        db,
        action="source_stream.created",
        organization_id=context.organization_id,
        actor_user_id=context.user.id,
        resource_type="source_stream",
        resource_id=stream.id,
        details={"table": f"{payload.schema_name}.{payload.table_name}"},
        request=request,
    )
    await db.commit()

    logger.info(
        "source_stream.created",
        stream_id=str(stream.id),
        table=stream.qualified_name,
    )
    return _stream_response(stream)


@router.get("/{source_id}/streams", response_model=list[StreamResponse], summary="List streams")
async def list_streams(
    source_id: uuid.UUID, db: DbSession, context: CurrentOrganization
) -> list[StreamResponse]:
    source = await _load_source(db, context=context, source_id=source_id)

    # A correlated subquery rather than an outer join with GROUP BY: it keeps
    # the observation-side tenant filter next to the observation query, and it
    # cannot accidentally drop streams that have no observations yet — which a
    # tenant filter in an outer join's WHERE clause silently would.
    observation_count = (
        select(func.count(Observation.id))
        .where(
            Observation.organization_id == context.organization_id,
            Observation.stream_id == SourceStream.id,
        )
        .correlate(SourceStream)
        .scalar_subquery()
    )

    rows = await db.execute(
        select(SourceStream, observation_count)
        .where(
            SourceStream.organization_id == context.organization_id,
            SourceStream.data_source_id == source.id,
        )
        .order_by(SourceStream.created_at)
    )
    return [_stream_response(stream, observation_count=int(count or 0)) for stream, count in rows]


@router.patch(
    "/{source_id}/streams/{stream_id}",
    response_model=StreamResponse,
    summary="Update a stream",
)
async def update_stream(
    source_id: uuid.UUID,
    stream_id: uuid.UUID,
    payload: UpdateStreamRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
    context: RequireAdmin,
) -> StreamResponse:
    await enforce_csrf(request, context.auth, settings)
    source = await _load_source(db, context=context, source_id=source_id)
    stream = await _load_stream(db, context=context, source=source, stream_id=stream_id)

    if payload.enabled is not None:
        stream.enabled = payload.enabled
    if payload.poll_interval_seconds is not None:
        stream.poll_interval_seconds = payload.poll_interval_seconds
    if payload.selected_columns is not None:
        stream.selected_columns = list(payload.selected_columns)

    # Changed together or not at all: the pair must stay consistent, and the
    # database CHECK enforces the same rule.
    if payload.event_time_semantics is not None or payload.event_time_column is not None:
        semantics = payload.event_time_semantics or stream.semantics
        column = (
            payload.event_time_column
            if payload.event_time_column is not None
            else stream.event_time_column
        )
        if semantics is EventTimeSemantics.INGEST_FALLBACK:
            column = None
        elif not column:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(f"An event-time column is required for '{semantics.value}' semantics."),
            )
        stream.event_time_semantics = semantics.value
        stream.event_time_column = column

    await db.commit()
    return _stream_response(stream)


@router.delete(
    "/{source_id}/streams/{stream_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a stream",
)
async def delete_stream(
    source_id: uuid.UUID,
    stream_id: uuid.UUID,
    request: Request,
    db: DbSession,
    settings: AppSettings,
    context: RequireAdmin,
) -> Response:
    await enforce_csrf(request, context.auth, settings)
    source = await _load_source(db, context=context, source_id=source_id)
    stream = await _load_stream(db, context=context, source=source, stream_id=stream_id)

    await db.delete(stream)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Sync ------------------------------------------------------------------


@router.post(
    "/{source_id}/sync",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SyncRunResponse,
    summary="Run a sync",
)
async def trigger_sync(
    source_id: uuid.UUID,
    payload: SyncRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
    context: RequireAdmin,
) -> SyncRunResponse:
    """Read the source's enabled streams and append observations.

    Runs inline and returns the finished run. Background scheduling belongs to
    the phase that owns it; running inline now means the result is verifiable
    in one request, which is what makes the vertical slice demonstrable.
    """
    await enforce_csrf(request, context.auth, settings)
    source = await _load_source(db, context=context, source_id=source_id)

    query = select(SourceStream).where(
        SourceStream.organization_id == context.organization_id,
        SourceStream.data_source_id == source.id,
    )
    if payload.stream_id is not None:
        query = query.where(SourceStream.id == payload.stream_id)
    else:
        query = query.where(SourceStream.enabled.is_(True))

    streams = list(await db.scalars(query.order_by(SourceStream.created_at)))
    if not streams:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=("This source has no enabled streams. Configure a table before syncing."),
        )

    async def builder() -> DataConnector:
        return await _open_connector(db, source)

    outcome = await run_sync(
        db,
        source=source,
        streams=streams,
        connector_builder=builder,
        idempotency_key=payload.idempotency_key or f"manual-{uuid.uuid4().hex}",
        triggered_by_user_id=context.user.id,
        settings=settings,
        incremental=not payload.full_refresh,
    )
    await db.commit()

    logger.info(
        "sync.completed",
        source_id=str(source_id),
        run_id=str(outcome.run.id),
        status=outcome.run.status,
        rows_created=outcome.run.rows_created,
    )
    return _run_response(outcome.run)


@router.get("/{source_id}/sync-runs", response_model=list[SyncRunResponse], summary="Sync history")
async def list_sync_runs(
    source_id: uuid.UUID,
    db: DbSession,
    context: CurrentOrganization,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[SyncRunResponse]:
    source = await _load_source(db, context=context, source_id=source_id)

    runs = await db.scalars(
        select(SyncRun)
        .where(
            SyncRun.organization_id == context.organization_id,
            SyncRun.source_id == source.id,
        )
        .order_by(SyncRun.started_at.desc())
        .limit(limit)
    )
    return [_run_response(run) for run in runs]


@router.get(
    "/{source_id}/sync-runs/{run_id}",
    response_model=SyncRunResponse,
    summary="Get a sync run",
)
async def get_sync_run(
    source_id: uuid.UUID,
    run_id: uuid.UUID,
    db: DbSession,
    context: CurrentOrganization,
) -> SyncRunResponse:
    source = await _load_source(db, context=context, source_id=source_id)
    run = await db.scalar(
        select(SyncRun).where(
            SyncRun.organization_id == context.organization_id,
            SyncRun.source_id == source.id,
            SyncRun.id == run_id,
        )
    )
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sync run not found.")
    return _run_response(run)


@router.get(
    "/{source_id}/observations",
    response_model=list[ObservationResponse],
    summary="Observations produced by this source",
)
async def list_observations(
    source_id: uuid.UUID,
    db: DbSession,
    context: CurrentOrganization,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ObservationResponse]:
    """Recent observations, newest ingestion first.

    Ordered by ``ingested_at``, not ``event_time``: this answers "what did we
    just take in", which is an ingestion question. Event-time ordering is the
    timeline's job, in a later phase.
    """
    source = await _load_source(db, context=context, source_id=source_id)

    observations = await db.scalars(
        select(Observation)
        .where(
            Observation.organization_id == context.organization_id,
            Observation.source_id == source.id,
        )
        .order_by(Observation.ingested_at.desc())
        .limit(limit)
    )
    return [ObservationResponse.model_validate(o) for o in observations]
