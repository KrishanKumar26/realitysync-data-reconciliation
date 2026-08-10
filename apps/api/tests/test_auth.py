"""Authentication lifecycle: registration, login, session, logout."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session import Session
from app.models.user import User
from tests.factories import DEFAULT_PASSWORD, login, register, unique_email

pytestmark = pytest.mark.integration


# --- Registration ----------------------------------------------------------


async def test_registration_creates_user_organization_and_session(
    client: AsyncClient, db: AsyncSession
) -> None:
    email = unique_email()

    response = await client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": DEFAULT_PASSWORD,
            "full_name": "Ada Lovelace",
            "organization_name": "Analytical Engines",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["authenticated"] is True
    assert body["user"]["email"] == email
    assert body["user"]["full_name"] == "Ada Lovelace"
    assert len(body["organizations"]) == 1
    assert body["organizations"][0]["name"] == "Analytical Engines"
    assert body["organizations"][0]["slug"] == "analytical-engines"
    assert body["organizations"][0]["role"] == "owner"
    assert body["active_organization_id"] == body["organizations"][0]["id"]

    stored = await db.scalar(select(User).where(User.email == email))
    assert stored is not None
    # The password was hashed, not stored.
    assert stored.password_hash.startswith("$argon2id$")
    assert DEFAULT_PASSWORD not in stored.password_hash


async def test_registration_rejects_a_duplicate_email(client: AsyncClient) -> None:
    account = await register(client)

    response = await client.post(
        "/api/auth/register",
        json={
            "email": account.email,
            "password": DEFAULT_PASSWORD,
            "full_name": "Someone Else",
            "organization_name": "Another Org",
        },
    )

    assert response.status_code == 409


async def test_email_uniqueness_is_case_insensitive(client: AsyncClient) -> None:
    """CITEXT means Ada@… and ada@… are one account, enforced by the index."""
    account = await register(client, email=unique_email("Mixed").upper())

    response = await client.post(
        "/api/auth/register",
        json={
            "email": account.email.lower(),
            "password": DEFAULT_PASSWORD,
            "full_name": "Case Twin",
            "organization_name": "Twin Org",
        },
    )

    assert response.status_code == 409


async def test_registration_enforces_the_password_policy(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/register",
        json={
            "email": unique_email(),
            "password": "short",
            "full_name": "Too Short",
            "organization_name": "Policy Org",
        },
    )

    assert response.status_code == 422


async def test_organization_slugs_are_unique(client: AsyncClient) -> None:
    """Two organizations with the same name get distinct slugs."""
    account = await register(client, organization_name="Duplicate Name")

    second = await client.post(
        "/api/organizations",
        json={"name": "Duplicate Name"},
        headers=account.auth_headers(),
    )

    assert second.status_code == 201
    assert second.json()["slug"] == "duplicate-name-2"


async def test_organization_name_with_no_ascii_letters_still_gets_a_valid_slug(
    client: AsyncClient,
) -> None:
    """A name that slugifies to nothing must not violate the slug CHECK."""
    account = await register(client)

    response = await client.post(
        "/api/organizations", json={"name": "!!!"}, headers=account.auth_headers()
    )

    assert response.status_code == 201
    slug = response.json()["slug"]
    assert len(slug) >= 2
    assert slug.startswith("org-")


# --- Login -----------------------------------------------------------------


async def test_login_with_valid_credentials_issues_a_session(
    client: AsyncClient, anonymous_client: AsyncClient
) -> None:
    account = await register(client)

    body = await login(anonymous_client, email=account.email)

    assert body["authenticated"] is True
    assert body["user"]["email"] == account.email
    assert anonymous_client.cookies.get("rs_session")


async def test_login_is_case_insensitive_on_email(
    client: AsyncClient, anonymous_client: AsyncClient
) -> None:
    account = await register(client, email=unique_email("case"))

    response = await anonymous_client.post(
        "/api/auth/login",
        json={"email": account.email.upper(), "password": DEFAULT_PASSWORD},
    )

    assert response.status_code == 200


@pytest.mark.parametrize("password", ["wrong-password-entirely", ""])
async def test_login_rejects_a_bad_password(
    client: AsyncClient, anonymous_client: AsyncClient, password: str
) -> None:
    account = await register(client)

    response = await anonymous_client.post(
        "/api/auth/login", json={"email": account.email, "password": password}
    )

    assert response.status_code in {401, 422}
    if response.status_code == 401:
        assert response.json()["error"]["message"] == "Invalid email or password."


async def test_login_for_an_unknown_account_gives_the_same_message(
    client: AsyncClient,
) -> None:
    """Identical wording for "no such user" and "wrong password".

    Different messages would turn the endpoint into a user-enumeration oracle.
    """
    account = await register(client)

    unknown = await client.post(
        "/api/auth/login",
        json={"email": unique_email("nobody"), "password": DEFAULT_PASSWORD},
    )
    wrong_password = await client.post(
        "/api/auth/login", json={"email": account.email, "password": "not-the-password"}
    )

    assert unknown.status_code == wrong_password.status_code == 401
    assert unknown.json()["error"]["message"] == wrong_password.json()["error"]["message"]


async def test_login_refuses_a_deactivated_account(
    client: AsyncClient, anonymous_client: AsyncClient, db: AsyncSession
) -> None:
    account = await register(client)
    user = await db.get(User, account.user_id)
    assert user is not None
    user.is_active = False
    await db.commit()

    response = await anonymous_client.post(
        "/api/auth/login", json={"email": account.email, "password": DEFAULT_PASSWORD}
    )

    # Same message as a wrong password: whether an account is disabled is not
    # something an unauthenticated caller should be able to discover.
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Invalid email or password."


# --- Session persistence ---------------------------------------------------


async def test_session_persists_across_requests(client: AsyncClient) -> None:
    account = await register(client)

    for _ in range(3):
        response = await client.get("/api/auth/session")
        assert response.status_code == 200
        assert response.json()["user"]["id"] == str(account.user_id)


async def test_session_survives_a_new_client_carrying_the_same_cookie(
    client: AsyncClient, app: FastAPI
) -> None:
    """Authentication lives in the cookie and the database, not in client state.

    Equivalent to closing the browser and reopening it.
    """
    account = await register(client)
    token = client.cookies.get("rs_session")
    assert token

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as fresh:
        fresh.cookies.set("rs_session", token)
        response = await fresh.get("/api/auth/session")

        assert response.status_code == 200
        assert response.json()["user"]["id"] == str(account.user_id)


# --- 5. Invalid and expired sessions are rejected --------------------------


async def test_an_unknown_session_token_is_rejected(client: AsyncClient) -> None:
    client.cookies.set("rs_session", "a-token-that-was-never-issued")

    session_response = await client.get("/api/auth/session")
    protected = await client.get("/api/organizations/current")

    assert session_response.json()["authenticated"] is False
    assert protected.status_code == 401


async def test_an_expired_session_is_rejected(client: AsyncClient, db: AsyncSession) -> None:
    account = await register(client)

    stored = await db.scalar(select(Session).where(Session.user_id == account.user_id))
    assert stored is not None
    stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db.commit()

    session_response = await client.get("/api/auth/session")
    protected = await client.get("/api/organizations/current")

    assert session_response.status_code == 200
    assert session_response.json() == {"authenticated": False, "reason": "expired"}
    assert protected.status_code == 401


async def test_an_idle_session_is_rejected(client: AsyncClient, db: AsyncSession, settings) -> None:
    """Absolute expiry is not the only limit; inactivity ends a session too."""
    account = await register(client)

    stored = await db.scalar(select(Session).where(Session.user_id == account.user_id))
    assert stored is not None
    stored.last_seen_at = datetime.now(UTC) - timedelta(
        seconds=settings.session_idle_timeout_seconds + 60
    )
    await db.commit()

    response = await client.get("/api/auth/session")

    assert response.json() == {"authenticated": False, "reason": "expired"}


async def test_a_session_whose_user_was_deactivated_is_rejected(
    client: AsyncClient, db: AsyncSession
) -> None:
    account = await register(client)
    user = await db.get(User, account.user_id)
    assert user is not None
    user.is_active = False
    await db.commit()

    protected = await client.get("/api/organizations/current")

    assert protected.status_code == 401


# --- 6. Logout invalidates the session -------------------------------------


async def test_logout_revokes_the_session_server_side(
    client: AsyncClient, db: AsyncSession, app: FastAPI
) -> None:
    """The token must be dead on the server, not merely dropped by the browser.

    A logout that only clears the cookie leaves a working credential in every
    proxy log and browser history it ever touched, so the test re-sends the
    original token from a client that never saw the logout.
    """
    account = await register(client)
    token = client.cookies.get("rs_session")
    assert token

    logout_response = await client.post("/api/auth/logout", headers=account.auth_headers())
    assert logout_response.status_code == 200

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as replay:
        replay.cookies.set("rs_session", token)
        assert (await replay.get("/api/auth/session")).json()["authenticated"] is False
        assert (await replay.get("/api/organizations/current")).status_code == 401

    stored = await db.scalar(select(Session).where(Session.user_id == account.user_id))
    assert stored is not None
    assert stored.revoked_at is not None
    assert stored.revoked_reason == "logout"


async def test_logout_clears_both_cookies(client: AsyncClient) -> None:
    account = await register(client)

    response = await client.post("/api/auth/logout", headers=account.auth_headers())

    assert response.status_code == 200
    assert not client.cookies.get("rs_session")
    assert not client.cookies.get("rs_csrf")


async def test_logout_without_a_session_succeeds(client: AsyncClient) -> None:
    """Idempotent: a client that lost its session still needs cookies cleared."""
    response = await client.post("/api/auth/logout")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


async def test_login_after_logout_issues_a_new_distinct_session(
    client: AsyncClient,
) -> None:
    account = await register(client)
    first_token = client.cookies.get("rs_session")

    await client.post("/api/auth/logout", headers=account.auth_headers())
    await login(client, email=account.email)

    second_token = client.cookies.get("rs_session")
    assert second_token
    assert second_token != first_token
