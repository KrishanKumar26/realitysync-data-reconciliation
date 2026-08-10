"""Timeline — bitemporal reconstruction over observations.

RealitySync stores two independent time axes on every observation, and the
whole point of storing both is that they answer different questions:

``event_time``   When the fact was true, per the source.
``ingested_at``  When RealitySync learned it.

They diverge whenever something arrives late. A reading taken on Monday and
delivered on Friday is *old news that we just heard*, and a system with one
axis cannot tell that from *a new reading taken on Friday*.

So the timeline supports two reconstructions:

**Valid time** — "what was true at T?"
    Filter on ``event_time <= T``. The world as the sources describe it.

**Transaction time** — "what did we know at T?"
    Filter on ``ingested_at <= T``. Replays our own knowledge, including the
    period when we were wrong because a correction had not arrived yet. This
    is what makes an audit answerable: not only what happened, but what we
    could have known when a decision was made.

Combining both — ``event_time <= E AND ingested_at <= K`` — reconstructs what
we believed at knowledge-time K about the world at event-time E.

None of this needs the Phase 0 confidence specification. It reports what
sources said and when, and asserts nothing about which is right.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.data_source import DataSource
from app.models.entity import EntityMapping
from app.models.observation import Observation

#: Bound on a single timeline page. A busy entity can have a very large number
#: of observations, and an unbounded query against it would be a denial of
#: service triggered by opening a screen.
MAX_TIMELINE_EVENTS = 500


class TimeAxis(StrEnum):
    """Which clock the timeline is ordered and filtered by.

    Named rather than boolean because the two are genuinely different
    questions, and a parameter called ``use_ingestion_time=True`` at a call
    site tells the reader nothing about which one they are asking.
    """

    #: When the facts were true.
    EVENT = "event"
    #: When RealitySync learned them.
    KNOWLEDGE = "knowledge"


@dataclass(frozen=True, slots=True)
class TimelineEvent:
    """One observation, presented as a point on the timeline."""

    observation_id: uuid.UUID
    entity_id: uuid.UUID | None
    external_id: str
    source_id: uuid.UUID
    source_name: str
    attribute_values: dict[str, Any]
    event_time: datetime
    ingested_at: datetime
    event_time_semantics: str
    fingerprint: str

    @property
    def arrived_late(self) -> bool:
        """True when this was learned materially after it was true.

        The signal that the two axes have diverged for this record. Surfaced so
        a reader can see *why* a reconstruction at knowledge-time differs from
        one at event-time, rather than being left to infer it.
        """
        return self.ingested_at > self.event_time

    @property
    def lag_seconds(self) -> float:
        return max((self.ingested_at - self.event_time).total_seconds(), 0.0)


@dataclass(frozen=True, slots=True)
class Timeline:
    """A reconstruction, with the parameters that produced it.

    The parameters are returned alongside the events on purpose: a timeline
    read without knowing which axis produced it is uninterpretable, and two
    screenshots of the same entity can legitimately differ.
    """

    axis: TimeAxis
    events: tuple[TimelineEvent, ...]
    as_of_event_time: datetime | None
    as_of_knowledge_time: datetime | None
    truncated: bool

    @property
    def late_arrival_count(self) -> int:
        return sum(1 for event in self.events if event.arrived_late)


def _base_query(*, organization_id: uuid.UUID) -> Select[Any]:
    """Observations joined to their source name, scoped to one tenant.

    Every tenant-owned table in the join is filtered on ``organization_id``,
    including the join conditions — the tenancy guard rejects anything less,
    correctly.
    """
    return (
        select(Observation, DataSource.name)
        .join(DataSource, DataSource.id == Observation.source_id)
        # Both tenant-owned tables are filtered in the WHERE rather than in the
        # ON clause. For an inner join the two are equivalent, and WHERE is
        # what the tenancy guard inspects — it does not see ORM join-ON
        # conditions, which SQLAlchemy keeps outside the statement tree until
        # compilation. Putting the filter where the guard can check it means a
        # future edit that drops one cannot pass silently.
        .where(
            Observation.organization_id == organization_id,
            DataSource.organization_id == organization_id,
        )
    )


async def reconstruct(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    entity_id: uuid.UUID | None = None,
    axis: TimeAxis = TimeAxis.EVENT,
    as_of_event_time: datetime | None = None,
    as_of_knowledge_time: datetime | None = None,
    attribute: str | None = None,
    limit: int = 100,
) -> Timeline:
    """Reconstruct the timeline for an entity, on the requested axis.

    ``as_of_event_time`` and ``as_of_knowledge_time`` are independent and may
    be combined: together they answer "what did we believe at K about the world
    at E". Passing neither returns everything, ordered by the chosen axis.
    """
    limit = max(1, min(limit, MAX_TIMELINE_EVENTS))
    query = _base_query(organization_id=organization_id)

    if entity_id is not None:
        # Observations reach an entity through the declared mapping, so the
        # subquery is the join. Scoped to the tenant on its own side because
        # entity_mappings is tenant-owned.
        mapped = (
            select(EntityMapping.stream_id, EntityMapping.external_id)
            .where(
                EntityMapping.organization_id == organization_id,
                EntityMapping.entity_id == entity_id,
            )
            .subquery()
        )
        query = query.join(
            mapped,
            (Observation.stream_id == mapped.c.stream_id)
            & (Observation.external_id == mapped.c.external_id),
        )

    if as_of_event_time is not None:
        query = query.where(Observation.event_time <= as_of_event_time)
    if as_of_knowledge_time is not None:
        query = query.where(Observation.ingested_at <= as_of_knowledge_time)

    # Ordering by the requested axis, then by the other, then by id. The full
    # chain is what makes paging reproducible when many observations share a
    # timestamp — very common when a sync ingests a batch at once.
    if axis is TimeAxis.EVENT:
        order = (
            Observation.event_time.desc(),
            Observation.ingested_at.desc(),
            Observation.id.desc(),
        )
    else:
        order = (
            Observation.ingested_at.desc(),
            Observation.event_time.desc(),
            Observation.id.desc(),
        )

    # One extra row, purely to detect truncation without a second count query.
    rows = (await db.execute(query.order_by(*order).limit(limit + 1))).all()
    truncated = len(rows) > limit

    events = tuple(
        TimelineEvent(
            observation_id=observation.id,
            entity_id=observation.entity_id,
            external_id=observation.external_id,
            source_id=observation.source_id,
            source_name=source_name,
            attribute_values=(
                {attribute: observation.payload.get(attribute)}
                if attribute is not None
                else dict(observation.payload)
            ),
            event_time=observation.event_time,
            ingested_at=observation.ingested_at,
            event_time_semantics=observation.event_time_semantics,
            fingerprint=observation.fingerprint,
        )
        for observation, source_name in rows[:limit]
    )

    return Timeline(
        axis=axis,
        events=events,
        as_of_event_time=as_of_event_time,
        as_of_knowledge_time=as_of_knowledge_time,
        truncated=truncated,
    )


@dataclass(frozen=True, slots=True)
class AttributeHistory:
    """How one attribute's asserted value changed over time, per source.

    Answers "when did the warehouse start saying 42, and what did it say
    before?" without asserting which value was correct.
    """

    attribute: str
    changes: tuple[TimelineEvent, ...]

    @property
    def distinct_values(self) -> tuple[Any, ...]:
        seen: list[Any] = []
        for event in self.changes:
            value = event.attribute_values.get(self.attribute)
            if value not in seen:
                seen.append(value)
        return tuple(seen)


async def attribute_history(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    entity_id: uuid.UUID,
    attribute: str,
    limit: int = 100,
) -> AttributeHistory:
    """Every observation that stated a value for `attribute`, newest first.

    Ordered by event time: this is a question about the world, not about our
    ingestion schedule.
    """
    timeline = await reconstruct(
        db,
        organization_id=organization_id,
        entity_id=entity_id,
        axis=TimeAxis.EVENT,
        attribute=attribute,
        limit=limit,
    )
    return AttributeHistory(
        attribute=attribute,
        changes=tuple(
            event for event in timeline.events if event.attribute_values.get(attribute) is not None
        ),
    )
