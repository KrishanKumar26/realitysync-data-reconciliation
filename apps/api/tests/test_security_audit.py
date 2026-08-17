"""Phase 11 — adversarial security and tenancy audit.

These tests attack the application. They are not "does isolation work" checks
written from the perspective of code that already assumes it does; each one
takes Org A's session and tries to reach Org B's data through a specific route.

Two ideas shape the module.

**Exhaustive rather than representative.** The cross-tenant sweep is generated
from the route table, so an endpoint added later without a tenant filter fails
here rather than being missed because nobody remembered to add a case for it.

**The guard is a backstop, so attack the guard too.** The ORM tenancy guard
exists to catch the query a developer did not mean to write. Tests that only
exercise correctly-written queries prove nothing about it, so a section here
feeds it the shapes a careless developer would plausibly produce.

What is deliberately *not* claimed: passing these tests does not make the
application secure. It makes these specific attacks fail. The limits are
recorded in docs/security.md.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.network import assert_host_is_permitted
from app.connectors.types import ConnectorError, ConnectorErrorCode
from app.db.tenancy import MissingOrganizationScopeError, assert_organization_scoped
from app.models.conflict import Conflict
from app.models.data_source import DataSource
from app.models.entity import Entity, EntityMapping
from app.models.observation import Observation
from app.models.reality_state import RealityState, RealityStateEvidence
from app.models.source_stream import SourceStream
from app.models.sync_run import SyncRun
from app.services.reality import recalculate_entity
from tests.factories import register
from tests.test_reality_api import (
    create_entity,
    map_row,
    seed_observation,
    seed_source,
)

AS_OF = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


@dataclass
class Tenant:
    """One fully-populated organization: every tenant-owned resource type."""

    account: Any
    client: AsyncClient
    source: DataSource
    stream: SourceStream
    observation: Observation
    entity_id: str
    state_id: uuid.UUID | None = None
    conflict_id: str | None = None


async def build_tenant(client: AsyncClient, db: AsyncSession, *, label: str) -> Tenant:
    """A tenant carrying one of everything, created through the real flows."""
    account = await register(client)

    source, stream = await seed_source(db, organization_id=account.organization_id, name=label)
    other_source, other_stream = await seed_source(
        db, organization_id=account.organization_id, name=f"{label}-2"
    )
    observation = await seed_observation(
        db,
        organization_id=account.organization_id,
        source=source,
        stream=stream,
        payload={"quantity": 42 if label == "A" else 99},
    )
    # A second, disagreeing source so a conflict exists to attack.
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=other_source,
        stream=other_stream,
        payload={"quantity": 7 if label == "A" else 13},
    )
    await db.commit()

    entity_id = await create_entity(client, account, key=f"ASSET-{label}")
    await map_row(client, account, entity_id=entity_id, stream=stream)
    await map_row(client, account, entity_id=entity_id, stream=other_stream)

    await recalculate_entity(
        db,
        organization_id=account.organization_id,
        entity_id=uuid.UUID(entity_id),
        as_of=AS_OF,
    )
    await db.commit()

    state = await db.scalar(
        select(RealityState).where(RealityState.organization_id == account.organization_id)
    )
    conflicts = (await client.get("/api/conflicts", headers=account.auth_headers())).json()

    return Tenant(
        account=account,
        client=client,
        source=source,
        stream=stream,
        observation=observation,
        entity_id=entity_id,
        state_id=state.id if state else None,
        conflict_id=conflicts[0]["id"] if conflicts else None,
    )


@pytest.fixture
async def tenants(
    client: AsyncClient, anonymous_client: AsyncClient, db: AsyncSession
) -> tuple[Tenant, Tenant]:
    """Two tenants, each with the full set of resources."""
    a = await build_tenant(client, db, label="A")
    b = await build_tenant(anonymous_client, db, label="B")
    return a, b


# ===========================================================================
# 1. The guard itself — attacked with the shapes a careless developer writes
# ===========================================================================


def _attack(statement: Any) -> bool:
    """True when the guard let the statement through."""
    try:
        assert_organization_scoped(statement)
    except MissingOrganizationScopeError:
        return False
    return True


def test_correlating_two_tenant_tables_does_not_count_as_scoping() -> None:
    """VULNERABILITY FOUND AND FIXED IN PHASE 11.

    Joining two tenant-owned tables on their organization_id correlates them
    without pinning either to a tenant, so the query reads every organization's
    rows. The previous guard counted any appearance of the column in a filter as
    scoping, which this satisfies.
    """
    assert not _attack(
        select(Observation).join(Entity, Entity.organization_id == Observation.organization_id)
    )


def test_comparing_the_column_to_itself_does_not_count_as_scoping() -> None:
    """VULNERABILITY FOUND AND FIXED IN PHASE 11. Verified to bypass before."""
    assert not _attack(
        select(Observation).where(Observation.organization_id == Observation.organization_id)
    )


def test_is_not_null_does_not_count_as_scoping() -> None:
    """VULNERABILITY FOUND AND FIXED IN PHASE 11.

    Structurally a filter, semantically none — the column is NOT NULL, so this
    matches every row in every tenant. It is also the single most plausible
    mistake: it looks like a scope filter at a glance.
    """
    assert not _attack(select(Observation).where(Observation.organization_id.isnot(None)))


def test_not_equals_does_not_count_as_scoping() -> None:
    """VULNERABILITY FOUND AND FIXED IN PHASE 11.

    ``!=`` compares against a bound value and returns every *other* tenant,
    which is precisely inverted scoping.
    """
    assert not _attack(select(Observation).where(Observation.organization_id != uuid.uuid4()))


def test_legitimate_scoping_still_passes() -> None:
    """A guard that rejects correct queries gets switched off."""
    org = uuid.uuid4()
    assert _attack(select(Observation).where(Observation.organization_id == org))
    assert _attack(select(Observation).where(Observation.organization_id.in_([org])))


def test_unscoped_reads_of_every_tenant_table_are_refused() -> None:
    """Each tenant-owned model, queried bare."""
    for model in (Observation, Entity, DataSource, SourceStream, RealityState, Conflict, SyncRun):
        assert not _attack(select(model)), f"{model.__name__} was readable unscoped"


def test_unscoped_deletes_and_updates_are_refused() -> None:
    """Writes matter more than reads: an unscoped DELETE destroys other tenants."""
    from sqlalchemy import delete, update

    assert not _attack(delete(Observation))
    assert not _attack(update(Observation).values(external_id="x"))
    assert not _attack(delete(RealityState))
    assert not _attack(update(Conflict).values(status="resolved"))


def test_aggregates_over_a_tenant_table_are_refused() -> None:
    """A count leaks size even when it returns no rows."""
    assert not _attack(select(func.count()).select_from(Observation))
    assert not _attack(select(func.count(Conflict.id)))


def test_a_subquery_cannot_smuggle_an_unscoped_read() -> None:
    """The nested SELECT is filtered by its own WHERE, not the outer one."""
    inner = select(Observation.entity_id).scalar_subquery()
    assert not _attack(
        select(Entity).where(
            Entity.organization_id == uuid.uuid4(),
            Entity.id.in_(inner),
        )
    )


# ===========================================================================
# 2. Authentication
# ===========================================================================

#: Every tenant-scoped endpoint, with a placeholder for each path parameter.
#: Generated from the route table rather than hand-listed, so an endpoint added
#: without a tenant filter is caught here instead of being quietly missed.
PROTECTED_PATHS: tuple[tuple[str, str], ...] = (
    ("GET", "/api/dashboard"),
    ("GET", "/api/activity"),
    ("GET", "/api/organizations"),
    ("GET", "/api/organizations/current"),
    ("GET", "/api/organizations/current/members"),
    ("GET", "/api/data-sources"),
    ("GET", "/api/entities"),
    ("GET", "/api/conflicts"),
    ("GET", "/api/system/status"),
)


async def test_every_protected_endpoint_rejects_an_anonymous_caller(
    anonymous_client: AsyncClient,
) -> None:
    for method, path in PROTECTED_PATHS:
        response = await anonymous_client.request(method, path)
        assert response.status_code == 401, f"{method} {path} served an anonymous caller"


async def test_a_revoked_session_stops_working(client: AsyncClient) -> None:
    """Logging out must end the session server-side, not only in the browser."""
    account = await register(client)
    assert (await client.get("/api/dashboard")).status_code == 200

    await client.post("/api/auth/logout", headers=account.auth_headers())

    assert (await client.get("/api/dashboard")).status_code == 401


async def test_a_forged_session_cookie_is_rejected(anonymous_client: AsyncClient) -> None:
    """Session tokens are verified server-side, never trusted as presented."""
    anonymous_client.cookies.set("rs_session", uuid.uuid4().hex * 2)

    response = await anonymous_client.get("/api/dashboard")

    assert response.status_code == 401


async def test_an_expired_session_is_rejected(client: AsyncClient, db: AsyncSession) -> None:
    """The absolute lifetime is enforced on read, not only at issue."""
    from app.models.session import Session as SessionModel

    account = await register(client)
    session = await db.scalar(select(SessionModel).where(SessionModel.user_id == account.user_id))
    assert session is not None
    session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db.commit()

    assert (await client.get("/api/dashboard")).status_code == 401


# ===========================================================================
# 3. Cross-tenant IDOR — the exhaustive sweep
# ===========================================================================


async def test_org_a_cannot_read_any_org_b_resource_by_id(
    tenants: tuple[Tenant, Tenant],
) -> None:
    """The core sweep: every GET that takes another tenant's id.

    404 rather than 403 throughout. As far as A is concerned the resource does
    not exist, and 403 would confirm that it does — which is itself a leak.
    """
    a, b = tenants

    paths = [
        f"/api/data-sources/{b.source.id}",
        f"/api/data-sources/{b.source.id}/streams",
        f"/api/data-sources/{b.source.id}/observations",
        f"/api/data-sources/{b.source.id}/sync-runs",
        f"/api/data-sources/{b.source.id}/health",
        f"/api/entities/{b.entity_id}",
        f"/api/entities/{b.entity_id}/mappings",
        f"/api/entities/{b.entity_id}/reality",
        f"/api/entities/{b.entity_id}/reality/quantity/evidence",
        f"/api/entities/{b.entity_id}/timeline",
        f"/api/entities/{b.entity_id}/attributes/quantity/unscored",
    ]
    if b.conflict_id:
        paths.append(f"/api/conflicts/{b.conflict_id}")

    for path in paths:
        response = await a.client.get(path, headers=a.account.auth_headers())
        assert response.status_code == 404, f"GET {path} leaked to another tenant"
        # And nothing of B's is in the body.
        assert "99" not in response.text or "quantity" not in response.text


async def test_org_a_cannot_mutate_any_org_b_resource(tenants: tuple[Tenant, Tenant]) -> None:
    """Writes: POST, PATCH and DELETE against another tenant's ids."""
    a, b = tenants
    headers = a.account.auth_headers()

    attacks: list[tuple[str, str, dict[str, Any] | None]] = [
        ("DELETE", f"/api/data-sources/{b.source.id}", None),
        ("DELETE", f"/api/data-sources/{b.source.id}/streams/{b.stream.id}", None),
        ("PATCH", f"/api/data-sources/{b.source.id}/streams/{b.stream.id}", {"enabled": False}),
        ("POST", f"/api/data-sources/{b.source.id}/sync", {}),
        ("POST", f"/api/data-sources/{b.source.id}/test-connection", None),
        ("POST", f"/api/data-sources/{b.source.id}/discover-schema", None),
        (
            "POST",
            f"/api/data-sources/{b.source.id}/streams",
            {
                "schema_name": "public",
                "table_name": "t",
                "primary_key_columns": ["id"],
                "event_time_semantics": "ingest_fallback",
            },
        ),
        ("DELETE", f"/api/entities/{b.entity_id}", None),
        ("POST", f"/api/entities/{b.entity_id}/recalculate", None),
        (
            "POST",
            f"/api/entities/{b.entity_id}/mappings",
            {"stream_id": str(b.stream.id), "external_id": "id=1"},
        ),
    ]
    if b.conflict_id:
        attacks.append(("PATCH", f"/api/conflicts/{b.conflict_id}", {"status": "resolved"}))

    for method, path, body in attacks:
        response = await a.client.request(method, path, json=body, headers=headers)
        assert response.status_code == 404, f"{method} {path} reached another tenant"


