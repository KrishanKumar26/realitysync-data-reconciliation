import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthGate } from "@/components/auth/auth-gate";
import { SessionProvider } from "@/components/auth/session-provider";
import { render } from "@testing-library/react";

import {
  ANONYMOUS,
  HEALTH_OK,
  authenticatedSession,
  stubApi,
  waitForSessionResolved,
} from "./helpers";

function renderApp() {
  return render(
    <SessionProvider>
      <AuthGate>
        <p>protected content</p>
      </AuthGate>
    </SessionProvider>,
  );
}

describe("session lifecycle", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("signs in and reveals the application", async () => {
    const user = userEvent.setup();
    stubApi({
      "/api/auth/session": { body: ANONYMOUS },
      "/api/auth/login": { body: authenticatedSession() },
      "/health": HEALTH_OK,
    });

    renderApp();
    await waitForSessionResolved();

    expect(screen.queryByText("protected content")).not.toBeInTheDocument();

    await user.type(screen.getByLabelText("Email"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "correct-horse-battery");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => {
      expect(screen.getByText("protected content")).toBeInTheDocument();
    });
    expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();
  });

  it("signs out and returns to the sign-in screen", async () => {
    const user = userEvent.setup();
    const { calls } = stubApi({
      "/api/auth/session": { body: authenticatedSession() },
      "/api/auth/logout": { body: { ok: true } },
      "/health": HEALTH_OK,
    });

    renderApp();
    await waitForSessionResolved();
    await waitFor(() => {
      expect(screen.getByText("protected content")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Sign out" }));

    await waitFor(() => {
      expect(screen.queryByText("protected content")).not.toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();

    const logout = calls.find((call) => call.url.endsWith("/api/auth/logout"));
    expect(logout?.method).toBe("POST");
  });

  it("signs the interface out even when the logout call fails", async () => {
    // The session is either already revoked or the server is unreachable.
    // Leaving the interface looking signed in would be the worse outcome.
    const user = userEvent.setup();
    stubApi({
      "/api/auth/session": { body: authenticatedSession() },
      "/api/auth/logout": {
        status: 500,
        body: { error: { code: "INTERNAL_ERROR", message: "boom" } },
      },
      "/health": HEALTH_OK,
    });

    renderApp();
    await waitForSessionResolved();
    await waitFor(() => {
      expect(screen.getByText("protected content")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Sign out" }));

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Sign in" }),
      ).toBeInTheDocument();
    });
  });

  it("asks the API who is signed in exactly once on mount", async () => {
    // A provider that refetches on every render turns one page load into a
    // request storm.
    const { calls } = stubApi({
      "/api/auth/session": { body: authenticatedSession() },
      "/health": HEALTH_OK,
    });

    renderApp();
    await waitForSessionResolved();
    await waitFor(() => {
      expect(screen.getByText("protected content")).toBeInTheDocument();
    });

    const sessionCalls = calls.filter((call) =>
      call.url.endsWith("/api/auth/session"),
    );
    expect(sessionCalls).toHaveLength(1);
  });
});
