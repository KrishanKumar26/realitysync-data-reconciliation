"""Organization routes.

Every route here that reads organization-owned data takes
:data:`~app.api.deps.CurrentOrganization`, so the tenant id is a required part
of the handler's signature rather than something it has to remember to look up.
Combined with the tenancy guard, a query that omits the scope raises instead of
returning another tenant's rows.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select

from app.api.deps import (
    AppSettings,
    CurrentAuth,
    CurrentOrganization,
    DbSession,
    RequireAdmin,
    enforce_csrf,
)
from app.core.logging import get_logger
from app.models.audit_log import AuditAction
from app.models.membership import Membership, OrganizationRole
from app.models.organization import Organization
from app.models.user import User
from app.schemas.organization import (
    CreateOrganizationRequest,
    MemberResponse,
    OrganizationResponse,
)
from app.services import audit
from app.services.auth import list_user_memberships, slugify, unique_slug

logger = get_logger(__name__)

router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.get(
    "",
    response_model=list[OrganizationResponse],
    summary="Organizations the caller belongs to",
)
async def list_organizations(db: DbSession, auth: CurrentAuth) -> list[Organization]:
    """List the caller's organizations.

    Bounded to the caller's own memberships, so it spans tenants only in the
    sense that a person spans them — it can never return an organization the
    caller is not a member of.
    """
    memberships = await list_user_memberships(db, auth.user.id)
    if not memberships:
        return []

    organization_ids = [m.organization_id for m in memberships]
    rows = await db.scalars(
        select(Organization)
        .where(Organization.id.in_(organization_ids))
        .order_by(Organization.name)
    )
    return list(rows)


@router.get(
    "/current",
    response_model=OrganizationResponse,
    summary="The session's active organization",
)
async def read_current_organization(db: DbSession, context: CurrentOrganization) -> Organization:
    """Return the active organization.

    ``context.organization_id`` came from the session, which the database
    guarantees points at a real membership, so no additional permission check
    is needed here.
    """
    organization = await db.get(Organization, context.organization_id)
    if organization is None:  # pragma: no cover - foreign keys prevent this
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found.")
    return organization


@router.get(
    "/current/members",
    response_model=list[MemberResponse],
    summary="Members of the active organization",
)
async def list_members(db: DbSession, context: CurrentOrganization) -> list[MemberResponse]:
    """List everyone in the active organization.

    The scope is not a filter the caller supplies — it comes from the session.
    There is no request parameter that could name a different organization, so
    there is nothing for a caller to tamper with.
    """
    rows = await db.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.organization_id == context.organization_id)
        .order_by(Membership.created_at)
    )
    return [
        MemberResponse(
            user_id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=membership.role_enum,
            joined_at=membership.created_at,
        )
        for membership, user in rows
    ]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=OrganizationResponse,
    summary="Create an organization",
)
async def create_organization(
    payload: CreateOrganizationRequest,
    request: Request,
    db: DbSession,
    auth: CurrentAuth,
    settings: AppSettings,
) -> Organization:
    """Create a new organization with the caller as its owner.

    Does not switch the session to it. Creating a workspace and moving into it
    are separate decisions, and silently relocating the caller's session would
    change what their next request sees.
    """
    await enforce_csrf(request, auth, settings)

    organization = Organization(
        name=payload.name, slug=await unique_slug(db, slugify(payload.name))
    )
    db.add(organization)
    await db.flush()

    db.add(
        Membership(
            user_id=auth.user.id,
            organization_id=organization.id,
            role=OrganizationRole.OWNER.value,
        )
    )
    await audit.record(
        db,
        action=AuditAction.ORGANIZATION_CREATED,
        organization_id=organization.id,
        actor_user_id=auth.user.id,
        resource_type="organization",
        resource_id=organization.id,
        details={"slug": organization.slug},
        request=request,
    )
    await db.commit()

    logger.info(
        "organization.created",
        organization_id=str(organization.id),
        user_id=str(auth.user.id),
    )
    return organization


@router.delete(
    "/current/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a member from the active organization",
)
async def remove_member(
    user_id: uuid.UUID,
    request: Request,
    db: DbSession,
    settings: AppSettings,
    context: RequireAdmin,
) -> Response:
    """Remove someone from the active organization.

    Requires admin or owner. Two rules beyond the role check:

    * You cannot remove yourself — leaving is a different operation with
      different consequences, and conflating them makes it easy to lock
      yourself out by accident.
    * You cannot remove the last owner, which would leave the organization with
      nobody able to administer it.

    Deleting the membership also destroys that member's sessions in this
    organization, through the composite foreign key's ON DELETE CASCADE. Access
    ends immediately rather than whenever their cookie happens to expire.
    """
    await enforce_csrf(request, context.auth, settings)

    if user_id == context.user.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You cannot remove yourself from an organization.",
        )

    membership = await db.scalar(
        select(Membership).where(
            Membership.organization_id == context.organization_id,
            Membership.user_id == user_id,
        )
    )
    if membership is None:
        # 404 is safe here: the caller is already an admin of this organization,
        # so "is this person a member" is not information they lack.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found.")

    if membership.role == OrganizationRole.OWNER.value:
        remaining_owners = await db.scalar(
            select(Membership)
            .where(
                Membership.organization_id == context.organization_id,
                Membership.role == OrganizationRole.OWNER.value,
                Membership.user_id != user_id,
            )
            .limit(1)
        )
        if remaining_owners is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An organization must keep at least one owner.",
            )

    await db.delete(membership)
    await audit.record(
        db,
        action="membership.removed",
        organization_id=context.organization_id,
        actor_user_id=context.user.id,
        resource_type="membership",
        resource_id=user_id,
        request=request,
    )
    await db.commit()

    logger.info(
        "organization.member_removed",
        organization_id=str(context.organization_id),
        removed_user_id=str(user_id),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
