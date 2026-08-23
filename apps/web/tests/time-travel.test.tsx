import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TimeTravel } from "@/components/reality/time-travel";

import { authenticatedSession, renderWithSession, stubApi } from "./helpers";

const SESSION = { "/api/auth/session": { body: authenticatedSession() } };

const PAST = {
  entity_id: "ent-1",
  known_at: "2026-08-15T10:00:00Z",
  observations_known: 1,
  observations_since: 1,
  attributes: [
    {
      attribute: "quantity",
      status: "confirmed",
      value: 42,
      value_selected: true,
      confidence: null,
      confidence_available: false,
      selection_reason:
        "1 source reported this value and none reported anything different.",
      supporting_count: 1,
      dissenting_count: 0,
      source_count: 1,
      candidate_count: 1,
    },
  ],
};

describe("TimeTravel", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("asks the API for the moment the user picked", async () => {
    const user = userEvent.setup({ delay: null });
    const { calls } = stubApi({
      ...SESSION,
      "/api/entities/ent-1/reality/as-of": { body: PAST },
    });

    await renderWithSession(<TimeTravel entityId="ent-1" />);

    await user.click(await screen.findByRole("button", { name: "Show me" }));

    const call = await waitFor(() => {
      const found = calls.find((c) => c.url.includes("/reality/as-of"));
      expect(found).toBeDefined();
      return found!;
    });
    // A GET: a past view must never write.
    expect(call.method).toBe("GET");
    expect(call.url).toContain("at=");
  });

  it("explains that later records are why the answer moved", async () => {
    // The number that makes this feature worth having.
    const user = userEvent.setup({ delay: null });
    stubApi({
      ...SESSION,
      "/api/entities/ent-1/reality/as-of": { body: PAST },
    });

    await renderWithSession(<TimeTravel entityId="ent-1" />);
    await user.click(await screen.findByRole("button", { name: "Show me" }));

    expect(
      await screen.findByText(/more have arrived since/),
    ).toBeInTheDocument();
    expect(await screen.findByText("quantity")).toBeInTheDocument();
  });

  it("says plainly when nothing has arrived since", async () => {
    const user = userEvent.setup({ delay: null });
    stubApi({
      ...SESSION,
      "/api/entities/ent-1/reality/as-of": {
        body: { ...PAST, observations_since: 0 },
      },
    });

    await renderWithSession(<TimeTravel entityId="ent-1" />);
    await user.click(await screen.findByRole("button", { name: "Show me" }));

    expect(
      await screen.findByText(
        /Nothing has arrived since, so this matches today/,
      ),
    ).toBeInTheDocument();
  });

  it("says nothing was known rather than showing an empty box", async () => {
    const user = userEvent.setup({ delay: null });
    stubApi({
      ...SESSION,
      "/api/entities/ent-1/reality/as-of": {
        body: {
          ...PAST,
          observations_known: 0,
          observations_since: 2,
          attributes: [],
        },
      },
    });

    await renderWithSession(<TimeTravel entityId="ent-1" />);
    await user.click(await screen.findByRole("button", { name: "Show me" }));

    expect(
      await screen.findByText(/Nothing had reached us by then/),
    ).toBeInTheDocument();
  });

  it("surfaces a failure instead of showing a stale answer", async () => {
    const user = userEvent.setup({ delay: null });
    stubApi({
      ...SESSION,
      "/api/entities/ent-1/reality/as-of": {
        status: 422,
        body: {
          error: {
            code: "VALIDATION_ERROR",
            message: "Provide a time zone, for example 2026-08-15T10:00:00Z.",
          },
        },
      },
    });

    await renderWithSession(<TimeTravel entityId="ent-1" />);
    await user.click(await screen.findByRole("button", { name: "Show me" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Provide a time zone",
    );
  });
});