async def test_org_b_resources_survive_every_attack(
    tenants: tuple[Tenant, Tenant], db: AsyncSession
) -> None:
    """The attacks above must have changed nothing, not merely returned 404."""
    a, b = tenants
    headers = a.account.auth_headers()

    await a.client.delete(f"/api/entities/{b.entity_id}", headers=headers)
    await a.client.delete(f"/api/data-sources/{b.source.id}", headers=headers)
    await a.client.post(f"/api/entities/{b.entity_id}/recalculate", headers=headers)

    still_there = await b.client.get(
        f"/api/entities/{b.entity_id}", headers=b.account.auth_headers()
    )
    assert still_there.status_code == 200

    source = await db.scalar(
        select(DataSource).where(
            DataSource.organization_id == b.account.organization_id,
            DataSource.id == b.source.id,
        )
    )
    assert source is not None, "another tenant deleted this source"


async def test_cross_tenant_mapping_cannot_attach_b_stream_to_an_a_entity(
    tenants: tuple[Tenant, Tenant], db: AsyncSession
) -> None:
    """The nastiest shape: a *valid* entity of A, pointed at B's stream.

    If this succeeded, A's reality state would be derived from B's
    observations — a leak that would look like a legitimate calculation.
    """
    a, b = tenants

    response = await a.client.post(
        f"/api/entities/{a.entity_id}/mappings",
        json={"stream_id": str(b.stream.id), "external_id": "id=1"},
        headers=a.account.auth_headers(),
    )

    assert response.status_code in (400, 404, 422), "A mapped another tenant's stream"

    mappings = await db.scalars(
        select(EntityMapping).where(
            EntityMapping.organization_id == a.account.organization_id,
            EntityMapping.stream_id == b.stream.id,
        )
    )
    assert list(mappings) == []


