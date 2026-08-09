"""Shared test fixtures.

No product fixtures exist and none should: Phase 0 §25 forbids mock business
data anywhere the application can reach. These fixtures wire the app and its
dependencies, nothing more.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("ENVIRONMENT", "test")


@pytest.fixture(scope="session")
def settings():  # type: ignore[no-untyped-def]
    from app.core.config import get_settings

    return get_settings()


@pytest.fixture
def app() -> Iterator[FastAPI]:
    from app.main import create_app

    yield create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client
