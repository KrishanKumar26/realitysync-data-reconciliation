"""Data sources and their credentials.

A data source is a customer system RealitySync reads from. It belongs to
exactly one organization and carries no secrets: connection details that are
safe to display (host, port, database, SSL mode) live in ``config``, and the
password lives encrypted in a separate table.

Splitting the secret into its own table is not cosmetic. Listing sources is a
common query; credentials should not be on the row that query returns, so that
the ordinary path never has the ciphertext in memory at all.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.encryption import EncryptedValue
from app.db.base import Base
from app.db.tenancy import OrganizationScoped, organization_id_column
from app.db.types import TimestampMixin, TimestampTZ, uuid_pk

if TYPE_CHECKING:
    from app.models.source_stream import SourceStream


class SourceKind(enum.StrEnum):
    """Connector implementations. PostgreSQL is the first."""

    POSTGRESQL = "postgresql"


class SourceStatus(enum.StrEnum):
    """Lifecycle of a source's connection.

    ``configured`` means credentials are stored but the connection has never
    been proven. It is a distinct state from ``connected`` on purpose: telling
    someone a source is connected when nothing has ever reached it is exactly
    the kind of unverified claim this product exists to eliminate.
    """

    CONFIGURED = "configured"
    CONNECTED = "connected"
    ERROR = "error"
    DISABLED = "disabled"


SOURCE_KINDS: tuple[str, ...] = tuple(k.value for k in SourceKind)
SOURCE_STATUSES: tuple[str, ...] = tuple(s.value for s in SourceStatus)


class DataSource(Base, OrganizationScoped, TimestampMixin):
    """A customer system RealitySync reads from."""

    __tablename__ = "data_sources"
    __table_args__ = (
        # Names are how people identify sources in the interface; two sources
        # called "Production" in one workspace would be indistinguishable.
        # Scoped to the organization, so tenants never collide with each other.
        UniqueConstraint("organization_id", "name", name="uq_data_sources_organization_name"),
        CheckConstraint("kind IN ('" + "', '".join(SOURCE_KINDS) + "')", name="kind_valid"),
        CheckConstraint("status IN ('" + "', '".join(SOURCE_STATUSES) + "')", name="status_valid"),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()

    #: Not separately indexed: uq_data_sources_organization_name already leads
    #: with organization_id.
    organization_id: Mapped[uuid.UUID] = organization_id_column(index=False)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'configured'")
    )

    #: Non-secret connection details: host, port, database, username, sslmode.
    #: Safe to return to the client. The password is never in here — there is a
    #: test asserting exactly that.
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    #: Result of the most recent connection attempt. Kept so the interface can
    #: show health without opening a connection on every page load.
    last_connected_at: Mapped[datetime | None] = mapped_column(TimestampTZ, nullable=True)
    last_connection_latency_ms: Mapped[int | None] = mapped_column(nullable=True)
    #: Sanitised message only — never driver text, never a connection string.
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(TimestampTZ, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(TimestampTZ, nullable=True)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # passive_deletes: let PostgreSQL's ON DELETE CASCADE remove children
    # rather than having SQLAlchemy load and delete them one by one.
    #
    # Not only an optimisation. Loading the children would emit a query
    # filtered on data_source_id alone, with no organization_id — which the
    # tenancy guard correctly rejects. Deferring to the database keeps the
    # delete both scoped and cheap.
    credential: Mapped[SourceCredential | None] = relationship(
        back_populates="data_source",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="raise",
        passive_deletes=True,
    )
    streams: Mapped[list[SourceStream]] = relationship(
        back_populates="data_source",
        cascade="all, delete-orphan",
        lazy="raise",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<DataSource id={self.id} kind={self.kind} status={self.status}>"


class SourceCredential(Base, TimestampMixin):
    """The encrypted credential for one data source.

    Not organization-scoped through the tenancy mixin: it has no
    ``organization_id`` of its own, and reaching it requires the data source,
    which is scoped. Adding a denormalised tenant column here would create a
    second place for the two to disagree.

    Every column is either ciphertext or the metadata needed to decrypt it.
    There is no plaintext column, so there is nothing to accidentally select.
    """

    __tablename__ = "source_credentials"
    __table_args__ = (
        # One credential per source: rotation replaces, never accumulates.
        UniqueConstraint("data_source_id", name="uq_source_credentials_data_source_id"),
        CheckConstraint("octet_length(nonce) = 12", name="nonce_length"),
        CheckConstraint("octet_length(ciphertext) > 0", name="ciphertext_not_empty"),
        CheckConstraint("key_version > 0", name="key_version_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()

    data_source_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: AES-256-GCM ciphertext, including the authentication tag.
    ciphertext: Mapped[bytes] = mapped_column(nullable=False)
    #: 96-bit nonce. Not secret; must simply never repeat under one key.
    nonce: Mapped[bytes] = mapped_column(nullable=False)
    #: Which key encrypted this, so rotation does not need a data migration.
    key_version: Mapped[int] = mapped_column(nullable=False, server_default=text("1"))
    algorithm: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'AES-256-GCM'")
    )

    data_source: Mapped[DataSource] = relationship(back_populates="credential", lazy="raise")

    def to_encrypted_value(self) -> EncryptedValue:
        return EncryptedValue(
            ciphertext=self.ciphertext,
            nonce=self.nonce,
            key_version=self.key_version,
            algorithm=self.algorithm,
        )

    def __repr__(self) -> str:
        # No ciphertext, no nonce. Neither is directly exploitable, but a repr
        # ends up in tracebacks and logs, and neither belongs there.
        return f"<SourceCredential data_source_id={self.data_source_id}>"
