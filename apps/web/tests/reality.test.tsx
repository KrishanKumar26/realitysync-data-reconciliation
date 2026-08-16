import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import RealityPage from "@/app/reality/page";

import { authenticatedSession, renderWithSession, stubApi } from "./helpers";

/**
 * The Reality page, with the Phase 9 contract.
 *
 * One rule dominates these tests: while the confidence specification is
 * unavailable, the page must never render a number where a score would go.
 * "0%" and "null%" are both worse than saying nothing — a reader has no way to
 * tell a fabricated score from a real one, and this product exists to make
 * that distinction impossible to lose.
 */

const SESSION = { "/api/auth/session": { body: authenticatedSession() } };

const ENTITY = {
  id: "ent-1",
  entity_type: "asset",
  natural_key: "LAPTOP-TEST",
  display_name: null,
  attributes: {},
  observation_count: 2,
  created_at: "2026-08-01T00:00:00Z",
};

const CONFIRMED_STATE = {
  id: "rs-1",
  entity_id: "ent-1",
  attribute: "quantity",
  value: 42,
  value_selected: true,
  confidence: null,
  confidence_available: false,
  status: "confirmed",
  confidence_breakdown: {
    available: false,
    reason: "specification_unavailable",
    blocked_on: "freshness",
  },
  selection_reason:
    "2 sources asserted this value and none asserted a different one.",
  valid_from: "2026-08-03T09:00:00Z",
  calculated_at: "2026-08-10T12:00:00Z",
  algorithm_version: "reality-engine/1.0.0-unspecified",
  supporting_count: 2,
  dissenting_count: 0,
  source_count: 2,
};

const CONTESTED_STATE = {
  ...CONFIRMED_STATE,
  id: "rs-2",
  attribute: "location",
  value: null,
  value_selected: false,
  status: "contested",
  selection_reason:
    "2 competing values were asserted. Choosing between them requires the weighting specification, which is unavailable, so no value has been selected.",
};

const EVIDENCE = [
  {
    observation_id: "obs-1",
    source_id: "src-1",
    stream_id: "stream-1",
    external_id: "id=1",
    role: "supporting",
    weight: "0",
    observed_value: 42,
    event_time: "2026-08-03T09:00:00Z",
    ingested_at: "2026-08-03T09:00:00Z",
    exclusion_reason: null,
  },
  {
    observation_id: "obs-2",
    source_id: "src-1",
    stream_id: "stream-1",
    external_id: "id=1",
    role: "excluded",
    weight: "0",
    observed_value: 7,
    event_time: "2026-08-01T09:00:00Z",
    ingested_at: "2026-08-07T09:00:00Z",
    exclusion_reason: "superseded_by_newer_observation_from_same_source",
  },
];

/**
 * The page auto-selects the first entity, so there is nothing to click — this
 * only waits for the entity picker to appear and hands back a user for the
 * interactions that follow.
 */
async function openEntity(): Promise<ReturnType<typeof userEvent.setup>> {
  const user = userEvent.setup();
  await screen.findByLabelText("Entity");
  return user;
}

