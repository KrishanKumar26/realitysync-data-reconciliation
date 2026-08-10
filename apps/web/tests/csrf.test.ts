import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch, readCsrfToken } from "@/lib/api";

/**
 * The client half of the CSRF contract.
 *
 * The API rejects a state-changing authenticated request whose header does not
 * match the token on the session row, so if the client stops sending it,
 * every write fails. That makes this worth testing directly rather than only
 * through the components that happen to trigger writes.
 */

function stubFetch() {
  const spy = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    headers: new Headers(),
    json: async () => ({ ok: true }),
  } as Response);
  vi.stubGlobal("fetch", spy);
  return spy;
}

function setCookie(value: string) {
  Object.defineProperty(document, "cookie", {
    value,
    writable: true,
    configurable: true,
  });
}

function headersOf(spy: ReturnType<typeof stubFetch>): Record<string, string> {
  const init = spy.mock.calls[0]?.[1] as RequestInit | undefined;
  return (init?.headers ?? {}) as Record<string, string>;
}

describe("CSRF token handling", () => {
  beforeEach(() => {
    setCookie("");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reads the token from the readable cookie", () => {
    setCookie("rs_csrf=abc123; other=value");

    expect(readCsrfToken()).toBe("abc123");
  });

  it("returns null when the cookie is absent", () => {
    setCookie("other=value");

    expect(readCsrfToken()).toBeNull();
  });

  it("does not confuse a similarly named cookie", () => {
    // "rs_csrf_backup" must not satisfy a request for "rs_csrf".
    setCookie("rs_csrf_backup=wrong; rs_csrf=right");

    expect(readCsrfToken()).toBe("right");
  });

  it("attaches the token to state-changing requests", async () => {
    setCookie("rs_csrf=token-value");
    const spy = stubFetch();

    await apiFetch("/api/auth/logout", { method: "POST" });

    expect(headersOf(spy)["X-CSRF-Token"]).toBe("token-value");
  });

  it("omits the token on safe requests", async () => {
    // A GET is not checked by the API, so sending the token would only spread
    // it into more logs and proxies for no benefit.
    setCookie("rs_csrf=token-value");
    const spy = stubFetch();

    await apiFetch("/api/auth/session");

    expect(headersOf(spy)["X-CSRF-Token"]).toBeUndefined();
  });

  it("sends credentials so the session cookie travels cross-origin", async () => {
    // Without this the browser omits cookies and every request is anonymous.
    const spy = stubFetch();

    await apiFetch("/api/auth/session");

    const init = spy.mock.calls[0]?.[1] as RequestInit;
    expect(init.credentials).toBe("include");
  });

  it("reads the cookie at call time rather than caching it", async () => {
    // Signing out and back in replaces the token; a cached copy would make
    // every write fail until a reload.
    setCookie("rs_csrf=first-token");
    const first = stubFetch();
    await apiFetch("/api/auth/logout", { method: "POST" });
    expect(headersOf(first)["X-CSRF-Token"]).toBe("first-token");

    vi.unstubAllGlobals();
    setCookie("rs_csrf=second-token");
    const second = stubFetch();
    await apiFetch("/api/auth/logout", { method: "POST" });
    expect(headersOf(second)["X-CSRF-Token"]).toBe("second-token");
  });
});
