"""Authentication routes.

Thin by design: each handler translates HTTP to a call in
:mod:`app.services.auth` and back. Transaction boundaries live here, so an
audit row and the action it records commit together or not at all.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.api.cookies import clear_session_cookies, set_session_cookies
from app.api.deps import (
    AppSettings,
    CurrentAuth,
    DbSession,
    OptionalAuth,
    enforce_csrf,
)
from app.core.logging import get_logger
from app.models.audit_log import AuditAction
from app.schemas.auth import (
    AnonymousSessionResponse,
    AuthenticatedSessionResponse,
    LoginRequest,
    LogoutResponse,
    OrganizationMembershipResponse,
    RegisterRequest,
    SwitchOrganizationRequest,
    UserResponse,
)
from app.services import audit
from app.services.auth import (
    AuthContext,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    NotAMemberError,
    authenticate,
    create_session,
    list_user_memberships,
    load_organizations,
    register_account,
    revoke_session,
    switch_organization,
)
from app.services.rate_limit import (
    LOGIN_POLICY,
    REGISTRATION_POLICY,
    get_rate_limiter,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["authentication"])

#: One message for every credential failure. Distinguishing "no such account"
#: from "wrong password" turns the login endpoint into a user-enumeration
#: oracle, which is a reconnaissance gift for credential stuffing.
_INVALID_CREDENTIALS = "Invalid email or password."


async def _session_payload(db: DbSession, context: AuthContext) -> AuthenticatedSessionResponse:
    """Build the signed-in response from an already-resolved context."""
    organization_ids = tuple(m.organization_id for m in context.memberships)
    organizations = await load_organizations(db, organization_ids)

    return AuthenticatedSessionResponse(
        user=UserResponse.model_validate(context.user),
        organizations=[
            OrganizationMembershipResponse(
                id=organization.id,
                name=organization.name,
                slug=organization.slug,
                role=membership.role_enum,
            )
            for membership in context.memberships
            if (organization := organizations.get(membership.organization_id)) is not None
        ],
        active_organization_id=context.organization_id,
        csrf_token=context.session.csrf_token,
        expires_at=context.session.expires_at,
    )


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=AuthenticatedSessionResponse,
    summary="Create an account and its first organization",
)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: DbSession,
    settings: AppSettings,
) -> AuthenticatedSessionResponse:
    """Register a user, create their organization, and sign them in.

    Registration and login in one step: requiring a separate login immediately
    after signup is friction with no security benefit, since the credentials
    were just proven.
    """
    verdict = await get_rate_limiter().check(
        REGISTRATION_POLICY, audit.client_ip(request) or "unknown"
    )
    if not verdict.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many registration attempts. Try again later.",
        )

    try:
        result = await register_account(
            db,
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
            organization_name=payload.organization_name,
            settings=settings,
        )
    except EmailAlreadyRegisteredError:
        # Registration cannot avoid revealing that an address is taken — the
        # account cannot be created either way. The mitigation is the rate
        # limit above, not a misleading success response.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists.",
        ) from None

    issued = await create_session(
        db,
        user=result.user,
        active_organization_id=result.organization.id,
        ip_address=audit.client_ip(request),
        user_agent=audit.user_agent(request),
        settings=settings,
    )

    await audit.record(
        db,
        action=AuditAction.USER_REGISTERED,
        organization_id=result.organization.id,
        actor_user_id=result.user.id,
        resource_type="user",
        resource_id=result.user.id,
        request=request,
    )
    await audit.record(
        db,
        action=AuditAction.ORGANIZATION_CREATED,
        organization_id=result.organization.id,
        actor_user_id=result.user.id,
        resource_type="organization",
        resource_id=result.organization.id,
        details={"slug": result.organization.slug},
        request=request,
    )
    await db.commit()

    set_session_cookies(
        response, token=issued.token, csrf_token=issued.csrf_token, settings=settings
    )
    # Logged without the token: the raw session token never reaches a log sink.
    logger.info(
        "auth.registered",
        user_id=str(result.user.id),
        organization_id=str(result.organization.id),
    )

    context = AuthContext(
        user=result.user,
        session=issued.session,
        memberships=(result.membership,),
        active_membership=result.membership,
    )
    return await _session_payload(db, context)


@router.post(
    "/login",
    response_model=AuthenticatedSessionResponse,
    summary="Sign in with email and password",
)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: DbSession,
    settings: AppSettings,
) -> AuthenticatedSessionResponse:
    """Authenticate and issue a session."""
    verdict = await get_rate_limiter().check(
        LOGIN_POLICY, f"{audit.client_ip(request) or 'unknown'}:{payload.email}"
    )
    if not verdict.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many sign-in attempts. Try again later.",
            headers=(
                {"Retry-After": str(verdict.retry_after_seconds)}
                if verdict.retry_after_seconds
                else None
            ),
        )

    try:
        user = await authenticate(
            db, email=payload.email, password=payload.password, settings=settings
        )
    except InvalidCredentialsError:
        await audit.record(
            db,
            action=AuditAction.SESSION_LOGIN_FAILED,
            # No organization: a failed login has no tenant context, which is
            # exactly why audit_logs.organization_id is nullable.
            details={"email": payload.email},
            request=request,
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=_INVALID_CREDENTIALS
        ) from None

    memberships = await list_user_memberships(db, user.id)
    # Sign in to the first organization the user joined. A session with no
    # organization is a valid state, so a user without memberships still gets a
    # working session and an interface that says why it is empty.
    active = memberships[0] if memberships else None

    issued = await create_session(
        db,
        user=user,
        active_organization_id=active.organization_id if active else None,
        ip_address=audit.client_ip(request),
        user_agent=audit.user_agent(request),
        settings=settings,
    )

    await audit.record(
        db,
        action=AuditAction.SESSION_LOGIN_SUCCEEDED,
        organization_id=active.organization_id if active else None,
        actor_user_id=user.id,
        resource_type="session",
        resource_id=issued.session.id,
        request=request,
    )
    await db.commit()

    set_session_cookies(
        response, token=issued.token, csrf_token=issued.csrf_token, settings=settings
    )
    logger.info("auth.login_succeeded", user_id=str(user.id))

    context = AuthContext(
        user=user,
        session=issued.session,
        memberships=memberships,
        active_membership=active,
    )
    return await _session_payload(db, context)


@router.get(
    "/session",
    response_model=AuthenticatedSessionResponse | AnonymousSessionResponse,
    summary="Who is signed in",
)
async def read_session(
    request: Request,
    db: DbSession,
    context: OptionalAuth,
) -> AuthenticatedSessionResponse | AnonymousSessionResponse:
    """Return the caller's session, or an anonymous state.

    200 in both cases. This endpoint answers a question, and "nobody is signed
    in" is a successful answer — returning 401 would make every client treat a
    normal first page load as an error.
    """
    if context is None:
        rejection = getattr(request.state, "session_rejection", None)
        # The interface says "your session ended, sign in again" rather than
        # showing a bare sign-in screen. Only the coarse category crosses the
        # boundary; the specific reason stays in the logs.
        return AnonymousSessionResponse(
            reason="expired" if rejection in {"expired", "idle_timeout", "revoked"} else "anonymous"
        )
    return await _session_payload(db, context)


@router.post(
    "/logout",
    response_model=LogoutResponse,
    summary="End the current session",
)
async def logout(
    request: Request,
    response: Response,
    db: DbSession,
    settings: AppSettings,
    context: OptionalAuth,
) -> LogoutResponse:
    """Revoke the session server-side and clear both cookies.

    Idempotent: logging out without a session succeeds. A client that has lost
    its session still needs the local cookies cleared, and returning an error
    would leave it stuck holding them.

    CSRF is enforced only when a session exists — see the explicit call below
    rather than a route dependency, because the anonymous case has no token to
    check and must still succeed.
    """
    if context is not None:
        await enforce_csrf(request, context, settings)
        await revoke_session(db, context.session, reason="logout")
        await audit.record(
            db,
            action=AuditAction.SESSION_LOGGED_OUT,
            organization_id=context.organization_id,
            actor_user_id=context.user.id,
            resource_type="session",
            resource_id=context.session.id,
            request=request,
        )
        await db.commit()
        logger.info("auth.logout", user_id=str(context.user.id))

    clear_session_cookies(response, settings=settings)
    return LogoutResponse()


@router.post(
    "/organization",
    response_model=AuthenticatedSessionResponse,
    summary="Switch the session's active organization",
)
async def switch_active_organization(
    payload: SwitchOrganizationRequest,
    request: Request,
    db: DbSession,
    auth: CurrentAuth,
    settings: AppSettings,
) -> AuthenticatedSessionResponse:
    """Point the session at another organization the caller belongs to.

    Membership is checked in the service, and the composite foreign key on
    ``sessions`` checks it again on flush. A request naming an organization the
    caller is not a member of gets 403 — not 404 — because the resource may
    well exist; what is missing is permission.
    """
    await enforce_csrf(request, auth, settings)

    try:
        membership = await switch_organization(
            db, context=auth, organization_id=payload.organization_id
        )
    except NotAMemberError:
        logger.warning(
            "auth.organization_switch_denied",
            user_id=str(auth.user.id),
            requested_organization_id=str(payload.organization_id),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of that organization.",
        ) from None

    await audit.record(
        db,
        action=AuditAction.ORGANIZATION_SWITCHED,
        organization_id=membership.organization_id,
        actor_user_id=auth.user.id,
        resource_type="organization",
        resource_id=membership.organization_id,
        request=request,
    )
    await db.commit()

    refreshed = AuthContext(
        user=auth.user,
        session=auth.session,
        memberships=auth.memberships,
        active_membership=membership,
    )
    return await _session_payload(db, refreshed)
