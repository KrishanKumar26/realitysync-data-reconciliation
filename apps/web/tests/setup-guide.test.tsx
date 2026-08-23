import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SetupGuide, setupSteps } from "@/components/onboarding/setup-guide";
import type { Dashboard } from "@/lib/dashboard";

/** A workspace with nothing done yet. */
function dashboard(overrides: Record<string, unknown> = {}): Dashboard {
  const base = {
    organization_id: "org-1",
    generated_at: "2026-08-18T10:00:00Z",
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
      algorithm_version: "v1",
      blocked_reason: null,
      missing_specifications: [],
    },
    activity: [],
  };
  return { ...base, ...overrides } as Dashboard;
}

const ALL_DONE = dashboard({
  sources: { ...dashboard().sources, total: 1, connected: 1 },
  ingestion: {
    ...dashboard().ingestion,
    stream_count: 1,
    observation_count: 3,
    entity_count: 1,
    mapped_entity_count: 1,
  },
  confidence: { ...dashboard().confidence, unscored_attribute_count: 4 },
});

describe("setupSteps", () => {
  it("marks nothing done for a brand new workspace", () => {
    expect(setupSteps(dashboard()).every((step) => !step.done)).toBe(true);
  });

  it("ticks a step only when the thing it describes actually exists", () => {
    // Derived from counts, never from a stored flag — so it cannot claim a
    // step is done when the data says otherwise.
    const steps = setupSteps(
      dashboard({ sources: { ...dashboard().sources, total: 1 } }),
    );
    expect(steps[0]!.done).toBe(true);
    expect(steps[1]!.done).toBe(false);
  });

  it("reopens a step when its data goes away", () => {
    // Deleting the only source must not leave setup looking finished.
    const steps = setupSteps(ALL_DONE);
    expect(steps.every((step) => step.done)).toBe(true);

    const undone = setupSteps({
      ...ALL_DONE,
      sources: { ...ALL_DONE.sources, total: 0 },
    });
    expect(undone[0]!.done).toBe(false);
  });
});

describe("SetupGuide", () => {
  it("points at the first unfinished step and only that one", () => {
    render(<SetupGuide dashboard={dashboard()} />);

    expect(screen.getByText("Connect a database")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /Add a database/ }),
    ).toHaveAttribute("href", "/sources");
    // One action at a time: six buttons at once is the wall this replaces.
    expect(screen.getAllByRole("link")).toHaveLength(1);
  });

  it("moves on once an earlier step is really done", () => {
    render(
      <SetupGuide
        dashboard={dashboard({
          sources: { ...dashboard().sources, total: 1, connected: 1 },
        })}
      />,
    );

    expect(
      screen.getByRole("link", { name: /Choose a table/ }),
    ).toBeInTheDocument();
  });

  it("reports progress out of the real total", () => {
    render(
      <SetupGuide
        dashboard={dashboard({
          sources: { ...dashboard().sources, total: 1 },
        })}
      />,
    );

    expect(screen.getByText("1 of 6")).toBeInTheDocument();
  });

  it("disappears when setup is finished", () => {
    // Rather than congratulating someone on every page load.
    const { container } = render(<SetupGuide dashboard={ALL_DONE} />);
    expect(container).toBeEmptyDOMElement();
  });
});
