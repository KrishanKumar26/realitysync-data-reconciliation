import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/shell/app-shell";
import { NAV_ITEMS } from "@/components/shell/nav";

function stubHealth(ok: boolean) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok,
      status: ok ? 200 : 503,
      headers: new Headers(),
      json: async () =>
        ok
          ? { status: "ok", service: "RealitySync API", version: "0.1.0", environment: "test" }
          : { error: { code: "SERVICE_UNAVAILABLE", message: "down" } },
    } as Response),
  );
}

describe("AppShell", () => {
  beforeEach(() => {
    stubHealth(true);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("boots and renders the product name", () => {
    render(
      <AppShell>
        <p>content</p>
      </AppShell>,
    );

    expect(screen.getByText("RealitySync")).toBeInTheDocument();
  });

  it("renders every navigation destination", () => {
    render(
      <AppShell>
        <p>content</p>
      </AppShell>,
    );

    const nav = screen.getByRole("navigation", { name: "Workspace" });
    for (const item of NAV_ITEMS) {
      expect(within(nav).getByRole("link", { name: item.label })).toHaveAttribute(
        "href",
        item.href,
      );
    }
  });

  it("renders its children", () => {
    render(
      <AppShell>
        <p>child content</p>
      </AppShell>,
    );

    expect(screen.getByText("child content")).toBeInTheDocument();
  });

  it("reports API connectivity once the probe resolves", async () => {
    render(
      <AppShell>
        <p>content</p>
      </AppShell>,
    );

    expect(screen.getByText("Checking API…")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("API connected")).toBeInTheDocument();
    });
  });

  it("reports API unavailability rather than claiming a connection", async () => {
    stubHealth(false);

    render(
      <AppShell>
        <p>content</p>
      </AppShell>,
    );

    await waitFor(() => {
      expect(screen.getByText("API unavailable")).toBeInTheDocument();
    });
    expect(screen.queryByText("API connected")).not.toBeInTheDocument();
  });

  it("exposes a skip link for keyboard users", () => {
    render(
      <AppShell>
        <p>content</p>
      </AppShell>,
    );

    expect(screen.getByRole("link", { name: "Skip to content" })).toBeInTheDocument();
  });
});
