import type { Metadata } from "next";

import { PhasePlaceholder } from "@/components/shell/phase-placeholder";

export const metadata: Metadata = { title: "Conflicts" };

export default function Page() {
  return (
    <PhasePlaceholder
      title="Conflicts"
      description="Disagreements detected between sources."
      phase={5}
      detail="Conflicts are raised by the deterministic conflict engine when trusted sources disagree. Implemented in Phase 5."
    />
  );
}
