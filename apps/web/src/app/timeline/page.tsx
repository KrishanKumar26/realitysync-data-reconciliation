import type { Metadata } from "next";

import { PhasePlaceholder } from "@/components/shell/phase-placeholder";

export const metadata: Metadata = { title: "Timeline" };

export default function Page() {
  return (
    <PhasePlaceholder
      title="Timeline"
      description="Observations, events and state changes over time."
      phase={5}
      detail="The bitemporal timeline distinguishes what happened from what RealitySync knew. Implemented in Phase 5."
    />
  );
}
