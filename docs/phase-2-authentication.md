# Phase 2 — Authentication and multi-tenancy

## Objective

Build the production-grade identity and tenancy foundation: users,
organizations, memberships, sessions, secure authentication, organization
context, protected routes, multi-tenant isolation, and a real frontend
authentication shell.

---

## The tenancy model

```
users            global identities — a person, not a tenant's copy of a person
  │
  │  memberships (user_id, organization_id, role)   ← the tenancy edge
  ▼
organizations    the tenant boundary; every owned record carries its id
  ▲
  │  sessions.active_organization_id
  │
sessions         server-side, revocable, one per sign-in
```

A user has access to an organization **if and only if** a membership row
exists. There is no other path.

---

## Three layers of isolation

Multi-tenancy is enforced three times, deliberately. Each layer catches what
the one above it might miss.

### 1. The database — a composite foreign key

```sql
FOREIGN KEY (user_id, active_organization_id)
    REFERENCES memberships (user_id, organization_id) ON DELETE CASCADE
```

A session **cannot** point at an organization the user is not a member of.
Not through an application bug, not through a manual `UPDATE`, not through a
compromised route handler. PostgreSQL rejects the row.

Verified directly:

```
ERROR:  insert or update on table "sessions" violates foreign key constraint
        "fk_sessions_active_membership"
DETAIL: Key (user_id, active_organization_id)=(…) is not present in table "memberships".
```

`active_organization_id` is nullable, and MATCH SIMPLE skips the check when any
column of the key is NULL — so "no organization selected" remains a valid state.
`ON DELETE CASCADE` means revoking a membership destroys the sessions acting in
that organization: access ends when the membership ends, not when the cookie
expires.

### 2. The ORM session — a scope guard

`app/db/tenancy.py` registers a `do_orm_execute` listener. Any SELECT, UPDATE or
DELETE touching a tenant-owned table without constraining its `organization_id`
raises `MissingOrganizationScopeError` instead of returning rows.

```python
select(Membership).where(Membership.role == "owner")   # raises
select(Membership).where(Membership.organization_id == org_id)  # fine
```

A forgotten filter becomes a loud failure in development and in tests, rather
than a quiet cross-tenant read in production. The escape hatch, `unscoped()`,
is used in exactly one place — listing a user's own memberships, which is
cross-tenant by definition because it is the question the organization selector
asks.

### 3. The route signature — a non-optional tenant id

Routes that read organization-owned data take `CurrentOrganization`, whose
`organization_id` is non-optional. The scope comes from the session, never from
a request parameter, so there is nothing for a caller to tamper with.

---

## Authentication flow

### Registration

```
POST /api/auth/register
  → validate (Pydantic)
  → hash password (Argon2id, 19 MiB / t=2 / p=1)
  → create user + organization + owner membership   (one transaction)
  → issue session, set cookies
  → audit: user.registered, organization.created
  ← 201 with the session payload
```

### Sign-in

```
POST /api/auth/login
  → Origin check (middleware)
  → look up user by CITEXT email
  → verify Argon2id hash — including against a dummy hash when no user matched,
    so timing does not reveal whether an address is registered
  → rehash if the cost setting has been raised since
  → issue session, set cookies
  → audit: session.login_succeeded / session.login_failed
```

### Every authenticated request

```
cookie rs_session
  → SHA-256 → sessions.token_hash lookup (indexed, single equality)
  → reject if revoked, past expires_at, or idle past the timeout
  → reject if the user is disabled
  → touch last_seen_at (at most once per minute)
  → load memberships + active membership
  → AuthContext
```

### Sign-out

```
POST /api/auth/logout
  → CSRF check (only when a session exists — logout is idempotent)
  → stamp revoked_at + revoked_reason
  → clear both cookies
```

The token is dead server-side, so a replayed cookie fails. Clearing the browser
copy is a convenience, not the control.

---

## Session design

Server-side and opaque, not JWT. The deciding property is **revocation**: an
operator must be able to end a session immediately — on logout, on password
change, on suspected compromise. A stateless token stays valid until it expires
no matter what the server thinks, and every workaround (short TTLs plus refresh
tokens, deny-lists) reintroduces server-side state with more moving parts.

| Property | Choice | Why |
| --- | --- | --- |
| Token | 256-bit CSPRNG, URL-safe | Nothing to guess |
| Storage | SHA-256 hash | A database disclosure yields no usable sessions |
| Hash choice | Fast, not Argon2 | The token is not guessable, and every request looks one up. Argon2 here would be self-inflicted DoS |
| Absolute lifetime | 14 days | Bounds the useful life of a stolen cookie |
| Idle timeout | 24 hours | Ends sessions nobody is using |
| `last_seen_at` writes | ≤ once per minute | Otherwise every request is a write |

