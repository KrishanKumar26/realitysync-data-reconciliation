"""Factory for the MySQL connector.

The single point where a decrypted password enters a MySQL connector. Same
shape as the PostgreSQL factory for the same reason: the credential's path
through the system should be reviewable by reading one small file.
"""

from __future__ import annotations

from typing import Any

from app.connectors.base import ConnectorFactory, DataConnector
from app.connectors.mysql.config import parse_config, validate_password
from app.connectors.mysql.connector import MysqlConnector
from app.core.config import get_settings


class MysqlConnectorFactory(ConnectorFactory):
    """Builds MysqlConnector instances from stored configuration."""

    def build(self, *, config: dict[str, Any], credentials: dict[str, Any]) -> DataConnector:
        # Validation before construction: a malformed configuration should fail
        # immediately with a specific message, not as a driver error after a
        # connection timeout.
        parsed = parse_config(config)
        password = validate_password(credentials.get("password"))
        settings = get_settings()

        return MysqlConnector(
            config=parsed,
            password=password,
            connect_timeout_seconds=settings.connector_connect_timeout_seconds,
            statement_timeout_seconds=settings.connector_statement_timeout_seconds,
            fetch_batch_size=settings.connector_fetch_batch_size,
        )
