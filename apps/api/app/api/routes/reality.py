"""Entity, reality, conflict and timeline routes.

Every route takes :data:`~app.api.deps.CurrentOrganization`, so the tenant id
is part of the handler's signature and comes from the session rather than the
request. Combined with the tenancy guard, a query that forgets to scope raises
instead of returning another tenant's rows.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from sqlalchemy import select

from app.api.deps import (
    AppSettings,
    CurrentOrganization,
    DbSession,
    RequireAdmin,
    enforce_csrf,
)
from app.core.logging import get_logger
from app.engine.spec import MISSING_SPECIFICATIONS
from app.models.conflict import Conflict, ConflictStatus
from app.models.entity import Entity, EntityMapping
from app.models.observation import Observation
from app.models.reality_state import RealityState, RealityStateEvidence
from app.schemas.reality import (
    ConflictResponse,
    CreateEntityRequest,
    CreateMappingRequest,
    EntityResponse,
    EvidenceResponse,
    MappingResponse,
    RealityStateResponse,
    RecalculateResponse,
    TimelineEventResponse,
    TimelineResponse,
    UnscoredAttributeResponse,
    UpdateConflictRequest,
)
from app.services import audit
from app.services.entities import (
    DuplicateEntityError,
    DuplicateMappingError,
    StreamNotFoundError,
    create_entity,
    get_entity,
    list_entities,
    map_observations,
)
from app.services.reality import (
    detection_as_dict,
    detection_for_entity,
    recalculate_entity,
)
from app.services.timeline import TimeAxis, reconstruct

logger = get_logger(__name__)

router = APIRouter(tags=["reality"])


async def _require_entity(
    db: DbSession, *, context: CurrentOrganization, entity_id: uuid.UUID
) -> Entity:
    """Fetch an entity in the caller's organization, or 404.

    404 rather than 403 for another tenant's entity: whether an id exists
    elsewhere is not something a caller should be able to probe.
    """
    entity = await get_entity(db, organization_id=context.organization_id, entity_id=entity_id)
    if entity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found.")
    return entity


# --- Entities --------------------------------------------------------------


@router.post(
    "/entities",
    status_code=status.HTTP_201_CREATED,
    response_model=EntityResponse,
    summary="Create an entity",
)
async def create_entity_route(
    payload: CreateEntityRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
    context: RequireAdmin,
) -> EntityResponse:
    await enforce_csrf(request, context.auth, settings)

    try:
        entity = await create_entity(
            db,
            organization_id=context.organization_id,
            entity_type=payload.entity_type,
            natural_key=payload.natural_key,
            display_name=payload.display_name,
        )
    except DuplicateEntityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An entity with that type and key already exists in this workspace.",
        ) from None

    await audit.record(
        db,
        action="entity.created",
        organization_id=context.organization_id,
        actor_user_id=context.user.id,
        resource_type="entity",
        resource_id=entity.id,
        details={"entity_type": entity.entity_type, "natural_key": entity.natural_key},
        request=request,
    )
    await db.commit()
    return EntityResponse.model_validate(entity)


@router.get("/entities", response_model=list[EntityResponse], summary="List entities")
async def list_entities_route(
    db: DbSession,
    context: CurrentOrganization,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[EntityResponse]:
    summaries = await list_entities(db, organization_id=context.organization_id, limit=limit)
    return [
        EntityResponse(
            id=s.entity.id,
            entity_type=s.entity.entity_type,
            natural_key=s.entity.natural_key,
            display_name=s.entity.display_name,
            mapping_count=s.mapping_count,
            observation_count=s.observation_count,
            created_at=s.entity.created_at,
        )
        for s in summaries
    ]


@router.get("/entities/{entity_id}", response_model=EntityResponse, summary="Get an entity")
async def get_entity_route(
    entity_id: uuid.UUID, db: DbSession, context: CurrentOrganization
) -> EntityResponse:
    entity = await _require_entity(db, context=context, entity_id=entity_id)
    return EntityResponse.model_validate(entity)


@router.post(
    "/entities/{entity_id}/mappings",
    status_code=status.HTTP_201_CREATED,
    response_model=MappingResponse,
    summary="Map a source row to this entity",
)
async def create_mapping_route(
    entity_id: uuid.UUID,
    payload: CreateMappingRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
    context: RequireAdmin,
) -> MappingResponse:
    """Declare that a source row describes this entity.

    Retroactive: observations the stream has already produced resolve to this
    entity immediately, because the mapping joins on ``external_id``. No
    re-sync, and no observation is rewritten.
    """
    await enforce_csrf(request, context.auth, settings)
    await _require_entity(db, context=context, entity_id=entity_id)

    try:
        mapping = await map_observations(
            db,
            organization_id=context.organization_id,
            entity_id=entity_id,
            stream_id=payload.stream_id,
            external_id=payload.external_id,
            created_by_user_id=context.user.id,
        )
    except StreamNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Stream not found."
        ) from None
    except DuplicateMappingError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That source row is already mapped to an entity.",
        ) from None

    await db.commit()
    return MappingResponse.model_validate(mapping)


@router.get(
    "/entities/{entity_id}/mappings",
    response_model=list[MappingResponse],
    summary="List an entity's mappings",
)
async def list_mappings_route(
    entity_id: uuid.UUID,
    db: DbSession,
    context: CurrentOrganization,
    limit: int = Query(default=500, ge=1, le=1000),
) -> list[MappingResponse]:
    await _require_entity(db, context=context, entity_id=entity_id)
    rows = await db.scalars(
        select(EntityMapping)
        .where(
            EntityMapping.organization_id == context.organization_id,
            EntityMapping.entity_id == entity_id,
        )
        .order_by(EntityMapping.created_at, EntityMapping.id)
        .limit(limit)
    )
    return [MappingResponse.model_validate(m) for m in rows]


# --- Reality state ---------------------------------------------------------


@router.post(
    "/entities/{entity_id}/recalculate",
    response_model=RecalculateResponse,
    summary="Recalculate this entity's reality state",
)
async def recalculate_route(
    entity_id: uuid.UUID,
    request: Request,
    db: DbSession,
    settings: AppSettings,
    context: RequireAdmin,
) -> RecalculateResponse:
    """Run the engine over this entity's observations.

    States are written whether or not they could be scored. ``blocked`` means
    "nothing carries a confidence score", not "nothing was written" — the
    Phase 5 behaviour of writing nothing left the Reality page
    indistinguishable from an empty workspace.

    Returns 200 rather than an error: the request succeeded and reported
    exactly what happened.
    """
    await enforce_csrf(request, context.auth, settings)
    await _require_entity(db, context=context, entity_id=entity_id)

    result = await recalculate_entity(
        db, organization_id=context.organization_id, entity_id=entity_id
    )
    await db.commit()

    return RecalculateResponse(
        entity_id=result.entity_id,
        attributes_considered=result.attributes_considered,
        states_written=result.states_written,
        conflicts_written=result.conflicts_written,
        calculated_at=result.calculated_at,
        states_unscored=len(result.unscored),
        unscored_attributes=[
            {"attribute": attribute, "blocked_on": missing}
            for attribute, missing in sorted(result.unscored)
        ],
        blocked=bool(result.unscored),
        blocked_on=sorted({missing for _, missing in result.unscored}),
        missing_specifications=(
            [{"name": n, "description": d} for n, d in MISSING_SPECIFICATIONS]
            if result.unscored
            else []
        ),
    )


@router.get(
    "/entities/{entity_id}/reality",
    response_model=list[RealityStateResponse],
    summary="Reality states for an entity",
)
async def list_reality_states_route(
    entity_id: uuid.UUID,
    db: DbSession,
    context: CurrentOrganization,
    limit: int = Query(default=500, ge=1, le=1000),
) -> list[RealityStateResponse]:
    await _require_entity(db, context=context, entity_id=entity_id)
    rows = await db.scalars(
        select(RealityState)
        .where(
            RealityState.organization_id == context.organization_id,
            RealityState.entity_id == entity_id,
        )
        .order_by(RealityState.attribute)
        .limit(limit)
    )
    return [RealityStateResponse.model_validate(s) for s in rows]


@router.get(
    "/entities/{entity_id}/reality/{attribute}/evidence",
    response_model=list[EvidenceResponse],
    summary="Evidence behind a reality state",
)
async def list_evidence_route(
    entity_id: uuid.UUID,
    attribute: str,
    db: DbSession,
    context: CurrentOrganization,
    limit: int = Query(default=500, ge=1, le=1000),
) -> list[EvidenceResponse]:
    """The provenance trail: every observation considered, and its role."""
    await _require_entity(db, context=context, entity_id=entity_id)

    state = await db.scalar(
        select(RealityState).where(
            RealityState.organization_id == context.organization_id,
            RealityState.entity_id == entity_id,
            RealityState.attribute == attribute,
        )
    )
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No reality state for that attribute."
        )

    # Joined to the observation for provenance. Both organization filters live
    # in WHERE rather than in the join condition: the ORM tenancy guard cannot
    # inspect a join's ON clause, so a tenant filter placed there is invisible
    # to it and would silently stop being enforced.
    rows = await db.execute(
        select(RealityStateEvidence, Observation)
        .join(Observation, Observation.id == RealityStateEvidence.observation_id)
        .where(
            RealityStateEvidence.organization_id == context.organization_id,
            Observation.organization_id == context.organization_id,
            RealityStateEvidence.reality_state_id == state.id,
        )
        # Total ordering, so the trail reads the same on every request.
        # Event time first because that is the axis a reader is reasoning
        # along; observation id last so ties can never reorder.
        .order_by(
            Observation.event_time,
            Observation.ingested_at,
            RealityStateEvidence.observation_id,
        )
        .limit(limit)
    )
    return [
        EvidenceResponse(
            observation_id=e.observation_id,
            source_id=observation.source_id,
            stream_id=observation.stream_id,
            external_id=observation.external_id,
            role=e.role,
            weight=e.weight,
            observed_value=e.observed_value,
            event_time=observation.event_time,
            ingested_at=observation.ingested_at,
            exclusion_reason=e.exclusion_reason,
        )
        for e, observation in rows
    ]


@router.get(
    "/entities/{entity_id}/attributes/{attribute}/unscored",
    response_model=UnscoredAttributeResponse,
    summary="What the evidence shows when scoring is unavailable",
)
async def unscored_attribute_route(
    entity_id: uuid.UUID,
    attribute: str,
    db: DbSession,
    context: CurrentOrganization,
) -> UnscoredAttributeResponse:
    """Distinct asserted values and whether they disagree.

    The honest fallback while the confidence specification is missing: which
    values exist and who says what, with no verdict about which is right.
    """
    await _require_entity(db, context=context, entity_id=entity_id)

    outcome = await detection_for_entity(
        db,
        organization_id=context.organization_id,
        entity_id=entity_id,
        attribute=attribute,
    )
    if outcome is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No observations state that attribute for this entity.",
        )
    if outcome.is_scored:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This attribute is fully scored; read it from the reality endpoint.",
        )

    payload: dict[str, Any] = detection_as_dict(outcome)
    return UnscoredAttributeResponse(**{k: v for k, v in payload.items() if k != "scored"})


# --- Conflicts -------------------------------------------------------------


@router.get("/conflicts", response_model=list[ConflictResponse], summary="List conflicts")
async def list_conflicts_route(
    db: DbSession,
    context: CurrentOrganization,
    conflict_status: Annotated[str | None, Query(alias="status")] = None,
    entity_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[ConflictResponse]:
    """Detected disagreements, newest first.

    Populated even while confidence scoring is blocked: detection is
    categorical and needs no formula, so this page carries real findings today.
    """
    query = (
        select(Conflict, Entity.natural_key)
        .join(Entity, Entity.id == Conflict.entity_id)
        # Both tenant-owned tables filtered in the WHERE, not the ON clause.
        # Equivalent for an inner join, and it is the WHERE the tenancy guard
        # inspects - ORM join-ON conditions stay outside the statement tree
        # until compilation, so a filter placed there would go unchecked.
        .where(
            Conflict.organization_id == context.organization_id,
            Entity.organization_id == context.organization_id,
        )
    )
    if conflict_status is not None:
        query = query.where(Conflict.status == conflict_status)
    if entity_id is not None:
        query = query.where(Conflict.entity_id == entity_id)

    # Total ordering. `detected_at` alone ties routinely: one recalculation
    # writes several conflicts in a single transaction, so they share an
    # instant and PostgreSQL is free to return them in any order. Two identical
    # requests could then disagree about what the system believes, which is the
    # wrong kind of inconsistency in a product built on determinism.
    rows = await db.execute(query.order_by(Conflict.detected_at.desc(), Conflict.id).limit(limit))
    return [
        ConflictResponse(
            **{
                **{
                    c: getattr(conflict, c)
                    for c in (
                        "id",
                        "entity_id",
                        "reality_state_id",
                        "attribute",
                        "conflict_type",
                        "severity",
                        "score",
                        "summary",
                        "details",
                        "detected_at",
                        "last_seen_at",
                        "resolved_at",
                        "resolution_note",
                    )
                },
                "status": ConflictStatus(conflict.status),
                "entity_natural_key": natural_key,
            }
        )
        for conflict, natural_key in rows
    ]


@router.get("/conflicts/{conflict_id}", response_model=ConflictResponse, summary="Get a conflict")
async def get_conflict_route(
    conflict_id: uuid.UUID, db: DbSession, context: CurrentOrganization
) -> ConflictResponse:
    conflict = await db.scalar(
        select(Conflict).where(
            Conflict.organization_id == context.organization_id,
            Conflict.id == conflict_id,
        )
    )
    if conflict is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conflict not found.")
    return ConflictResponse.model_validate(conflict)


@router.patch(
    "/conflicts/{conflict_id}", response_model=ConflictResponse, summary="Update a conflict"
)
async def update_conflict_route(
    conflict_id: uuid.UUID,
    payload: UpdateConflictRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
    context: RequireAdmin,
) -> ConflictResponse:
    """Acknowledge, resolve or dismiss.

    A human act, recorded as one: who did it and when. The engine never sets
    these — it reports what it sees, and resolving a conflict does not and
    cannot change the reality state.
    """
    await enforce_csrf(request, context.auth, settings)

    conflict = await db.scalar(
        select(Conflict).where(
            Conflict.organization_id == context.organization_id,
            Conflict.id == conflict_id,
        )
    )
    if conflict is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conflict not found.")

    conflict.status = payload.status
    conflict.resolution_note = payload.note
    if payload.status in {ConflictStatus.RESOLVED.value, ConflictStatus.DISMISSED.value}:
        conflict.resolved_at = datetime.now(UTC)
        conflict.resolved_by_user_id = context.user.id
    else:
        conflict.resolved_at = None
        conflict.resolved_by_user_id = None

    await audit.record(
        db,
        action=f"conflict.{payload.status}",
        organization_id=context.organization_id,
        actor_user_id=context.user.id,
        resource_type="conflict",
        resource_id=conflict.id,
        details={"attribute": conflict.attribute, "type": conflict.conflict_type},
        request=request,
    )
    await db.commit()
    return ConflictResponse.model_validate(conflict)


# --- Timeline --------------------------------------------------------------


@router.get(
    "/entities/{entity_id}/timeline",
    response_model=TimelineResponse,
    summary="Bitemporal timeline for an entity",
)
async def timeline_route(
    entity_id: uuid.UUID,
    db: DbSession,
    context: CurrentOrganization,
    axis: str = Query(default="event", pattern="^(event|knowledge)$"),
    as_of_event_time: Annotated[datetime | None, Query()] = None,
    as_of_knowledge_time: Annotated[datetime | None, Query()] = None,
    attribute: Annotated[str | None, Query()] = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> TimelineResponse:
    """Reconstruct what was true, or what was known, at a point in time.

    ``axis=event`` answers "what was true at T". ``axis=knowledge`` answers
    "what did we know at T" — which differs precisely when something arrived
    late, and is what makes an audit answerable.

    The two ``as_of`` filters are independent and combine: together they
    reconstruct what we believed at knowledge-time K about the world at
    event-time E.
    """
    await _require_entity(db, context=context, entity_id=entity_id)

    timeline = await reconstruct(
        db,
        organization_id=context.organization_id,
        entity_id=entity_id,
        axis=TimeAxis(axis),
        as_of_event_time=as_of_event_time,
        as_of_knowledge_time=as_of_knowledge_time,
        attribute=attribute,
        limit=limit,
    )

    return TimelineResponse(
        axis=timeline.axis.value,
        as_of_event_time=timeline.as_of_event_time,
        as_of_knowledge_time=timeline.as_of_knowledge_time,
        event_count=len(timeline.events),
        late_arrival_count=timeline.late_arrival_count,
        truncated=timeline.truncated,
        events=[
            TimelineEventResponse(
                observation_id=e.observation_id,
                external_id=e.external_id,
                source_id=e.source_id,
                source_name=e.source_name,
                values=e.attribute_values,
                event_time=e.event_time,
                ingested_at=e.ingested_at,
                event_time_semantics=e.event_time_semantics,
                arrived_late=e.arrived_late,
                lag_seconds=e.lag_seconds,
            )
            for e in timeline.events
        ],
    )


@router.delete(
    "/entities/{entity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an entity",
)
async def delete_entity_route(
    entity_id: uuid.UUID,
    request: Request,
    db: DbSession,
    settings: AppSettings,
    context: RequireAdmin,
) -> Response:
    """Delete an entity, its mappings, states and conflicts.

    Observations are untouched: they are immutable statements sources made, and
    they remain true regardless of how we chose to group them.
    """
    await enforce_csrf(request, context.auth, settings)
    entity = await _require_entity(db, context=context, entity_id=entity_id)

    await db.delete(entity)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