Passwords get Argon2id; session tokens get SHA-256. Swapping them is a mistake
in either direction — one is catastrophic, the other merely expensive.

---

## CSRF strategy

Cookie authentication means the browser attaches credentials to cross-site
requests automatically. Two defences, covering different cases:

**Authenticated state-changing requests — a session-bound token.**
`rs_csrf` is readable; its value must be echoed in `X-CSRF-Token`. The
submitted value is compared against `sessions.csrf_token`, *not* against the
cookie. Plain double-submit assumes an attacker cannot control both values,
which fails when a cookie can be planted from a sibling subdomain; validating
against server state removes the assumption.

**Login and registration — the `Origin` header.**
There is no session yet, so there is no token to bind to. Without this, login
CSRF lets an attacker sign a victim into an attacker-controlled account, so
everything the victim then does happens inside the attacker's workspace. A
*missing* Origin is allowed: non-browser clients do not send one and are not
subject to CSRF, since no ambient cookie is attached.

---

## Database changes

Migration `0002_identity_tenancy`, five tables:

| Table | Notes |
| --- | --- |
| `users` | CITEXT email (case-insensitive uniqueness in the index), Argon2id hash, soft `is_active` disable |
| `organizations` | CITEXT slug with format and length CHECKs |
| `memberships` | UNIQUE (user_id, organization_id), role CHECK, tenant-owned |
| `sessions` | token_hash UNIQUE, composite FK to memberships, partial indexes on live rows |
| `audit_logs` | nullable organization_id, FKs ON DELETE SET NULL |

**Native constraints used:** CITEXT for case-insensitive uniqueness; CHECK for
role, slug format, slug length, non-blank name, email shape; a composite
foreign key for session/membership consistency; partial indexes filtered on
`revoked_at IS NULL`; `ON DELETE CASCADE` for tenant-owned rows and
`ON DELETE SET NULL` for audit references.

`memberships` carries exactly two indexes — the unique constraint on
`(user_id, organization_id)` and one on `organization_id`. Autogenerate
proposed three more; a separate `user_id` index is redundant because PostgreSQL
uses the leftmost prefix of the composite.

`audit_logs.organization_id` is nullable because the events most worth
recording — a failed login for an unknown address — have no tenant context.

---

## Two bugs found and fixed

**A validation error echoed the submitted password.** Pydantic's `errors()`
includes the offending value as `input`. That list was returned verbatim, so a
password failing the length policy came back in the response body *and* went
into the error log. Redaction by key name could not catch it: the key was
`input`, not `password`. The same `ctx` field also held a non-serialisable
exception object, which turned every custom-validator failure into a 500
instead of a 422 — that is how the leak was noticed. Fixed in
`app/middleware/errors.py`, which now returns only `loc`, `msg` and `type`.
Regression test: `test_a_validation_failure_does_not_echo_the_submitted_value`.

**Sign-out produced an unhandled promise rejection.** The provider set the
signed-out state in a `finally` but still rethrew, and every call site
discarded the promise. Signing out now always succeeds from the caller's point
of view, which is what the surrounding code already assumed.

---

## Verification

All against a real PostgreSQL, with every account created through the real
registration endpoint. No seeding, no direct-insert fixtures, no mock data.

| # | Required proof | Result |
| - | -------------- | ------ |
| 1 | Authenticated user can access their organization | Pass |
| 2 | Unauthenticated user is rejected | Pass — 401 on five protected routes |
| 3 | User cannot access another organization's data | Pass — members, list, switch, remove |
| 4 | Membership permissions are enforced | Pass — viewer refused, owner satisfies "admin" |
| 5 | Invalid/expired session is rejected | Pass — unknown, expired, idle, disabled user |
| 6 | Logout invalidates the session | Pass — replayed token fails |
| 7 | Organization switching is correctly scoped | Pass — server-side, persisted on the session row |
| 8 | organization_id cannot be silently omitted | Pass — guard raises on SELECT/UPDATE/DELETE |
| 9 | Authentication secrets are never logged | Pass — full lifecycle, stdout scanned |
| 10 | Password hashes are never returned | Pass — every endpoint checked against the real hash |

Beyond the checklist: the composite foreign key was verified by attempting a
forged cross-tenant session directly in SQL, and again through the ORM.

---

## Deliberately not built

| Not built | Phase | Why |
| --- | --- | --- |
| Redis rate limiting | later | The seam and policies exist; the backing store belongs with the phase that owns it |
| Member invitations | later | Needs email delivery |
| Password reset | later | Needs email delivery |
| Email verification | later | Column exists; enabling it is policy, not migration |
| Role changes in the UI | later | The check is enforced; the screen is not the Phase 2 deliverable |
| Audit log UI | later | Rows are written and indexed for it |
| Connectors, Reality Engine, conflicts | 3–5 | Out of scope by instruction |
