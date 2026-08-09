import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import OverviewPage from "@/app/page";

const HEALTH = {
  status: "ok",
  service: "RealitySync API",
  version: "0.1.0",
  environment: "test",
};

function stubFetch(ok: boolean) {
  const spy = vi.fn().mockResolvedValue({
    ok,
    status: ok ? 200 : 503,
    headers: new Headers(),
    json: async () =>
      ok ? HEALTH : { error: { code: "SERVICE_UNAVAILABLE", message: "down" } },
  } as Response);
  vi.stubGlobal("fetch", spy);
  return spy;
}

describe("Overview page", () => {
  beforeEach(() => {
    stubFetch(true);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows a loading state before the probe resolves", () => {
    render(<OverviewPage />);

    expect(screen.getByTestId("api-status-loading")).toBeInTheDocument();
  });

  it("renders the API details once connected", async () => {
    render(<OverviewPage />);

    await waitFor(() => {
      expect(screen.getByTestId("api-status-connected")).toBeInTheDocument();
    });
    expect(screen.getByText("0.1.0")).toBeInTheDocument();
  });

  it("shows an actionable error state when the API is unreachable", async () => {
    stubFetch(false);

    render(<OverviewPage />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.getByText("API unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("re-checks connectivity on demand", async () => {
    const spy = stubFetch(true);
    const user = userEvent.setup();

    render(<OverviewPage />);
    await waitFor(() => {
      expect(screen.getByTestId("api-status-connected")).toBeInTheDocument();
    });
    const callsBefore = spy.mock.calls.length;

    await user.click(screen.getByRole("button", { name: "Re-check" }));

    await waitFor(() => {
      expect(spy.mock.calls.length).toBeGreaterThan(callsBefore);
    });
  });

  it("presents an empty state instead of fabricated metrics", async () => {
    render(<OverviewPage />);

    expect(screen.getByText("No sources connected")).toBeInTheDocument();
    // No confidence score, entity count or conflict count may appear before
    // real observations exist.
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });
});
