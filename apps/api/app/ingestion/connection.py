"""Opening a connector for a stored source.

Extracted from the data-sources routes so the scheduler can reach it without
importing an HTTP layer. This is the only path that turns a stored, encrypted
credential into a live connection, and keeping it in one function keeps the
number of places that hold plaintext at one.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import DataConnector
from app.connectors.registry import build_connector
from app.models.data_source import DataSource
from app.services.credentials import load_credentials


async def open_connector(db: AsyncSession, source: DataSource) -> DataConnector:
    """Build and connect a connector for `source`.

    The plaintext credential exists as a local for the duration of one call and
    is handed straight to the factory. It is never returned, logged or stored.
    """
    credentials = await load_credentials(db, data_source=source)
    connector = build_connector(kind=source.kind, config=source.config, credentials=credentials)
    await connector.connect()
    return connector
