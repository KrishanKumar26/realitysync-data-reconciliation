"""MySQL connector.

The second source type, and the point at which "adding a connector requires no
downstream changes" stops being an assertion. Nothing in ``app/ingestion``,
``app/engine`` or the API changed to accommodate this file — it implements
``DataConnector`` and registers itself, exactly as the interface documentation
claimed it would need to.

Read-only, over TLS, with the same guarantees the PostgreSQL connector makes.
The mechanisms differ because MySQL differs:

* **Read-only** is a session variable rather than a connection option.
  ``SET SESSION TRANSACTION READ ONLY`` is issued after connecting, so the
  server rejects a write even if a bug in this file attempted one.
* **Identifier quoting** uses backticks, applied by one function that rejects
  a backtick in the input rather than escaping it. Every schema, table and
  column name here arrives from stream configuration.
* **Streaming** uses an unbuffered cursor. aiomysql's default buffers the
  entire result in the client, which would defeat the whole point of an async
  iterator over a table larger than memory.
* **Schemas** are databases. MySQL has no schema layer inside a database, so
  ``schema_name`` in the connector vocabulary maps to a MySQL database name.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import aiomysql

from app.connectors.base import DataConnector
from app.connectors.mysql.config import MysqlConnectionConfig
from app.connectors.mysql.errors import map_exception
from app.connectors.postgres.connector import build_external_id
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

#: MySQL's own databases. Presenting them as ingestible would be noise at best.
SYSTEM_SCHEMAS: frozenset[str] = frozenset(
    {"information_schema", "performance_schema", "mysql", "sys"}
)

#: Types that can carry an event time. `year` is excluded deliberately: it has
#: no month or day, so any instant derived from it would be invented.
TEMPORAL_DATA_TYPES: frozenset[str] = frozenset({"timestamp", "datetime", "date"})

_TABLE_TYPE_LABELS: dict[str, str] = {
    "BASE TABLE": "table",
    "VIEW": "view",
    "SYSTEM VIEW": "view",
}


def quote_identifier(name: str) -> str:
    """Backtick-quote a MySQL identifier.

    A backtick in the input is rejected rather than escaped. Doubling it would
    be correct MySQL, but these names come from stream configuration and a
    legitimate table name does not contain one — refusing is a smaller surface
    than escaping, and it fails loudly instead of silently producing a
    different identifier than intended.
    """
    if "`" in name or "\x00" in name:
        raise ConnectorError(
            ConnectorErrorCode.INVALID_CONFIGURATION,
            "A table or column name contains characters RealitySync cannot use safely.",
            detail=f"rejected identifier: {name!r}",
        )
    return f"`{name}`"


class MysqlConnector(DataConnector):
    """Read-only MySQL connector."""

    kind = "mysql"
    version = "1"

    def __init__(
        self,
        *,
        config: MysqlConnectionConfig,
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

        self._connection: aiomysql.Connection | None = None
        self._connected_at: datetime | None = None
        self._latency_ms: int | None = None
        self._last_error: ConnectorError | None = None

    def __repr__(self) -> str:
        # No password, no credentials.
        return f"<MysqlConnector target={self._config.display_target}>"

    # --- Connection -------------------------------------------------------

    async def connect(self) -> None:
        if self._connection is not None and not self._connection.closed:
            return

        started = time.perf_counter()
        try:
            self._connection = await aiomysql.connect(
                host=self._config.host,
                port=self._config.port,
                db=self._config.database,
                user=self._config.username,
                password=self._password,
                # An explicit context, never None: aiomysql connects in
                # plaintext when given no SSL argument, so this is what makes
                # the TLS requirement real on the client side.
                ssl=self._config.build_ssl_context(),
                connect_timeout=self._connect_timeout,
                autocommit=True,
                charset="utf8mb4",
                program_name="realitysync",
            )
        except Exception as exc:
            error = map_exception(exc, operation="connect")
            self._last_error = error
            logger.warning(
                "connector.mysql.connect_failed",
                code=error.code.value,
                target=self._config.display_target,
                detail=error.detail,
            )
            raise error from exc

        try:
            await self._apply_session_guards()
        except Exception as exc:
            # A connection we cannot make read-only is not one we are willing
            # to keep. Closing it here means no code path can hold a writable
            # handle to a customer's database.
            await self.disconnect()
            error = map_exception(exc, operation="connect")
            self._last_error = error
            raise error from exc

        self._latency_ms = int((time.perf_counter() - started) * 1000)
        self._connected_at = datetime.now(UTC)
        self._last_error = None

    async def _apply_session_guards(self) -> None:
        """Make the session read-only and bound in time.

        PostgreSQL takes both as connection options; MySQL needs statements.
        ``max_execution_time`` is milliseconds and applies to SELECTs, which is
        exactly the statement class this connector issues.
        """
        connection = self._require_connection()
        async with connection.cursor() as cursor:
            await cursor.execute("SET SESSION TRANSACTION READ ONLY")
            await cursor.execute(
                "SET SESSION max_execution_time = %s", (self._statement_timeout * 1000,)
            )

    async def disconnect(self) -> None:
        """Close the connection. Never raises — it runs in cleanup paths where
        an exception would mask the error that sent us there."""
        connection = self._connection
        self._connection = None
        if connection is None or connection.closed:
            return
        try:
            connection.close()
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("connector.mysql.disconnect_failed", error=str(exc))

    def _require_connection(self) -> aiomysql.Connection:
        if self._connection is None or self._connection.closed:
            raise ConnectorError(
                ConnectorErrorCode.UNKNOWN,
                "The connector is not connected.",
                detail="Operation attempted before connect()",
            )
        return self._connection

    # --- Test -------------------------------------------------------------

    async def test_connection(self) -> ConnectionTestResult:
        """Verify reachability, TLS, authentication and catalog access."""
        started = time.perf_counter()
        await self.connect()
        connection = self._require_connection()

        try:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT DATABASE()  AS `database`,
                           CURRENT_USER() AS connected_as,
                           VERSION()    AS server_version
                    """
                )
                info = dict(await cursor.fetchone() or {})

                # The negotiated cipher, read from the session rather than
                # assumed from the requested mode. Empty means the session is
                # not encrypted.
                await cursor.execute("SHOW SESSION STATUS LIKE 'Ssl_version'")
                ssl_row = dict(await cursor.fetchone() or {})
                await cursor.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
                cipher_row = dict(await cursor.fetchone() or {})
        except Exception as exc:
            error = map_exception(exc, operation="test_connection")
            self._last_error = error
            raise error from exc

        tls_version = str(ssl_row.get("Value") or "") or None
        cipher = str(cipher_row.get("Value") or "")

        # Checked rather than assumed. This is the guarantee the whole TLS
        # policy rests on, and the server is the only authority on whether the
        # session was actually encrypted.
        if not tls_version or not cipher:
            raise ConnectorError(
                ConnectorErrorCode.TLS_FAILED,
                "The connection was established without encryption.",
                detail=f"Ssl_version={tls_version!r} Ssl_cipher={cipher!r}",
                remediation="RealitySync requires TLS. Enable it on the source database.",
            )

        latency_ms = int((time.perf_counter() - started) * 1000)

        warnings: list[str] = []
        can_discover = await self._can_read_catalog()
        if not can_discover:
            warnings.append(
                "This account cannot see any tables, so schema discovery will "
                "return nothing. Grant SELECT on the database you want to sync."
            )

        version = str(info.get("server_version") or "")
        return ConnectionTestResult(
            status="connected",
            database=_as_text(info.get("database")),
            server_version=f"MySQL {version}" if version else None,
            latency_ms=latency_ms,
            tls_version=tls_version,
            connected_as=_as_text(info.get("connected_as")),
            can_discover_schema=can_discover,
            warnings=tuple(warnings),
        )

    async def _can_read_catalog(self) -> bool:
        """Whether this account can see any non-system table.

        information_schema is readable by everyone, but it is filtered by
        privilege: an account with no grants sees no rows. That makes "can this
        account discover anything" answerable without needing a grant to ask.
        """
        connection = self._require_connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM information_schema.tables
                    WHERE table_schema NOT IN %s
                    """,
                    (tuple(SYSTEM_SCHEMAS),),
                )
                row = await cursor.fetchone()
        except Exception:
            return False
        return bool(row and int(row[0]) > 0)

    # --- Discovery --------------------------------------------------------

    async def discover_schema(self, *, include_system_schemas: bool = False) -> DiscoveredSchema:
        """Describe the database from information_schema.

        Metadata only. Row counts come from ``information_schema.tables``,
        which is the storage engine's estimate — never ``COUNT(*)``, because
        discovery runs against a customer's production database and scanning
        every table to describe it would be an outage caused by a
        configuration screen.
        """
        await self.connect()
        connection = self._require_connection()

        try:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                # Aliased to lower case: MySQL labels an unaliased
                # information_schema column in upper case, and DictCursor keys
                # come back exactly as the server labelled them.
                if include_system_schemas:
                    await cursor.execute(
                        "SELECT schema_name AS schema_name "
                        "FROM information_schema.schemata ORDER BY schema_name"
                    )
                else:
                    await cursor.execute(
                        "SELECT schema_name AS schema_name "
                        "FROM information_schema.schemata "
                        "WHERE schema_name NOT IN %s ORDER BY schema_name",
                        (tuple(SYSTEM_SCHEMAS),),
                    )
                schema_rows = [_as_text(r["schema_name"]) or "" for r in await cursor.fetchall()]

                await cursor.execute(
                    self._COLUMN_QUERY
                    if include_system_schemas
                    else self._COLUMN_QUERY_EXCLUDING_SYSTEM,
                    () if include_system_schemas else (tuple(SYSTEM_SCHEMAS),),
                )
                tables = self._assemble_tables(await cursor.fetchall())
        except Exception as exc:
            error = map_exception(exc, operation="discover_schema")
            self._last_error = error
            raise error from exc

        # information_schema hides what the account cannot see, so a schema
        # that exists but is invisible simply does not appear. There is no way
        # to distinguish "absent" from "not permitted" without a privilege this
        # account should not have, so nothing is reported as inaccessible
        # rather than guessing.
        logger.info(
            "connector.mysql.schema_discovered",
            target=self._config.display_target,
            schemas=len(schema_rows),
            tables=len(tables),
        )

        return DiscoveredSchema(
            tables=tuple(tables),
            schemas=tuple(schema_rows),
            inaccessible_schemas=(),
            discovered_at=datetime.now(UTC),
        )

    _COLUMN_SELECT = """
        SELECT
            c.table_schema                              AS schema_name,
            c.table_name                                AS table_name,
            c.column_name                               AS column_name,
            c.data_type                                 AS data_type,
            c.column_type                               AS column_type,
            c.is_nullable                               AS is_nullable,
            c.column_default                            AS column_default,
            c.ordinal_position                          AS ordinal_position,
            c.column_key                                AS column_key,
            t.table_type                                AS table_type,
            t.table_rows                                AS table_rows,
            t.table_comment                             AS table_comment
        FROM information_schema.columns c
        JOIN information_schema.tables t
          ON t.table_schema = c.table_schema AND t.table_name = c.table_name
    """

    _COLUMN_QUERY = _COLUMN_SELECT + " ORDER BY c.table_schema, c.table_name, c.ordinal_position"

    _COLUMN_QUERY_EXCLUDING_SYSTEM = (
        _COLUMN_SELECT
        + " WHERE c.table_schema NOT IN %s"
        + " ORDER BY c.table_schema, c.table_name, c.ordinal_position"
    )

    def _assemble_tables(self, rows: list[dict[str, Any]]) -> list[DiscoveredTable]:
        grouped: dict[tuple[str, str], dict[str, Any]] = {}

        for row in rows:
            schema_name = _as_text(row["schema_name"]) or ""
            table_name = _as_text(row["table_name"]) or ""
            key = (schema_name, table_name)
            entry = grouped.setdefault(
                key,
                {
                    "columns": [],
                    "table_type": _as_text(row["table_type"]) or "BASE TABLE",
                    "table_rows": row["table_rows"],
                    "comment": _as_text(row["table_comment"]) or None,
                },
            )

            data_type = (_as_text(row["data_type"]) or "").lower()
            column_key = (_as_text(row["column_key"]) or "").upper()
            entry["columns"].append(
                DiscoveredColumn(
                    name=_as_text(row["column_name"]) or "",
                    # column_type carries the precision — `decimal(12,3)`
                    # rather than `decimal` — which is what an operator needs
                    # to see when choosing identity columns.
                    data_type=_as_text(row["column_type"]) or data_type,
                    nullable=(_as_text(row["is_nullable"]) or "").upper() == "YES",
                    # MySQL exposes only "is this part of the primary key", not
                    # its position within a composite one. Ordinal position is
                    # the closest honest answer, and ordering by it reproduces
                    # the key order for the common case.
                    primary_key_position=(
                        int(row["ordinal_position"]) if column_key == "PRI" else None
                    ),
                    is_temporal=data_type in TEMPORAL_DATA_TYPES,
                    default=_as_text(row["column_default"]),
                )
            )

        return [
            DiscoveredTable(
                schema_name=schema_name,
                table_name=table_name,
                columns=tuple(entry["columns"]),
                approximate_row_count=(
                    int(entry["table_rows"]) if entry["table_rows"] is not None else None
                ),
                kind=_TABLE_TYPE_LABELS.get(str(entry["table_type"]), "table"),
                comment=entry["comment"],
            )
            for (schema_name, table_name), entry in grouped.items()
        ]

    # --- Reading ----------------------------------------------------------

    def _build_select(self, selector: StreamSelector) -> tuple[str, list[Any]]:
        """Compose the read query.

        Identifiers are backtick-quoted by ``quote_identifier``; values are
        bound parameters. No value is ever formatted into the statement.
        """
        relation = (
            f"{quote_identifier(selector.schema_name)}.{quote_identifier(selector.table_name)}"
        )

        if selector.selected_columns:
            # The identity and event-time columns are always needed, even if
            # the operator did not tick them: without them a row has no
            # identity and no time.
            required = list(selector.selected_columns)
            for column in (*selector.primary_key_columns, selector.event_time_column):
                if column and column not in required:
                    required.append(column)
            projection = ", ".join(quote_identifier(c) for c in required)
        else:
            projection = "*"

        params: list[Any] = []
        where = ""
        if selector.event_time_column and selector.since_event_time is not None:
            # >= rather than >, for the same reason as PostgreSQL: a source
            # with second granularity can write several rows at one instant,
            # and > would skip the ones after the cursor. Re-reading is free
            # because the fingerprint makes it idempotent; skipping is not.
            where = f" WHERE {quote_identifier(selector.event_time_column)} >= %s"
            params.append(selector.since_event_time)

        order_columns = [
            *([selector.event_time_column] if selector.event_time_column else []),
            *selector.primary_key_columns,
        ]
        order_by = " ORDER BY " + ", ".join(quote_identifier(c) for c in order_columns)

        limit = ""
        if selector.limit is not None:
            limit = " LIMIT %s"
            params.append(int(selector.limit))

        # Every identifier in this string passed through quote_identifier,
        # which rejects backticks and nulls outright, and every *value* is a
        # bound parameter — nothing user-supplied is formatted in unvalidated.
        # The PostgreSQL connector avoids this warning by composing with
        # psycopg.sql; aiomysql offers no equivalent, so the safety lives in
        # the quoting function instead.
        statement = f"SELECT {projection} FROM {relation}{where}{order_by}{limit}"  # noqa: S608
        return statement, params

    async def _iterate(self, selector: StreamSelector) -> AsyncIterator[SourceRecord]:
        await self.connect()
        connection = self._require_connection()
        query, params = self._build_select(selector)

        try:
            # Unbuffered: aiomysql's default cursor reads the entire result
            # into client memory before yielding anything, which would defeat
            # the point of streaming a table larger than memory.
            async with connection.cursor(aiomysql.SSDictCursor) as cursor:
                await cursor.execute(query, tuple(params) if params else None)
                while True:
                    rows = await cursor.fetchmany(self._fetch_batch_size)
                    if not rows:
                        break
                    for row in rows:
                        yield self._to_record(dict(row), selector)
        except Exception as exc:
            error = map_exception(exc, operation="fetch_data")
            self._last_error = error
            raise error from exc

    def _to_record(self, row: dict[str, Any], selector: StreamSelector) -> SourceRecord:
        """Turn a driver row into a SourceRecord.

        Values stay close to driver-native; canonical normalisation happens in
        the ingestion layer so every connector produces identical observations
        for identical values. The one adjustment is bytes, which MySQL returns
        for binary columns and which no JSON payload can carry.
        """
        missing = [c for c in selector.primary_key_columns if c not in row]
        if missing:
            raise ConnectorError(
                ConnectorErrorCode.NOT_FOUND,
                "A configured identity column is missing from the source table.",
                detail=f"missing primary key columns: {missing}",
                remediation="Run schema discovery again and reconfigure the stream.",
            )

        values = {key: _coerce(value) for key, value in row.items()}
        external_id = build_external_id(
            {column: values[column] for column in selector.primary_key_columns}
        )

        event_time: datetime | None = None
        if selector.event_time_column:
            raw = row.get(selector.event_time_column)
            if isinstance(raw, datetime):
                # MySQL DATETIME carries no offset and TIMESTAMP is returned
                # already converted to the session time zone. Assuming UTC is
                # the only defensible choice; guessing a local zone would
                # silently shift every event.
                event_time = raw if raw.tzinfo else raw.replace(tzinfo=UTC)
            elif isinstance(raw, date):
                # A DATE column names a day, not an instant. Midnight UTC is
                # the start of that day rather than an invented time within it.
                event_time = datetime(raw.year, raw.month, raw.day, tzinfo=UTC)

        return SourceRecord(external_id=external_id, values=values, event_time=event_time)

    def fetch_data(self, selector: StreamSelector) -> AsyncIterator[SourceRecord]:
        """Every row matching the selector."""
        return self._iterate(selector)

    def fetch_changes(self, selector: StreamSelector) -> AsyncIterator[SourceRecord]:
        """Rows at or after ``selector.since_event_time``.

        A filtered read, not a change feed. MySQL has binlog-based replication,
        but consuming it needs REPLICATION SLAVE privilege and a durable
        position — neither of which belongs in a least-privilege read-only
        account. The same two consequences as PostgreSQL apply, and are stated
        plainly because a caller who assumes otherwise will be wrong:

        * **Deletes are invisible.** A removed row produces no record, and its
          existing observations remain — correctly, since they were true when
          made.
        * **A row whose event time does not advance on update is invisible.**
          Configure those streams without incremental reads.
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


def _as_text(value: Any) -> str | None:
    """information_schema columns come back as str or bytes depending on the
    server's collation handling. Normalised here so callers see one type."""
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _coerce(value: Any) -> Any:
    """Make a driver value representable in an observation payload.

    Only bytes are converted. Decimal, datetime and date are left alone — the
    ingestion layer normalises them, and converting a Decimal here would lose
    the scale the source declared, which is precisely the kind of silent
    change this product exists to catch.
    """
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            # Binary that is not text. Hex keeps it comparable and stable
            # across syncs, which is what the fingerprint needs.
            return value.hex()
    if isinstance(value, (Decimal, datetime, date)):
        return value
    return value
