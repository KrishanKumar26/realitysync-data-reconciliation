"""Entity and mapping management.

The anchor everything in Phase 4 and 5 hangs from. Observations know about
*rows*; reality states, conflicts and timelines are about *things*. A mapping
is the declared bridge between the two.

Mappings are created by explicit human decision and never inferred — the Phase 0
rule, and the reason is worth restating: an inferred identity that is wrong
merges two real-world things irreversibly. Every state, conflict and
explanation downstream would then be about a chimera, and no later correction
could untangle which observation belonged to which.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.entity import Entity, EntityMapping
from app.models.observation import Observation
from app.models.source_stream import SourceStream

logger = get_logger(__name__)


class EntityError(Exception):
    """Base class for entity failures that routes translate to HTTP."""


class DuplicateEntityError(EntityError):
    """An entity with this type and natural key already exists."""


class DuplicateMappingError(EntityError):
    """This source row is already mapped to an entity."""


class StreamNotFoundError(EntityError):
    """The stream does not exist in this organization."""


@dataclass(frozen=True, slots=True)
class EntitySummary:
    """An entity with the counts a listing screen needs."""

    entity: Entity
    mapping_count: int
    observation_count: int


async def create_entity(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    entity_type: str,
    natural_key: str,
    display_name: str | None = None,
) -> Entity:
    """Create an entity. Raises if the natural key is taken."""
    existing = await db.scalar(
        select(Entity.id).where(
            Entity.organization_id == organization_id,
            Entity.entity_type == entity_type,
            Entity.natural_key == natural_key,
        )
    )
    if existing is not None:
        raise DuplicateEntityError

    entity = Entity(
        organization_id=organization_id,
        entity_type=entity_type.strip(),
        natural_key=natural_key.strip(),
        display_name=(display_name or "").strip() or None,
    )
    db.add(entity)
    await db.flush()

    logger.info(
        "entity.created",
        entity_id=str(entity.id),
        organization_id=str(organization_id),
        entity_type=entity_type,
    )
    return entity


async def map_observations(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    entity_id: uuid.UUID,
    stream_id: uuid.UUID,
    external_id: str,
    created_by_user_id: uuid.UUID | None = None,
) -> EntityMapping:
    """Declare that a source row describes an entity.

    Retroactive by construction: the mapping joins on ``external_id``, so every
    observation the stream has already produced resolves to this entity
    immediately. No re-sync, and no observation is rewritten — observations are
    immutable, and a mapping is a statement *about* them, not a change *to*
    them.
    """
    stream = await db.scalar(
        select(SourceStream).where(
            SourceStream.organization_id == organization_id,
            SourceStream.id == stream_id,
        )
    )
    if stream is None:
        raise StreamNotFoundError

    existing = await db.scalar(
        select(EntityMapping.id).where(
            EntityMapping.organization_id == organization_id,
            EntityMapping.stream_id == stream_id,
            EntityMapping.external_id == external_id,
        )
    )
    if existing is not None:
        raise DuplicateMappingError

    mapping = EntityMapping(
        organization_id=organization_id,
        entity_id=entity_id,
        stream_id=stream_id,
        external_id=external_id,
        created_by_user_id=created_by_user_id,
    )
    db.add(mapping)
    await db.flush()

    logger.info(
        "entity.mapped",
        entity_id=str(entity_id),
        stream_id=str(stream_id),
        external_id=external_id,
    )
    return mapping


async def get_entity(
    db: AsyncSession, *, organization_id: uuid.UUID, entity_id: uuid.UUID
) -> Entity | None:
    entity: Entity | None = await db.scalar(
        select(Entity).where(Entity.organization_id == organization_id, Entity.id == entity_id)
    )
    return entity


async def list_entities(
    db: AsyncSession, *, organization_id: uuid.UUID, limit: int = 100
) -> list[EntitySummary]:
    """Entities with mapping and observation counts.

    Correlated subqueries rather than joins with GROUP BY: each count is
    filtered on its own tenant column, which is both correct and what the
    tenancy guard requires.
    """
    mapping_count = (
        select(func.count(EntityMapping.id))
        .where(
            EntityMapping.organization_id == organization_id,
            EntityMapping.entity_id == Entity.id,
        )
        .correlate(Entity)
        .scalar_subquery()
    )

    rows = await db.execute(
        select(Entity, mapping_count)
        .where(Entity.organization_id == organization_id)
        .order_by(Entity.natural_key)
        .limit(limit)
    )

    listed = [(entity, int(mappings or 0)) for entity, mappings in rows]
    counts = await count_observations_by_entity(
        db,
        organization_id=organization_id,
        entity_ids=[entity.id for entity, _ in listed],
    )

    return [
        EntitySummary(
            entity=entity,
            mapping_count=mappings,
            observation_count=counts.get(entity.id, 0),
        )
        for entity, mappings in listed
    ]


async def count_observations_by_entity(
    db: AsyncSession, *, organization_id: uuid.UUID, entity_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """Observation counts for many entities, in one query.

    The list endpoint previously called :func:`count_observations` once per
    entity — measured at N+4 queries for N entities, so a workspace with two
    hundred entities spent two hundred round trips on a column of integers.

    ``count(DISTINCT observations.id)`` rather than ``count(*)``: a mapping is
    unique per (stream, external_id), so today the two agree, but a plain count
    would silently double if that ever stopped being true. The distinct version
    cannot be wrong.

    Both organization filters are in WHERE rather than the join condition. The
    tenancy guard cannot inspect a join's ON clause, so a tenant filter placed
    there is invisible to it — the lesson Phase 5 learned twice.
    """
    if not entity_ids:
        return {}

    rows = await db.execute(
        select(EntityMapping.entity_id, func.count(func.distinct(Observation.id)))
        .join(
            Observation,
            (Observation.stream_id == EntityMapping.stream_id)
            & (Observation.external_id == EntityMapping.external_id),
        )
        .where(
            EntityMapping.organization_id == organization_id,
            Observation.organization_id == organization_id,
            EntityMapping.entity_id.in_(entity_ids),
        )
        .group_by(EntityMapping.entity_id)
    )
    # An entity with no matching observation is absent from a grouped result,
    # and its count is zero rather than missing.
    return {row[0]: int(row[1]) for row in rows}


async def count_observations(
    db: AsyncSession, *, organization_id: uuid.UUID, entity_id: uuid.UUID
) -> int:
    """How many observations resolve to this entity through its mappings."""
    mapped = (
        select(EntityMapping.stream_id, EntityMapping.external_id)
        .where(
            EntityMapping.organization_id == organization_id,
            EntityMapping.entity_id == entity_id,
        )
        .subquery()
    )
    total = await db.scalar(
        select(func.count(Observation.id))
        .join(
            mapped,
            (Observation.stream_id == mapped.c.stream_id)
            & (Observation.external_id == mapped.c.external_id),
        )
        .where(Observation.organization_id == organization_id)
    )
    return int(total or 0)


async def load_observations_for_entity(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    entity_id: uuid.UUID,
) -> list[Observation]:
    """Every observation mapped to this entity, oldest event time first.

    The engine's input. Ordered deterministically so a calculation over the
    same data is byte-identical between runs — the ordering the engine relies
    on is its own, but a stable input removes one more source of drift.
    """
    mapped = (
        select(EntityMapping.stream_id, EntityMapping.external_id)
        .where(
            EntityMapping.organization_id == organization_id,
            EntityMapping.entity_id == entity_id,
        )
        .subquery()
    )
    rows = await db.scalars(
        select(Observation)
        .join(
            mapped,
            (Observation.stream_id == mapped.c.stream_id)
            & (Observation.external_id == mapped.c.external_id),
        )
        .where(Observation.organization_id == organization_id)
        .order_by(Observation.event_time, Observation.ingested_at, Observation.id)
    )
    return list(rows)
