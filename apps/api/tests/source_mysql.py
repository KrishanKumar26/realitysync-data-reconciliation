"""Fixtures for the disposable source MySQL.

**Test infrastructure, not product data.** Same contract as
``tests/source_db.py``: real tables in a real server that RealitySync connects
to over TLS with stored credentials, exactly as it would connect to a
customer's database. RealitySync's own database is never seeded — every
observation these tests assert on was read from an actual row over an actual
connection.
"""

from __future__ import annotations

import os
import ssl
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import aiomysql
import pytest

#: Defaults match the `source-mysql` service in docker-compose.yml. Override to
#: point the suite at any other MySQL with TLS enabled.
MYSQL_HOST = os.environ.get("TEST_SOURCE_MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.environ.get("TEST_SOURCE_MYSQL_PORT", "3308"))
MYSQL_DB = os.environ.get("TEST_SOURCE_MYSQL_DB", "source_demo")
MYSQL_ROOT = os.environ.get("TEST_SOURCE_MYSQL_ROOT", "root")
MYSQL_ROOT_PASSWORD = os.environ.get("TEST_SOURCE_MYSQL_ROOT_PASSWORD", "change-me-locally")
MYSQL_READER = os.environ.get("TEST_SOURCE_MYSQL_READER", "realitysync_reader")
MYSQL_READER_PASSWORD = os.environ.get("TEST_SOURCE_MYSQL_READER_PASSWORD", "change-me-locally")

#: The connector refuses anything weaker, and the reader account is created
#: with REQUIRE SSL so the server refuses plaintext for it too.
MYSQL_SSL_MODE = os.environ.get("TEST_SOURCE_MYSQL_SSL_MODE", "require")

MYSQL_SKIP_REASON = (
    f"No source MySQL at {MYSQL_HOST}:{MYSQL_PORT}. "
    "Start it with:  docker compose --profile dev-source up -d source-mysql  "
    "(or set TEST_SOURCE_MYSQL_HOST / TEST_SOURCE_MYSQL_PORT to another TLS-enabled server)."
)


def _permissive_tls() -> ssl.SSLContext:
    """TLS without certificate verification, for the fixture's own connections.

    The server presents a self-signed certificate it generated at
    initialisation. The *connector* under test builds its own context from the
    configured ssl_mode; this one only exists so the fixture can create and
    drop tables.
    """
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def mysql_reader_config() -> dict[str, object]:
    """Connector configuration for the least-privilege reader."""
    return {
        "host": MYSQL_HOST,
        "port": MYSQL_PORT,
        "database": MYSQL_DB,
        "username": MYSQL_READER,
        "ssl_mode": MYSQL_SSL_MODE,
    }


def mysql_reader_credentials() -> dict[str, object]:
    return {"password": MYSQL_READER_PASSWORD}


async def _root_connection() -> aiomysql.Connection:
    return await aiomysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        db=MYSQL_DB,
        user=MYSQL_ROOT,
        password=MYSQL_ROOT_PASSWORD,
        ssl=_permissive_tls(),
        autocommit=True,
        charset="utf8mb4",
    )


async def mysql_available() -> bool:
    try:
        connection = await _root_connection()
    except Exception:
        return False
    connection.close()
    return True


@dataclass(frozen=True, slots=True)
class MysqlSourceTable:
    """A real table in the source MySQL, created for one test."""

    schema_name: str
    table_name: str

    @property
    def qualified(self) -> str:
        return f"`{self.schema_name}`.`{self.table_name}`"


@pytest.fixture(scope="session")
async def require_source_mysql() -> None:
    """Skip when no source MySQL is reachable.

    Skipped, never silently passed: a connector test that reports success
    without connecting to anything is worse than no test.
    """
    if not await mysql_available():
        pytest.skip(MYSQL_SKIP_REASON, allow_module_level=True)


@pytest.fixture
async def mysql_table(require_source_mysql: None) -> AsyncIterator[MysqlSourceTable]:
    """Create a uniquely-named table in the source MySQL, and drop it after.

    Deliberately mirrors the PostgreSQL fixture's shape — same columns, same
    semantics — so a test can assert that two different source *types* holding
    the same values produce equivalent observations.
    """
    table = MysqlSourceTable(schema_name=MYSQL_DB, table_name=f"rs_test_{uuid.uuid4().hex[:12]}")

    connection = await _root_connection()
    try:
        async with connection.cursor() as cursor:
            await cursor.execute(
                f"""
                CREATE TABLE {table.qualified} (
                    record_id   BIGINT PRIMARY KEY,
                    reference   VARCHAR(64) NOT NULL,
                    status      VARCHAR(32) NOT NULL,
                    amount      DECIMAL(12,3),
                    observed_at DATETIME NOT NULL,
                    notes       TEXT
                )
                """
            )
            # The reader's SELECT grant on source_demo.* covers tables created
            # later, so no per-table grant is needed. Asserted by the tests
            # rather than assumed.
    finally:
        connection.close()

    try:
        yield table
    finally:
        connection = await _root_connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(f"DROP TABLE IF EXISTS {table.qualified}")
        finally:
            connection.close()


async def insert_mysql_rows(table: MysqlSourceTable, rows: list[dict[str, Any]]) -> None:
    """Insert rows into the source table, as the source system would."""
    connection = await _root_connection()
    try:
        async with connection.cursor() as cursor:
            for row in rows:
                merged: dict[str, Any] = {"notes": None, "amount": None, **row}
                await cursor.execute(
                    f"""
                    INSERT INTO {table.qualified}
                        (record_id, reference, status, amount, observed_at, notes)
                    VALUES (%(record_id)s, %(reference)s, %(status)s, %(amount)s,
                            %(observed_at)s, %(notes)s)
                    """,  # noqa: S608 - identifier is generated, values are bound
                    merged,
                )
    finally:
        connection.close()


async def mysql_catalog_row_estimate(table: MysqlSourceTable) -> int | None:
    """The storage engine's row estimate, straight from information_schema.

    Lets a test assert that discovery reported *this* number rather than a
    number it obtained by scanning the table.
    """
    connection = await _root_connection()
    try:
        async with connection.cursor() as cursor:
            await cursor.execute(
                "SELECT table_rows FROM information_schema.tables "
                "WHERE table_schema = %s AND table_name = %s",
                (table.schema_name, table.table_name),
            )
            row = await cursor.fetchone()
    finally:
        connection.close()
    return None if row is None or row[0] is None else int(row[0])


async def execute_on_mysql(statement: str, params: tuple[Any, ...] | None = None) -> None:
    """Run a statement against the source, for update and delete scenarios."""
    connection = await _root_connection()
    try:
        async with connection.cursor() as cursor:
            await cursor.execute(statement, params)
    finally:
        connection.close()
