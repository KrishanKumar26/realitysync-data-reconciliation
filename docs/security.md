# Security and tenancy

What protects tenant data, how it is enforced, what was adversarially tested in
Phase 11, and — the part that matters most — what remains unverified.

This document does not claim the system is secure. It states what was attacked
and what survived.

---

## Tenancy enforcement — three independent layers

An organization is a tenant. Isolation is enforced three times over, because a
single control that can be bypassed by one forgotten `WHERE` clause is not a
control.

### 1. The database

`organization_id` is `NOT NULL` on every tenant-owned table, with
`ON DELETE CASCADE` to `organizations`. There is no orphan state: a row whose
organization is gone belongs to nobody, and an orphan is worse than a deleted
row because no scoped query can reach it and no tenant can be told it exists.

Sessions carry a **composite foreign key**
`sessions(user_id, active_organization_id) → memberships(user_id, organization_id)`.
A session cannot name an organization the user is not a member of; the database
refuses the row.

### 2. The ORM tenancy guard

A `do_orm_execute` listener inspects every ORM `SELECT`/`UPDATE`/`DELETE`. A
statement touching a tenant-owned table without pinning that table's
`organization_id` raises `MissingOrganizationScopeError` rather than returning
rows.

**Pinning means `==` or `IN` against a bound value.** Phase 11 tightened this;
see *Vulnerabilities found* below. Presence of the column in a filter is not
enough.

`unscoped()` is the only way past, and there are exactly two uses, both bounded:

| Where | Why it spans tenants | Bounded by |
| --- | --- | --- |
| `services/auth.py` — list a user's memberships | "which organizations do I belong to" cannot be answered from inside one | one user's own rows |
| `ingestion/scheduler.py` — find due streams | a background loop has no single tenant | reads scheduling metadata only; the sync it leads to is tenant-scoped |

### 3. Route signatures

Product routes take a non-optional `CurrentOrganization`, so a handler cannot
be written without a tenant in scope. Cross-tenant resources return **404**,
not 403 — as far as the caller is concerned the resource does not exist, and
403 would confirm that it does.

The exception is switching organization, which returns **403**: the caller
supplied the organization id themselves, so membership is the thing being
denied and saying so leaks nothing.

---

## Vulnerabilities found in Phase 11

Six. Four in the ORM tenancy guard, one in the connector host policy, one in
identifier validation. All fixed.

### SSRF through the connector host — the most serious

**A tenant could aim RealitySync at internal infrastructure and have it connect
on their behalf, from inside the deployment's network.**

The host of a data source is supplied by whoever configures it, and nothing
restricted it. `_validate_host` accepted any IP literal unconditionally. Phase 3
recorded "private-network databases are outside MVP" as *scope*; scope is not a
control.

Verified exploitable from an ordinary tenant account. The connection test's own
error codes formed a working port scanner:

| Target | Error | What it revealed |
| --- | --- | --- |
| `169.254.169.254:80` | `timeout` | host exists, filtered — the cloud metadata service |
| `10.0.0.1:5432` | `timeout` | private range reachable |
| `127.0.0.1:6379` | `unreachable` | **nothing listening on that port** |
| `postgres:5432` | `tls_failed` | **a PostgreSQL is running here** |

`unreachable` against `tls_failed` is the entire scan: one says the port is
closed, the other says a database answered. That is enough to map a private
network from a signup form.

And it gets worse than mapping. The application's own database is reachable by
hostname from inside the deployment, and its default credentials are published
in `.env.example`. A tenant who tried them would read **every other tenant's
data** through a feature working exactly as designed. In this deployment the
attempt failed only because the local PostgreSQL has no TLS and the connector
requires it — an accident of the dev setup, not a control, and one that
disappears in production where managed databases do offer TLS.

Fixed in `app/connectors/network.py`: the host is resolved and every address it
resolves to must be publicly routable. Loopback, RFC1918, link-local, reserved,
multicast and unspecified are all refused, in both connectors, at configuration
time **and** again at connect time — a stored config outlives its validation.

The refusal message deliberately does not echo the resolved address. Reporting
"127.0.0.1 is private" back would preserve exactly the oracle the check closes.

