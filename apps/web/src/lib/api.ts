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

  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      // Session cookies are the authentication mechanism from Phase 2 onward.
      credentials: "include",
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        "X-Request-ID": createRequestId(),
        ...headers,
      },
    });

    const body: unknown = await response.json().catch(() => null);

    if (!response.ok) {
      const envelope = body as ApiErrorBody | null;
      throw new ApiError({
        code: envelope?.error?.code ?? "HTTP_ERROR",
        message: envelope?.error?.message ?? `Request failed (${response.status})`,
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
