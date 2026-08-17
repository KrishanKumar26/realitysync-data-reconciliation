/**
 * Workspace navigation.
 *
 * Structure follows the approved information architecture. `phase` records the
 * phase that makes a destination functional, so the UI can say plainly what is
 * not built yet instead of presenting an empty screen as if it were finished.
 *
 * `group` splits the list into "the questions you ask" and "the machinery that
 * answers them". Six flat items is already past the point where a person reads
 * the list rather than recognising a shape in it.
 */

import {
  Database,
  GitCompareArrows,
  History,
  LayoutDashboard,
  Settings,
  Target,
  type LucideIcon,
} from "lucide-react";

export type NavGroup = "monitor" | "configure";

export interface NavItem {
  href: string;
  label: string;
  description: string;
  icon: LucideIcon;
  group: NavGroup;
  /** Development phase in which this screen becomes functional. */
  phase: number;
}

export const NAV_GROUP_LABELS: Record<NavGroup, string> = {
  monitor: "Monitor",
  configure: "Configure",
};

export const NAV_ITEMS: readonly NavItem[] = [
  {
    href: "/",
    label: "Overview",
    description: "Confidence, source health and recent activity",
    icon: LayoutDashboard,
    group: "monitor",
    phase: 6,
  },
  {
    href: "/reality",
    // The route keeps its original path: changing a URL breaks every existing
    // link and bookmark, and the label is the part a person actually reads.
    label: "Current State",
    description: "Items, their current values and the evidence behind them",
    icon: Target,
    group: "monitor",
    phase: 4,
  },
  {
    href: "/conflicts",
    label: "Conflicts",
    description: "Detected disagreements between sources",
    icon: GitCompareArrows,
    group: "monitor",
    phase: 5,
  },
  {
    href: "/timeline",
    label: "Timeline",
    description: "Records, events and changes over time",
    icon: History,
    group: "monitor",
    phase: 5,
  },
  {
    href: "/sources",
    label: "Sources",
    description: "Connected databases and APIs",
    icon: Database,
    group: "configure",
    phase: 3,
  },
  {
    href: "/settings",
    label: "Settings",
    description: "Workspace, members and security",
    icon: Settings,
    // Functional from Phase 2: workspace details and the member list are real
    // records from the active organization.
    group: "configure",
    phase: 2,
  },
] as const;
