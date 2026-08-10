import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import ConflictsPage from "@/app/conflicts/page";
import TimelinePage from "@/app/timeline/page";

import { authenticatedSession, renderWithSession, stubApi } from "./helpers";

const SESSION = { "/api/auth/session": { body: authenticatedSession() } };

const UNGRADED_CONFLICT = {
  id: "conflict-1",
  entity_id: "entity-1",
  entity_natural_key: "LAPTOP-001",
  reality_state_id: null,
  attribute: "quantity",
  conflict_type: "value_conflict",
  // The confidence specification is missing, so this was detected but not graded.
  severity: "unspecified",
  status: "open",
  score: null,
  summary: "2 distinct values for 'quantity'.",
  details: {
    divergence: "15",
    competing_values: [
      { value: 42, weight: "0", share: "0", sources: ["s1"], observation_count: 1 },
      { value: 57, weight: "0", share: "0", sources: ["s2"], observation_count: 1 },
    ],
  },
  detected_at: "2026-08-10T10:00:00Z",
  last_seen_at: "2026-08-10T10:00:00Z",
  resolved_at: null,
  resolution_note: null,
};

const ENTITY = {
  id: "entity-1",
  entity_type: "asset",
  natural_key: "LAPTOP-001",
  display_name: null,
  mapping_count: 2,
  observation_count: 2,
  created_at: "2026-08-01T00:00:00Z",
};

const TIMELINE = {
  axis: "event",
  as_of_event_time: null,
  as_of_knowledge_time: null,
  event_count: 2,
  late_arrival_count: 1,
  truncated: false,
  events: [
    {
      observation_id: "obs-2",
      external_id: "id=1",
      source_id: "s2",
      source_name: "ERP",
      values: { quantity: 57 },
      event_time: "2026-08-04T09:00:00Z",
      ingested_at: "2026-08-04T09:00:00Z",
      event_time_semantics: "observed",
      arrived_late: false,
      lag_seconds: 0,
    },
    {
      observation_id: "obs-1",
      external_id: "id=1",
      source_id: "s1",
      source_name: "Warehouse",
      values: { quantity: 42 },
      event_time: "2026-08-03T09:00:00Z",
      ingested_at: "2026-08-07T09:00:00Z",
      event_time_semantics: "observed",
      arrived_late: true,
      lag_seconds: 4 * 24 * 3600,
    },
  ],
};

describe("Conflicts page", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows an empty state until sources actually disagree", async () => {
    stubApi({ ...SESSION, "/api/conflicts": { body: [] } });

    await renderWithSession(<ConflictsPage />);

    expect(await screen.findByText("No open conflicts")).toBeInTheDocument();
  });

  it("renders the facts a detected conflict carries", async () => {
    stubApi({ ...SESSION, "/api/conflicts": { body: [UNGRADED_CONFLICT] } });

    await renderWithSession(<ConflictsPage />);

    expect(await screen.findByText("Value conflict")).toBeInTheDocument();
    expect(screen.getByText("LAPTOP-001")).toBeInTheDocument();
    expect(screen.getByText("15")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("57")).toBeInTheDocument();
  });

  it("says a conflict is not graded rather than showing it as low severity", async () => {
    // The distinction the whole product turns on. Rendering an ungraded
    // conflict as "low" would claim an assessment nobody made.
    stubApi({ ...SESSION, "/api/conflicts": { body: [UNGRADED_CONFLICT] } });

    await renderWithSession(<ConflictsPage />);

    expect(await screen.findByText("Not graded")).toBeInTheDocument();
    expect(screen.getByText("Not scored")).toBeInTheDocument();
    expect(screen.queryByText("low")).not.toBeInTheDocument();
  });

  it("explains why grading is unavailable", async () => {
    stubApi({ ...SESSION, "/api/conflicts": { body: [UNGRADED_CONFLICT] } });

    await renderWithSession(<ConflictsPage />);

    expect(
      await screen.findByText(/requires the confidence specification/i),
    ).toBeInTheDocument();
  });

  it("shows a graded conflict's severity and score when available", async () => {
    stubApi({
      ...SESSION,
      "/api/conflicts": {
        body: [{ ...UNGRADED_CONFLICT, severity: "high", score: "0.594" }],
      },
    });

    await renderWithSession(<ConflictsPage />);

    expect(await screen.findByText("high")).toBeInTheDocument();
    expect(screen.getByText("0.594")).toBeInTheDocument();
    expect(screen.queryByText("Not graded")).not.toBeInTheDocument();
  });

  it("moves a conflict through its lifecycle", async () => {
    const user = userEvent.setup();
    const { calls } = stubApi({
      ...SESSION,
      "/api/conflicts": { body: [UNGRADED_CONFLICT] },
      "/api/conflicts/conflict-1": { body: { ...UNGRADED_CONFLICT, status: "resolved" } },
    });

    await renderWithSession(<ConflictsPage />);
    await user.click(await screen.findByRole("button", { name: "Mark resolved" }));

    await waitFor(() => {
      const patch = calls.find((c) => c.method === "PATCH");
      expect(patch?.body).toEqual({ status: "resolved" });
    });
  });

  it("offers no lifecycle actions on an already-resolved conflict", async () => {
    stubApi({
      ...SESSION,
      "/api/conflicts": {
        body: [{ ...UNGRADED_CONFLICT, status: "resolved", resolved_at: "2026-08-10T11:00:00Z" }],
      },
    });

    await renderWithSession(<ConflictsPage />);

    await screen.findByText("Value conflict");
    expect(screen.queryByRole("button", { name: "Mark resolved" })).not.toBeInTheDocument();
  });

  it("filters by status", async () => {
    const user = userEvent.setup();
    const { calls } = stubApi({ ...SESSION, "/api/conflicts": { body: [] } });

    await renderWithSession(<ConflictsPage />);
    await user.click(screen.getByRole("button", { name: "Resolved" }));

    await waitFor(() => {
      expect(calls.some((c) => c.url.includes("status=resolved"))).toBe(true);
    });
  });
});