`CONNECTOR_ALLOW_PRIVATE_HOSTS` exists for self-hosted deployments whose
databases genuinely sit on a private network. It defaults to **off**, so the
safe posture is what you get without deciding anything. The dev stack and the
test suite opt in, because Docker service addresses are private by definition.

Verified after the fix: all four targets now return an identical
`invalid_configuration`, so nothing distinguishes a closed port from a running
database. Legitimate sources over TLS 1.3 — both PostgreSQL and MySQL — still
connect.

### Identifier validation at the API boundary

A stream could be created with a table name containing a backtick. The MySQL
connector's `quote_identifier` refuses it, so there was no injection — but the
refusal happened at query time, which meant a stored configuration that failed
on every subsequent sync and nobody looking at the stream could see why.

`Identifier` now carries a conservative allowlist pattern (`^[A-Za-z0-9_$-]+$`),
so the boundary refuses what the connector would have refused later. An
allowlist rather than a denylist: enumerating dangerous characters means losing
to the first one nobody thought of.

### Four in the ORM guard

None was remotely exploitable — each required a developer to write the query —
but the guard exists precisely to catch the query nobody meant to write, and it
was accepting four shapes that scope nothing.

| # | Shape | Why it bypassed | Severity |
| --- | --- | --- | --- |
| 1 | `a.organization_id = b.organization_id` | correlates two tenant tables without pinning either | High — reads every tenant |
| 2 | `organization_id = organization_id` | self-comparison, always true | High |
| 3 | `organization_id IS NOT NULL` | structurally a filter, semantically none — the column is `NOT NULL` | **Highest realistic risk**: it looks like a scope filter at a glance |
| 4 | `organization_id != :org` | pins a value and returns every *other* tenant | High — inverted scoping |

The old rule counted any appearance of `organization_id` in a `WHERE` or
`JOIN ... ON` as scoping. The new rule requires a comparison with `==` or `IN`
**against a bound value**, which all four fail and every query in the
application passes.

Shape 1 happened to fail closed already, because SQLAlchemy keeps an ORM
join's `ON` clause outside the statement tree until compilation — the same
quirk that bit Phase 5 twice, working in our favour this time. It is now
rejected by rule rather than by accident.

Each is pinned by a regression test in `tests/test_security_audit.py`, and the
fix was verified not to reject any of the 24 legitimate scoping call sites.

---

## What was adversarially tested

58 tests in `tests/test_security_audit.py`. They attack; they do not inspect.

**The guard** — every bypass shape above, plus unscoped `SELECT`/`UPDATE`/
`DELETE` against each of the seven tenant-owned models, aggregates
(`COUNT` leaks size even when it returns no rows), and a scalar subquery
attempting to smuggle an unscoped read past an outer filter.

**Authentication** — anonymous access to every protected endpoint, a forged
session cookie, a session revoked by logout, and a session past its absolute
expiry.

**Cross-tenant IDOR** — two fully-populated organizations, then Org A's
session against every one of Org B's ids: 12 `GET` paths and 11 mutations
(`POST`, `PATCH`, `DELETE`, sync, recalculate, test-connection,
discover-schema), including nested resources two levels deep.

The subtlest case has its own test: mapping **B's stream to A's own entity**.
Both ids are individually plausible and the entity genuinely belongs to A. Had
it succeeded, A's reality state would have been derived from B's observations —
a leak that would look like a legitimate calculation.

**Survival** — the attacks must change nothing, not merely return 404. B's
resources are re-read afterwards and must still exist.

**Membership** — switching to a foreign organization, and access after a
membership is revoked.

**SSRF** — every non-public address class refused, the refusal message checked
for not echoing the resolved address, the escape hatch confirmed explicit, the
field default confirmed off, and both connectors confirmed to refuse at connect
time.

**Mass assignment** — a client-supplied `organization_id` and `id` in a create
request must not be honoured; extra fields are refused outright rather than
silently dropped.

**Malformed input** — non-UUID path parameters, path traversal, SQL fragments
and null bytes must produce a validation error rather than a crash, with no
traceback, SQLAlchemy or psycopg text in the response.

**SQL injection through identifiers** — hostile table names refused both at the
connector and at the API.

