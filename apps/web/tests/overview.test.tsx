import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import OverviewPage from "@/app/page";
import RealityPage from "@/app/reality/page";

import { authenticatedSession, renderWithSession, stubApi } from "./helpers";

const SESSION = { "/api/auth/session": { body: authenticatedSession() } };

const EMPTY_DASHBOARD = {
  organization_id: "org-1",
  generated_at: "2026-08-11T12:00:00Z",
  window_days: 7,
  is_empty: true,
  sources: {
    total: 0,
    connected: 0,
    never_tested: 0,
    errored: 0,
    disabled: 0,
    sources: [],
  },
  ingestion: {
    observation_count: 0,
    observations_in_window: 0,
    entity_count: 0,
    mapped_entity_count: 0,
    unmapped_entity_count: 0,
    stream_count: 0,
    enabled_stream_count: 0,
    last_sync_at: null,
    syncs_in_window: 0,
    failed_syncs_in_window: 0,
  },
  conflicts: {
    open: 0,
    acknowledged: 0,
    resolved: 0,
    dismissed: 0,
    outstanding: 0,
    by_severity: {},
    ungraded: 0,
    newest_open_at: null,
  },
  confidence: {
    available: false,
    scored_state_count: 0,
    unscored_attribute_count: 0,
    average_confidence: null,
    lowest_confidence: null,
    highest_confidence: null,
    algorithm_version: "reality-engine/1.0.0-unspecified",
    blocked_reason:
      "The Reality Engine cannot produce a confidence score: the approved confidence specification is unavailable, so no score is shown rather than an invented one.",
    missing_specifications: [
      {
        name: "freshness",
        description: "Decay curve mapping observation age to 0..1.",
      },
      {
        name: "conflict_score",
        description: "Formula producing the 0..1 conflict score.",
      },
    ],
  },
  activity: [],
};

const ACTIVE_DASHBOARD = {
  ...EMPTY_DASHBOARD,
  is_empty: false,
  sources: {
    total: 3,
    connected: 1,
    never_tested: 1,
    errored: 1,
    disabled: 0,
    sources: [
      {
        source_id: "s1",
        name: "Warehouse",
        kind: "postgresql",
        status: "connected",
        stream_count: 2,
        observation_count: 120,
        last_connected_at: "2026-08-11T10:00:00Z",
        last_synced_at: "2026-08-11T10:05:00Z",
        last_error: null,
        last_error_at: null,
        never_tested: false,
      },
      {
        source_id: "s2",
        name: "ERP",
        kind: "postgresql",
        status: "error",
        stream_count: 1,
        observation_count: 4,
        last_connected_at: null,
        last_synced_at: null,
        last_error: "The database refused the connection.",
        last_error_at: "2026-08-11T09:00:00Z",
        never_tested: false,
      },
      {
        source_id: "s3",
        name: "Billing",
        kind: "postgresql",
        status: "configured",
        stream_count: 0,
        observation_count: 0,
        last_connected_at: null,
        last_synced_at: null,
        last_error: null,
        last_error_at: null,
        never_tested: true,
      },
    ],
  },
  ingestion: {
    ...EMPTY_DASHBOARD.ingestion,
    observation_count: 124,
    observations_in_window: 30,
    entity_count: 5,
    mapped_entity_count: 3,
    unmapped_entity_count: 2,
    stream_count: 3,
    enabled_stream_count: 2,
    last_sync_at: "2026-08-11T10:05:00Z",
    syncs_in_window: 6,
    failed_syncs_in_window: 1,
  },
  conflicts: {
    ...EMPTY_DASHBOARD.conflicts,
    open: 4,
    acknowledged: 1,
    resolved: 2,
    outstanding: 5,
    by_severity: {},
    ungraded: 5,
  },
  activity: [
    {
      kind: "sync",
      occurred_at: "2026-08-11T10:05:00Z",
      summary: "Ingested 12 new observations",
      detail: null,
      resource_type: "sync_run",
      resource_id: "r1",
      severity: null,
    },
    {
      kind: "sync",
      occurred_at: "2026-08-11T09:00:00Z",
      summary: "A sync failed",
      detail: "The database refused the connection.",
      resource_type: "sync_run",
      resource_id: "r2",
      severity: "error",
    },
  ],
};

