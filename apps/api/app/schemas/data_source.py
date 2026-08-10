"""Data source, stream and sync API models.

The response types have no password field anywhere. That is the mechanism by
which "credentials never reach the frontend" holds: not a rule each route
remembers, but a shape that cannot express the value. A future endpoint that
tried to return one would have to add a field to do it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.connectors.postgres.config import ALLOWED_SSL_MODES
from app.models.data_source import SourceKind, SourceStatus
from app.models.source_stream import EventTimeSemantics
from app.models.sync_run import SyncStatus

Identifier = Annotated[str, Field(min_length=1, max_length=128)]


# --- Requests --------------------------------------------------------------


class PostgresConnectionInput(BaseModel):
    """Connection parameters supplied when creating a source.

    The password is here — it has to arrive somehow — and this is the only
    model in the API that carries one. It is a *request* type; nothing
    serialises it back.
    """

    model_config = ConfigDict(extra="forbid")

    host: Annotated[str, Field(min_length=1, max_length=253)]
    port: Annotated[int, Field(ge=1, le=65535)] = 5432
    database: Identifier
    username: Identifier
    password: Annotated[str, Field(min_length=1, max_length=1024)]
    #: Only genuinely-encrypted modes. 'disable', 'allow' and 'prefer' are
    #: rejected here and again in the connector, because a downgrade to
    #: plaintext would put a customer's production credentials on the wire.
    ssl_mode: Literal["require", "verify-ca", "verify-full"] = "require"

    @field_validator("ssl_mode", mode="before")
    @classmethod
    def _reject_insecure_modes(cls, value: object) -> object:
        if isinstance(value, str) and value.strip().lower() not in ALLOWED_SSL_MODES:
            raise ValueError(
                f"SSL mode must be one of {', '.join(ALLOWED_SSL_MODES)}. "
                "RealitySync requires an encrypted connection to your database."
            )
        return value.strip().lower() if isinstance(value, str) else value


class CreateDataSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=200)]
    kind: Literal["postgresql"] = "postgresql"
    connection: PostgresConnectionInput

    @field_validator("name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Name must not be blank")
        return stripped


class CreateStreamRequest(BaseModel):
    """Configure a discovered table for ingestion."""

    model_config = ConfigDict(extra="forbid")

    schema_name: Identifier
    table_name: Identifier
    primary_key_columns: Annotated[list[Identifier], Field(min_length=1, max_length=16)]
    event_time_column: Identifier | None = None
    event_time_semantics: EventTimeSemantics = EventTimeSemantics.INGEST_FALLBACK
    selected_columns: list[Identifier] = Field(default_factory=list)
    enabled: bool = True
    poll_interval_seconds: Annotated[int, Field(ge=30, le=86_400)] = 300

    @field_validator("event_time_semantics", mode="after")
    @classmethod
    def _semantics_known(cls, value: EventTimeSemantics) -> EventTimeSemantics:
        return value

    def validate_time_configuration(self) -> None:
        """Enforce the column/semantics pairing.

        Mirrors the database CHECK. Doing it here as well turns a constraint
        violation into a clear 422 naming the field, rather than a 500 from a
        failed INSERT.
        """
        needs_column = self.event_time_semantics is not EventTimeSemantics.INGEST_FALLBACK
        if needs_column and not self.event_time_column:
            raise ValueError(
                f"An event-time column is required when semantics are "
                f"'{self.event_time_semantics.value}'. Use 'ingest_fallback' if the "
                f"table has no usable time column."
            )
        if not needs_column and self.event_time_column:
            raise ValueError(
                "'ingest_fallback' means there is no event-time column; leave it unset."
            )


class UpdateStreamRequest(BaseModel):
    """Partial update. Unset fields are left alone."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    poll_interval_seconds: Annotated[int, Field(ge=30, le=86_400)] | None = None
    selected_columns: list[Identifier] | None = None
    event_time_column: Identifier | None = None
    event_time_semantics: EventTimeSemantics | None = None


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Restrict the run to one stream. Omit to sync every enabled stream.
    stream_id: uuid.UUID | None = None
    #: Read everything rather than only rows at or after the high-water mark.
    full_refresh: bool = False
    #: Supplied by the client so a retry after a timeout returns the original
    #: run instead of starting a second one.
    idempotency_key: Annotated[str, Field(min_length=8, max_length=128)] | None = None


