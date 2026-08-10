"""Fixtures for the disposable source database.

**Test infrastructure, not product data.** These helpers create real tables in
a real PostgreSQL that RealitySync connects to exactly as it would connect to a
customer's database — over TCP, with TLS, using stored credentials.

The distinction matters and is worth being explicit about:

* Tables created here are the *source system's* data, standing in for a
  customer's. Creating them is how the connector gets something real to read.
* RealitySync's own database is never seeded. Every observation in these tests
  is produced by the connector reading an actual row over an actual
  connection — not inserted by a fixture.

A fabricated observation would make these tests pass while the connector was
broken, which is the one outcome they exist to prevent.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import psycopg
import pytest

#: Defaults match the `source-postgres` service in docker-compose.yml. Override
#: to point the suite at any other PostgreSQL that has TLS enabled.
SOURCE_HOST = os.environ.get("TEST_SOURCE_HOST", "localhost")
SOURCE_PORT = int(os.environ.get("TEST_SOURCE_PORT", "5434"))
SOURCE_DB = os.environ.get("TEST_SOURCE_DB", "source_demo")
SOURCE_OWNER = os.environ.get("TEST_SOURCE_OWNER", "source_owner")
SOURCE_OWNER_PASSWORD = os.environ.get("TEST_SOURCE_OWNER_PASSWORD", "change-me-locally")
SOURCE_READER = os.environ.get("TEST_SOURCE_READER", "realitysync_reader")
SOURCE_READER_PASSWORD = os.environ.get("TEST_SOURCE_READER_PASSWORD", "change-me-locally")

#: The connector refuses anything weaker, and the source rejects plaintext.
SOURCE_SSL_MODE = os.environ.get("TEST_SOURCE_SSL_MODE", "require")

SKIP_REASON = (
    f"No source PostgreSQL at {SOURCE_HOST}:{SOURCE_PORT}. "
    "Start it with:  docker compose --profile dev-source up -d source-postgres  "
    "(or set TEST_SOURCE_HOST / TEST_SOURCE_PORT to another TLS-enabled server)."
)


def owner_dsn() -> str:
    return (
        f"host={SOURCE_HOST} port={SOURCE_PORT} dbname={SOURCE_DB} "
        f"user={SOURCE_OWNER} password={SOURCE_OWNER_PASSWORD} sslmode={SOURCE_SSL_MODE}"
    )


def reader_config() -> dict[str, object]:
    """The connection RealitySync stores, as an operator would enter it.

    Reads as the least-privilege role, so the tests exercise the permissions a
    customer would actually grant rather than an owner's.
    """
    return {
        "host": SOURCE_HOST,
        "port": SOURCE_PORT,
        "database": SOURCE_DB,
        "username": SOURCE_READER,
        "ssl_mode": SOURCE_SSL_MODE,
    }


def reader_credentials() -> dict[str, object]:
    return {"password": SOURCE_READER_PASSWORD}


async def source_available() -> bool:
    try:
        connection = await psycopg.AsyncConnection.connect(owner_dsn(), connect_timeout=5)
    except Exception:
        return False
    await connection.close()
    return True


@dataclass(frozen=True, slots=True)
class SourceTable:
    """A real table in the source database, created for one test."""

    schema_name: str
    table_name: str

    @property
    def qualified(self) -> str:
        return f"{self.schema_name}.{self.table_name}"


@pytest.fixture(scope="session")
async def require_source_db() -> None:
    """Skip the module when no source database is reachable.

    Skipped, never silently passed: a connector test that reports success
    without connecting to anything is worse than no test.
    """
    if not await source_available():
        pytest.skip(SKIP_REASON, allow_module_level=True)


@pytest.fixture
async def source_table(require_source_db: None) -> AsyncIterator[SourceTable]:
    """Create a uniquely-named table in the source database, and drop it after.

    Unique names let tests run concurrently against one database, and mean a
    crashed test leaves one identifiable table behind rather than corrupting a
    shared one.
    """
    table = SourceTable(schema_name="public", table_name=f"rs_test_{uuid.uuid4().hex[:12]}")

    async with await psycopg.AsyncConnection.connect(owner_dsn(), autocommit=True) as conn:
        await conn.execute(
            f"""
            CREATE TABLE {table.qualified} (
                record_id   bigint PRIMARY KEY,
                reference   text NOT NULL,
                status      text NOT NULL,
                amount      numeric(12,3),
                observed_at timestamptz NOT NULL,
                notes       text
            )
            """
        )
        # The reader role gets SELECT through ALTER DEFAULT PRIVILEGES, but
        # granting explicitly keeps the fixture working against a source that
        # was set up without those defaults.
        await conn.execute(f"GRANT SELECT ON {table.qualified} TO {SOURCE_READER}")

    try:
        yield table
    finally:
        async with await psycopg.AsyncConnection.connect(owner_dsn(), autocommit=True) as conn:
            await conn.execute(f"DROP TABLE IF EXISTS {table.qualified}")


async def insert_rows(table: SourceTable, rows: list[dict[str, object]]) -> None:
    """Insert rows into the source table, as the source system would."""
    async with await psycopg.AsyncConnection.connect(owner_dsn(), autocommit=True) as conn:
        for row in rows:
            await conn.execute(
                f"""
                INSERT INTO {table.qualified}
                    (record_id, reference, status, amount, observed_at, notes)
                VALUES (%(record_id)s, %(reference)s, %(status)s, %(amount)s,
                        %(observed_at)s, %(notes)s)
                """,  # noqa: S608 - identifier is generated, values are bound
                {"notes": None, "amount": None, **row},
            )


async def execute_on_source(
    table: SourceTable, statement: str, params: dict[str, object] | None = None
) -> None:
    """Run a statement against the source, for update and delete scenarios."""
    async with await psycopg.AsyncConnection.connect(owner_dsn(), autocommit=True) as conn:
        await conn.execute(statement.format(table=table.qualified), params or {})
