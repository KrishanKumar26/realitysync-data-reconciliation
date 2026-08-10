"""PostgreSQL connector.

Reads a customer's PostgreSQL over TLS, describes it from the catalog, and
yields rows. Read-only by construction: the connection is opened with
``default_transaction_read_only=on``, so even a bug cannot write to a
customer's production database.

Identifier handling deserves a note. Schema, table and column names reach this
module from stream configuration, so every one of them is passed through
``psycopg.sql.Identifier``, which quotes and escapes properly. No SQL is built
by string formatting anywhere in this file, and the catalog queries take schema
names as bound parameters rather than interpolating them.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from app.connectors.base import DataConnector
from app.connectors.postgres.config import PostgresConnectionConfig
from app.connectors.postgres.errors import map_exception
from app.connectors.types import (
    ConnectionTestResult,
    ConnectorError,
    ConnectorErrorCode,
    ConnectorHealth,
    DiscoveredColumn,
    DiscoveredSchema,
    DiscoveredTable,
    SourceRecord,
    StreamSelector,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Schemas excluded from discovery unless explicitly requested. These hold the
#: catalog itself; presenting them as ingestible would be noise at best.
SYSTEM_SCHEMAS: frozenset[str] = frozenset({"information_schema", "pg_catalog", "pg_toast"})

#: Types that can carry an event time.
TEMPORAL_TYPE_PREFIXES: tuple[str, ...] = (
    "timestamp with time zone",
    "timestamp without time zone",
    "timestamp",
    "date",
)

#: Relkinds worth offering: ordinary and partitioned tables, views,
#: materialised views, foreign tables. Indexes and sequences are not data.
READABLE_RELKINDS: tuple[str, ...] = ("r", "p", "v", "m", "f")

_RELKIND_LABELS: dict[str, str] = {
    "r": "table",
    "p": "partitioned table",
    "v": "view",
    "m": "materialized view",
    "f": "foreign table",
}


class PostgresConnector(DataConnector):
    """Read-only PostgreSQL connector."""

    kind = "postgresql"
    version = "1"

    def __init__(
        self,
        *,
        config: PostgresConnectionConfig,
        password: str,
        connect_timeout_seconds: int = 10,
        statement_timeout_seconds: int = 30,
        fetch_batch_size: int = 1_000,
    ) -> None:
        self._config = config
        # The one place the password lives. Never logged, never in a repr,
        # never returned.
        self._password = password
        self._connect_timeout = connect_timeout_seconds
        self._statement_timeout = statement_timeout_seconds
        self._fetch_batch_size = fetch_batch_size

        self._connection: psycopg.AsyncConnection[Any] | None = None
        self._connected_at: datetime | None = None
        self._latency_ms: int | None = None
        self._last_error: ConnectorError | None = None

    def __repr__(self) -> str:
        # No password, and no full conninfo.
        return f"<PostgresConnector target={self._config.display_target}>"

    # --- Connection -------------------------------------------------------

    def _conninfo(self) -> str:
        """Build the libpq connection string.

        ``make_conninfo`` escapes values properly. Building this by hand would
        make a password containing a space or a quote either fail or, worse,
        change the meaning of the string.
        """
        return make_conninfo(
            host=self._config.host,
            port=self._config.port,
            dbname=self._config.database,
            user=self._config.username,
            password=self._password,
            sslmode=self._config.ssl_mode.value,
            connect_timeout=self._connect_timeout,
            # Read-only enforced by the server, not by our good intentions.
            # statement_timeout bounds a runaway query against a customer's
            # production database.
            options=(
                f"-c default_transaction_read_only=on "
                f"-c statement_timeout={self._statement_timeout * 1000}"
            ),
            # So a DBA looking at pg_stat_activity knows who is connected.
            application_name="realitysync",
        )

    async def connect(self) -> None:
        if self._connection is not None and not self._connection.closed:
            return

        started = time.perf_counter()
        try:
            self._connection = await psycopg.AsyncConnection.connect(
                self._conninfo(), autocommit=True, row_factory=dict_row
            )
        except Exception as exc:
            error = map_exception(exc, operation="connect")
            self._last_error = error
            # detail carries driver text; the redaction filter scrubs it, and
            # it never leaves the server.
            logger.warning(
                "connector.postgres.connect_failed",
                code=error.code.value,
                target=self._config.display_target,
                detail=error.detail,
            )
            raise error from exc

        self._latency_ms = int((time.perf_counter() - started) * 1000)
        self._connected_at = datetime.now(UTC)
        self._last_error = None

    async def disconnect(self) -> None:
        """Close the connection. Never raises — it runs in cleanup paths where
        an exception would mask the error that sent us there."""
        connection = self._connection
        self._connection = None
        if connection is None or connection.closed:
            return
        try:
            await connection.close()
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("connector.postgres.disconnect_failed", error=str(exc))

    def _require_connection(self) -> psycopg.AsyncConnection[Any]:
        if self._connection is None or self._connection.closed:
            raise ConnectorError(
                ConnectorErrorCode.UNKNOWN,
                "The connector is not connected.",
                detail="Operation attempted before connect()",
            )
        return self._connection

    # --- Test -------------------------------------------------------------

    async def test_connection(self) -> ConnectionTestResult:
        """Verify reachability, TLS, authentication and catalog access.

        Reports whether discovery will work rather than only whether the
        connection opened — a role that can log in but cannot read the catalog
        produces a confusing failure one step later otherwise.
        """
        started = time.perf_counter()
        await self.connect()
        connection = self._require_connection()

        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT
                        current_database()                       AS database,
                        current_user                             AS connected_as,
                        version()                                AS server_version,
                        (SELECT ssl FROM pg_stat_ssl
                          WHERE pid = pg_backend_pid())          AS ssl_active,
                        (SELECT version FROM pg_stat_ssl
                          WHERE pid = pg_backend_pid())          AS tls_version
                    """
                )
                row = await cursor.fetchone()
        except Exception as exc:
            error = map_exception(exc, operation="test_connection")
            self._last_error = error
            raise error from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        info: dict[str, Any] = dict(row or {})

        warnings: list[str] = []

        # A connection that reached us unencrypted despite sslmode=require
        # would mean libpq behaved unexpectedly. Checked rather than assumed:
        # this is the guarantee the whole SSL policy rests on.
        if info.get("ssl_active") is not True:
            raise ConnectorError(
                ConnectorErrorCode.TLS_FAILED,
                "The connection was established without encryption.",
                detail="pg_stat_ssl reported ssl=false",
                remediation="RealitySync requires TLS. Enable ssl on the source database.",
            )

        can_discover = await self._can_read_catalog()
        if not can_discover:
            warnings.append(
                "This role cannot read the system catalog, so schema discovery "
                "will return nothing. Grant USAGE on the schemas you want to sync."
            )

        server_version = str(info.get("server_version") or "")
        # version() returns a long banner including build host and compiler.
        # Only the release number is useful, and the rest is server detail.
        short_version = " ".join(server_version.split()[:2]) if server_version else None

        return ConnectionTestResult(
            status="connected",
            database=info.get("database"),
            server_version=short_version,
            latency_ms=latency_ms,
            tls_version=info.get("tls_version"),
            connected_as=info.get("connected_as"),
            can_discover_schema=can_discover,
            warnings=tuple(warnings),
        )

    async def _can_read_catalog(self) -> bool:
        """Whether this role can see any non-system schema."""
        connection = self._require_connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT count(*) AS visible
                    FROM pg_namespace n
                    WHERE n.nspname <> ALL(%(system)s)
                      AND n.nspname NOT LIKE 'pg\\_%%'
                      AND has_schema_privilege(n.oid, 'USAGE')
                    """,
                    {"system": list(SYSTEM_SCHEMAS)},
                )
                row = await cursor.fetchone()
        except Exception:
            return False
        return bool(row and int(row["visible"]) > 0)

    # --- Discovery --------------------------------------------------------

    async def discover_schema(self, *, include_system_schemas: bool = False) -> DiscoveredSchema:
        """Describe the database from its catalog.

        Reads metadata only. Row counts come from the planner's estimate
        (``pg_class.reltuples``), never ``COUNT(*)`` — discovery runs against a
        customer's production database, and scanning every table to describe it
        would be an outage caused by a configuration screen.
        """
        await self.connect()
        connection = self._require_connection()

        excluded = [] if include_system_schemas else list(SYSTEM_SCHEMAS)

        try:
            async with connection.cursor() as cursor:
                # Schemas, split by whether this role can actually use them.
                await cursor.execute(
                    """
                    SELECT
                        n.nspname                              AS schema_name,
                        has_schema_privilege(n.oid, 'USAGE')   AS accessible
                    FROM pg_namespace n
                    WHERE (%(include_system)s OR (
                            n.nspname <> ALL(%(excluded)s)
                            AND n.nspname NOT LIKE 'pg\\_%%'
                          ))
                    ORDER BY n.nspname
                    """,
                    {"excluded": excluded, "include_system": include_system_schemas},
                )
                schema_rows = await cursor.fetchall()

                accessible = [r["schema_name"] for r in schema_rows if r["accessible"]]
                inaccessible = [r["schema_name"] for r in schema_rows if not r["accessible"]]

                tables: list[DiscoveredTable] = []
                if accessible:
                    await cursor.execute(
                        self._COLUMN_QUERY,
                        {"schemas": accessible, "relkinds": list(READABLE_RELKINDS)},
                    )
                    tables = self._assemble_tables(await cursor.fetchall())

        except Exception as exc:
            error = map_exception(exc, operation="discover_schema")
            self._last_error = error
            raise error from exc

        logger.info(
            "connector.postgres.schema_discovered",
            target=self._config.display_target,
            schemas=len(accessible),
            tables=len(tables),
            inaccessible_schemas=len(inaccessible),
        )

        return DiscoveredSchema(
            tables=tuple(tables),
            schemas=tuple(accessible),
            inaccessible_schemas=tuple(inaccessible),
            discovered_at=datetime.now(UTC),
        )

    #: One pass over the catalog: relations, their columns, primary-key
    #: positions and row estimates.
    #:
    #: `has_table_privilege` filters to relations this role can actually
    #: SELECT from. Listing a table that cannot be read would let someone
    #: configure a stream that fails on its first sync, with the real cause
    #: several steps behind them.
    _COLUMN_QUERY = """
        SELECT
            n.nspname                                        AS schema_name,
            c.relname                                        AS table_name,
            c.relkind                                        AS relkind,
            CASE WHEN c.reltuples < 0 THEN NULL
                 ELSE c.reltuples::bigint END                AS approximate_row_count,
            obj_description(c.oid, 'pg_class')               AS table_comment,
            a.attname                                        AS column_name,
            format_type(a.atttypid, a.atttypmod)             AS data_type,
            NOT a.attnotnull                                 AS nullable,
            pk.ordinal                                       AS primary_key_position,
            pg_get_expr(d.adbin, d.adrelid)                  AS column_default
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
        LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
        LEFT JOIN LATERAL (
            SELECT k.ordinality AS ordinal
            FROM pg_index i
            CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ordinality)
            WHERE i.indrelid = c.oid
              AND i.indisprimary
              AND k.attnum = a.attnum
            LIMIT 1
        ) pk ON TRUE
        WHERE n.nspname = ANY(%(schemas)s)
          AND c.relkind = ANY(%(relkinds)s)
          AND has_table_privilege(c.oid, 'SELECT')
        ORDER BY n.nspname, c.relname, a.attnum
    """

    def _assemble_tables(self, rows: list[dict[str, Any]]) -> list[DiscoveredTable]:
        """Fold the flat column rows into tables."""
        grouped: dict[tuple[str, str], dict[str, Any]] = {}

        for row in rows:
            key = (row["schema_name"], row["table_name"])
            entry = grouped.setdefault(
                key,
                {
                    "relkind": row["relkind"],
                    "approximate_row_count": row["approximate_row_count"],
                    "comment": row["table_comment"],
                    "columns": [],
                },
            )
            data_type = str(row["data_type"])
            entry["columns"].append(
                DiscoveredColumn(
                    name=row["column_name"],
                    data_type=data_type,
                    nullable=bool(row["nullable"]),
                    primary_key_position=row["primary_key_position"],
                    is_temporal=data_type.startswith(TEMPORAL_TYPE_PREFIXES),
                    default=row["column_default"],
                )
            )

        return [
            DiscoveredTable(
                schema_name=schema_name,
                table_name=table_name,
                columns=tuple(entry["columns"]),
                approximate_row_count=entry["approximate_row_count"],
                kind=_RELKIND_LABELS.get(entry["relkind"], "table"),
                comment=entry["comment"],
            )
            for (schema_name, table_name), entry in grouped.items()
        ]

    async def discover_relkinds(self) -> tuple[str, ...]:  # pragma: no cover - helper
        return READABLE_RELKINDS

    # --- Reading ----------------------------------------------------------

    def _build_select(self, selector: StreamSelector) -> tuple[sql.Composed, dict[str, Any]]:
        """Compose the read query.

        Identifiers go through ``sql.Identifier`` and values through bound
        parameters, so a table or column name cannot alter the statement's
        structure however it is spelled.
        """
        relation = sql.Identifier(selector.schema_name, selector.table_name)

        if selector.selected_columns:
            # The primary key and event-time columns are always needed, even if
            # the operator did not tick them: without them a row has no identity
            # and no time.
            required = list(selector.selected_columns)
            for column in (*selector.primary_key_columns, selector.event_time_column):
                if column and column not in required:
                    required.append(column)
            projection: sql.Composable = sql.SQL(", ").join(sql.Identifier(c) for c in required)
        else:
            projection = sql.SQL("*")

        params: dict[str, Any] = {}
        where: sql.Composable = sql.SQL("")
        if selector.event_time_column and selector.since_event_time is not None:
            # >= rather than >: a source with second-granularity timestamps can
            # write several rows at the same instant, and > would skip the ones
            # that arrived after the cursor was taken. Re-reading is free
            # because the fingerprint makes it idempotent; skipping is not.
            where = sql.SQL(" WHERE {} >= {}").format(
                sql.Identifier(selector.event_time_column), sql.Placeholder("since")
            )
            params["since"] = selector.since_event_time

        # Deterministic order: event time first where available, then the
        # primary key. Without a total order, two runs over the same data could
        # interleave differently, which makes a partial sync unreproducible.
        order_columns = [
            *([selector.event_time_column] if selector.event_time_column else []),
            *selector.primary_key_columns,
        ]
        order_by: sql.Composable = sql.SQL(" ORDER BY ") + sql.SQL(", ").join(
            sql.Identifier(c) for c in order_columns
        )

        limit: sql.Composable = sql.SQL("")
        if selector.limit is not None:
            limit = sql.SQL(" LIMIT {}").format(sql.Placeholder("row_limit"))
            params["row_limit"] = selector.limit

        query = (
            sql.SQL("SELECT ")
            + projection
            + sql.SQL(" FROM ")
            + relation
            + where
            + order_by
            + limit
        )
        return query, params

    async def _iterate(self, selector: StreamSelector) -> AsyncIterator[SourceRecord]:
        await self.connect()
        connection = self._require_connection()
        query, params = self._build_select(selector)

        try:
            # A server-side cursor streams: a source table can be far larger
            # than this process's memory, and a client-side fetch would try to
            # materialise all of it.
            #
            # The explicit transaction is required — DECLARE CURSOR is only
            # valid inside one, and the connection runs in autocommit so that
            # ordinary metadata queries do not hold a transaction open against
            # a customer's database. The transaction is read-only, inherited
            # from default_transaction_read_only.
            async with connection.transaction():
                async with connection.cursor(name="realitysync_read") as cursor:
                    cursor.itersize = self._fetch_batch_size
                    await cursor.execute(query, params or None)

                    async for row in cursor:
                        yield self._to_record(row, selector)
        except Exception as exc:
            error = map_exception(exc, operation="fetch_data")
            self._last_error = error
            raise error from exc

    def _to_record(self, row: dict[str, Any], selector: StreamSelector) -> SourceRecord:
        """Turn a driver row into a SourceRecord.

        Values stay driver-native; canonical normalisation happens in the
        ingestion layer so every connector produces identical observations for
        identical values.
        """
        missing = [c for c in selector.primary_key_columns if c not in row]
        if missing:
            raise ConnectorError(
                ConnectorErrorCode.NOT_FOUND,
                "A configured identity column is missing from the source table.",
                detail=f"missing primary key columns: {missing}",
                remediation="Run schema discovery again and reconfigure the stream.",
            )

        external_id = build_external_id(
            {column: row[column] for column in selector.primary_key_columns}
        )

        event_time: datetime | None = None
        if selector.event_time_column:
            raw = row.get(selector.event_time_column)
            if isinstance(raw, datetime):
                # A source column declared `timestamp without time zone` has no
                # offset. Assuming UTC is the only defensible choice — guessing
                # a local zone would silently shift every event.
                event_time = raw if raw.tzinfo else raw.replace(tzinfo=UTC)

        return SourceRecord(external_id=external_id, values=dict(row), event_time=event_time)

    def fetch_data(self, selector: StreamSelector) -> AsyncIterator[SourceRecord]:
        """Every row matching the selector."""
        return self._iterate(selector)

    def fetch_changes(self, selector: StreamSelector) -> AsyncIterator[SourceRecord]:
        """Rows at or after ``selector.since_event_time``.

        PostgreSQL offers no change feed without logical replication, which the
        MVP does not use, so this is a filtered read. Two consequences, stated
        plainly because a caller who assumes otherwise will be wrong:

        * **Deletes are invisible.** A row removed from the source produces no
          record here, and its existing observations remain — correctly, since
          they were true when made.
        * **A row whose event time does not advance on update is invisible.**
          For those tables, configure the stream without incremental reads.

        With no event-time column there is nothing to filter on, so this
        degrades to a full read. That is handled by the selector carrying no
        ``since_event_time``, not by a special case here.
        """
        return self._iterate(selector)

    # --- Health -----------------------------------------------------------

    async def get_health(self) -> ConnectorHealth:
        connected = self._connection is not None and not self._connection.closed
        return ConnectorHealth(
            connected=connected,
            last_successful_connection_at=self._connected_at,
            last_error_code=self._last_error.code.value if self._last_error else None,
            last_error_message=self._last_error.message if self._last_error else None,
            latency_ms=self._latency_ms,
            # Target only. No username, no password, no connection string.
            details={"target": self._config.display_target},
        )


def build_external_id(key_values: dict[str, Any]) -> str:
    """Build the source's identifier for a row from its key columns.

    Sorted by column name and delimited so that composite keys are
    unambiguous: without a delimiter, ("ab", "c") and ("a", "bc") would produce
    the same identifier and two different rows would be treated as one.
    """
    parts = [f"{name}={_scalar(key_values[name])}" for name in sorted(key_values)]
    return "|".join(parts)


def _scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat() if value.tzinfo else value.isoformat()
    return str(value)
