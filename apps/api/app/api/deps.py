"""Request dependencies: authentication, CSRF and organization context.

The layering is deliberate and each step is a separate dependency, so a route
declares exactly how much context it needs and cannot accidentally get more:

    get_db            -> a session
    optional_context  -> AuthContext | None   (never rejects)
    require_auth      -> AuthContext          (401 if not signed in)
    require_organization -> OrganizationContext  (409 if none selected)
    require_role(...)    -> OrganizationContext  (403 if under-privileged)

`require_organization` is the important one: it yields an object carrying a
non-optional ``organization_id``. A route that takes it cannot forget to scope
its queries, because the tenant id is right there in its signature — and if it
still forgets, the tenancy guard in app/db/tenancy.py raises.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.security import tokens_equal
from app.db.session import get_session
from app.models.membership import Membership, OrganizationRole
from app.models.user import User
from app.services.auth import AuthContext, SessionRejection, resolve_session

logger = get_logger(__name__)

DbSession = Annotated[AsyncSession, Depends(get_session)]
AppSettings = Annotated[Settings, Depends(get_settings)]

#: Methods that cannot change state, and so need no CSRF token.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


@dataclass(frozen=True, slots=True)
class OrganizationContext:
    """An authenticated caller acting inside a specific organization."""

    auth: AuthContext
    membership: Membership

    @property
    def organization_id(self) -> uuid.UUID:
        """The tenant id. Non-optional — that is the whole point of this type."""
        return self.membership.organization_id

    @property
    def user(self) -> User:
        return self.auth.user

    @property
    def role(self) -> OrganizationRole:
        return self.membership.role_enum


async def optional_context(
    request: Request, db: DbSession, settings: AppSettings
) -> AuthContext | None:
    """Resolve the session cookie if there is a usable one.

    Never raises. Routes that must reject anonymous callers use
    :func:`require_auth`; this exists for endpoints that answer differently
    rather than failing — chiefly "am I signed in?".

    The rejection reason is attached to request.state so the session endpoint
    can distinguish "never signed in" from "your session ended" without the
    reason ever reaching the client verbatim.
    """
    token = request.cookies.get(settings.cookie_name)
    if not token:
        return None

    outcome = await resolve_session(db, token=token, settings=settings)
    if isinstance(outcome, SessionRejection):
        request.state.session_rejection = outcome.reason
        logger.info(
            "auth.session.rejected",
            reason=outcome.reason,
            path=request.url.path,
        )
        return None

    request.state.auth = outcome
    return outcome


OptionalAuth = Annotated["AuthContext | None", Depends(optional_context)]


async def require_auth(context: OptionalAuth) -> AuthContext:
    """Require a signed-in caller, or 401."""
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    return context


CurrentAuth = Annotated[AuthContext, Depends(require_auth)]


async def enforce_csrf(request: Request, auth: CurrentAuth, settings: AppSettings) -> None:
    """Double-submit CSRF check on state-changing authenticated requests.

    The session cookie is HttpOnly, so JavaScript on another origin cannot read
    it — but the browser will still attach it to a cross-site request. The
    defence is a token the attacker cannot read: it is delivered in a readable
    cookie and must be echoed in a request header, which the same-origin policy
    prevents a foreign page from doing.

    The submitted value is compared against the token stored on the session row,
    not against the cookie. Plain cookie-vs-header double-submit trusts that two
    values the attacker may both control happen to match; validating against
    server state removes that assumption, which matters because a cookie set
    from a sibling subdomain can overwrite the browser's copy.

    Login and registration are exempt: there is no session yet, so there is
    nothing to forge with. Those routes are protected by
    :func:`enforce_origin` instead.
    """
    if request.method in SAFE_METHODS:
        return

    submitted = request.headers.get(settings.csrf_header_name)
    if not submitted or not tokens_equal(submitted, auth.session.csrf_token):
        logger.warning(
            "auth.csrf.rejected",
            path=request.url.path,
            method=request.method,
            had_header=bool(submitted),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed.",
        )


async def require_organization(
    auth: CurrentAuth, csrf: Annotated[None, Depends(enforce_csrf)]
) -> OrganizationContext:
    """Require an authenticated caller with an organization selected.

    409 rather than 400: the request is well-formed, but the session is in a
    state that cannot serve it. The client's move is to select an organization
    and retry, which is a conflict of state, not a malformed request.
    """
    if auth.active_membership is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No organization selected for this session.",
        )
    return OrganizationContext(auth=auth, membership=auth.active_membership)


CurrentOrganization = Annotated[OrganizationContext, Depends(require_organization)]


def require_role(
    minimum: OrganizationRole,
) -> Callable[[OrganizationContext], Awaitable[OrganizationContext]]:
    """Build a dependency requiring at least `minimum` in the active organization.

    Roles are ranked, so a check is "at least this privileged" rather than an
    exact match — an owner passes every check an admin passes, without every
    route having to enumerate the roles above the one it cares about.
    """

    async def dependency(context: CurrentOrganization) -> OrganizationContext:
        if not context.role.satisfies(minimum):
            logger.warning(
                "auth.role.denied",
                required=minimum.value,
                actual=context.role.value,
                organization_id=str(context.organization_id),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires the {minimum.value} role.",
            )
        return context

    return dependency


#: Built once at import so routes annotate with a type rather than calling
#: require_role() in a parameter default, which would construct a new
#: dependency on every request.
RequireOwner = Annotated[OrganizationContext, Depends(require_role(OrganizationRole.OWNER))]
RequireAdmin = Annotated[OrganizationContext, Depends(require_role(OrganizationRole.ADMIN))]
RequireMember = Annotated[OrganizationContext, Depends(require_role(OrganizationRole.MEMBER))]
