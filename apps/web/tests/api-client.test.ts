import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiFetch, fetchHealth } from "@/lib/api";

const HEALTH_BODY = {
  status: "ok",
  service: "RealitySync API",
  version: "0.1.0",
  environment: "test",
};

function mockFetch(response: Partial<Response> & { json: () => Promise<unknown> }) {
  return vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    headers: new Headers(),
    ...response,
  } as Response);
}

describe("apiFetch", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", mockFetch({ json: async () => HEALTH_BODY }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns the parsed body on success", async () => {
    await expect(fetchHealth()).resolves.toEqual(HEALTH_BODY);
  });

  it("sends credentials so session cookies are included", async () => {
    await fetchHealth();

    const init = vi.mocked(globalThis.fetch).mock.calls[0]?.[1];
    expect(init?.credentials).toBe("include");
  });

  it("attaches a correlation id to every request", async () => {
    await fetchHealth();

    const init = vi.mocked(globalThis.fetch).mock.calls[0]?.[1];
    const headers = init?.headers as Record<string, string>;
    expect(headers["X-Request-ID"]).toMatch(/^req_/);
  });

  it("maps an error envelope onto ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        ok: false,
        status: 503,
        headers: new Headers({ "X-Request-ID": "req_abc" }),
        json: async () => ({
          error: {
            code: "SERVICE_UNAVAILABLE",
            message: "Dependencies unavailable",
            request_id: "req_abc",
          },
        }),
      }),
    );

    await expect(fetchHealth()).rejects.toMatchObject({
      name: "ApiError",
      code: "SERVICE_UNAVAILABLE",
      status: 503,
      requestId: "req_abc",
    });
  });

  it("converts a network failure into a typed error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("failed")));

    await expect(fetchHealth()).rejects.toBeInstanceOf(ApiError);
    await expect(fetchHealth()).rejects.toMatchObject({ code: "NETWORK_ERROR" });
  });

  it("aborts and reports a timeout when the API does not respond", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(
        (_url: string, init: RequestInit) =>
          new Promise((_resolve, reject) => {
            init.signal?.addEventListener("abort", () => {
              reject(new DOMException("Aborted", "AbortError"));
            });
          }),
      ),
    );

    await expect(apiFetch("/health", { timeoutMs: 5 })).rejects.toMatchObject({
      code: "TIMEOUT",
    });
  });
});