**Credentials** — never in any API response, never in the logs (checked with a
handler on the root logger, not `caplog`, so the test cannot be silently
disabled by a plugin flag), encrypted at rest with the plaintext absent from the
raw row, and absent from connection-failure error messages.

**Enumeration** — dashboard aggregates count only the caller's tenant, the
activity feed carries no other tenant's ids, and a foreign id is
indistinguishable from an absent one (same status *and* same message), so the
404/403 split cannot become an existence oracle.

**Background jobs** — the scheduler's grouping is verified per organization:
every stream it attributes to a tenant must belong to that tenant's source. And
a scheduled run must record `triggered_by_user_id = NULL`, because nobody
triggered it.

**Schema** — `organization_id` is `NOT NULL` on every tenant table, an index
leads with `organization_id` on every tenant table (the isolation filter is on
the hot path of every query, so an unindexed one is both a performance and an
availability problem), and deleting an organization leaves no rows behind in
any of the seven tables.

---

## Live verification

Two real organizations against the running PostgreSQL, no mocks. Org A's
session was pointed at Org B's real source and entity ids:

| Attack | Result |
| --- | --- |
| 9 cross-tenant `GET` paths | 404 |
| `DELETE` source, `DELETE` entity | 404 |
| `POST` recalculate, `POST` sync | 404 |
| Switch active organization to B | 403 |
| Unauthenticated `GET /api/dashboard` | 401 |
| B re-reads its own source and entity afterwards | 200 — nothing was destroyed |
| Credential in the API response | absent; only `password_set: true` |
| Credential in the API logs | 0 occurrences |

---

## Known limitations — what is *not* verified

Stated plainly, because a security document that lists only successes is
misleading.

**The guard cannot see a correlated subquery in a SELECT list.** A tenant-owned
table reached only from there would not be detected. Nothing in the application
does this; it is a gap in the backstop, not a known leak.

**The guard cannot check values, only shapes.** It confirms a query pins
`organization_id` to *some* bound value; it cannot know the value is the
caller's. That is the route layer's job, and it is why the layers are
independent rather than redundant.

**Raw SQL is not guarded at all.** `assert_organization_scoped` only inspects
ORM `Select`/`Update`/`Delete`. The only raw SQL in the application is
PostgreSQL advisory locks, which touch no tenant data. Any future `text()`
query against a tenant table would be entirely unprotected.

**`INSERT` is not guarded.** A write with a wrong `organization_id` would not be
caught by the ORM listener. Routes derive it from the session context, so it is
not currently reachable, but the layer does not cover it.

**DNS rebinding is closed for PostgreSQL, open for MySQL.** The host is
validated at configuration time and again at connect time. For PostgreSQL the
address that passes the check is now the address dialled: `resolve_connect_address`
returns it and it is handed to libpq as `hostaddr`, while `host` still carries
the name for SNI and certificate verification — the two are separate parameters
for exactly this reason, so pinning the address does not weaken TLS.

MySQL remains unpinned. aiomysql takes a single `host`, used both to dial and
as the TLS `server_hostname`, so substituting the address would break hostname
verification under `verify-full`. Two checks raise the cost there; they do not
eliminate it.

The same pin prefers **IPv4**. Managed providers publish both A and AAAA
records and several hosting platforms have no outbound IPv6 route, where the
driver would otherwise pick the AAAA record and fail with "network is
unreachable" against a database reachable over IPv4.

**Not tested:** rate-limit bypass under concurrency, session fixation, CSRF
token entropy, timing attacks on login, TLS configuration of the deployment,
dependency vulnerabilities, and any denial-of-service surface. None of these
were in Phase 11's scope.

**No penetration test has been performed.** These are the attacks we thought
of. That is not the same as the attacks that exist.

---

## Audit trail

Security-sensitive actions record actor, organization, action, resource and
request id in `audit_logs`. `organization_id` is nullable there on purpose: a
failed login has no tenant context, and inventing one would put a false
association in the record.

Automated actions do **not** claim a human actor. A scheduled sync records
`triggered_by_user_id = NULL`, asserted by test — nobody pressed anything, and
naming a person would be a false entry in the record people trust most.
