import type { Metadata } from "next";

import { PhasePlaceholder } from "@/components/shell/phase-placeholder";

export const metadata: Metadata = { title: "Reality" };

export default function Page() {
  return (
    <PhasePlaceholder
      title="Reality"
      description="Entities, current state and the evidence behind it."
      phase={4}
      detail="Entities and reality states appear once observations are ingested from a connected source. The Reality Engine is implemented in Phase 4."
    />
  );
}
