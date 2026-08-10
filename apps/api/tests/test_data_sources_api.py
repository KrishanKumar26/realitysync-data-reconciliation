"""Data source API: isolation, authorisation and credential handling.

Uses the real HTTP surface with real accounts created through registration, so
these exercise what a client actually reaches.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.data_source import DataSource, SourceCredential
from tests.factories import register
from tests.source_db import reader_config

pytestmark = pytest.mark.integration

SOURCE_PASSWORD = "source-password-never-returned"


def connection_payload(**overrides: Any) -> dict[str, Any]:
    config = reader_config()
    return {
        "host": config["host"],
        "port": config["port"],
        "database": config["database"],
        "username": config["username"],
        "password": SOURCE_PASSWORD,
        "ssl_mode": "require",
        **overrides,
    }


async def create_source(
    client: AsyncClient, account: Any, *, name: str = "Test source", **overrides: Any
) -> dict[str, Any]:
    response = await client.post(
        "/api/data-sources",
        json={
            "name": name,
            "kind": "postgresql",
            "connection": connection_payload(**overrides),
        },
        headers=account.auth_headers(),
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


# --- Credential handling ---------------------------------------------------


async def test_the_password_is_never_returned_by_any_endpoint(
    client: AsyncClient, db: AsyncSession
) -> None:
    account = await register(client)
    source = await create_source(client, account)
    source_id = source["id"]

    responses = [
        await client.get("/api/data-sources"),
        await client.get(f"/api/data-sources/{source_id}"),
        await client.get(f"/api/data-sources/{source_id}/health"),
        await client.get(f"/api/data-sources/{source_id}/streams"),
        await client.get(f"/api/data-sources/{source_id}/sync-runs"),
    ]

    for response in responses:
        assert SOURCE_PASSWORD not in response.text, f"{response.url} leaked the password"
        assert "password" not in response.text.lower().replace("password_set", "")


async def test_the_creation_response_confirms_storage_without_the_value(
    client: AsyncClient,
) -> None:
    account = await register(client)
    source = await create_source(client, account)

    assert source["connection"]["password_set"] is True
    assert "password" not in source["connection"]
    # The rest of the connection is safe to show, and useful.
    assert source["connection"]["host"] == reader_config()["host"]
    assert source["connection"]["ssl_mode"] == "require"


async def test_credentials_are_encrypted_at_rest(client: AsyncClient, db: AsyncSession) -> None:
    account = await register(client)
    source = await create_source(client, account)

    record = await db.scalar(
        select(SourceCredential).where(SourceCredential.data_source_id == uuid.UUID(source["id"]))
    )

    assert record is not None
    assert record.algorithm == "AES-256-GCM"
    assert len(record.nonce) == 12
    assert SOURCE_PASSWORD.encode() not in record.ciphertext


async def test_the_stored_config_contains_no_password(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The config column is returned to clients verbatim, so it must be clean."""
    account = await register(client)
    source = await create_source(client, account)

    stored = await db.scalar(
        select(DataSource).where(
            DataSource.organization_id == account.organization_id,
            DataSource.id == uuid.UUID(source["id"]),
        )
    )

    assert stored is not None
    assert "password" not in stored.config
    assert SOURCE_PASSWORD not in str(stored.config)


