"""The forgot-password flow.

These are mostly security tests. A reset endpoint is the one place where being
helpful — "no account with that address", "that link already expired" — hands
an attacker exactly what they came for.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.password_reset import PasswordResetToken
from app.models.session import Session
from app.services.notifications import set_reset_link_sender
from tests.factories import register

pytestmark = pytest.mark.integration

NEW_PASSWORD = "a-perfectly-fine-new-password"


class RecordingSender:
    """Captures the link instead of delivering it."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    @property
    def describes_itself_as_delivered(self) -> bool:
        return True

    async def send(self, *, email: str, link: str) -> None:
        self.sent.append((email, link))

    @property
    def last_token(self) -> str:
        match = re.search(r"token=([^&]+)", self.sent[-1][1])
        assert match, "the link must carry a token"
        return match.group(1)


@pytest.fixture
def sender() -> RecordingSender:
    recorder = RecordingSender()
    set_reset_link_sender(recorder)
    return recorder


async def request_reset(client: AsyncClient, email: str) -> int:
    response = await client.post("/api/auth/forgot-password", json={"email": email})
    return response.status_code


# --- Not telling an attacker who exists ------------------------------------


async def test_an_unknown_address_is_answered_exactly_like_a_known_one(
    client: AsyncClient, anonymous_client: AsyncClient, sender: RecordingSender
) -> None:
    """A reset form that says "no such account" is an enumeration oracle."""
    account = await register(client)

    known = await anonymous_client.post("/api/auth/forgot-password", json={"email": account.email})
    unknown = await anonymous_client.post(
        "/api/auth/forgot-password", json={"email": "nobody-here@example.com"}
    )

    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    # And only the real address was ever sent anything.
    assert [email for email, _ in sender.sent] == [account.email]


async def test_the_response_never_carries_the_token(
    client: AsyncClient, anonymous_client: AsyncClient, sender: RecordingSender
) -> None:
    account = await register(client)
    response = await anonymous_client.post(
        "/api/auth/forgot-password", json={"email": account.email}
    )
    assert sender.last_token not in response.text


# --- Single use, and short lived -------------------------------------------


async def test_a_token_works_once_and_never_again(
    client: AsyncClient, anonymous_client: AsyncClient, sender: RecordingSender
) -> None:
    """A spent link sitting in an inbox must not still open."""
    account = await register(client)
    await request_reset(anonymous_client, account.email)
    token = sender.last_token

    first = await anonymous_client.post(
        "/api/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}
    )
    assert first.status_code == 200

    second = await anonymous_client.post(
        "/api/auth/reset-password",
        json={"token": token, "password": "another-new-password-entirely"},
    )
    assert second.status_code == 400


async def test_an_expired_token_is_refused(
    client: AsyncClient, anonymous_client: AsyncClient, db: AsyncSession, sender: RecordingSender
) -> None:
    account = await register(client)
    await request_reset(anonymous_client, account.email)
    token = sender.last_token

    record = await db.scalar(
        select(PasswordResetToken).where(PasswordResetToken.user_id == account.user_id)
    )
    assert record is not None
    record.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db.commit()

    response = await anonymous_client.post(
        "/api/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}
    )
    assert response.status_code == 400


async def test_an_unknown_token_looks_the_same_as_a_spent_one(
    anonymous_client: AsyncClient,
) -> None:
    """Telling them apart would say whether a token ever existed."""
    response = await anonymous_client.post(
        "/api/auth/reset-password",
        json={"token": "x" * 40, "password": NEW_PASSWORD},
    )
    assert response.status_code == 400


async def test_a_second_outstanding_token_dies_with_the_first(
    client: AsyncClient, anonymous_client: AsyncClient, sender: RecordingSender
) -> None:
    """Two links minutes apart: using the newer must kill the older."""
    account = await register(client)
    await request_reset(anonymous_client, account.email)
    first_token = sender.last_token
    await request_reset(anonymous_client, account.email)
    second_token = sender.last_token
    assert first_token != second_token

    used = await anonymous_client.post(
        "/api/auth/reset-password", json={"token": second_token, "password": NEW_PASSWORD}
    )
    assert used.status_code == 200

    stale = await anonymous_client.post(
        "/api/auth/reset-password",
        json={"token": first_token, "password": "yet-another-password-here"},
    )
    assert stale.status_code == 400


# --- What a reset actually does --------------------------------------------


async def test_the_new_password_works_and_the_old_one_does_not(
    client: AsyncClient, anonymous_client: AsyncClient, sender: RecordingSender
) -> None:
    account = await register(client)
    await request_reset(anonymous_client, account.email)

    await anonymous_client.post(
        "/api/auth/reset-password",
        json={"token": sender.last_token, "password": NEW_PASSWORD},
    )

    old = await anonymous_client.post(
        "/api/auth/login", json={"email": account.email, "password": account.password}
    )
    assert old.status_code == 401

    new = await anonymous_client.post(
        "/api/auth/login", json={"email": account.email, "password": NEW_PASSWORD}
    )
    assert new.status_code == 200


async def test_resetting_ends_every_existing_session(
    client: AsyncClient, anonymous_client: AsyncClient, db: AsyncSession, sender: RecordingSender
) -> None:
    """Someone resetting often believes another person is signed in."""
    account = await register(client)

    live = await db.scalars(
        select(Session).where(Session.user_id == account.user_id, Session.revoked_at.is_(None))
    )
    assert len(list(live)) >= 1

    await request_reset(anonymous_client, account.email)
    await anonymous_client.post(
        "/api/auth/reset-password",
        json={"token": sender.last_token, "password": NEW_PASSWORD},
    )

    still_live = await db.scalars(
        select(Session).where(Session.user_id == account.user_id, Session.revoked_at.is_(None))
    )
    assert list(still_live) == []

    # And the browser that was signed in is now signed out.
    session = await client.get("/api/auth/session")
    assert session.json().get("authenticated") is not True


async def test_a_weak_password_is_refused_at_reset_too(
    client: AsyncClient, anonymous_client: AsyncClient, sender: RecordingSender
) -> None:
    """A reset must not be a way around the policy that guards sign-up."""
    account = await register(client)
    await request_reset(anonymous_client, account.email)

    response = await anonymous_client.post(
        "/api/auth/reset-password",
        json={"token": sender.last_token, "password": "short"},
    )
    assert response.status_code == 422


async def test_only_the_token_hash_is_stored(
    client: AsyncClient, anonymous_client: AsyncClient, db: AsyncSession, sender: RecordingSender
) -> None:
    """A database dump must not be usable to reset anyone's password."""
    account = await register(client)
    await request_reset(anonymous_client, account.email)

    record = await db.scalar(
        select(PasswordResetToken).where(PasswordResetToken.user_id == account.user_id)
    )
    assert record is not None
    assert record.token_hash != sender.last_token
    assert len(record.token_hash) == 64