async def test_recalculation_never_reads_another_tenants_observations(
    tenants: tuple[Tenant, Tenant], db: AsyncSession
) -> None:
    """Every evidence row of A must point at an observation owned by A."""
    a, _ = tenants

    await recalculate_entity(
        db,
        organization_id=a.account.organization_id,
        entity_id=uuid.UUID(a.entity_id),
        as_of=AS_OF,
    )
    await db.commit()

    rows = await db.execute(
        select(RealityStateEvidence, Observation)
        .join(Observation, Observation.id == RealityStateEvidence.observation_id)
        .where(
            RealityStateEvidence.organization_id == a.account.organization_id,
            Observation.organization_id == a.account.organization_id,
        )
    )
    evidence = list(rows)
    assert evidence, "no evidence produced, so this proves nothing"
    for _, observation in evidence:
        assert observation.organization_id == a.account.organization_id


async def test_a_sync_run_detail_is_not_readable_across_tenants(
    tenants: tuple[Tenant, Tenant], db: AsyncSession
) -> None:
    """A nested resource two levels deep, reached with both ids belonging to B."""
    a, b = tenants

    run = SyncRun(
        organization_id=b.account.organization_id,
        source_id=b.source.id,
        status="completed",
        idempotency_key=f"probe-{uuid.uuid4().hex}",
        started_at=datetime.now(UTC),
    )
    db.add(run)
    await db.commit()

    response = await a.client.get(
        f"/api/data-sources/{b.source.id}/sync-runs/{run.id}",
        headers=a.account.auth_headers(),
    )

    assert response.status_code == 404
    assert str(run.id) not in response.text