describe("Overview", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows a loading state before the dashboard resolves", async () => {
    // Rendering the empty state while the request is still in flight would
    // tell someone their workspace is unconnected when it may not be.
    let resolve!: () => void;
    const gate = new Promise<void>((r) => {
      resolve = r;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input).includes("/api/dashboard")) await gate;
        return {
          ok: true,
          status: 200,
          headers: new Headers(),
          json: async () =>
            String(input).includes("/api/dashboard")
              ? EMPTY_DASHBOARD
              : authenticatedSession(),
        } as Response;
      }),
    );

    await renderWithSession(<OverviewPage />);

    expect(screen.getByTestId("overview-loading")).toBeInTheDocument();
    expect(screen.queryByText("Nothing connected yet")).not.toBeInTheDocument();

    resolve();
    expect(
      await screen.findByText("Nothing connected yet"),
    ).toBeInTheDocument();
  });

  it("offers onboarding when nothing is connected", async () => {
    stubApi({ ...SESSION, "/api/dashboard": { body: EMPTY_DASHBOARD } });

    await renderWithSession(<OverviewPage />);

    expect(
      await screen.findByText("Nothing connected yet"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Connect a source" }),
    ).toHaveAttribute("href", "/sources");
  });

  it("says confidence is unavailable rather than showing zero", async () => {
    // The line Phase 6 turns on. A gauge at 0% would claim we are certain of
    // nothing; the truth is nobody has told us how to measure.
    stubApi({ ...SESSION, "/api/dashboard": { body: ACTIVE_DASHBOARD } });

    await renderWithSession(<OverviewPage />);

    expect(await screen.findByText("Confidence")).toBeInTheDocument();
    expect(screen.getByText(/Not available/)).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.queryByText("0%")).not.toBeInTheDocument();
    expect(screen.queryByText("0.0%")).not.toBeInTheDocument();
  });

  it("explains what is blocking confidence", async () => {
    stubApi({ ...SESSION, "/api/dashboard": { body: ACTIVE_DASHBOARD } });

    await renderWithSession(<OverviewPage />);

    expect(
      await screen.findByText(
        /approved confidence specification is unavailable/i,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("2 specifications required")).toBeInTheDocument();
  });

  it("shows a real average when confidence becomes available", async () => {
    stubApi({
      ...SESSION,
      "/api/dashboard": {
        body: {
          ...ACTIVE_DASHBOARD,
          confidence: {
            ...ACTIVE_DASHBOARD.confidence,
            available: true,
            scored_state_count: 12,
            average_confidence: 71.0,
            lowest_confidence: 42.5,
            highest_confidence: 96.2,
            blocked_reason: null,
            missing_specifications: [],
          },
        },
      },
    });

    await renderWithSession(<OverviewPage />);

    expect(await screen.findByText("71.0%")).toBeInTheDocument();
    expect(screen.getByText("42.5%")).toBeInTheDocument();
    expect(screen.queryByText(/Not available/)).not.toBeInTheDocument();
  });

  it("renders real source health counts", async () => {
    stubApi({ ...SESSION, "/api/dashboard": { body: ACTIVE_DASHBOARD } });

    await renderWithSession(<OverviewPage />);

    const panel = (
      await screen.findByRole("heading", { name: "Sources" })
    ).closest("section")!;
    expect(within(panel).getByText("Warehouse")).toBeInTheDocument();
    expect(
      within(panel).getByText("The database refused the connection."),
    ).toBeInTheDocument();
  });

  it("distinguishes never-tested from failing", async () => {
    // "We have not checked" is not "it is broken".
    stubApi({ ...SESSION, "/api/dashboard": { body: ACTIVE_DASHBOARD } });

    await renderWithSession(<OverviewPage />);

    // Appears twice, and that agreement is the point: once as the fleet stat
    // and once as the badge on the source itself.
    expect((await screen.findAllByText("Not yet tested")).length).toBe(2);
    expect(screen.getByText("Failing")).toBeInTheDocument();
    expect(
      screen.getByText(/credentials stored, connection unproven/i),
    ).toBeInTheDocument();
    // And it is not lumped in with the failing count.
    expect(screen.getByText("Connection failed")).toBeInTheDocument();
  });

  it("reports ungraded conflicts separately from graded ones", async () => {
    stubApi({ ...SESSION, "/api/dashboard": { body: ACTIVE_DASHBOARD } });

    await renderWithSession(<OverviewPage />);

    expect(await screen.findByText("Not graded")).toBeInTheDocument();
    expect(
      screen.getByText(/severity needs the confidence specification/i),
    ).toBeInTheDocument();
  });

  it("renders the real activity feed and flags failures", async () => {
    stubApi({ ...SESSION, "/api/dashboard": { body: ACTIVE_DASHBOARD } });

    await renderWithSession(<OverviewPage />);

    expect(
      await screen.findByText("Ingested 12 new observations"),
    ).toBeInTheDocument();
    expect(screen.getByText("A sync failed")).toBeInTheDocument();
  });

  it("surfaces a load failure rather than an empty dashboard", async () => {
    // An error rendered as "nothing connected" would send someone to
    // reconnect a source that is already fine.
    stubApi({
      ...SESSION,
      "/api/dashboard": {
        status: 500,
        body: {
          error: { code: "INTERNAL_ERROR", message: "Database unavailable." },
        },
      },
    });

    await renderWithSession(<OverviewPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load the overview",
    );
    expect(screen.queryByText("Nothing connected yet")).not.toBeInTheDocument();
  });

  it("refreshes on demand", async () => {
    const user = userEvent.setup();
    const { calls } = stubApi({
      ...SESSION,
      "/api/dashboard": { body: ACTIVE_DASHBOARD },
    });

    await renderWithSession(<OverviewPage />);
    await user.click(await screen.findByRole("button", { name: "Refresh" }));

    const dashboardCalls = calls.filter((c) =>
      c.url.includes("/api/dashboard"),
    );
    expect(dashboardCalls.length).toBeGreaterThan(1);
  });
});

