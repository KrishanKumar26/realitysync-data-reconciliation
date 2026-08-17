"""Factory for the PostgreSQL connector.

The single point where a decrypted password enters a connector. Keeping it in
one small, obvious place means the credential's path through the system can be
reviewed by reading one file.
"""

from __future__ import annotations

from typing import Any

from app.connectors.base import ConnectorFactory, DataConnector
from app.connectors.postgres.config import parse_config, validate_password
from app.connectors.postgres.connector import PostgresConnector
from app.core.config import get_settings


class PostgresConnectorFactory(ConnectorFactory):
    """Builds PostgresConnector instances from stored configuration."""

    def build(self, *, config: dict[str, Any], credentials: dict[str, Any]) -> DataConnector:
        # Validation before construction: a malformed configuration should fail
        # immediately with a specific message, not as a driver error after a
        # connection timeout.
        parsed = parse_config(config)
        password = validate_password(credentials.get("password"))
        settings = get_settings()

        return PostgresConnector(
            config=parsed,
            password=password,
            connect_timeout_seconds=settings.connector_connect_timeout_seconds,
            statement_timeout_seconds=settings.connector_statement_timeout_seconds,
            fetch_batch_size=settings.connector_fetch_batch_size,
            allow_private_hosts=settings.connector_allow_private_hosts,
        )
