# Architecture

Component boundaries and data flow for RealitySync. This describes the approved
target architecture; the Phase 1 column records what exists today.

---

## Layers

```
┌───────────────────────────────────────────────────────────────┐
│ CLIENT — Next.js                                              │
│   Route groups · API client · Application shell               │
└───────────────┬───────────────────────────────────────────────┘
                │ HTTPS / JSON
                ▼
┌───────────────────────────────────────────────────────────────┐
│ API — FastAPI                                                 │
│   Middleware: request id → CORS → error envelope              │
│   Routers (thin) · Pydantic schemas at the boundary           │
└───────────────┬───────────────────────────────────────────────┘
                ▼
┌───────────────────────────────────────────────────────────────┐
│ APPLICATION SERVICES                                          │
│   Orchestration · transactions · authorisation                │
└───────────────┬───────────────────────────────────────────────┘
                ▼
┌───────────────────────────────────────────────────────────────┐
│ REALITY ENGINE — pure, deterministic, zero I/O   [Phase 4+]   │
│   Normalise · validate · reconcile · score · detect conflicts │
└───────────────┬───────────────────────────────────────────────┘
                ▼
┌───────────────────────────────────────────────────────────────┐
│ PERSISTENCE                                                   │
│   PostgreSQL (system of record) · Redis (ephemeral only)      │
└───────────────────────────────────────────────────────────────┘
```

---

## Component responsibilities

| Component      | Owns                                                                 | Must never                                              |
| -------------- | -------------------------------------------------------------------- | ------------------------------------------------------- |
| Frontend       | Rendering, navigation, caching, loading/empty/error presentation      | Hold credentials, compute confidence, invent a metric   |
| API            | Auth, authorisation, validation, transactions, error envelope         | Contain reconciliation maths, return credentials        |
| Connectors     | Protocol specifics, pooling, retries, schema discovery                | Touch the application database, decide truth            |
| Reality Engine | Validation rules, weighting, reconciliation, confidence, conflicts    | Perform I/O, read a wall clock, import a connector      |
| Conflict Engine| Detection, severity, lifecycle, evidence freezing                     | Assert a conflict without linked evidence               |
| AI layer       | Prompt construction, citation validation                              | Compute state, access the database, invent evidence     |
| PostgreSQL     | System of record, integrity, ordering                                 | Hold business logic in triggers                         |
| Redis          | Rate limiting, SSE fan-out, realtime tickets                          | Hold anything authoritative                             |

The **one-way dependency** — engine never imports connectors — is what makes a
new data source cheap. It will be enforced by an architecture test.

---

## Time model

Two independent axes, both required:

| Axis              | Columns                        | Question answered                      |
| ----------------- | ------------------------------ | -------------------------------------- |
| Valid time        | `event_time`, `valid_from/to`  | What actually happened at T?           |
| Transaction time  | `ingested_at`, `decided_at`, `superseded_at` | What did RealitySync know at T? |

These diverge whenever a late observation corrects the past. A single-axis model
can answer one question or the other, never both — which is why the bitemporal
structure is present from the start rather than retrofitted.

Status: **Phase 5.** No temporal tables exist yet.

---

## Confidence

```
w_o     = R_source × Freshness × Quality
Ceiling = 1 − Π(1 − R_source)  over supporting sources, capped at 0.99
Base    = 0.40·reliability + 0.30·freshness + 0.15·quality + 0.15·agreement
Score   = 100 × Ceiling × Base × penalties
```

Deterministic, bounded at 99, and stored with its full component breakdown so
any score can be re-derived and explained.

> **Superseded, Phase 4.** This document previously recorded Base as
> `0.40·agreement + 0.30·freshness + 0.15·quality + 0.15·validation`. The
> Phase 4 brief specifies `reliability = 0.40, freshness = 0.30,
> quality = 0.15, agreement = 0.15`, which is the version implemented. Two
> terms changed: reliability replaces agreement at 0.40, and agreement
> replaces validation at 0.15. Reliability therefore appears in both the
> Ceiling and the Base — a deliberate double weighting of source authority,
> confirmed rather than assumed.

---

## Redis

Exactly three uses, none authoritative:

1. Rate limiting
2. SSE pub/sub fan-out across instances
3. Single-use realtime tickets

Synchronisation and idempotency use PostgreSQL instead — advisory locks and
unique constraints. **Redis unavailable means degraded, never broken, never
wrong.** Every key is reconstructible from PostgreSQL.

Status: connectivity only in Phase 1; the three uses arrive with the features
that need them.

---

## Implementation status

| Area                    | State                                                     |
| ----------------------- | --------------------------------------------------------- |
| Configuration           | Implemented — environment-driven, validated, fails fast    |
| Structured logging      | Implemented — JSON, request id, secret redaction           |
| Error envelope          | Implemented — uniform shape, safe messages                 |
| Database connectivity   | Implemented — async engine, pooling, health probe          |
| Redis connectivity      | Implemented — client lifecycle, health probe               |
| Migrations              | Implemented — Alembic, foundation + identity migrations    |
| Health / readiness      | Implemented — `/health`, `/ready`                          |
| Frontend shell          | Implemented — navigation, design tokens, states            |
| API client              | Implemented — typed, correlated, timeouts, error mapping   |
| Identity and tenancy    | Implemented — users, organizations, memberships (Phase 2)  |
| Authentication          | Implemented — Argon2id, server-side sessions, CSRF         |
| Multi-tenant isolation  | Implemented — composite FK, ORM scope guard, route context |
| Audit trail             | Implemented — append-only, nullable tenant                 |
| Frontend auth shell     | Implemented — sign-in, org selector, sign-out, states      |
| Rate limiting           | Seam only — Redis implementation in a later phase          |
| Connector interface     | Implemented — DataConnector, types, registry (Phase 3)     |
| PostgreSQL connector    | Implemented — TLS-only, read-only, catalog discovery       |
| Credential encryption   | Implemented — AES-256-GCM, row-bound, rotatable            |
| Observation ingestion   | Implemented — normalised, fingerprinted, idempotent        |
| Sync runs               | Implemented — advisory-locked, full lifecycle recorded     |
| Reality Engine          | Not started — Phase 4                                      |
| Conflict engine         | Not started — Phase 5                                      |
| AI investigation        | Not started — Phase 8                                      |

## Connectors

`app/ingestion` depends on `app/connectors/base.DataConnector` and never on a
concrete connector. That one-way arrow is what makes a new source type cheap:
write a class, register it, change nothing downstream. Details in
[phase-3-postgres-connector.md](phase-3-postgres-connector.md).

## Tenancy

Organizations are tenants; memberships connect users to them; sessions carry
the active organization. Isolation is enforced three times over — in the
database, in the ORM session, and in route signatures — because a single layer
that can be bypassed by one forgotten `WHERE` clause is not a control. The full
model is documented in [phase-2-authentication.md](phase-2-authentication.md).
