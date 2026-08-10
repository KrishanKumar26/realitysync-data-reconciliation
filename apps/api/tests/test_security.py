"""Security properties: CSRF, origin, cookies, secret handling.

These assert the properties that are easy to break silently — nothing here
fails loudly in manual testing, which is exactly why it needs tests.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import hash_token, verify_password
from app.models.session import Session
from app.models.user import User
from tests.factories import DEFAULT_PASSWORD, register, unique_email

pytestmark = pytest.mark.integration


def _walk(value: Any, path: str = "") -> list[tuple[str, Any]]:
    """Flatten nested JSON into (path, value) pairs."""
    found: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            found.extend(_walk(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_walk(item, f"{path}[{index}]"))
    else:
        found.append((path, value))
    return found


# --- 10. Password hashes are never returned --------------------------------


async def test_no_endpoint_returns_a_password_hash(client: AsyncClient, db: AsyncSession) -> None:
    """Check every Phase 2 response against the real stored hash."""
    account = await register(client)

    user = await db.get(User, account.user_id)
    assert user is not None
    stored_hash = user.password_hash
    assert stored_hash.startswith("$argon2id$")

    responses = [
        await client.get("/api/auth/session"),
        await client.get("/api/organizations"),
        await client.get("/api/organizations/current"),
        await client.get("/api/organizations/current/members"),
        await client.post(
            "/api/auth/organization",
            json={"organization_id": str(account.organization_id)},
            headers=account.auth_headers(),
        ),
    ]

    for response in responses:
        body = response.text
        assert stored_hash not in body, f"{response.url} leaked the password hash"
        assert DEFAULT_PASSWORD not in body, f"{response.url} leaked the password"
        assert "$argon2" not in body, f"{response.url} leaked a hash"

        for path, _ in _walk(response.json()):
            assert "password" not in path.lower(), f"{response.url} exposed {path}"


async def test_registration_and_login_responses_carry_no_password_field(
    client: AsyncClient, anonymous_client: AsyncClient
) -> None:
    account = await register(client)
    login_response = await anonymous_client.post(
        "/api/auth/login", json={"email": account.email, "password": DEFAULT_PASSWORD}
    )

    for path, _ in _walk(login_response.json()):
        assert "password" not in path.lower()


async def test_the_stored_hash_is_a_real_argon2id_hash(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Verify it hashes rather than merely looking like a hash."""
    account = await register(client)

    user = await db.get(User, account.user_id)
    assert user is not None
    assert verify_password(DEFAULT_PASSWORD, user.password_hash)
    assert not verify_password("something-else-entirely", user.password_hash)


# --- 9. Authentication secrets are never logged ----------------------------


async def test_no_secret_reaches_the_logs(
    client: AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    """Drive a full lifecycle and inspect everything written to stdout.

    Covers the session token, the CSRF token and the password. The session
    token is the sharpest case: it is a live credential, and a log sink is a
    place secrets tend to survive far longer than anywhere else.
    """
    capsys.readouterr()  # discard anything from fixture setup

    email = unique_email()
    register_response = await client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": DEFAULT_PASSWORD,
            "full_name": "Log Probe",
            "organization_name": "Log Probe Org",
        },
    )
    assert register_response.status_code == 201

    session_token = client.cookies.get("rs_session")
    csrf_token = client.cookies.get("rs_csrf")
    assert session_token and csrf_token

    await client.get("/api/auth/session")
    await client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf_token})
    await client.post("/api/auth/login", json={"email": email, "password": DEFAULT_PASSWORD})
    # A failure path too: error handling is where secrets usually escape.
    await client.post("/api/auth/login", json={"email": email, "password": "wrong-password-here"})

    captured = capsys.readouterr()
    logs = captured.out + captured.err

    assert session_token not in logs, "the session token reached the logs"
    assert csrf_token not in logs, "the CSRF token reached the logs"
    assert DEFAULT_PASSWORD not in logs, "the password reached the logs"
    assert "wrong-password-here" not in logs, "a failed password reached the logs"


