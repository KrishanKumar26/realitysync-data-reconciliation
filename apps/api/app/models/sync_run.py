"""Sync runs — the record of every ingestion attempt.

Written whether the run succeeds or fails. A failed sync that leaves no trace
is indistinguishable from a sync that never started, and "why is this source
stale" is unanswerable without the failures.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.tenancy import OrganizationScoped, organization_id_column
from app.db.types import TimestampTZ, uuid_pk


class SyncStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    #: A run that never started because another was already holding the
    #: source's advisory lock. Distinct from `failed`: nothing went wrong.
    SKIPPED = "skipped"


SYNC_STATUSES: tuple[str, ...] = tuple(s.value for s in SyncStatus)

TERMINAL_STATUSES: frozenset[str] = frozenset(
    {SyncStatus.COMPLETED, SyncStatus.FAILED, SyncStatus.SKIPPED}
)


class SyncRun(Base, OrganizationScoped):
    """One ingestion attempt against one source."""

    __tablename__ = "sync_runs"
    __table_args__ = (
        # Makes retrying safe: a client that resends the same request after a
        # timeout gets the original run rather than starting a second one.
        UniqueConstraint(
            "source_id", "idempotency_key", name="uq_sync_runs_source_idempotency_key"
        ),
        CheckConstraint("status IN ('" + "', '".join(SYNC_STATUSES) + "')", name="status_valid"),
        CheckConstraint(
            "rows_seen >= 0 AND rows_created >= 0 AND rows_skipped >= 0",
            name="counters_non_negative",
        ),
        # Every row read is either created or skipped. A run where the counts
        # do not add up means the sync logic lost track, and silently reporting
        # wrong numbers about ingestion would undermine the whole product.
        CheckConstraint(
            "status <> 'completed' OR rows_seen = rows_created + rows_skipped",
            name="completed_counters_balance",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="completed_after_started",
        ),
        # The sync history view: newest first, per source.
        Index("ix_sync_runs_source_id_started_at", "source_id", text("started_at DESC")),
        Index(
            "ix_sync_runs_organization_id_started_at",
            "organization_id",
            text("started_at DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()

    #: Not separately indexed: ix_sync_runs_organization_id_started_at already
    #: leads with organization_id.
    organization_id: Mapped[uuid.UUID] = organization_id_column(index=False)

    source_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Null for a run covering every enabled stream on the source.
    stream_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("source_streams.id", ondelete="CASCADE"),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )

    started_at: Mapped[datetime] = mapped_column(
        TimestampTZ, nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(TimestampTZ, nullable=True)

    rows_seen: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    rows_created: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    #: Rows that produced a fingerprint already present — the idempotency path.
    rows_skipped: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))

    #: Stable code clients can branch on, e.g. "connection_failed".
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Sanitised message. Never driver text, never a connection string.
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Supplied by the caller, or generated. Bounded by the unique constraint
    #: above, so a duplicate request cannot start a duplicate run.
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)

    #: Per-stream detail for a multi-stream run.
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def duration_ms(self) -> int | None:
        if self.completed_at is None:
            return None
        return int((self.completed_at - self.started_at).total_seconds() * 1000)

    def __repr__(self) -> str:
        return f"<SyncRun id={self.id} status={self.status} rows_seen={self.rows_seen}>"