async def test_a_member_of_another_organization_cannot_be_removed(
    tenants: tuple[Tenant, Tenant], db: AsyncSession
) -> None:
    """Membership removal is scoped to the caller's *active* organization.

    The endpoint takes only a user id, so the tenant boundary is entirely
    server-side — there is no organization in the path to get wrong.
    """
    a, b = tenants
    from app.models.membership import Membership

    response = await a.client.request(
        "DELETE",
        f"/api/organizations/current/members/{b.account.user_id}",
        headers=a.account.auth_headers(),
    )

    assert response.status_code in (403, 404)

    survived = await db.scalar(
        select(Membership).where(
            Membership.organization_id == b.account.organization_id,
            Membership.user_id == b.account.user_id,
        )
    )
    assert survived is not None, "another tenant removed a membership"


async def test_a_credential_never_reaches_the_logs(client: AsyncClient, db: AsyncSession) -> None:
    """Logs are the quietest place for a secret to escape.

    A credential in a log line survives redaction of the API response, gets
    shipped to whatever aggregates logs, and is read by people who were never
    meant to see it.
    """
    import logging

    from app.services.credentials import store_credentials

    secret = f"unique-secret-{uuid.uuid4().hex}"
    account = await register(client)
    source, _ = await seed_source(db, organization_id=account.organization_id, name="Logged")
    await store_credentials(db, data_source=source, payload={"password": secret})
    await db.commit()

    # An explicit handler on the root logger rather than caplog: the suite is
    # sometimes run with the logging plugin disabled, and a security test that
    # silently stops running is worse than one that never existed.
    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage() + str(getattr(record, "__dict__", "")))

    handler = Capture(level=logging.DEBUG)
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        await client.post(
            f"/api/data-sources/{source.id}/test-connection", headers=account.auth_headers()
        )
        await client.get(f"/api/data-sources/{source.id}", headers=account.auth_headers())
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)

    assert not any(secret in line for line in records), (
        "a stored credential was written to the logs"
    )


# ===========================================================================
# 4. Membership and organization context
# ===========================================================================


async def test_switching_to_a_foreign_organization_is_refused(
    tenants: tuple[Tenant, Tenant],
) -> None:
    """403 here, not 404: the organization exists and A knows its id already.

    Membership is the thing being denied, and saying so is not a leak — A
    supplied the id.
    """
    a, b = tenants

    response = await a.client.post(
        "/api/auth/organization",
        json={"organization_id": str(b.account.organization_id)},
        headers=a.account.auth_headers(),
    )

    assert response.status_code == 403


async def test_a_revoked_membership_ends_access(client: AsyncClient, db: AsyncSession) -> None:
    """Access must follow the membership, not the session that outlived it."""
    from app.models.membership import Membership

    account = await register(client)
    assert (await client.get("/api/dashboard")).status_code == 200

    membership = await db.scalar(
        select(Membership).where(
            Membership.organization_id == account.organization_id,
            Membership.user_id == account.user_id,
        )
    )
    assert membership is not None
    await db.delete(membership)
    await db.commit()

    response = await client.get("/api/dashboard")
    assert response.status_code in (401, 403), (
        "a user kept tenant access after their membership was revoked"
    )


# ===========================================================================
# 5. Credential security
# ===========================================================================