describe("Timeline page", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("explains what a timeline needs before one exists", async () => {
    stubApi({ ...SESSION, "/api/entities": { body: [] } });

    await renderWithSession(<TimelinePage />);

    expect(await screen.findByText("No entities yet")).toBeInTheDocument();
  });

  it("renders both time axes for every observation", async () => {
    // The two axes are the feature. Showing only one would make a
    // late-arriving correction indistinguishable from a fresh reading.
    stubApi({
      ...SESSION,
      "/api/entities": { body: [ENTITY] },
      "/timeline": { body: TIMELINE },
    });

    await renderWithSession(<TimelinePage />);

    await screen.findByText("Warehouse");
    expect(screen.getAllByText("true at").length).toBe(2);
    expect(screen.getAllByText("learned at").length).toBe(2);
  });

  it("flags a late arrival rather than leaving it to be inferred", async () => {
    stubApi({
      ...SESSION,
      "/api/entities": { body: [ENTITY] },
      "/timeline": { body: TIMELINE },
    });

    await renderWithSession(<TimelinePage />);

    expect(await screen.findByText("arrived 4d late")).toBeInTheDocument();
    expect(
      screen.getByText(/1 arrived after the fact was true/),
    ).toBeInTheDocument();
  });

  it("switches between what was true and what we knew", async () => {
    const user = userEvent.setup();
    const { calls } = stubApi({
      ...SESSION,
      "/api/entities": { body: [ENTITY] },
      "/timeline": { body: TIMELINE },
    });

    await renderWithSession(<TimelinePage />);
    await screen.findByText("Warehouse");

    await user.click(screen.getByRole("button", { name: "What we knew" }));

    await waitFor(() => {
      expect(calls.some((c) => c.url.includes("axis=knowledge"))).toBe(true);
    });
  });

  it("says both views agree when nothing arrived late", async () => {
    stubApi({
      ...SESSION,
      "/api/entities": { body: [ENTITY] },
      "/timeline": {
        body: {
          ...TIMELINE,
          late_arrival_count: 0,
          events: [{ ...TIMELINE.events[0] }],
        },
      },
    });

    await renderWithSession(<TimelinePage />);

    expect(await screen.findByText(/both views agree/)).toBeInTheDocument();
  });

  it("reports truncation instead of implying a complete history", async () => {
    stubApi({
      ...SESSION,
      "/api/entities": { body: [ENTITY] },
      "/timeline": { body: { ...TIMELINE, truncated: true } },
    });

    await renderWithSession(<TimelinePage />);

    expect(await screen.findByText(/More exist/)).toBeInTheDocument();
  });

  it("surfaces a load failure rather than an empty timeline", async () => {
    stubApi({
      ...SESSION,
      "/api/entities": { body: [ENTITY] },
      "/timeline": {
        status: 500,
        body: { error: { code: "INTERNAL_ERROR", message: "Database unavailable." } },
      },
    });

    await renderWithSession(<TimelinePage />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load the timeline",
    );
  });

  it("lists each entity with its real observation count", async () => {
    stubApi({
      ...SESSION,
      "/api/entities": { body: [ENTITY] },
      "/timeline": { body: TIMELINE },
    });

    await renderWithSession(<TimelinePage />);

    const select = await screen.findByLabelText("Entity");
    expect(within(select).getByRole("option")).toHaveTextContent("LAPTOP-001 (2)");
  });
});
