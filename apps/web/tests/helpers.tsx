import {
  render,
  screen,
  waitFor,
  waitForElementToBeRemoved,
} from "@testing-library/react";
import type { ReactElement } from "react";
import { expect, vi } from "vitest";

import { SessionProvider, useSession } from "@/components/auth/session-provider";
import type {
  AuthenticatedSession,
  OrganizationMembership,
  SessionState,
} from "@/lib/api";

/**
 * A fetch stub that routes by path.
 *
 * The components under test now talk to several endpoints, so a single
 * blanket mock would make it impossible to tell which call a test is really
 * exercising. Routes are matched by path suffix; an unmatched path fails
 * loudly rather than returning a plausible empty body, because a silent
 * default is how a test ends up passing against an endpoint that was never
 * called.
 */
export interface StubRoute {
  status?: number;
  body: unknown;
}

export interface RecordedCall {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: unknown;
}

export function stubApi(routes: Record<string, StubRoute | (() => StubRoute)>): {
  calls: RecordedCall[];
} {
  const calls: RecordedCall[] = [];

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();

      const headers: Record<string, string> = {};
      const rawHeaders = init?.headers;
      if (rawHeaders && typeof rawHeaders === "object" && !Array.isArray(rawHeaders)) {
        for (const [key, value] of Object.entries(rawHeaders)) {
          headers[key.toLowerCase()] = String(value);
        }
      }

      calls.push({
        url,
        method,
        headers,
        body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
      });

      const key = Object.keys(routes).find((path) => url.endsWith(path));
      if (key === undefined) {
        throw new Error(`No stub registered for ${method} ${url}`);
      }

      const route = routes[key];
      const resolved = typeof route === "function" ? route() : route;
      const status = resolved?.status ?? 200;

      return {
        ok: status >= 200 && status < 300,
        status,
        headers: new Headers(),
        json: async () => resolved?.body,
      } as Response;
    }),
  );

  return { calls };
}

export const HEALTH_OK = {
  body: {
    status: "ok",
    service: "RealitySync API",
    version: "0.1.0",
    environment: "test",
  },
};

export function organization(
  overrides: Partial<OrganizationMembership> = {},
): OrganizationMembership {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    name: "Northwind Logistics",
    slug: "northwind-logistics",
    role: "owner",
    ...overrides,
  };
}

/**
 * A session payload shaped exactly like the API's.
 *
 * Not product data: these are the credentials-and-identity fields the auth
 * shell renders, and the values are obviously synthetic. No metric, entity or
 * reality state is fabricated anywhere in these tests.
 */
export function authenticatedSession(
  overrides: Partial<AuthenticatedSession> = {},
): AuthenticatedSession {
  const organizations = overrides.organizations ?? [organization()];
  return {
    authenticated: true,
    user: {
      id: "99999999-9999-9999-9999-999999999999",
      email: "ada@example.com",
      full_name: "Ada Lovelace",
      created_at: "2026-01-01T00:00:00Z",
      last_login_at: null,
    },
    organizations,
    active_organization_id: organizations[0]?.id ?? null,
    csrf_token: "csrf-token-value",
    expires_at: "2026-12-31T00:00:00Z",
    ...overrides,
  };
}

export const ANONYMOUS: SessionState = { authenticated: false, reason: "anonymous" };
export const EXPIRED: SessionState = { authenticated: false, reason: "expired" };

/**
 * Reports the provider's status into the DOM.
 *
 * Needed because most components under test have no loading state of their
 * own to wait on — without a marker, a test would assert against the provider's
 * initial "loading" render and see an empty component.
 */
function SessionProbe() {
  const { status } = useSession();
  return (
    <span data-testid="session-status" hidden>
      {status.kind}
    </span>
  );
}

/** Render inside a SessionProvider and wait for the session to resolve. */
export async function renderWithSession(ui: ReactElement) {
  const result = render(
    <SessionProvider>
      <SessionProbe />
      {ui}
    </SessionProvider>,
  );

  await waitFor(() => {
    expect(screen.getByTestId("session-status")).not.toHaveTextContent("loading");
  });
  return result;
}

/** Wait until an AuthGate has replaced its loading state. */
export async function waitForSessionResolved() {
  const loading = screen.queryByTestId("session-loading");
  if (loading) await waitForElementToBeRemoved(loading);
}