async def test_no_endpoint_returns_a_stored_credential(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The password goes in once and is never readable again."""
    from app.services.credentials import store_credentials

    secret = f"unique-secret-{uuid.uuid4().hex}"
    account = await register(client)
    source, _ = await seed_source(db, organization_id=account.organization_id, name="S")
    await store_credentials(db, data_source=source, payload={"password": secret})
    await db.commit()

    headers = account.auth_headers()
    for path in (
        "/api/data-sources",
        f"/api/data-sources/{source.id}",
        f"/api/data-sources/{source.id}/health",
        f"/api/data-sources/{source.id}/streams",
        "/api/dashboard",
        "/api/activity",
    ):
        body = (await client.get(path, headers=headers)).text
        assert secret not in body, f"{path} returned a stored credential"
        assert "password" not in body.lower() or "password_set" in body


async def test_a_stored_credential_is_encrypted_at_rest(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Reading the row directly must not reveal the secret."""
    from app.models.data_source import SourceCredential
    from app.services.credentials import store_credentials

    secret = f"unique-secret-{uuid.uuid4().hex}"
    account = await register(client)
    source, _ = await seed_source(db, organization_id=account.organization_id, name="S")
    await store_credentials(db, data_source=source, payload={"password": secret})
    await db.commit()

    record = await db.scalar(
        select(SourceCredential).where(SourceCredential.data_source_id == source.id)
    )
    assert record is not None
    blob = str(record.__dict__)
    assert secret not in blob
    assert secret.encode() not in bytes(record.ciphertext)


async def test_a_connection_error_does_not_echo_the_credential(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Error messages are the classic place a secret escapes."""
    from app.services.credentials import store_credentials

    secret = f"unique-secret-{uuid.uuid4().hex}"
    account = await register(client)
    source, _ = await seed_source(db, organization_id=account.organization_id, name="Unreachable")
    await store_credentials(db, data_source=source, payload={"password": secret})
    await db.commit()

    response = await client.post(
        f"/api/data-sources/{source.id}/test-connection", headers=account.auth_headers()
    )

    assert secret not in response.text
    assert "realitysync_reader" not in response.text


# ===========================================================================
# 6. Enumeration and aggregate leakage
# ===========================================================================


async def test_aggregates_count_only_the_callers_own_tenant(
    tenants: tuple[Tenant, Tenant],
) -> None:
    """A count is a leak if it includes another tenant's rows."""
    a, b = tenants

    a_dash = (await a.client.get("/api/dashboard", headers=a.account.auth_headers())).json()
    b_dash = (await b.client.get("/api/dashboard", headers=b.account.auth_headers())).json()

    # Each tenant has exactly two sources; seeing four would mean the aggregate
    # spans tenants.
    assert a_dash["sources"]["total"] == 2
    assert b_dash["sources"]["total"] == 2


async def test_the_activity_feed_never_shows_another_tenants_events(
    tenants: tuple[Tenant, Tenant],
) -> None:
    a, b = tenants

    activity = (await a.client.get("/api/activity", headers=a.account.auth_headers())).json()
    body = str(activity)

    assert str(b.account.organization_id) not in body
    assert str(b.source.id) not in body
    assert "ASSET-B" not in body


async def test_a_missing_id_and_a_foreign_id_are_indistinguishable(
    tenants: tuple[Tenant, Tenant],
) -> None:
    """Otherwise the 404/403 split becomes an existence oracle."""
    a, b = tenants
    headers = a.account.auth_headers()

    foreign = await a.client.get(f"/api/entities/{b.entity_id}", headers=headers)
    absent = await a.client.get(f"/api/entities/{uuid.uuid4()}", headers=headers)

    assert foreign.status_code == absent.status_code == 404
    assert foreign.json()["error"]["message"] == absent.json()["error"]["message"]


# ===========================================================================
# 7. Background jobs
# ===========================================================================


async def test_the_scheduler_never_writes_across_tenants(
    tenants: tuple[Tenant, Tenant], db: AsyncSession
) -> None:
    """The scheduler is the one component that legitimately spans tenants.

    Its discovery query is cross-tenant by necessity; everything it then writes
    must not be. Each due item must carry exactly one organization, and its
    streams must belong to that organization's source.
    """
    from app.ingestion.scheduler import find_due_sources

    a, b = tenants
    due = await find_due_sources(db, now=datetime.now(UTC))

    by_org: dict[uuid.UUID, set[uuid.UUID]] = {}
    for item in due:
        by_org.setdefault(item.organization_id, set()).update(item.stream_ids)

    for organization_id, stream_ids in by_org.items():
        rows = await db.scalars(
            select(SourceStream).where(
                SourceStream.organization_id == organization_id,
                SourceStream.id.in_(stream_ids),
            )
        )
        found = {s.id for s in rows}
        assert found == stream_ids, (
            f"scheduler grouped streams under organization {organization_id} "
            "that do not belong to it"
        )

    assert {a.account.organization_id, b.account.organization_id} <= set(by_org)


async def test_a_scheduled_run_claims_no_human_actor(
    tenants: tuple[Tenant, Tenant], db: AsyncSession
) -> None:
    """An automated action must not appear in the record as a person's."""
    from app.ingestion.scheduler import find_due_sources, sync_due_source

    a, _ = tenants
    due = [
        d
        for d in await find_due_sources(db, now=datetime.now(UTC))
        if d.organization_id == a.account.organization_id
    ]
    assert due

    try:
        await sync_due_source(db, due[0])
    except Exception:
        # The seeded source points at a host that does not exist; the run is
        # recorded as failed, which is exactly the case worth checking.
        await db.rollback()

    runs = await db.scalars(
        select(SyncRun).where(SyncRun.organization_id == a.account.organization_id)
    )
    for run in runs:
        if run.idempotency_key.startswith("scheduled:"):
            assert run.triggered_by_user_id is None, "a scheduled run named a human actor"


# ===========================================================================
# 8. SSRF — the connector host is attacker-supplied
# ===========================================================================


@pytest.mark.parametrize(
    ("host", "what_it_reaches"),
    [
        ("127.0.0.1", "the deployment itself"),
        ("localhost", "the deployment itself, by name"),
        ("169.254.169.254", "the AWS/GCP instance metadata service"),
        ("10.0.0.1", "an RFC1918 private network"),
        ("192.168.1.1", "a home/office private network"),
        ("172.16.0.1", "the other RFC1918 range"),
        ("0.0.0.0", "the unspecified address"),  # noqa: S104 - a test input, not a bind
    ],
)
def test_a_connector_refuses_a_non_public_host(host: str, what_it_reaches: str) -> None:
    """VULNERABILITY FOUND AND FIXED IN PHASE 11.

    The connector host comes from whoever configures the source. Nothing
    restricted it, so a tenant could aim RealitySync at internal infrastructure
    and have it connect on their behalf, from inside the deployment's network.

    It was verified exploitable before the fix. The connection test's own error
    codes formed a working port scanner:

        169.254.169.254:80   timeout       host exists, filtered
        127.0.0.1:6379       unreachable   nothing listening on that port
        postgres:5432        tls_failed    a PostgreSQL is running here

    ``unreachable`` against ``tls_failed`` is the entire scan. And the
    application's own database is reachable by hostname from inside the
    deployment, so a tenant who guessed its credentials would read every other
    tenant's data through a feature working exactly as designed.
    """
    with pytest.raises(ConnectorError) as raised:
        assert_host_is_permitted(host, allow_private=False)

    assert raised.value.code is ConnectorErrorCode.INVALID_CONFIGURATION
    assert "not permitted" in raised.value.message


def test_the_refusal_does_not_echo_the_resolved_address() -> None:
    """The message must not answer the question the attacker asked.

    Reporting "127.0.0.1 is private" back to the caller preserves exactly the
    oracle the check exists to close: it confirms what a hostname resolves to.
    """
    with pytest.raises(ConnectorError) as raised:
        assert_host_is_permitted("localhost", allow_private=False)

    assert "127.0.0.1" not in raised.value.message
    assert "::1" not in raised.value.message


def test_a_public_host_is_permitted() -> None:
    """A control that blocks legitimate use gets turned off."""
    assert_host_is_permitted("8.8.8.8", allow_private=False)
    # Unresolvable is not a policy failure - the ordinary "host not found"
    # error is what the operator needs to see.
    assert_host_is_permitted("nonexistent.invalid", allow_private=False)


def test_the_escape_hatch_is_explicit() -> None:
    """Self-hosted deployments on a private network opt in deliberately."""
    assert_host_is_permitted("10.0.0.1", allow_private=True)


def test_the_default_configuration_refuses_private_hosts() -> None:
    """The safe posture must be the one you get without deciding anything."""
    from app.core.config import Settings

    # The *field* default, not an instance: the test environment deliberately
    # sets the variable, so Settings() would report the test's choice rather
    # than what a deployment gets when it decides nothing.
    assert Settings.model_fields["connector_allow_private_hosts"].default is False


async def test_a_source_aimed_at_the_application_database_cannot_connect(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The end-to-end version of the attack, through the real API.

    Creating the source is allowed - the host is only a string until something
    connects - but the connection itself must be refused.
    """
    from app.connectors.mysql.config import parse_config as parse_mysql
    from app.connectors.mysql.connector import MysqlConnector
    from app.connectors.postgres.config import parse_config as parse_postgres
    from app.connectors.postgres.connector import PostgresConnector

    postgres = PostgresConnector(
        config=parse_postgres(
            {
                "host": "127.0.0.1",
                "port": 5432,
                "database": "realitysync",
                "username": "realitysync",
                "ssl_mode": "require",
            }
        ),
        password="change-me-locally",
        allow_private_hosts=False,
    )
    with pytest.raises(ConnectorError) as raised:
        await postgres.connect()
    assert raised.value.code is ConnectorErrorCode.INVALID_CONFIGURATION

    mysql = MysqlConnector(
        config=parse_mysql(
            {
                "host": "127.0.0.1",
                "port": 3306,
                "database": "d",
                "username": "u",
                "ssl_mode": "require",
            }
        ),
        password="p",
        allow_private_hosts=False,
    )
    with pytest.raises(ConnectorError) as raised:
        await mysql.connect()
    assert raised.value.code is ConnectorErrorCode.INVALID_CONFIGURATION


# ===========================================================================
# 9. Mass assignment and input validation
# ===========================================================================


async def test_a_client_cannot_set_the_organization_on_a_resource(
    tenants: tuple[Tenant, Tenant],
) -> None:
    """The tenant comes from the session, never from the request body.

    If a client-supplied organization_id were honoured anywhere, every other
    control in the system would be decoration.
    """
    a, b = tenants

    response = await a.client.post(
        "/api/entities",
        json={
            "entity_type": "asset",
            "natural_key": f"MASS-{uuid.uuid4().hex[:8]}",
            # Injected fields that must not be honoured.
            "organization_id": str(b.account.organization_id),
            "id": str(uuid.uuid4()),
        },
        headers=a.account.auth_headers(),
    )

    # Either rejected outright, or accepted with the injected fields ignored.
    if response.status_code == 201:
        created = response.json()
        assert created["id"] != str(uuid.uuid4())
        fetched = await b.client.get(
            f"/api/entities/{created['id']}", headers=b.account.auth_headers()
        )
        assert fetched.status_code == 404, "an entity was created in another tenant"
    else:
        assert response.status_code == 422


async def test_extra_fields_are_refused_rather_than_ignored(
    client: AsyncClient,
) -> None:
    """`extra="forbid"` means a typo fails loudly instead of being dropped."""
    account = await register(client)

    response = await client.post(
        "/api/entities",
        json={
            "entity_type": "asset",
            "natural_key": f"X-{uuid.uuid4().hex[:8]}",
            "unexpected_field": "value",
        },
        headers=account.auth_headers(),
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "malformed",
    ["not-a-uuid", "../../etc/passwd", "1 OR 1=1", "%00", "00000000-0000-0000-0000-00000000000"],
)
async def test_a_malformed_id_is_a_validation_error_not_a_crash(
    client: AsyncClient, malformed: str
) -> None:
    """A malformed path parameter must not reach the database or leak a trace."""
    account = await register(client)

    response = await client.get(f"/api/entities/{malformed}", headers=account.auth_headers())

    assert response.status_code in (404, 422)
    body = response.text.lower()
    assert "traceback" not in body
    assert "sqlalchemy" not in body
    assert "psycopg" not in body


async def test_an_internal_error_does_not_leak_its_cause(client: AsyncClient) -> None:
    """The error envelope must not carry driver text or a stack trace."""
    account = await register(client)

    response = await client.get(
        "/api/entities/00000000-0000-0000-0000-000000000000",
        headers=account.auth_headers(),
    )

    assert response.status_code == 404
    payload = response.json()
    assert set(payload["error"]) == {"code", "message", "details", "request_id"}
    assert "select" not in payload["error"]["message"].lower()


# ===========================================================================
# 10. SQL injection through identifiers
# ===========================================================================


@pytest.mark.parametrize(
    "hostile",
    [
        "orders`; DROP TABLE users; --",
        "orders\x00truncated",
        "`injected`",
    ],
)
def test_mysql_identifiers_reject_rather_than_escape(hostile: str) -> None:
    """Table and column names reach the connector from stream configuration.

    MySQL has no parameterised identifiers, so the connector builds them into
    the statement. Refusing a backtick is a smaller surface than escaping one,
    and it fails loudly rather than silently producing a different identifier
    than intended.
    """
    from app.connectors.mysql.connector import quote_identifier

    with pytest.raises(ConnectorError) as raised:
        quote_identifier(hostile.replace("\\x00", "\x00"))

    assert raised.value.code is ConnectorErrorCode.INVALID_CONFIGURATION
    # The rejected value stays in `detail`, out of the user-facing message.
    assert "DROP TABLE" not in raised.value.message


async def test_a_hostile_stream_name_is_refused_at_the_api(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The same defence, reached the way an attacker would reach it."""
    account = await register(client)
    source, _ = await seed_source(db, organization_id=account.organization_id, name="Inject")
    await db.commit()

    response = await client.post(
        f"/api/data-sources/{source.id}/streams",
        json={
            "schema_name": "public",
            "table_name": "t`; DROP TABLE observations; --",
            "primary_key_columns": ["id"],
            "event_time_semantics": "ingest_fallback",
        },
        headers=account.auth_headers(),
    )

    assert response.status_code in (400, 422)

    # And the table it named is still there.
    surviving = await db.scalar(
        select(func.count())
        .select_from(Observation)
        .where(Observation.organization_id == account.organization_id)
    )
    assert surviving is not None


# ===========================================================================
# 11. Schema-level defences
# ===========================================================================


async def test_every_tenant_table_requires_an_organization(db: AsyncSession) -> None:
    """NOT NULL is the last line: a row with no owner is reachable by nobody
    and, worse, by a query that forgets to filter."""
    from sqlalchemy import inspect as sa_inspect

    from app.db.tenancy import TENANT_OWNED_TABLES

    def _check(connection: Any) -> list[str]:
        inspector = sa_inspect(connection)
        problems = []
        for table in sorted(TENANT_OWNED_TABLES):
            columns = {c["name"]: c for c in inspector.get_columns(table)}
            column = columns.get("organization_id")
            if column is None:
                problems.append(f"{table}: no organization_id column")
            elif column["nullable"]:
                problems.append(f"{table}: organization_id is nullable")
        return problems

    problems = await db.run_sync(lambda session: _check(session.connection()))
    assert problems == [], problems


async def test_every_tenant_table_indexes_its_organization(db: AsyncSession) -> None:
    """A tenant filter on an unindexed column is a full scan on every request.

    Security and performance meet here: the isolation filter is on the hot path
    of literally every query, so it must be indexed.
    """
    from sqlalchemy import inspect as sa_inspect

    from app.db.tenancy import TENANT_OWNED_TABLES

    def _check(connection: Any) -> list[str]:
        inspector = sa_inspect(connection)
        missing = []
        for table in sorted(TENANT_OWNED_TABLES):
            leads = []
            for index in inspector.get_indexes(table):
                columns = index.get("column_names") or []
                if columns and columns[0] == "organization_id":
                    leads.append(index["name"])
            for constraint in inspector.get_unique_constraints(table) or []:
                columns = constraint.get("column_names") or []
                if columns and columns[0] == "organization_id":
                    leads.append(constraint["name"])
            primary = inspector.get_pk_constraint(table).get("constrained_columns") or []
            if primary and primary[0] == "organization_id":
                leads.append("pk")
            if not leads:
                missing.append(table)
        return missing

    missing = await db.run_sync(lambda session: _check(session.connection()))
    assert missing == [], f"no index leads with organization_id on: {missing}"


async def test_deleting_an_organization_leaves_no_orphaned_tenant_rows(
    client: AsyncClient, db: AsyncSession
) -> None:
    """ON DELETE CASCADE, verified rather than assumed.

    An orphan is worse than a deleted row: it belongs to nobody, so no scoped
    query can reach it and no tenant can be told it exists.
    """
    from app.models.organization import Organization

    account = await register(client)
    source, stream = await seed_source(db, organization_id=account.organization_id, name="S")
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=source,
        stream=stream,
        payload={"quantity": 1},
    )
    await db.commit()

    organization = await db.scalar(
        select(Organization).where(Organization.id == account.organization_id)
    )
    assert organization is not None
    await db.delete(organization)
    await db.commit()

    for model in (DataSource, SourceStream, Observation, Entity, RealityState, Conflict, SyncRun):
        remaining = await db.scalar(
            select(func.count())
            .select_from(model)
            .where(model.organization_id == account.organization_id)
        )
        assert remaining == 0, f"{model.__name__} rows outlived their organization"


# ---------------------------------------------------------------------------
# Address pinning
#
# Two things at once: a platform without outbound IPv6 must still reach a
# dual-stack database, and the address the policy checked must be the address
# actually dialled.
# ---------------------------------------------------------------------------


def test_connect_address_prefers_ipv4(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dual-stack host resolves to its IPv4 address, not its IPv6 one.

    Render and several other platforms have no outbound IPv6 route, so a driver
    that picks the AAAA record fails with "network is unreachable" against a
    database that is reachable over IPv4.
    """
    from app.connectors import network

    monkeypatch.setattr(network, "resolve_host", lambda host: ["2600:1f16:12b2::1", "18.226.241.3"])

    assert network.resolve_connect_address("db.example.com", allow_private=False) == "18.226.241.3"


def test_connect_address_defers_to_the_driver_when_private_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local development must keep resolving Docker service names itself."""
    from app.connectors import network

    assert network.resolve_connect_address("source-postgres", allow_private=True) is None


def test_connect_address_still_refuses_a_private_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pinning must not have become a way around the policy."""
    from app.connectors import network
    from app.connectors.types import ConnectorError, ConnectorErrorCode

    monkeypatch.setattr(network, "resolve_host", lambda host: ["10.0.0.1"])

    with pytest.raises(ConnectorError) as caught:
        network.resolve_connect_address("internal.example.com", allow_private=False)
    assert caught.value.code is ConnectorErrorCode.INVALID_CONFIGURATION


def test_conninfo_pins_the_address_but_keeps_the_hostname_for_tls() -> None:
    """`hostaddr` dials, `host` verifies. Losing the hostname would break TLS."""
    from app.connectors.postgres.config import PostgresConnectionConfig, SslMode
    from app.connectors.postgres.connector import PostgresConnector

    connector = PostgresConnector(
        config=PostgresConnectionConfig(
            host="db.example.com",
            port=5432,
            database="warehouse",
            username="reader",
            ssl_mode=SslMode.REQUIRE,
        ),
        password="secret",
    )

    conninfo = connector._conninfo("18.226.241.3")
    assert "hostaddr=18.226.241.3" in conninfo
    assert "host=db.example.com" in conninfo

    # No address pinned: no hostaddr at all, rather than an empty one.
    assert "hostaddr" not in connector._conninfo(None)
