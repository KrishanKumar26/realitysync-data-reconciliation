"""Shared test fixtures.

No product fixtures exist and none should: Phase 0 §25 forbids mock business
data anywhere the application can reach. Every user and organization in these
tests is created by calling the real registration endpoint, so the tests
exercise the same code path production does — there is no seeding shortcut that
could pass while the real flow is broken.

Isolation comes from a transaction per test. Each test runs inside an outer
transaction that is rolled back at the end, and the application's own
``commit()`` calls land on a savepoint inside it. Tests therefore see committed
behaviour without leaving rows behind, and they do not need to clean up after
each other.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest

os.environ.setdefault("ENVIRONMENT", "test")
# Origins the tests exercise. testserver is httpx's ASGI host; the localhost
# entry keeps the shape realistic.
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000,http://testserver")
# Argon2id at production cost (19 MiB, t=2) would add roughly a quarter second
# to every password operation, and the suite performs many. Correctness does
# not depend on the cost — only the time an attacker needs does — so tests run
# at the cheapest valid setting. The production values live in Settings and are
# covered by their own assertion in test_security.py.
os.environ.setdefault("ARGON2_TIME_COST", "1")
os.environ.setdefault("ARGON2_MEMORY_COST_KIB", "8")
os.environ.setdefault("ARGON2_PARALLELISM", "1")

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession


@pytest.fixture(scope="session")
def settings():  # type: ignore[no-untyped-def]
    from app.core.config import get_settings

    return get_settings()


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


# --- Database --------------------------------------------------------------


@pytest.fixture
async def db_connection() -> AsyncIterator[AsyncConnection]:
    """A connection wrapped in a transaction that is always rolled back."""
    from app.db.session import get_engine

    engine = get_engine()
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            yield connection
        finally:
            await transaction.rollback()


@pytest.fixture
async def db(db_connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    """A session bound to the test transaction.

    ``join_transaction_mode="create_savepoint"`` makes the application's
    ``commit()`` release a savepoint rather than end the outer transaction, so
    routes commit normally and the whole test still rolls back.
    """
    session = AsyncSession(
        bind=db_connection,
        expire_on_commit=False,
        autoflush=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        await session.close()


# --- Application -----------------------------------------------------------


@pytest.fixture
def app(db: AsyncSession) -> Iterator[FastAPI]:
    """The real application, with its database dependency pointed at `db`."""
    from app.db.session import get_session
    from app.main import create_app

    application = create_app()

    async def _override() -> AsyncIterator[AsyncSession]:
        yield db

    application.dependency_overrides[get_session] = _override
    try:
        yield application
    finally:
        application.dependency_overrides.clear()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """An HTTP client with a cookie jar, so sessions persist across requests."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client


@pytest.fixture
async def anonymous_client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """A second client with an independent cookie jar.

    Needed for multi-tenancy tests: two signed-in users have to exist at the
    same time, and a shared jar would let one overwrite the other's session.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client


# --- Source database -------------------------------------------------------
# Fixtures for the disposable *source* PostgreSQL that connector tests read
# from. See tests/source_db.py for the distinction between test infrastructure
# and product data.
pytest_plugins = ["tests.source_db", "tests.source_mysql"]
