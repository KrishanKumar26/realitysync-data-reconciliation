"""Canonical connector types.

The vocabulary every connector speaks. Deliberately contains nothing
PostgreSQL-specific: a REST connector, a Snowflake connector and a CSV
connector must all be describable in these terms, or the abstraction is wrong.

The dependency direction is the point. Ingestion, and later the Reality Engine,
import *these* types. They never import a connector implementation, so adding a
source type cannot require changing anything downstream of it.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class ConnectorErrorCode(enum.StrEnum):
    """Stable, client-facing failure categories.

    A closed set, because the interface and the API both branch on these. Every
    driver exception a connector encounters is mapped onto one of them — raw
    driver text never reaches a user, since it routinely contains hostnames,
    usernames and occasionally the connection string itself.
    """

    #: Host unresolvable, refused, or unroutable.
    UNREACHABLE = "unreachable"
    #: Connection or query exceeded its timeout.
    TIMEOUT = "timeout"
    #: TLS could not be negotiated, or the certificate failed verification.
    TLS_FAILED = "tls_failed"
    #: The server accepted the connection but rejected the credentials.
    AUTHENTICATION_FAILED = "authentication_failed"
    #: Authenticated, but lacking a privilege the operation needs.
    PERMISSION_DENIED = "permission_denied"
    #: The named database, schema, table or column does not exist.
    NOT_FOUND = "not_found"
    #: Connection parameters failed validation before any attempt was made.
    INVALID_CONFIGURATION = "invalid_configuration"
    #: The source rejected the query for a reason we can describe safely.
    QUERY_FAILED = "query_failed"
    #: Anything unrecognised. Logged in full server-side, generic to the client.
    UNKNOWN = "unknown"


class ConnectorError(Exception):
    """A connector failure with a safe, actionable message.

    ``message`` is written for the person configuring the source and is shown
    to them verbatim, so it must never contain credentials, connection strings
    or driver output. ``detail`` holds the original text for server-side logs
    only, and is never serialised into a response.
    """

    def __init__(
        self,
        code: ConnectorErrorCode,
        message: str,
        *,
        detail: str | None = None,
        remediation: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail
        #: What the operator should actually do about it.
        self.remediation = remediation

    def __repr__(self) -> str:
        return f"<ConnectorError {self.code}: {self.message}>"


@dataclass(frozen=True, slots=True)
class ConnectionTestResult:
    """Outcome of a connection test.

    Carries only facts about the *server*. No credentials, and no connection
    string — the caller already knows what they configured, and echoing it back
    only creates another place for it to leak.
    """

    status: str
    database: str | None = None
    server_version: str | None = None
    latency_ms: int | None = None
    #: Negotiated TLS version, proving the connection was actually encrypted
    #: rather than merely requested to be.
    tls_version: str | None = None
    #: The role the connector authenticated as, useful for diagnosing
    #: permission problems. Not a secret — it is what the operator typed.
    connected_as: str | None = None
    #: Whether the role can read the catalog, i.e. whether discovery will work.
    can_discover_schema: bool = False
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DiscoveredColumn:
    name: str
    data_type: str
    nullable: bool
    #: Position in the primary key, 1-based; None when not part of one.
    primary_key_position: int | None = None
    #: True when the type can carry an event time.
    is_temporal: bool = False
    default: str | None = None

    @property
    def is_primary_key(self) -> bool:
        return self.primary_key_position is not None


@dataclass(frozen=True, slots=True)
class DiscoveredTable:
    schema_name: str
    table_name: str
    columns: tuple[DiscoveredColumn, ...]
    #: Planner estimate, not a COUNT(*). Discovery must never scan the data it
    #: is describing — on a large table that is an expensive query against a
    #: customer's production database, run at configuration time.
    approximate_row_count: int | None = None
    kind: str = "table"
    comment: str | None = None

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"

    @property
    def primary_key_columns(self) -> tuple[str, ...]:
        keyed = [c for c in self.columns if c.primary_key_position is not None]
        keyed.sort(key=lambda c: c.primary_key_position or 0)
        return tuple(c.name for c in keyed)

    @property
    def temporal_columns(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns if c.is_temporal)


@dataclass(frozen=True, slots=True)
class DiscoveredSchema:
    """Everything discovery found, plus what it could not reach.

    ``inaccessible_schemas`` is reported rather than silently dropped: "you
    cannot see this schema" is useful, actionable information, and a discovery
    result that quietly omits it looks identical to one where the schema does
    not exist.
    """

    tables: tuple[DiscoveredTable, ...]
    schemas: tuple[str, ...] = ()
    inaccessible_schemas: tuple[str, ...] = ()
    discovered_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """One row as the connector read it.

    ``values`` are still driver-native here; normalisation into canonical JSON
    happens in the ingestion layer, not the connector, so every connector
    produces identical observations for identical values.
    """

    external_id: str
    values: dict[str, Any]
    #: Event time as the source reports it, or None when the stream has no
    #: usable time column — ingestion decides the fallback, so the connector
    #: never invents a timestamp.
    event_time: datetime | None = None


@dataclass(frozen=True, slots=True)
class StreamSelector:
    """What to read, independent of any ORM model.

    The connector receives this rather than a ``SourceStream`` row, so it has
    no dependency on the application's persistence layer and can be tested with
    a literal.
    """

    schema_name: str
    table_name: str
    primary_key_columns: tuple[str, ...]
    event_time_column: str | None = None
    selected_columns: tuple[str, ...] = ()
    #: Incremental high-water mark: only rows at or after this event time.
    since_event_time: datetime | None = None
    limit: int | None = None

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"


@dataclass(frozen=True, slots=True)
class ConnectorHealth:
    """Connector state for display. Never contains credentials."""

    connected: bool
    last_successful_connection_at: datetime | None = None
    last_sync_at: datetime | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    latency_ms: int | None = None
    details: dict[str, Any] = field(default_factory=dict)
