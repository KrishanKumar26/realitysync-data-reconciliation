"""Helpers for creating real records through the real endpoints.

Not factories in the usual sense: nothing here inserts rows directly. Every
account is created by calling ``POST /api/auth/register``, so a test that
passes proves the production registration path works. A direct-insert helper
would be faster and would also let the whole suite stay green while
registration was broken.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from httpx import AsyncClient

#: Comfortably above the 12-character policy minimum.
DEFAULT_PASSWORD = "correct-horse-battery-staple"


def unique_email(prefix: str = "user") -> str:
    """A collision-free address.

    Tests roll back, but they also run concurrently against one database in CI,
    and the users.email unique index is real.

    example.com, not example.test: EmailStr rejects RFC 2606 special-use TLDs
    such as .test and .invalid, which is correct behaviour and worth keeping.
    """
    return f"{prefix}-{uuid.uuid4().hex[:12]}@example.com"


@dataclass(frozen=True, slots=True)
class Account:
    """A registered user, their organization, and their signed-in client."""

    client: AsyncClient
    email: str
    password: str
    user_id: uuid.UUID
    organization_id: uuid.UUID
    csrf_token: str

    def auth_headers(self) -> dict[str, str]:
        """Headers for a state-changing request from this account."""
        return {"X-CSRF-Token": self.csrf_token}


async def register(
    client: AsyncClient,
    *,
    email: str | None = None,
    password: str = DEFAULT_PASSWORD,
    full_name: str = "Test Person",
    organization_name: str = "Test Organization",
) -> Account:
    """Register an account and return it, signed in on `client`."""
    email = email or unique_email()
    response = await client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": password,
            "full_name": full_name,
            "organization_name": organization_name,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()

    return Account(
        client=client,
        email=email,
        password=password,
        user_id=uuid.UUID(body["user"]["id"]),
        organization_id=uuid.UUID(body["active_organization_id"]),
        csrf_token=body["csrf_token"],
    )


async def login(
    client: AsyncClient, *, email: str, password: str = DEFAULT_PASSWORD
) -> dict[str, object]:
    """Sign in on `client` and return the session payload."""
    response = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body
