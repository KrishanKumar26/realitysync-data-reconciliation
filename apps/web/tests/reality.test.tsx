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

/**
 * Creating an entity and binding source rows to it.
 *
 * The gap this closes: the Reality page told you to "create an entity and map
 * a synced table to it" and gave you no way to do either. The API had both
 * endpoints from the start; the interface never called them, so a workspace
 * could connect a source, sync real rows, and then stop — every downstream
 * feature depends on an entity existing.
 */
describe("Entity setup", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("offers a way to create one when there are no entities", async () => {
    // The empty state used to describe the action without providing it.
    stubApi({ ...SESSION, "/api/entities": { body: [] } });

    await renderWithSession(<RealityPage />);

    expect(await screen.findByText("No entities yet")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "New entity" }),
    ).toBeInTheDocument();
  });

  it("creates an entity through the real endpoint", async () => {
    const user = userEvent.setup();
    const { calls } = stubApi({
      ...SESSION,
      "/api/entities": { body: [] },
    });

    await renderWithSession(<RealityPage />);
    await user.click(await screen.findByRole("button", { name: "New entity" }));

    await user.type(screen.getByLabelText("Natural key"), "LAPTOP-13");
    await user.click(screen.getByRole("button", { name: "Create entity" }));

    await waitFor(() => {
      const request = calls.find(
        (call) => call.method === "POST" && call.url.endsWith("/api/entities"),
      );
      expect(request).toBeDefined();
      const body = request?.body as Record<string, unknown>;
      expect(body.natural_key).toBe("LAPTOP-13");
      expect(body.entity_type).toBe("sku");
    });
  });

  it("shows how many source rows the selected entity is bound to", async () => {
    stubApi({
      ...SESSION,
      "/api/entities": { body: [ENTITY] },
      "/api/entities/ent-1/reality": { body: [CONFIRMED_STATE] },
      "/api/entities/ent-1/mappings": { body: [] },
    });

    await renderWithSession(<RealityPage />);
    await openEntity();

    // Two, not one: a single source cannot disagree with anything, so the
    // count is the thing worth saying out loud.
    expect(
      await screen.findByText(/Map at least two sources/),
    ).toBeInTheDocument();
  });

  it("offers observed row ids rather than asking anyone to type one", async () => {
    // The external id format is an internal convention. Typed freehand, a typo
    // produces a mapping that matches nothing and reports no error at all.
    const user = userEvent.setup();
    stubApi({
      ...SESSION,
      "/api/entities": { body: [ENTITY] },
      "/api/entities/ent-1/reality": { body: [CONFIRMED_STATE] },
      "/api/entities/ent-1/mappings": { body: [] },
      "/api/data-sources": {
        body: [{ id: "src-1", name: "Warehouse", kind: "postgresql" }],
      },
      "/api/data-sources/src-1/streams": {
        body: [{ id: "stream-1", qualified_name: "public.wms_inventory" }],
      },
      "/api/data-sources/src-1/observations": {
        body: [
          { external_id: "sku_id=1" },
          { external_id: "sku_id=2" },
          { external_id: "sku_id=1" },
        ],
      },
    });

    await renderWithSession(<RealityPage />);
    await openEntity();

    await user.click(
      await screen.findByRole("button", { name: "Map a source row" }),
    );

    // Deduplicated: the same row observed twice is still one row.
    await waitFor(() =>
      expect(
        screen.getByRole("option", { name: "sku_id=1" }),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByRole("option", { name: "sku_id=2" }),
    ).toBeInTheDocument();
  });

  it("says what to do when a source has nothing to map yet", async () => {
    const user = userEvent.setup();
    stubApi({
      ...SESSION,
      "/api/entities": { body: [ENTITY] },
      "/api/entities/ent-1/reality": { body: [CONFIRMED_STATE] },
      "/api/entities/ent-1/mappings": { body: [] },
      "/api/data-sources": {
        body: [{ id: "src-1", name: "Warehouse", kind: "postgresql" }],
      },
      "/api/data-sources/src-1/streams": { body: [] },
      "/api/data-sources/src-1/observations": { body: [] },
    });

    await renderWithSession(<RealityPage />);
    await openEntity();
    await user.click(
      await screen.findByRole("button", { name: "Map a source row" }),
    );

    expect(await screen.findByText(/Sync it first/)).toBeInTheDocument();
  });
});
