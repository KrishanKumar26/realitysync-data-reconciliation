"""Operational status.

Distinct from ``/health`` and ``/ready``, and deliberately so:

``/health``   Unauthenticated liveness. Touches nothing. Platform probe.
``/ready``    Unauthenticated readiness. Gates deployment. Phase 1 contract.
``/status``   **Authenticated** operational detail, for a person diagnosing
              something rather than a load balancer making a routing decision.

The split matters for two reasons. The probes must stay cheap and stable —
anything that reads them is doing so on a timer, and changing their semantics
changes rollout behaviour. And this endpoint reports which subsystems are
degraded, which is exactly the information not to hand an unauthenticated
caller: "the rate limiter is currently not enforcing" is a useful sentence for
an operator and a useful sentence for an attacker.

Degradation is reported, not hidden. Redis being down does not make the API
unhealthy — it holds nothing authoritative — but it does mean rate limiting has
stopped enforcing, and an operator should be able to see that plainly rather
than infer it from a quiet log line.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from app.api.deps import AppSettings, CurrentAuth
from app.cache.redis import get_redis
from app.core.logging import get_logger
from app.engine.spec import ALGORITHM_VERSION, MISSING_SPECIFICATIONS
from app.ingestion.scheduler import scheduler_state
from app.schemas.system import (
    ComponentState,
    ComponentStatus,
    SystemStatusResponse,
)
from app.services.rate_limit import RedisRateLimiter, get_rate_limiter

logger = get_logger(__name__)

router = APIRouter(prefix="/system", tags=["operations"])


@router.get("/status", response_model=SystemStatusResponse, summary="Operational status")
async def system_status(
    settings: AppSettings,
    auth: CurrentAuth,
) -> SystemStatusResponse:
    """Which subsystems are working, and which are degraded.

    Requires a session. Not organization-scoped: this describes the deployment,
    not a tenant's data, and contains nothing belonging to any workspace.
    """
    components: list[ComponentStatus] = []

    # --- Redis ---
    # Probed first, because whether rate limiting is actually enforcing is a
    # consequence of this answer rather than of how the process was configured.
    redis_state, redis_detail = await _probe_redis()
    components.append(ComponentStatus(name="redis", state=redis_state, detail=redis_detail))

    # --- Rate limiting ---
    # Reported as what it is doing, not as what it was set up to do. A limiter
    # installed against an unreachable Redis is failing open on every request:
    # calling that "operational" because the object exists would hide the one
    # fact an operator needs during an outage.
    if not settings.rate_limiting_enabled:
        components.append(
            ComponentStatus(
                name="rate_limiting",
                state="disabled",
                detail=(
                    "Disabled by configuration. Safe only if rate limiting is "
                    "terminated at the edge."
                ),
            )
        )
    elif not isinstance(get_rate_limiter(), RedisRateLimiter):
        components.append(
            ComponentStatus(
                name="rate_limiting",
                state="degraded",
                detail="No Redis-backed limiter installed; requests are not limited.",
            )
        )
    elif redis_state != "operational":
        components.append(
            ComponentStatus(
                name="rate_limiting",
                state="degraded",
                detail=(
                    "Failing open because Redis is unreachable; attempts are "
                    "not being counted. Authentication is unaffected."
                ),
            )
        )
    else:
        components.append(
            ComponentStatus(
                name="rate_limiting",
                state="operational",
                detail="Redis-backed sliding window.",
            )
        )

    # --- Outbound mail ---
    # "degraded", not "operational": password reset works, but the link only
    # reaches the server log, so an operator has to hand it over. Reporting
    # that as healthy would hide a setup step from the person responsible for
    # it — and the API deliberately tells the *requester* nothing either way.
    components.append(
        ComponentStatus(
            name="email",
            state="operational" if settings.mail_configured else "degraded",
            detail=(
                "Password reset links are emailed."
                if settings.mail_configured
                else (
                    "No mail provider configured. Password reset links are "
                    "written to the server log instead of being sent."
                )
            ),
        )
    )

    # --- Sync scheduler ---
    # Not authoritative: if it never runs, nothing is wrong and no observation
    # is lost — sources are simply staler than configured, and manual sync
    # still works. So a stopped scheduler is reported, not treated as an
    # outage of the product.
    scheduler = scheduler_state()
    if not settings.sync_scheduler_enabled:
        components.append(
            ComponentStatus(
                name="sync_scheduler",
                state="disabled",
                detail="Disabled by configuration. Sources refresh only on manual sync.",
            )
        )
    elif not scheduler.running:
        components.append(
            ComponentStatus(
                name="sync_scheduler",
                state="degraded",
                detail="Enabled but not running; scheduled syncs are not happening.",
            )
        )
    else:
        components.append(
            ComponentStatus(
                name="sync_scheduler",
                state="operational",
                detail=(
                    f"{scheduler.ticks} ticks, {scheduler.sources_synced} sources synced, "
                    f"{scheduler.failures} failures"
                    + (f" (last: {scheduler.last_error})" if scheduler.last_error else "")
                    + "."
                ),
            )
        )

    # --- Reality Engine ---
    # Reported as degraded rather than operational while scoring is blocked.
    # An engine that cannot produce its primary output is not working, and
    # saying "operational" because the process is alive would be the kind of
    # unverified green light this product exists to avoid.
    components.append(
        ComponentStatus(
            name="reality_engine",
            state="degraded",
            detail=(
                f"Detection and bitemporal reconstruction operational. Confidence "
                f"scoring unavailable: {len(MISSING_SPECIFICATIONS)} specifications "
                f"missing. Algorithm: {ALGORITHM_VERSION}."
            ),
        )
    )

    # Worst state wins. A deployment with one degraded subsystem is degraded;
    # averaging or majority-voting would let a real problem hide behind
    # healthy neighbours.
    #
    # Note this currently always reports `degraded`, because confidence scoring
    # is blocked on the missing specification. That is the correct answer, and
    # it should stop being the answer the day the specification arrives — not
    # before.
    overall = _worst([component.state for component in components])

    return SystemStatusResponse(
        status=overall,
        environment=settings.environment,
        version=settings.api_version,
        checked_at=datetime.now(UTC),
        components=components,
    )


async def _probe_redis() -> tuple[ComponentState, str]:
    """Ping Redis, without letting a failure reach the caller as an error."""
    try:
        await get_redis().ping()
    except Exception as exc:
        logger.info("system.redis_unavailable", error_type=type(exc).__name__)
        return (
            "degraded",
            "Unreachable. Rate limiting fails open; nothing authoritative is lost.",
        )
    return "operational", "Reachable."


#: Only these two drag the overall verdict down. `disabled` is deliberately
#: excluded: it is a deployment choice, and letting it colour the top-level
#: status would train operators to ignore the field on deployments that
#: legitimately terminate rate limiting at the edge.
_FAILING: tuple[ComponentState, ...] = ("down", "degraded")


def _worst(states: list[ComponentState]) -> ComponentState:
    for state in _FAILING:
        if state in states:
            return state
    return "operational"