const ENTITY = {
  id: "entity-1",
  entity_type: "shipment",
  natural_key: "SHIP-001",
  display_name: null,
  mapping_count: 2,
  observation_count: 3,
  created_at: "2026-08-01T00:00:00Z",
};

describe("Reality page", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("explains what is needed before there is anything to reason about", async () => {
    stubApi({ ...SESSION, "/api/entities": { body: [] } });

    await renderWithSession(<RealityPage />);

    expect(await screen.findByText("No items yet")).toBeInTheDocument();
  });

  it("shows an empty state rather than implying there is nothing to say", async () => {
    stubApi({
      ...SESSION,
      "/api/entities": { body: [ENTITY] },
      "/reality": { body: [] },
    });

    await renderWithSession(<RealityPage />);

    expect(await screen.findByText("No values yet")).toBeInTheDocument();
  });

  it("reports a blocked recalculation with what is missing", async () => {
    const user = userEvent.setup();
    stubApi({
      ...SESSION,
      "/api/entities": { body: [ENTITY] },
      "/reality": { body: [] },
      "/recalculate": {
        body: {
          entity_id: "entity-1",
          attributes_considered: 4,
          states_written: 4,
          conflicts_written: 6,
          calculated_at: "2026-08-11T12:00:00Z",
          states_unscored: 4,
          unscored_attributes: [
            { attribute: "quantity", blocked_on: "freshness" },
          ],
          blocked: true,
          blocked_on: ["freshness"],
          missing_specifications: [
            { name: "freshness", description: "Decay curve." },
          ],
        },
      },
    });

    await renderWithSession(<RealityPage />);
    await user.click(
      await screen.findByRole("button", { name: "Recalculate" }),
    );

    // CHANGED IN PHASE 9. This previously asserted the heading "No reality
    // state could be produced", which matched the Phase 5 behaviour of writing
    // nothing when scoring was blocked. States are written now — only the score
    // is withheld — so a heading saying nothing was produced would be false.
    expect(
      await screen.findByText("Values recorded without confidence scores"),
    ).toBeInTheDocument();
    expect(screen.getByText(/Blocked on: freshness/)).toBeInTheDocument();
    // Detection still ran — that is the useful half.
    expect(screen.getByText(/6/)).toBeInTheDocument();
  });

  it("renders scored states when the engine can produce them", async () => {
    stubApi({
      ...SESSION,
      "/api/entities": { body: [ENTITY] },
      "/reality": {
        body: [
          {
            id: "rs-1",
            entity_id: "entity-1",
            attribute: "quantity",
            value: 42,
            value_selected: true,
            confidence: "71.0",
            confidence_available: true,
            status: "contested",
            confidence_breakdown: {},
            selection_reason: "Selected from 2 competing values.",
            valid_from: "2026-08-01T09:00:00Z",
            calculated_at: "2026-08-11T12:00:00Z",
            algorithm_version: "reality-engine/1.0.0",
            supporting_count: 2,
            dissenting_count: 1,
            source_count: 2,
          },
        ],
      },
    });

    await renderWithSession(<RealityPage />);

    expect(await screen.findByText("quantity")).toBeInTheDocument();
    // A score that genuinely exists is still rendered as a number. The Phase 9
    // change withholds a score that does not exist; it does not stop the page
    // showing one that does, which is what will happen once the specification
    // arrives.
    expect(screen.getByText(/71\.0%/)).toBeInTheDocument();
    expect(screen.getByText(/contested/)).toBeInTheDocument();
    expect(
      screen.getByText("Selected from 2 competing values."),
    ).toBeInTheDocument();
  });
});
