"""Multi-tenancy isolation.

The proofs that matter most in this codebase. Every account here is created
through the real registration endpoint against a real PostgreSQL, so these test
the deployed behaviour rather than a mock of it.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.tenancy import (
    MissingOrganizationScopeError,
    assert_organization_scoped,
    unscoped,
)
from app.models.membership import Membership
from app.models.session import Session
from tests.factories import register

pytestmark = pytest.mark.integration


# --- 1. An authenticated user can reach their own organization -------------


async def test_authenticated_user_can_access_their_organization(
    client: AsyncClient,
) -> None:
    account = await register(client, organization_name="Northwind Logistics")

    response = await client.get("/api/organizations/current")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(account.organization_id)
    assert body["name"] == "Northwind Logistics"


async def test_member_list_returns_only_the_active_organization(
    client: AsyncClient,
) -> None:
    account = await register(client)

    response = await client.get("/api/organizations/current/members")

    assert response.status_code == 200
    members = response.json()
    assert len(members) == 1
    assert members[0]["user_id"] == str(account.user_id)
    assert members[0]["role"] == "owner"


# --- 2. An unauthenticated user is rejected --------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/organizations"),
        ("GET", "/api/organizations/current"),
        ("GET", "/api/organizations/current/members"),
        ("POST", "/api/organizations"),
        ("POST", "/api/auth/organization"),
    ],
)
async def test_protected_routes_reject_anonymous_callers(
    client: AsyncClient, method: str, path: str
) -> None:
    response = await client.request(method, path, json={})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_session_endpoint_reports_anonymous_without_erroring(
    client: AsyncClient,
) -> None:
    """`/api/auth/session` answers "nobody" with 200, not 401."""
    response = await client.get("/api/auth/session")

    assert response.status_code == 200
    assert response.json() == {"authenticated": False, "reason": "anonymous"}


# --- 3. A user cannot reach another organization's data --------------------


async def test_user_cannot_read_another_organizations_members(
    client: AsyncClient, anonymous_client: AsyncClient
) -> None:
    """The decisive isolation test: two real tenants, no leakage between them."""
    alice = await register(client, organization_name="Alice Industries")
    bob = await register(anonymous_client, organization_name="Bob Supplies")

    assert alice.organization_id != bob.organization_id

    alice_members = (await client.get("/api/organizations/current/members")).json()
    bob_members = (await anonymous_client.get("/api/organizations/current/members")).json()

    alice_ids = {m["user_id"] for m in alice_members}
    bob_ids = {m["user_id"] for m in bob_members}

    assert alice_ids == {str(alice.user_id)}
    assert bob_ids == {str(bob.user_id)}
    assert not alice_ids & bob_ids


async def test_user_cannot_switch_into_an_organization_they_do_not_belong_to(
    client: AsyncClient, anonymous_client: AsyncClient
) -> None:
    await register(client)
    bob = await register(anonymous_client)

    alice_csrf = client.cookies.get("rs_csrf")
    response = await client.post(
        "/api/auth/organization",
        json={"organization_id": str(bob.organization_id)},
        headers={"X-CSRF-Token": alice_csrf or ""},
    )

    assert response.status_code == 403
    # 403, not 404: the organization exists. Pretending otherwise would be a
    # lie that also leaks nothing useful, since the caller learns the same
    # thing either way.
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_organization_list_contains_only_the_callers_organizations(
    client: AsyncClient, anonymous_client: AsyncClient
) -> None:
    alice = await register(client, organization_name="Alice Only")
    bob = await register(anonymous_client, organization_name="Bob Only")

    alice_orgs = (await client.get("/api/organizations")).json()

    ids = {org["id"] for org in alice_orgs}
    assert ids == {str(alice.organization_id)}
    assert str(bob.organization_id) not in ids


async def test_removing_a_member_of_another_organization_is_not_possible(
    client: AsyncClient, anonymous_client: AsyncClient
) -> None:
    """Bob's user id is meaningless inside Alice's organization."""
    await register(client)
    bob = await register(anonymous_client)

    alice_csrf = client.cookies.get("rs_csrf")
    response = await client.delete(
        f"/api/organizations/current/members/{bob.user_id}",
        headers={"X-CSRF-Token": alice_csrf or ""},
    )

    # 404 within Alice's own organization — the scope came from her session, so
    # the lookup could never have found Bob in the first place.
    assert response.status_code == 404


# --- 4. Membership permissions are enforced --------------------------------


async def test_role_below_the_requirement_is_refused(
    client: AsyncClient, anonymous_client: AsyncClient, db: AsyncSession
) -> None:
    """A viewer cannot remove members; requires admin or above."""
    owner = await register(client, organization_name="Role Test Org")
    viewer = await register(anonymous_client)

    # Add the second user to the first organization as a viewer. Written
    # directly because inviting members is not a Phase 2 endpoint — the
    # membership row itself is real, and it is what the check reads.
    db.add(
        Membership(
            user_id=viewer.user_id,
            organization_id=owner.organization_id,
            role="viewer",
        )
    )
    await db.commit()

    viewer_csrf = anonymous_client.cookies.get("rs_csrf")
    switched = await anonymous_client.post(
        "/api/auth/organization",
        json={"organization_id": str(owner.organization_id)},
        headers={"X-CSRF-Token": viewer_csrf or ""},
    )
    assert switched.status_code == 200

    response = await anonymous_client.delete(
        f"/api/organizations/current/members/{owner.user_id}",
        headers={"X-CSRF-Token": viewer_csrf or ""},
    )

    assert response.status_code == 403
    assert "admin" in response.json()["error"]["message"]


async def test_role_at_or_above_the_requirement_is_allowed(
    client: AsyncClient, anonymous_client: AsyncClient, db: AsyncSession
) -> None:
    """An owner satisfies a requirement of admin — ranks, not exact matches."""
    owner = await register(client, organization_name="Rank Test Org")
    other = await register(anonymous_client)

    db.add(
        Membership(
            user_id=other.user_id,
            organization_id=owner.organization_id,
            role="member",
        )
    )
    await db.commit()

    response = await client.delete(
        f"/api/organizations/current/members/{other.user_id}",
        headers=owner.auth_headers(),
    )

    assert response.status_code == 204


async def test_last_owner_cannot_be_removed(
    client: AsyncClient, anonymous_client: AsyncClient, db: AsyncSession
) -> None:
    owner = await register(client, organization_name="Sole Owner Org")
    admin = await register(anonymous_client)

    db.add(
        Membership(
            user_id=admin.user_id,
            organization_id=owner.organization_id,
            role="admin",
        )
    )
    await db.commit()

    admin_csrf = anonymous_client.cookies.get("rs_csrf")
    await anonymous_client.post(
        "/api/auth/organization",
        json={"organization_id": str(owner.organization_id)},
        headers={"X-CSRF-Token": admin_csrf or ""},
    )

    response = await anonymous_client.delete(
        f"/api/organizations/current/members/{owner.user_id}",
        headers={"X-CSRF-Token": admin_csrf or ""},
    )

    assert response.status_code == 409
    assert "at least one owner" in response.json()["error"]["message"]


# --- 7. Organization switching is correctly scoped -------------------------


async def test_switching_organization_changes_what_the_session_sees(
    client: AsyncClient,
) -> None:
    """The point of switching: the next request reads a different tenant."""
    account = await register(client, organization_name="First Workspace")

    created = await client.post(
        "/api/organizations",
        json={"name": "Second Workspace"},
        headers=account.auth_headers(),
    )
    assert created.status_code == 201
    second_id = created.json()["id"]

    # Creating an organization must not move the session into it.
    before = await client.get("/api/organizations/current")
    assert before.json()["id"] == str(account.organization_id)

    switched = await client.post(
        "/api/auth/organization",
        json={"organization_id": second_id},
        headers=account.auth_headers(),
    )
    assert switched.status_code == 200
    assert switched.json()["active_organization_id"] == second_id

    after = await client.get("/api/organizations/current")
    assert after.json()["id"] == second_id
    assert after.json()["name"] == "Second Workspace"


async def test_switch_survives_a_new_request_because_it_is_stored_server_side(
    client: AsyncClient, db: AsyncSession, anonymous_client: AsyncClient
) -> None:
    account = await register(client, organization_name="Persistence Org")
    created = await client.post(
        "/api/organizations",
        json={"name": "Switched Into"},
        headers=account.auth_headers(),
    )
    second_id = uuid.UUID(created.json()["id"])

    await client.post(
        "/api/auth/organization",
        json={"organization_id": str(second_id)},
        headers=account.auth_headers(),
    )

    # Read the row itself: the active organization lives on the session record,
    # not in a client-supplied header that a caller could tamper with.
    stored = await db.scalar(select(Session).where(Session.user_id == account.user_id))
    assert stored is not None
    assert stored.active_organization_id == second_id


# --- 8. organization_id cannot be silently omitted -------------------------


async def test_unscoped_query_on_a_tenant_owned_table_raises(
    db: AsyncSession,
) -> None:
    """The guard that makes a forgotten filter a loud failure.

    Without this, `select(Membership).where(Membership.role == "owner")` would
    quietly return every organization's owners.
    """
    with pytest.raises(MissingOrganizationScopeError) as exc_info:
        await db.scalars(select(Membership).where(Membership.role == "owner"))

    assert "memberships" in str(exc_info.value)


async def test_scoped_query_on_a_tenant_owned_table_is_allowed(
    client: AsyncClient, db: AsyncSession
) -> None:
    account = await register(client)

    rows = await db.scalars(
        select(Membership).where(Membership.organization_id == account.organization_id)
    )

    assert [m.user_id for m in rows.all()] == [account.user_id]


async def test_unscoped_context_manager_permits_cross_tenant_reads(
    db: AsyncSession,
) -> None:
    """The escape hatch works, and is required to be explicit."""
    with unscoped():
        result = await db.scalars(select(Membership).limit(1))
        result.all()  # no exception


def test_guard_covers_updates_and_deletes_not_just_selects() -> None:
    """A forgotten filter on a write is worse than on a read."""
    from sqlalchemy import delete, update

    with pytest.raises(MissingOrganizationScopeError):
        assert_organization_scoped(update(Membership).values(role="owner"))

    with pytest.raises(MissingOrganizationScopeError):
        assert_organization_scoped(delete(Membership))


def test_guard_accepts_writes_that_are_scoped() -> None:
    from sqlalchemy import delete, update

    organization_id = uuid.uuid4()

    assert_organization_scoped(
        update(Membership).where(Membership.organization_id == organization_id).values(role="admin")
    )
    assert_organization_scoped(
        delete(Membership).where(Membership.organization_id == organization_id)
    )


# --- Database-level enforcement --------------------------------------------


async def test_database_refuses_a_session_pointed_at_a_foreign_organization(
    client: AsyncClient, anonymous_client: AsyncClient, db: AsyncSession
) -> None:
    """PostgreSQL itself is the last line of defence.

    Even with the application bypassed entirely, the composite foreign key
    makes a cross-tenant session unrepresentable.
    """
    alice = await register(client)
    bob = await register(anonymous_client)

    forged = Session(
        user_id=alice.user_id,
        token_hash="0" * 64,
        csrf_token="x" * 40,
        active_organization_id=bob.organization_id,
        expires_at=(
            await db.scalar(select(Session.expires_at).where(Session.user_id == alice.user_id))
        ),
    )
    db.add(forged)

    with pytest.raises(IntegrityError) as exc_info:
        await db.flush()

    assert "fk_sessions_active_membership" in str(exc_info.value)
    await db.rollback()