describe("Reality page", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows a loading state before the entities arrive", async () => {
    // Rendering the empty state while the request is in flight would tell
    // someone their workspace has no entities when it may be full.
    let resolve!: () => void;
    const gate = new Promise<void>((r) => {
      resolve = r;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/entities")) await gate;
        return {
          ok: true,
          status: 200,
          headers: new Headers(),
          json: async () =>
            url.includes("/api/entities") ? [] : authenticatedSession(),
        } as Response;
      }),
    );

    await renderWithSession(<RealityPage />);

    expect(screen.getByTestId("reality-loading")).toBeInTheDocument();
    expect(screen.queryByText("No entities yet")).not.toBeInTheDocument();

    resolve();
  });

  it("offers guidance rather than an empty screen when nothing is mapped", async () => {
    stubApi({ ...SESSION, "/api/entities": { body: [] } });

    await renderWithSession(<RealityPage />);

    expect(await screen.findByText("No entities yet")).toBeInTheDocument();
  });

  it("surfaces a load failure instead of showing an empty workspace", async () => {
    // An error rendered as "no entities" would suggest the workspace is empty
    // when it may be full.
    stubApi({
      ...SESSION,
      "/api/entities": {
        status: 500,
        body: {
          error: { code: "INTERNAL_ERROR", message: "Database unavailable." },
        },
      },
    });

    await renderWithSession(<RealityPage />);

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("No entities yet")).not.toBeInTheDocument();
  });

  it("never renders a confidence number while the specification is unavailable", async () => {
    // The single most important assertion on this page.
    stubApi({
      ...SESSION,
      "/api/entities": { body: [ENTITY] },
      "/api/entities/ent-1/reality": { body: [CONFIRMED_STATE] },
    });

    await renderWithSession(<RealityPage />);
    await openEntity();

    const row = (await screen.findByText("quantity")).closest("li");
    expect(row).not.toBeNull();

    expect(
      within(row!).getByText(/confidence unavailable/),
    ).toBeInTheDocument();
    expect(within(row!).queryByText(/0%/)).not.toBeInTheDocument();
    expect(within(row!).queryByText(/null/)).not.toBeInTheDocument();
    expect(within(row!).queryByText(/%/)).not.toBeInTheDocument();
  });

  it("shows the selected value for a confirmed state", async () => {
    stubApi({
      ...SESSION,
      "/api/entities": { body: [ENTITY] },
      "/api/entities/ent-1/reality": { body: [CONFIRMED_STATE] },
    });

    await renderWithSession(<RealityPage />);
    await openEntity();

    const row = (await screen.findByText("quantity")).closest("li");
    expect(within(row!).getByText("42")).toBeInTheDocument();
    expect(within(row!).getByText(/confirmed/)).toBeInTheDocument();
  });

  it("says no value was selected rather than rendering null", async () => {
    // "null" in a value box reads as "the value is null", which is a different
    // and false claim.
    stubApi({
      ...SESSION,
      "/api/entities": { body: [ENTITY] },
      "/api/entities/ent-1/reality": { body: [CONTESTED_STATE] },
    });

    await renderWithSession(<RealityPage />);
    await openEntity();

    const row = (await screen.findByText("location")).closest("li");
    expect(within(row!).getByText("No value selected")).toBeInTheDocument();
    expect(within(row!).getByText(/contested/)).toBeInTheDocument();
    expect(within(row!).queryByText("null")).not.toBeInTheDocument();
  });

  it("explains a state through its evidence, with both timestamps", async () => {
    stubApi({
      ...SESSION,
      "/api/entities": { body: [ENTITY] },
      "/api/entities/ent-1/reality": { body: [CONFIRMED_STATE] },
      "/api/entities/ent-1/reality/quantity/evidence": { body: EVIDENCE },
    });

    await renderWithSession(<RealityPage />);
    const user = await openEntity();

    await user.click(await screen.findByText("Evidence"));

    await waitFor(() =>
      expect(screen.getByText("supported")).toBeInTheDocument(),
    );

    // The excluded observation is shown with its reason, not hidden.
    expect(screen.getByText("excluded")).toBeInTheDocument();
    expect(
      screen.getByText(/superseded by newer observation from same source/),
    ).toBeInTheDocument();

    // Both axes, always: the gap between them is what a late arrival looks
    // like, and showing one would hide it.
    expect(screen.getAllByText(/true at .* · learned/).length).toBe(2);
  });

  it("reports an evidence load failure without breaking the page", async () => {
    stubApi({
      ...SESSION,
      "/api/entities": { body: [ENTITY] },
      "/api/entities/ent-1/reality": { body: [CONFIRMED_STATE] },
      "/api/entities/ent-1/reality/quantity/evidence": {
        status: 500,
        body: {
          error: { code: "INTERNAL_ERROR", message: "Evidence unavailable." },
        },
      },
    });

    await renderWithSession(<RealityPage />);
    const user = await openEntity();

    await user.click(await screen.findByText("Evidence"));

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    // The state itself is still readable.
    expect(screen.getByText("quantity")).toBeInTheDocument();
  });

  it("reports what a recalculation could not score", async () => {
    stubApi({
      ...SESSION,
      "/api/entities": { body: [ENTITY] },
      "/api/entities/ent-1/reality": { body: [CONFIRMED_STATE] },
      "/api/entities/ent-1/recalculate": {
        status: 200,
        body: {
          entity_id: "ent-1",
          attributes_considered: 2,
          states_written: 2,
          conflicts_written: 1,
          calculated_at: "2026-08-10T12:00:00Z",
          states_unscored: 2,
          unscored_attributes: [
            { attribute: "quantity", blocked_on: "freshness" },
            { attribute: "location", blocked_on: "freshness" },
          ],
          blocked: true,
          blocked_on: ["freshness"],
          missing_specifications: [
            { name: "freshness", description: "Decay curve and constant." },
          ],
        },
      },
    });

    await renderWithSession(<RealityPage />);
    const user = await openEntity();

    await user.click(
      await screen.findByRole("button", { name: "Recalculate" }),
    );

    // "blocked" must not read as "nothing happened" — states were written.
    expect(
      await screen.findByText("States written without confidence scores"),
    ).toBeInTheDocument();
    expect(screen.getByText(/Blocked on: freshness/)).toBeInTheDocument();
  });

  it("renders states in the order the API returned them", async () => {
    // The API orders by attribute; the page must not reorder or sort again,
    // or two clients could disagree about what the system believes.
    stubApi({
      ...SESSION,
      "/api/entities": { body: [ENTITY] },
      "/api/entities/ent-1/reality": {
        body: [CONFIRMED_STATE, CONTESTED_STATE],
      },
    });

    await renderWithSession(<RealityPage />);
    await openEntity();

    await screen.findByText("quantity");
    const attributes = screen
      .getAllByRole("listitem")
      .map((item) => item.querySelector("span")?.textContent)
      .filter((text) => text === "quantity" || text === "location");

    expect(attributes).toEqual(["quantity", "location"]);
  });
});
