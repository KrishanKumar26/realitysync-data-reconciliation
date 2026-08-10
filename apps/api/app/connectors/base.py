"""The DataConnector interface.

Every source type implements this and nothing beyond it that ingestion is
allowed to know about. The rule that keeps the architecture extensible:

    Ingestion and the Reality Engine depend on DataConnector.
    DataConnector depends on nothing in ingestion or the engine.

Adding Snowflake, a REST API or a CSV drop means writing one class and
registering it. No downstream file changes, which is what makes the claim
"connectors can be added without touching core logic" true rather than
aspirational — there is an architecture test asserting the dependency
direction holds.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from types import TracebackType
from typing import Any

from app.connectors.types import (
    ConnectionTestResult,
    ConnectorHealth,
    DiscoveredSchema,
    SourceRecord,
    StreamSelector,
)


class DataConnector(ABC):
    """Read-only access to one external system.

    Implementations must be **read-only**. Nothing in this interface can
    express a write, and PostgreSQL connections are additionally opened with
    ``default_transaction_read_only``, so a bug cannot mutate a customer's
    database.

    Instances are per-operation, not shared. A connector holds a live
    connection and credentials in memory; keeping one alive across requests
    would mean holding a customer's credentials in memory indefinitely for no
    benefit.
    """

    #: Value matching DataSource.kind. Used by the registry.
    kind: str = ""

    #: Recorded in observation provenance, so a fingerprint change caused by a
    #: connector change is traceable rather than mysterious.
    version: str = "1"

    # --- Lifecycle --------------------------------------------------------

    @abstractmethod
    async def connect(self) -> None:
        """Open the connection. Raises ConnectorError on failure."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close the connection. Must be safe to call twice, and must not
        raise — it runs in cleanup paths where an exception would mask the
        original error."""

    # --- Inspection -------------------------------------------------------

    @abstractmethod
    async def test_connection(self) -> ConnectionTestResult:
        """Verify reachability, TLS, authentication and discovery permissions.

        Returns a structured result rather than a bare boolean: "it did not
        work" is not actionable, whereas "authenticated, but the role cannot
        read the catalog" tells the operator exactly what to fix.
        """

    @abstractmethod
    async def discover_schema(self, *, include_system_schemas: bool = False) -> DiscoveredSchema:
        """List schemas, tables and columns from catalog metadata.

        Must not read table data. Discovery runs against a customer's
        production database at configuration time, and a connector that
        scanned rows to describe them would be an outage waiting to happen.
        """

    @abstractmethod
    async def get_health(self) -> ConnectorHealth:
        """Current connector state. Never includes credentials."""

    # --- Reading ----------------------------------------------------------

    @abstractmethod
    def fetch_data(self, selector: StreamSelector) -> AsyncIterator[SourceRecord]:
        """Yield every row matching `selector`.

        An async iterator rather than a list: a source table can be far larger
        than memory, and the ingestion path commits in batches as records
        arrive.
        """

    @abstractmethod
    def fetch_changes(self, selector: StreamSelector) -> AsyncIterator[SourceRecord]:
        """Yield rows changed since ``selector.since_event_time``.

        Where a source offers no change feed, an implementation may fall back
        to a filtered read — but it must say so in its documentation, because
        a filtered read cannot see deletes, and a caller that believes it has a
        true change feed will silently miss them.
        """

    # --- Context manager --------------------------------------------------

    async def __aenter__(self) -> DataConnector:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.disconnect()


class ConnectorFactory(ABC):
    """Builds a connector from stored configuration and credentials.

    Exists so ingestion never constructs a connector class directly, and so the
    single point where decrypted credentials enter a connector is one reviewable
    call.
    """

    @abstractmethod
    def build(self, *, config: dict[str, Any], credentials: dict[str, Any]) -> DataConnector:
        """Construct a connector. Must validate config before returning."""
