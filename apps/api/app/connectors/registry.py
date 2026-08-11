"""Connector registry.

Maps a source kind to its factory. The only place that knows which
implementations exist, so ingestion resolves a connector by string and never
imports PostgreSQL — or anything else specific — directly.
"""

from __future__ import annotations

from typing import Any

from app.connectors.base import ConnectorFactory, DataConnector
from app.connectors.types import ConnectorError, ConnectorErrorCode

_FACTORIES: dict[str, ConnectorFactory] = {}


def register(kind: str, factory: ConnectorFactory) -> None:
    """Register a factory for `kind`."""
    _FACTORIES[kind] = factory


def supported_kinds() -> tuple[str, ...]:
    return tuple(sorted(_FACTORIES))


def build_connector(
    *, kind: str, config: dict[str, Any], credentials: dict[str, Any]
) -> DataConnector:
    """Construct a connector for `kind`.

    `credentials` is decrypted plaintext. It goes straight into the connector
    and must not be logged, echoed or retained by the caller.
    """
    factory = _FACTORIES.get(kind)
    if factory is None:
        raise ConnectorError(
            ConnectorErrorCode.INVALID_CONFIGURATION,
            f"No connector is available for source type '{kind}'.",
            remediation=f"Supported types: {', '.join(supported_kinds()) or 'none'}.",
        )
    return factory.build(config=config, credentials=credentials)


def _install_builtin_connectors() -> None:
    """Register the connectors shipped with the application.

    Imported here rather than at module top level so the registry module itself
    stays free of implementation dependencies — the import direction that keeps
    core logic independent of any particular source type.
    """
    from app.connectors.mysql.factory import MysqlConnectorFactory
    from app.connectors.postgres.factory import PostgresConnectorFactory
    from app.models.data_source import SourceKind

    register(SourceKind.POSTGRESQL.value, PostgresConnectorFactory())
    register(SourceKind.MYSQL.value, MysqlConnectorFactory())


_install_builtin_connectors()
