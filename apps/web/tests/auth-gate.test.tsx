import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthGate } from "@/components/auth/auth-gate";
import { SessionProvider } from "@/components/auth/session-provider";

import {
  ANONYMOUS,
  EXPIRED,
  HEALTH_OK,
  authenticatedSession,
  stubApi,
  waitForSessionResolved,
} from "./helpers";

function renderGate() {
  return render(
    <SessionProvider>
      <AuthGate>
        <p>protected content</p>
      </AuthGate>
    </SessionProvider>,
  );
}

/**
 * The four session states must be visibly different.
 *
 * Collapsing any pair of them produces a specific, well-known bug — each test
 * below names the one it prevents.
 */
describe("AuthGate", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows a loading state before the session resolves, not a sign-in form", async () => {
    // The classic cookie-auth bug: rendering the sign-in form while the
    // session request is still in flight makes an already-signed-in user see
    // a flash of "signed out" on every reload.
    let resolveSession!: () => void;
    const gate = new Promise<void>((resolve) => {
      resolveSession = resolve;
    });

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        await gate;
        return {
          ok: true,
          status: 200,
          headers: new Headers(),
          json: async () => ANONYMOUS,
        } as Response;
      }),
    );

    renderGate();

    expect(screen.getByTestId("session-loading")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign in" })).not.toBeInTheDocument();

    resolveSession();
    await waitFor(() => {
      expect(screen.queryByTestId("session-loading")).not.toBeInTheDocument();
    });
  });

  it("shows the sign-in screen when anonymous", async () => {
    stubApi({ "/api/auth/session": { body: ANONYMOUS } });

    renderGate();
    await waitForSessionResolved();

    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByText("protected content")).not.toBeInTheDocument();
  });

  it("says the session ended rather than silently showing a sign-in form", async () => {
    stubApi({ "/api/auth/session": { body: EXPIRED } });

    renderGate();
    await waitForSessionResolved();

    expect(screen.getByRole("status")).toHaveTextContent("Your session ended");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
  });

  it("renders the application when authenticated", async () => {
    stubApi({
      "/api/auth/session": { body: authenticatedSession() },
      "/health": HEALTH_OK,
    });

    renderGate();
    await waitForSessionResolved();

    await waitFor(() => {
      expect(screen.getByText("protected content")).toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: "Sign in" })).not.toBeInTheDocument();
  });

  it("distinguishes an unreachable API from being signed out", async () => {
    // Showing a sign-in form during a backend outage blames the user for the
    // outage and offers an action that cannot succeed.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );

    renderGate();
    await waitForSessionResolved();

    expect(screen.getByRole("alert")).toHaveTextContent("Cannot reach RealitySync");
    expect(screen.getByText(/session has not ended/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign in" })).not.toBeInTheDocument();
  });
});
