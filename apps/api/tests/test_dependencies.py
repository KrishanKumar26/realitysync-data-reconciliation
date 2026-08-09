"""Live dependency connectivity.

These tests require PostgreSQL and Redis to be reachable. Run them with the
Docker Compose stack up:

    docker compose up -d postgres redis
    pytest -m integration
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.cache.redis import close_redis, get_redis
from app.db.session import dispose_engine, get_sessionmaker

pytestmark = pytest.mark.integration


async def test_postgres_is_reachable() -> None:
    try:
        async with get_sessionmaker()() as session:
            result = await session.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
    finally:
        await dispose_engine()


async def test_citext_extension_is_installed() -> None:
    """Migration 0001 must have run against this database."""
    try:
        async with get_sessionmaker()() as session:
            result = await session.execute(
                text("SELECT COUNT(*) FROM pg_extension WHERE extname = 'citext'")
            )
            assert result.scalar_one() == 1
    finally:
        await dispose_engine()


async def test_alembic_version_is_recorded() -> None:
    """The migration system must have stamped this database."""
    try:
        async with get_sessionmaker()() as session:
            result = await session.execute(text("SELECT version_num FROM alembic_version"))
            assert result.scalar_one() == "0001_foundation"
    finally:
        await dispose_engine()


async def test_redis_is_reachable() -> None:
    try:
        assert await get_redis().ping() is True
    finally:
        await close_redis()


async def test_redis_round_trip() -> None:
    client = get_redis()
    try:
        await client.set("rs:test:foundation", "ok", ex=10)
        assert await client.get("rs:test:foundation") == "ok"
        await client.delete("rs:test:foundation")
    finally:
        await close_redis()


async def test_ready_endpoint_against_live_dependencies(client: AsyncClient) -> None:
    try:
        response = await client.get("/ready")

        assert response.status_code == 200
        body = response.json()
        assert body == {
            **body,
            "status": "ready",
            "database": "ok",
            "redis": "ok",
        }
    finally:
        await dispose_engine()
        await close_redis()
