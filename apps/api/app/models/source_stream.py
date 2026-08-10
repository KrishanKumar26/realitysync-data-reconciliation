"""Source streams — a table selected for ingestion.

A stream is the unit of sync: one table, its identity column(s), and how its
rows map onto RealitySync's two time axes.

``event_time_semantics`` is the load-bearing field. It records *what the event
time actually means*, which is the difference between "this happened at 10:30"
and "we wrote this down at 10:30". Losing that distinction makes late-arriving
corrections indistinguishable from real changes.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.tenancy import OrganizationScoped
from app.db.types import TimestampMixin, TimestampTZ, uuid_pk

if TYPE_CHECKING:
    from app.models.data_source import DataSource


class EventTimeSemantics(enum.StrEnum):
    """What the stream's event-time column means.

    ``observed``
        The column records when the fact was true in the world. The ideal case.

    ``recorded``
        The column records when the source system wrote the row. Close to the
        truth, but not the same thing — a row written at 10:30 may describe
        something that happened at 09:00.

    ``ingest_fallback``
        The table has no usable time column, so ingestion time stands in.
        Ordering within this stream is "when RealitySync saw it", nothing more.

    No confidence penalty is attached to ``recorded`` — that was settled in
    Phase 0. The distinction is preserved because it is what makes a root-cause
    explanation possible later; penalising it would be inventing a number.
    """

    OBSERVED = "observed"
    RECORDED = "recorded"
    INGEST_FALLBACK = "ingest_fallback"


EVENT_TIME_SEMANTICS: tuple[str, ...] = tuple(s.value for s in EventTimeSemantics)


class SourceStream(Base, OrganizationScoped, TimestampMixin):
    """One table configured for ingestion."""

    __tablename__ = "source_streams"
    __table_args__ = (
        # A table can be configured once per source. Two streams over the same
        # table would produce duplicate observations with different stream ids,
        # which no downstream dedup could untangle.
        UniqueConstraint(
            "data_source_id",
            "schema_name",
            "table_name",
            name="uq_source_streams_source_schema_table",
        ),
        CheckConstraint(
            "event_time_semantics IN ('" + "', '".join(EVENT_TIME_SEMANTICS) + "')",
            name="event_time_semantics_valid",
        ),
        # A stream with no identity column cannot produce a stable entity
        # reference, so every observation would look like a new thing.
        CheckConstraint("array_length(primary_key_columns, 1) >= 1", name="primary_key_required"),
        # ingest_fallback is precisely the case where no column is named; every
        # other semantics value requires one. Enforced here so the two fields
        # cannot drift into a combination the sync code has no meaning for.
        CheckConstraint(
            "(event_time_semantics = 'ingest_fallback' AND event_time_column IS NULL)"
            " OR (event_time_semantics <> 'ingest_fallback' AND event_time_column IS NOT NULL)",
            name="event_time_column_matches_semantics",
        ),
        CheckConstraint("poll_interval_seconds >= 30", name="poll_interval_minimum"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    # organization_id from OrganizationScoped.

    data_source_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    schema_name: Mapped[str] = mapped_column(String(128), nullable=False)
    table_name: Mapped[str] = mapped_column(String(128), nullable=False)

    #: Columns forming the source row's identity, in order. Their values become
    #: the external entity key, so changing this changes what counts as "the
    #: same thing" — which is why it is validated against discovered metadata.
    primary_key_columns: Mapped[list[str]] = mapped_column(ARRAY(String(128)), nullable=False)

    event_time_column: Mapped[str | None] = mapped_column(String(128), nullable=True)
    event_time_semantics: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'ingest_fallback'")
    )

    #: Columns to ingest. Empty means all of them; naming them explicitly keeps
    #: an unrelated new column in the source from changing every fingerprint.
    selected_columns: Mapped[list[str]] = mapped_column(
        ARRAY(String(128)), nullable=False, server_default=text("'{}'::varchar[]")
    )

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    poll_interval_seconds: Mapped[int] = mapped_column(nullable=False, server_default=text("300"))

    last_synced_at: Mapped[datetime | None] = mapped_column(TimestampTZ, nullable=True)
    #: High-water mark for incremental reads: the greatest event time seen so
    #: far. Null until the first sync completes.
    last_event_time: Mapped[datetime | None] = mapped_column(TimestampTZ, nullable=True)

    data_source: Mapped[DataSource] = relationship(back_populates="streams", lazy="raise")

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"

    @property
    def semantics(self) -> EventTimeSemantics:
        return EventTimeSemantics(self.event_time_semantics)

    def __repr__(self) -> str:
        return f"<SourceStream id={self.id} table={self.qualified_name}>"
