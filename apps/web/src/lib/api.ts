/**
 * Typed API client.
 *
 * Thin wrapper over fetch that establishes the conventions the rest of the
 * product will rely on: credentialed requests, a per-request correlation id,
 * a normalised error shape, and timeouts so a hung backend cannot hang the UI.
 *
 * Phase 1 uses it for the health check only.
 */

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const DEFAULT_TIMEOUT_MS = 8000;

/** Error envelope returned by the API for every non-2xx response. */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details?: unknown;
    request_id?: string | null;
  };
}

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly requestId: string | null;
  readonly details: unknown;

  constructor(params: {
    code: string;
    message: string;
    status: number;
    requestId?: string | null;
    details?: unknown;
  }) {
    super(params.message);
    this.name = "ApiError";
    this.code = params.code;
    this.status = params.status;
    this.requestId = params.requestId ?? null;
    this.details = params.details;
  }
}

function createRequestId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `req_${crypto.randomUUID().replace(/-/g, "")}`;
  }
  return `req_${Math.random().toString(16).slice(2)}${Date.now().toString(16)}`;
}

export interface ApiRequestOptions extends Omit<RequestInit, "signal"> {
  /** Abort the request after this many milliseconds. */
  timeoutMs?: number;
}

/** Name of the readable cookie carrying the CSRF token. */
const CSRF_COOKIE = "rs_csrf";
const CSRF_HEADER = "X-CSRF-Token";

const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/**
 * The CSRF token, as the API last reported it.
 *
 * Held in memory rather than read only from the cookie, because the cookie is
 * unreadable whenever the API and the web app are on different domains — and
 * that is the normal production shape, not an edge case.
 *
 * `document.cookie` exposes only cookies belonging to the page's own domain.
 * With the API on `api.example.com` and the app on `app.example.com`, the
 * browser stores and sends `rs_csrf` correctly but the app's JavaScript cannot
 * see it, so every state-changing request went out with no token and was
 * refused. Reads worked, writes did not.
 *
 * This is invisible in local development, where both run on `localhost` and
 * cookies ignore the port, so one origin can read the other's cookie. It first
 * appears on a real deployment.
 */
let csrfToken: string | null = null;

/**
 * Record the token from an authenticated response.
 *
 * Called wherever the API reports one — sign-in, registration, session
 * refresh, organization switch. Each of those replaces the token server-side,
 * so a stale copy would fail every write until a reload.
 */
export function rememberCsrfToken(token: string | null): void {
  csrfToken = token;
}

/**
 * Read the CSRF token for the next state-changing request.
 *
 * Memory first, cookie second. The cookie fallback keeps same-origin and
 * local-development setups working, and covers a page that was reloaded
 * before any session request has repopulated memory.
 */
export function readCsrfToken(): string | null {
  if (csrfToken) return csrfToken;
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(
    new RegExp(`(?:^|;\\s*)${CSRF_COOKIE}=([^;]*)`),
  );
  const value = match?.[1];
  return value === undefined ? null : decodeURIComponent(value);
}

/**
 * Perform an API request and parse the JSON body.
 *
 * Throws {@link ApiError} for non-2xx responses, network failures and
 * timeouts, so callers branch on a single error type.
 */
export async function apiFetch<T>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, headers, ...init } = options;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  const method = (init.method ?? "GET").toUpperCase();
  // Attached only to state-changing requests, which are the only ones the API
  // checks. Sending it on every GET would leak the token into more places for
  // no benefit.
  const csrfToken = UNSAFE_METHODS.has(method) ? readCsrfToken() : null;

  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      // Session cookies are the authentication mechanism. Without this the
      // browser sends no cookie cross-origin and every request is anonymous.
      credentials: "include",
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        "X-Request-ID": createRequestId(),
        ...(csrfToken ? { [CSRF_HEADER]: csrfToken } : {}),
        ...headers,
      },
    });

    const body: unknown = await response.json().catch(() => null);

    if (!response.ok) {
      const envelope = body as ApiErrorBody | null;
      throw new ApiError({
        code: envelope?.error?.code ?? "HTTP_ERROR",
        message:
          envelope?.error?.message ?? `Request failed (${response.status})`,
        status: response.status,
        requestId:
          envelope?.error?.request_id ?? response.headers.get("X-Request-ID"),
        details: envelope?.error?.details,
      });
    }

    return body as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError({
        code: "TIMEOUT",
        message: "The API did not respond in time.",
        status: 0,
      });
    }
    throw new ApiError({
      code: "NETWORK_ERROR",
      message: "Could not reach the API.",
      status: 0,
    });
  } finally {
    clearTimeout(timer);
  }
}

/* --- Health ------------------------------------------------------------- */

export interface HealthResponse {
  status: "ok";
  service: string;
  version: string;
  environment: string;
}

export function fetchHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/health", { cache: "no-store" });
}

/* --- Authentication ----------------------------------------------------- */

export type OrganizationRole = "owner" | "admin" | "member" | "viewer";

export interface AuthUser {
  id: string;
  email: string;
  full_name: string;
  created_at: string;
  last_login_at: string | null;
}

export interface OrganizationMembership {
  id: string;
  name: string;
  slug: string;
  role: OrganizationRole;
}

export interface AuthenticatedSession {
  authenticated: true;
  user: AuthUser;
  organizations: OrganizationMembership[];
  active_organization_id: string | null;
  csrf_token: string;
  expires_at: string;
}

export interface AnonymousSession {
  authenticated: false;
  /**
   * "expired" means a session existed and ended, so the interface can say so
   * instead of showing a bare sign-in form as though nothing happened.
   */
  reason: "anonymous" | "expired";
}

export type SessionState = AuthenticatedSession | AnonymousSession;

/**
 * Capture the CSRF token from any response that carries one.
 *
 * Every endpoint that establishes or refreshes a session returns the current
 * token, so recording it here means the app never depends on being able to
 * read the cookie — which it cannot do across domains.
 */
function withToken<T extends SessionState | AuthenticatedSession>(
  response: T,
): T {
  if ("csrf_token" in response) rememberCsrfToken(response.csrf_token);
  return response;
}

export function fetchSession(): Promise<SessionState> {
  return apiFetch<SessionState>("/api/auth/session", {
    cache: "no-store",
  }).then(withToken);
}

export function loginRequest(input: {
  email: string;
  password: string;
}): Promise<AuthenticatedSession> {
  return apiFetch<AuthenticatedSession>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify(input),
  }).then(withToken);
}

export function registerRequest(input: {
  email: string;
  password: string;
  full_name: string;
  organization_name: string;
}): Promise<AuthenticatedSession> {
  return apiFetch<AuthenticatedSession>("/api/auth/register", {
    method: "POST",
    body: JSON.stringify(input),
  }).then(withToken);
}

export function logoutRequest(): Promise<{ ok: true }> {
  return apiFetch<{ ok: true }>("/api/auth/logout", { method: "POST" }).then(
    (result) => {
      // The session is gone, so the token is too. Keeping it would send a
      // token belonging to a session the server has already revoked.
      rememberCsrfToken(null);
      return result;
    },
  );
}

export function switchOrganizationRequest(
  organizationId: string,
): Promise<AuthenticatedSession> {
  return apiFetch<AuthenticatedSession>("/api/auth/organization", {
    method: "POST",
    body: JSON.stringify({ organization_id: organizationId }),
  }).then(withToken);
}
