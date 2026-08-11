# Phase 7 — Hardening, observability and operations

Phase 7 closes the two operational gaps the earlier phases left open by name:
the Redis-backed rate limiter that Phase 2 built a seam for, and an operational
view that reports degradation instead of leaving it to be inferred from logs.

Scope was taken from the repository's own declarations rather than chosen:

| Declared in                    | Said                                                   |
| ------------------------------ | ------------------------------------------------------ |
| `docs/phase-2-authentication.md` | "Redis rate limiting — later. The seam and policies exist" |
| `docs/architecture.md`         | "Rate limiting — Seam only, Redis implementation in a later phase" |
| `app/services/rate_limit.py`   | `NullRateLimiter` marked as not enforcement            |

---

## Rate limiting

A **sliding** window over a Redis sorted set, one key per (policy, identity).

A fixed window is cheaper and wrong for this purpose: it permits a burst of
`2x max` across a boundary — ten attempts at 4:59 and ten more at 5:01 both pass
a five-minute fixed window. For a credential-stuffing defence, that doubling is
the attack.

The whole check is one pipeline — evict expired entries, count what remains,
record this attempt, reset the TTL — so two concurrent requests cannot both read
a stale count. Members are unique per attempt rather than keyed on the
timestamp, so two attempts in the same millisecond are both counted.

| Policy           | Limit | Window  | Identity            |
| ---------------- | ----- | ------- | ------------------- |
| `auth.login`     | 10    | 5 min   | client IP + email   |
| `auth.register`  | 5     | 1 hour  | client IP           |

Login is keyed on IP **and** email so one attacker cannot lock out a shared
office NAT, and so spraying one password across many accounts still meets a
per-account limit.

### It fails open

When Redis is unreachable, requests are allowed. This follows directly from the
architecture's Redis rule — *unavailable means degraded, never broken, never
wrong* — and it is the single most consequential decision in the phase.

A limiter that denied everything when its own datastore blipped would lock every
customer out of the product because a cache restarted. Redis holds nothing
authoritative here; losing it costs enforcement, not correctness.

The allowance is marked `degraded` on the verdict, logged, and surfaced on the
status endpoint, so "we are not currently limiting" stays an observable fact
rather than a silent one.

### Refusals

`429` in the standard error envelope, with the headers a client actually acts
on:

```
Retry-After: 300
X-RateLimit-Limit: 10
X-RateLimit-Remaining: 0
X-RateLimit-Window: 300
```

`Retry-After` is computed from the oldest attempt still in the window, so it
says when capacity genuinely returns rather than restating the window length.

Implementing this exposed a real defect in the Phase 1 error handler: the
`StarletteHTTPException` handler rebuilt the response and dropped
`exc.headers`, so a 429 raised with `Retry-After` was served without it. Fixed
in `app/middleware/errors.py` — the same bug would have silently discarded
`WWW-Authenticate` on any future 401 challenge.

A rate-limited login returns the same message regardless of whether the account
exists. The 429 declines to check the credentials; it must not become a
user-enumeration oracle in the process.

---

## `GET /api/system/status`

Authenticated operational detail, distinct from the Phase 1 probes:

| Endpoint  | Audience        | Auth | Purpose                              |
| --------- | --------------- | ---- | ------------------------------------ |
| `/health` | platform        | no   | liveness; touches nothing            |
| `/ready`  | deployment      | no   | readiness; gates rollout             |
| `/status` | a person        | yes  | which subsystems are degraded, and why |

Authenticated because "the rate limiter is currently not enforcing" is a useful
sentence for an operator and an equally useful sentence for an attacker. Not
organization-scoped: it describes the deployment and contains no tenant data.

Each component reports **what it is doing, not what it was configured to do**.
An installed limiter pointed at an unreachable Redis reports `degraded`, not
`operational` — it is allowing every request. Reporting configuration instead of
behaviour would put the one misleading line in the response an operator reads
during an outage.

`disabled` is kept distinct from `degraded` and does not drag the overall
verdict down: it is a deployment choice, and a deployment that legitimately
terminates rate limiting at the edge should not learn to ignore the field.

**The endpoint currently always reports `degraded`**, because confidence scoring
is blocked on the missing Phase 0 specification. That is the correct answer. An
engine that cannot produce its primary output is not operational, and reporting
green because the process is alive is precisely the unverified signal this
product exists to eliminate. It should stop being the answer the day the
specification arrives — not before.

---

## Verification

Backend 397 passed / 1 skipped. Frontend 88 passed. `ruff`, `mypy --strict` and
`tsc` clean. `alembic check` reports no new operations — Phase 7 adds no schema.

Live against the running deployment:

- Attempts 1–10 returned 401, attempt 11 returned 429 with all four headers.
- With `redis` stopped: `/health` 200, logins still processed, a valid
  credential still signed in successfully, and `/api/system/status` reported
  both `redis` and `rate_limiting` as degraded with the reason.
- After `redis` restarted, both returned to `operational` without an API
  restart.

The fail-open test uses a client pointed at a closed port rather than a mock, so
it exercises the same exception path a real outage produces.

---

## Deliberately not done

**No rate limiting on the product API.** The limits protect credential
endpoints, which are the ones an unauthenticated attacker can reach. Per-tenant
quotas on authenticated endpoints are a capacity decision nobody has made, and
guessing a number here would either be too low to be safe to ship or too high
to be worth having.

**No metrics endpoint.** Structured logs carry request id, method, path, status
and duration already. A Prometheus surface needs a decision about what is
scraping it, which is deployment context this repository does not have.

**No alerting thresholds.** `/api/system/status` reports state; what to page on
is an operations decision, not a code one.

---

## Still blocked on the Phase 0 specification

Unchanged, and enumerated in `app/engine/spec.py::MISSING_SPECIFICATIONS`.
Phase 7 adds no confidence behaviour and removes none; the engine component on
the status endpoint reports the count of missing specifications directly, so the
blockage is visible in operations rather than only in the source.
