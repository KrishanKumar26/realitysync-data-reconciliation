/**
 * Workspace navigation.
 *
 * Structure follows the approved information architecture. `phase` records the
 * phase that makes a destination functional, so the UI can say plainly what is
 * not built yet instead of presenting an empty screen as if it were finished.
 */

export interface NavItem {
  href: string;
  label: string;
  description: string;
  /** Development phase in which this screen becomes functional. */
  phase: number;
}

export const NAV_ITEMS: readonly NavItem[] = [
  {
    href: "/",
    label: "Overview",
    description: "Reality confidence, source health and recent activity",
    phase: 6,
  },
  {
    href: "/sources",
    label: "Sources",
    description: "Connected databases and APIs",
    phase: 3,
  },
  {
    href: "/reality",
    label: "Reality",
    description: "Entities, current state and evidence",
    phase: 4,
  },
  {
    href: "/conflicts",
    label: "Conflicts",
    description: "Detected disagreements between sources",
    phase: 5,
  },
  {
    href: "/timeline",
    label: "Timeline",
    description: "Observations, events and state changes over time",
    phase: 5,
  },
  {
    href: "/settings",
    label: "Settings",
    description: "Workspace, members and security",
    // Functional from Phase 2: workspace details and the member list are real
    // records from the active organization.
    phase: 2,
  },
] as const;