async def test_a_validation_failure_does_not_echo_the_submitted_value(
    client: AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression test.

    Pydantic includes the offending value as ``input`` in its error list. When
    that list was returned verbatim, a password that failed the length policy
    came back in the response body and went into the error log. Redaction by
    key name could not catch it: the key was "input", not "password".
    """
    capsys.readouterr()
    secret = "tiny-secret"  # 11 characters: one below the policy minimum

    response = await client.post(
        "/api/auth/register",
        json={
            "email": unique_email(),
            "password": secret,
            "full_name": "Policy Probe",
            "organization_name": "Policy Probe Org",
        },
    )

    assert response.status_code == 422
    assert secret not in response.text
    assert secret not in capsys.readouterr().out

    # The response still says which field is wrong and why.
    details = response.json()["error"]["details"]
    assert details[0]["loc"] == ["body", "password"]
    assert "at least 12 characters" in details[0]["msg"]


async def test_session_tokens_are_stored_hashed(client: AsyncClient, db: AsyncSession) -> None:
    """A database disclosure must not hand over usable sessions."""
    account = await register(client)
    raw_token = client.cookies.get("rs_session")
    assert raw_token

    stored = await db.scalar(select(Session).where(Session.user_id == account.user_id))
    assert stored is not None
    assert stored.token_hash != raw_token
    assert stored.token_hash == hash_token(raw_token)
    assert len(stored.token_hash) == 64


async def test_audit_rows_never_contain_credentials(client: AsyncClient, db: AsyncSession) -> None:
    """A failed login is audited; the password it used is not."""
    account = await register(client)
    await client.post(
        "/api/auth/login",
        json={"email": account.email, "password": "a-very-wrong-password"},
    )

    from app.models.audit_log import AuditLog

    rows = await db.scalars(select(AuditLog))
    for row in rows:
        serialised = json.dumps(row.details)
        assert "a-very-wrong-password" not in serialised
        assert DEFAULT_PASSWORD not in serialised


# --- CSRF ------------------------------------------------------------------


async def test_state_changing_request_without_a_csrf_token_is_refused(
    client: AsyncClient,
) -> None:
    account = await register(client)

    response = await client.post(
        "/api/auth/organization",
        json={"organization_id": str(account.organization_id)},
    )

    assert response.status_code == 403
    assert "CSRF" in response.json()["error"]["message"]


async def test_state_changing_request_with_a_wrong_csrf_token_is_refused(
    client: AsyncClient,
) -> None:
    account = await register(client)

    response = await client.post(
        "/api/auth/organization",
        json={"organization_id": str(account.organization_id)},
        headers={"X-CSRF-Token": "not-the-right-token"},
    )

    assert response.status_code == 403


async def test_csrf_is_validated_against_the_session_not_the_cookie(
    client: AsyncClient,
) -> None:
    """Overwriting the readable cookie must not let a caller choose the token.

    This is the difference between plain double-submit and validating against
    server state: a cookie planted from a sibling subdomain would defeat the
    former.
    """
    account = await register(client)
    client.cookies.set("rs_csrf", "attacker-chosen-value")

    response = await client.post(
        "/api/auth/organization",
        json={"organization_id": str(account.organization_id)},
        headers={"X-CSRF-Token": "attacker-chosen-value"},
    )

    assert response.status_code == 403

    # The genuine token still works.
    ok = await client.post(
        "/api/auth/organization",
        json={"organization_id": str(account.organization_id)},
        headers=account.auth_headers(),
    )
    assert ok.status_code == 200


async def test_safe_methods_need_no_csrf_token(client: AsyncClient) -> None:
    await register(client)

    assert (await client.get("/api/organizations/current")).status_code == 200


# --- Origin ----------------------------------------------------------------


async def test_state_changing_request_from_a_foreign_origin_is_refused(
    client: AsyncClient,
) -> None:
    """Covers login CSRF, which no session-bound token can defend."""
    response = await client.post(
        "/api/auth/login",
        json={"email": unique_email(), "password": DEFAULT_PASSWORD},
        headers={"Origin": "https://evil.example.com"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["message"] == "Request origin is not allowed."


async def test_state_changing_request_from_an_allowed_origin_proceeds(
    client: AsyncClient,
) -> None:
    account = await register(client)

    response = await client.post(
        "/api/auth/organization",
        json={"organization_id": str(account.organization_id)},
        headers={**account.auth_headers(), "Origin": "http://testserver"},
    )

    assert response.status_code == 200


async def test_a_request_without_an_origin_header_is_allowed(
    client: AsyncClient,
) -> None:
    """Non-browser clients send no Origin and cannot be CSRF'd."""
    response = await client.post(
        "/api/auth/register",
        json={
            "email": unique_email(),
            "password": DEFAULT_PASSWORD,
            "full_name": "No Origin",
            "organization_name": "No Origin Org",
        },
    )

    assert response.status_code == 201


async def test_safe_methods_are_not_origin_checked(client: AsyncClient) -> None:
    """A cross-origin GET is CORS's problem, not a CSRF risk."""
    response = await client.get("/api/auth/session", headers={"Origin": "https://evil.example.com"})

    assert response.status_code == 200


# --- Cookies ---------------------------------------------------------------


async def test_session_cookie_is_httponly_and_csrf_cookie_is_not(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/auth/register",
        json={
            "email": unique_email(),
            "password": DEFAULT_PASSWORD,
            "full_name": "Cookie Probe",
            "organization_name": "Cookie Probe Org",
        },
    )

    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith("rs_session="))
    csrf_cookie = next(c for c in cookies if c.startswith("rs_csrf="))

    # The credential must be unreadable from JavaScript.
    assert "HttpOnly" in session_cookie
    # The CSRF token must be readable, or the client cannot echo it.
    assert "HttpOnly" not in csrf_cookie

    for cookie in (session_cookie, csrf_cookie):
        assert "SameSite=lax" in cookie
        assert "Path=/" in cookie


# --- Configuration ---------------------------------------------------------


def test_production_rejects_insecure_cookies() -> None:
    with pytest.raises(ValueError, match="COOKIE_SECURE"):
        Settings(
            environment="production",
            secret_key="a-real-production-secret",
            cookie_secure=False,
            cors_origins=["https://app.example.com"],
        )


def test_samesite_none_requires_secure() -> None:
    with pytest.raises(ValueError, match="COOKIE_SAMESITE"):
        Settings(environment="development", cookie_samesite="none", cookie_secure=False)


def test_idle_timeout_cannot_exceed_the_session_lifetime() -> None:
    with pytest.raises(ValueError, match="SESSION_IDLE_TIMEOUT_SECONDS"):
        Settings(session_lifetime_seconds=100, session_idle_timeout_seconds=200)


def test_default_argon2_parameters_meet_the_owasp_recommendation() -> None:
    """The test suite lowers the cost; the shipped default must not be lowered.

    Guards against the reduced test parameters silently becoming the
    production values.
    """
    defaults = Settings.model_fields

    assert defaults["argon2_memory_cost_kib"].default == 19456
    assert defaults["argon2_time_cost"].default == 2
    assert defaults["password_min_length"].default == 12