async def test_credentials_never_reach_the_logs(
    client: AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    account = await register(client)
    capsys.readouterr()

    source = await create_source(client, account)
    await client.post(
        f"/api/data-sources/{source['id']}/test-connection", headers=account.auth_headers()
    )

    logs = capsys.readouterr()
    assert SOURCE_PASSWORD not in logs.out + logs.err


# --- TLS policy ------------------------------------------------------------


@pytest.mark.parametrize("mode", ["disable", "allow", "prefer"])
async def test_insecure_ssl_modes_are_refused(client: AsyncClient, mode: str) -> None:
    account = await register(client)

    response = await client.post(
        "/api/data-sources",
        json={
            "name": f"Insecure {mode}",
            "kind": "postgresql",
            "connection": connection_payload(ssl_mode=mode),
        },
        headers=account.auth_headers(),
    )

    assert response.status_code == 422


async def test_malformed_connection_parameters_are_refused(client: AsyncClient) -> None:
    account = await register(client)

    response = await client.post(
        "/api/data-sources",
        json={
            "name": "Malformed",
            "kind": "postgresql",
            "connection": connection_payload(port=99999),
        },
        headers=account.auth_headers(),
    )

    assert response.status_code == 422


# --- Authentication and authorisation --------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/data-sources"),
        ("POST", "/api/data-sources"),
        ("GET", "/api/data-sources/{id}"),
        ("POST", "/api/data-sources/{id}/test-connection"),
        ("POST", "/api/data-sources/{id}/discover-schema"),
        ("GET", "/api/data-sources/{id}/streams"),
        ("POST", "/api/data-sources/{id}/streams"),
        ("POST", "/api/data-sources/{id}/sync"),
        ("GET", "/api/data-sources/{id}/sync-runs"),
        ("GET", "/api/data-sources/{id}/observations"),
        ("DELETE", "/api/data-sources/{id}"),
    ],
)
async def test_every_route_rejects_anonymous_callers(
    client: AsyncClient, anonymous_client: AsyncClient, method: str, path: str
) -> None:
    account = await register(client)
    source = await create_source(client, account)

    response = await anonymous_client.request(method, path.format(id=source["id"]), json={})

    assert response.status_code == 401


async def test_a_viewer_cannot_create_a_source(
    client: AsyncClient, anonymous_client: AsyncClient, db: AsyncSession
) -> None:
    """Creating a source stores a credential; that needs admin or above."""
    from app.models.membership import Membership

    owner = await register(client, organization_name="Role Org")
    viewer = await register(anonymous_client)

    db.add(
        Membership(
            user_id=viewer.user_id,
            organization_id=owner.organization_id,
            role="viewer",
        )
    )
    await db.commit()

    viewer_csrf = anonymous_client.cookies.get("rs_csrf") or ""
    await anonymous_client.post(
        "/api/auth/organization",
        json={"organization_id": str(owner.organization_id)},
        headers={"X-CSRF-Token": viewer_csrf},
    )

    response = await anonymous_client.post(
        "/api/data-sources",
        json={
            "name": "Viewer source",
            "kind": "postgresql",
            "connection": connection_payload(),
        },
        headers={"X-CSRF-Token": viewer_csrf},
    )

    assert response.status_code == 403


async def test_state_changing_requests_need_a_csrf_token(client: AsyncClient) -> None:
    await register(client)

    response = await client.post(
        "/api/data-sources",
        json={
            "name": "No CSRF",
            "kind": "postgresql",
            "connection": connection_payload(),
        },
    )

    assert response.status_code == 403


# --- Multi-tenancy ---------------------------------------------------------


async def test_a_source_is_invisible_to_another_organization(
    client: AsyncClient, anonymous_client: AsyncClient
) -> None:
    alice = await register(client, organization_name="Alice Data")
    await register(anonymous_client, organization_name="Bob Data")

    source = await create_source(client, alice, name="Alice production")

    listing = await anonymous_client.get("/api/data-sources")
    assert listing.json() == []

    # 404, not 403: whether an id exists in another tenant is not something a
    # caller should be able to probe.
    assert (await anonymous_client.get(f"/api/data-sources/{source['id']}")).status_code == 404


