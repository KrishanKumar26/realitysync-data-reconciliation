"""Overview routes.

Read-only and organization-scoped. Both endpoints take
:data:`~app.api.deps.CurrentOrganization`, so the tenant id comes from the
session rather than the request and there is nothing for a caller to tamper
with.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentOrganization, DbSession
from app.schemas.dashboard import (
    ActivityItemResponse,
    ConfidenceResponse,
    ConflictSummaryResponse,
    DashboardResponse,
    IngestionSummaryResponse,
    MissingSpecification,
    SourceHealthResponse,
    SourceSummaryResponse,
)
from app.services.dashboard import (
    DEFAULT_ACTIVITY_WINDOW,
    MAX_ACTIVITY_ITEMS,
    build_dashboard,
    recent_activity,
)

router = APIRouter(tags=["overview"])


@router.get("/dashboard", response_model=DashboardResponse, summary="Overview")
async def get_dashboard(
    db: DbSession,
    context: CurrentOrganization,
    window_days: Annotated[int, Query(ge=1, le=90)] = DEFAULT_ACTIVITY_WINDOW.days,
    activity_limit: Annotated[int, Query(ge=1, le=MAX_ACTIVITY_ITEMS)] = 20,
) -> DashboardResponse:
    """Source health, ingestion counts, conflicts, confidence and activity.

    Every number is a real count from a real table. Confidence is the one field
    that may be unavailable — while the approved specification is missing, no
    reality state carries a score, and the response says so rather than
    reporting zero.
    """
    dashboard = await build_dashboard(
        db,
        organization_id=context.organization_id,
        window=timedelta(days=window_days),
        activity_limit=activity_limit,
    )

    return DashboardResponse(
        organization_id=dashboard.organization_id,
        generated_at=dashboard.generated_at,
        window_days=dashboard.window_days,
        is_empty=dashboard.is_empty,
        sources=SourceSummaryResponse(
            total=dashboard.sources.total,
            connected=dashboard.sources.connected,
            never_tested=dashboard.sources.never_tested,
            errored=dashboard.sources.errored,
            disabled=dashboard.sources.disabled,
            sources=[
                SourceHealthResponse(
                    source_id=s.source_id,
                    name=s.name,
                    kind=s.kind,
                    status=s.status,
                    stream_count=s.stream_count,
                    observation_count=s.observation_count,
                    last_connected_at=s.last_connected_at,
                    last_synced_at=s.last_synced_at,
                    last_error=s.last_error,
                    last_error_at=s.last_error_at,
                    never_tested=s.has_never_been_tested,
                )
                for s in dashboard.sources.sources
            ],
        ),
        ingestion=IngestionSummaryResponse(
            observation_count=dashboard.ingestion.observation_count,
            observations_in_window=dashboard.ingestion.observations_in_window,
            entity_count=dashboard.ingestion.entity_count,
            mapped_entity_count=dashboard.ingestion.mapped_entity_count,
            unmapped_entity_count=dashboard.ingestion.unmapped_entity_count,
            stream_count=dashboard.ingestion.stream_count,
            enabled_stream_count=dashboard.ingestion.enabled_stream_count,
            last_sync_at=dashboard.ingestion.last_sync_at,
            syncs_in_window=dashboard.ingestion.syncs_in_window,
            failed_syncs_in_window=dashboard.ingestion.failed_syncs_in_window,
        ),
        conflicts=ConflictSummaryResponse(
            open=dashboard.conflicts.open,
            acknowledged=dashboard.conflicts.acknowledged,
            resolved=dashboard.conflicts.resolved,
            dismissed=dashboard.conflicts.dismissed,
            outstanding=dashboard.conflicts.outstanding,
            by_severity=dashboard.conflicts.by_severity,
            ungraded=dashboard.conflicts.ungraded,
            newest_open_at=dashboard.conflicts.newest_open_at,
        ),
        confidence=ConfidenceResponse(
            available=dashboard.confidence.available,
            scored_state_count=dashboard.confidence.scored_state_count,
            unscored_attribute_count=dashboard.confidence.unscored_attribute_count,
            average_confidence=dashboard.confidence.average_confidence,
            lowest_confidence=dashboard.confidence.lowest_confidence,
            highest_confidence=dashboard.confidence.highest_confidence,
            algorithm_version=dashboard.confidence.algorithm_version,
            blocked_reason=dashboard.confidence.blocked_reason,
            missing_specifications=[
                MissingSpecification(name=name, description=description)
                for name, description in dashboard.confidence.missing_specifications
            ],
        ),
        activity=[
            ActivityItemResponse(
                kind=item.kind,
                occurred_at=item.occurred_at,
                summary=item.summary,
                detail=item.detail,
                resource_type=item.resource_type,
                resource_id=item.resource_id,
                severity=item.severity,
            )
            for item in dashboard.activity
        ],
    )


@router.get("/activity", response_model=list[ActivityItemResponse], summary="Recent activity")
async def get_activity(
    db: DbSession,
    context: CurrentOrganization,
    window_days: Annotated[int, Query(ge=1, le=90)] = DEFAULT_ACTIVITY_WINDOW.days,
    limit: Annotated[int, Query(ge=1, le=MAX_ACTIVITY_ITEMS)] = 50,
) -> list[ActivityItemResponse]:
    """The activity feed on its own, for polling without the full dashboard."""
    since = datetime.now(UTC) - timedelta(days=window_days)
    items = await recent_activity(
        db, organization_id=context.organization_id, since=since, limit=limit
    )
    return [
        ActivityItemResponse(
            kind=item.kind,
            occurred_at=item.occurred_at,
            summary=item.summary,
            detail=item.detail,
            resource_type=item.resource_type,
            resource_id=item.resource_id,
            severity=item.severity,
        )
        for item in items
    ]
