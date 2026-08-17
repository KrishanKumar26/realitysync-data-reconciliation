import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/shell/app-shell";
import { NAV_ITEMS } from "@/components/shell/nav";

import {
  HEALTH_OK,
  authenticatedSession,
  renderWithSession,
  stubApi,
} from "./helpers";

/**
 * The shell renders only for an authenticated session, so every test here
 * wraps it in a provider backed by a resolved session — the same condition
 * under which it appears in the product.
 */
function shellRoutes(healthOk = true) {
  return {
    "/api/auth/session": { body: authenticatedSession() },
    "/health": healthOk
      ? HEALTH_OK
      : {
          status: 503,
          body: { error: { code: "SERVICE_UNAVAILABLE", message: "down" } },
        },
  };
}

describe("AppShell", () => {
  beforeEach(() => {
    stubApi(shellRoutes());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("boots and renders the product name", async () => {
    await renderWithSession(
      <AppShell>
        <p>content</p>
      </AppShell>,
    );

    expect(screen.getByText("RealitySync")).toBeInTheDocument();
  });

  it("renders every navigation destination", async () => {
    await renderWithSession(
      <AppShell>
        <p>content</p>
      </AppShell>,
    );

    const nav = screen.getByRole("navigation", { name: "Workspace" });
    for (const item of NAV_ITEMS) {
      expect(
        within(nav).getByRole("link", { name: item.label }),
      ).toHaveAttribute("href", item.href);
    }
  });

  it("renders its children", async () => {
    await renderWithSession(
      <AppShell>
        <p>child content</p>
      </AppShell>,
    );

    expect(screen.getByText("child content")).toBeInTheDocument();
  });

  it("shows the signed-in user and their workspace", async () => {
    await renderWithSession(
      <AppShell>
        <p>content</p>
      </AppShell>,
    );

    expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();
    expect(screen.getByText("ada@example.com")).toBeInTheDocument();
    expect(screen.getByText("Northwind Logistics")).toBeInTheDocument();
  });

  it("reports API connectivity once the probe resolves", async () => {
    await renderWithSession(
      <AppShell>
        <p>content</p>
      </AppShell>,
    );

    await waitFor(() => {
      expect(screen.getByText("API connected")).toBeInTheDocument();
    });
  });

  it("reports API unavailability rather than claiming a connection", async () => {
    vi.unstubAllGlobals();
    stubApi(shellRoutes(false));

    await renderWithSession(
      <AppShell>
        <p>content</p>
      </AppShell>,
    );

    await waitFor(() => {
      expect(screen.getByText("API unavailable")).toBeInTheDocument();
    });
    expect(screen.queryByText("API connected")).not.toBeInTheDocument();
  });

  it("exposes a skip link for keyboard users", async () => {
    await renderWithSession(
      <AppShell>
        <p>content</p>
      </AppShell>,
    );

    expect(
      screen.getByRole("link", { name: "Skip to content" }),
    ).toBeInTheDocument();
  });
});