@pytest.mark.parametrize(
    ("method", "suffix"),
    [
        ("POST", "/test-connection"),
        ("POST", "/discover-schema"),
        ("POST", "/sync"),
        ("GET", "/streams"),
        ("GET", "/sync-runs"),
        ("GET", "/observations"),
        ("DELETE", ""),
    ],
)
async def test_no_operation_reaches_another_organizations_source(
    client: AsyncClient, anonymous_client: AsyncClient, method: str, suffix: str
) -> None:
    alice = await register(client)
    bob = await register(anonymous_client)
    source = await create_source(client, alice)

    response = await anonymous_client.request(
        method,
        f"/api/data-sources/{source['id']}{suffix}",
        json={} if method in {"POST", "PATCH"} else None,
        headers=bob.auth_headers(),
    )

    assert response.status_code == 404


async def test_stream_access_is_scoped_to_the_owning_source(
    client: AsyncClient, anonymous_client: AsyncClient
) -> None:
    alice = await register(client)
    bob = await register(anonymous_client)

    alice_source = await create_source(client, alice)
    bob_source = await create_source(anonymous_client, bob)

    stream = await client.post(
        f"/api/data-sources/{alice_source['id']}/streams",
        json={
            "schema_name": "public",
            "table_name": "anything",
            "primary_key_columns": ["id"],
            "event_time_semantics": "ingest_fallback",
        },
        headers=alice.auth_headers(),
    )
    assert stream.status_code == 201

    # Bob cannot reach Alice's stream, even through his own source's path.
    response = await anonymous_client.patch(
        f"/api/data-sources/{bob_source['id']}/streams/{stream.json()['id']}",
        json={"enabled": False},
        headers=bob.auth_headers(),
    )

    assert response.status_code == 404


async def test_deleting_a_source_is_scoped(
    client: AsyncClient, anonymous_client: AsyncClient, db: AsyncSession
) -> None:
    alice = await register(client)
    bob = await register(anonymous_client)
    source = await create_source(client, alice)

    denied = await anonymous_client.delete(
        f"/api/data-sources/{source['id']}", headers=bob.auth_headers()
    )
    assert denied.status_code == 404

    # Still there.
    assert (await client.get(f"/api/data-sources/{source['id']}")).status_code == 200

    allowed = await client.delete(f"/api/data-sources/{source['id']}", headers=alice.auth_headers())
    assert allowed.status_code == 204


async def test_duplicate_source_names_are_refused_within_an_organization(
    client: AsyncClient, anonymous_client: AsyncClient
) -> None:
    alice = await register(client)
    bob = await register(anonymous_client)

    await create_source(client, alice, name="Production")

    duplicate = await client.post(
        "/api/data-sources",
        json={
            "name": "Production",
            "kind": "postgresql",
            "connection": connection_payload(),
        },
        headers=alice.auth_headers(),
    )
    assert duplicate.status_code == 409

    # The constraint is per organization, so another tenant may use the name.
    assert (
        await anonymous_client.post(
            "/api/data-sources",
            json={
                "name": "Production",
                "kind": "postgresql",
                "connection": connection_payload(),
            },
            headers=bob.auth_headers(),
        )
    ).status_code == 201


# --- Stream configuration --------------------------------------------------


async def test_a_stream_needs_a_time_column_unless_it_declares_otherwise(
    client: AsyncClient,
) -> None:
    """Mirrors the database CHECK, so the client gets a 422 not a 500."""
    account = await register(client)
    source = await create_source(client, account)

    response = await client.post(
        f"/api/data-sources/{source['id']}/streams",
        json={
            "schema_name": "public",
            "table_name": "orders",
            "primary_key_columns": ["id"],
            "event_time_semantics": "observed",
            # No event_time_column.
        },
        headers=account.auth_headers(),
    )

    assert response.status_code == 422
    assert "event-time column" in response.json()["error"]["message"]


async def test_syncing_without_streams_is_refused_clearly(client: AsyncClient) -> None:
    account = await register(client)
    source = await create_source(client, account)

    response = await client.post(
        f"/api/data-sources/{source['id']}/sync",
        json={},
        headers=account.auth_headers(),
    )

    assert response.status_code == 409
    assert "no enabled streams" in response.json()["error"]["message"]