# --- Responses -------------------------------------------------------------


class ConnectionSummary(BaseModel):
    """Non-secret connection details, safe to display.

    Structurally incapable of carrying a password: there is no field for one,
    and it is built from the stored config, which never held one either.
    """

    host: str
    port: int
    database: str
    username: str
    ssl_mode: str
    #: Whether a credential is stored. The value itself is never returned, and
    #: this is all the interface needs to say "Password saved".
    password_set: bool = True


class DataSourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: SourceKind
    status: SourceStatus
    connection: ConnectionSummary
    last_connected_at: datetime | None = None
    last_connection_latency_ms: int | None = None
    last_synced_at: datetime | None = None
    #: Sanitised message. Never driver output.
    last_error: str | None = None
    last_error_at: datetime | None = None
    stream_count: int = 0
    observation_count: int = 0
    created_at: datetime


class ConnectionTestResponse(BaseModel):
    """Structured result of a connection test."""

    status: Literal["connected", "failed"]
    database: str | None = None
    server_version: str | None = None
    latency_ms: int | None = None
    #: Proof the session was actually encrypted, read from pg_stat_ssl rather
    #: than assumed from the requested SSL mode.
    tls_version: str | None = None
    connected_as: str | None = None
    can_discover_schema: bool = False
    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    remediation: str | None = None


class ColumnResponse(BaseModel):
    name: str
    data_type: str
    nullable: bool
    is_primary_key: bool
    is_temporal: bool


class TableResponse(BaseModel):
    schema_name: str
    table_name: str
    qualified_name: str
    kind: str
    #: Planner estimate, never a COUNT(*). Labelled approximate in the UI so
    #: nobody mistakes it for a fact about the data.
    approximate_row_count: int | None
    columns: list[ColumnResponse]
    primary_key_columns: list[str]
    temporal_columns: list[str]
    #: True when a stream already exists for this table.
    configured: bool = False


class SchemaDiscoveryResponse(BaseModel):
    schemas: list[str]
    tables: list[TableResponse]
    #: Schemas that exist but this role cannot read. Reported rather than
    #: dropped: "you cannot see this" is actionable, and silently omitting it
    #: looks identical to the schema not existing.
    inaccessible_schemas: list[str] = Field(default_factory=list)
    discovered_at: datetime | None = None


class StreamResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    data_source_id: uuid.UUID
    schema_name: str
    table_name: str
    qualified_name: str
    primary_key_columns: list[str]
    event_time_column: str | None
    event_time_semantics: EventTimeSemantics
    selected_columns: list[str]
    enabled: bool
    poll_interval_seconds: int
    last_synced_at: datetime | None
    last_event_time: datetime | None
    observation_count: int = 0
    created_at: datetime


class SyncStreamDetail(BaseModel):
    stream_id: uuid.UUID | None = None
    table: str | None = None
    rows_seen: int = 0
    rows_created: int = 0
    rows_skipped: int = 0
    error_code: str | None = None
    error_message: str | None = None


class SyncRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: uuid.UUID
    stream_id: uuid.UUID | None
    status: SyncStatus
    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None
    rows_seen: int
    rows_created: int
    rows_skipped: int
    error_code: str | None
    error_message: str | None
    streams: list[SyncStreamDetail] = Field(default_factory=list)


class ObservationResponse(BaseModel):
    """An observation as the API presents it.

    Included so the vertical slice is verifiable end to end — you can see the
    rows that were ingested. Reading observations for analysis is a later
    phase; this is provenance, not product.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: uuid.UUID
    stream_id: uuid.UUID
    external_id: str
    entity_mapping_state: str
    payload: dict[str, Any]
    event_time: datetime
    event_time_semantics: str
    ingested_at: datetime
    fingerprint: str
    provenance: dict[str, Any]


class ConnectorHealthResponse(BaseModel):
    """Source health. Contains no credentials."""

    status: SourceStatus
    connected: bool
    last_connected_at: datetime | None = None
    last_synced_at: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    latency_ms: int | None = None
