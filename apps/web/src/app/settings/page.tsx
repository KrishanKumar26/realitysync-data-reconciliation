import type { Metadata } from "next";

import { PhasePlaceholder } from "@/components/shell/phase-placeholder";

export const metadata: Metadata = { title: "Settings" };

export default function Page() {
  return (
    <PhasePlaceholder
      title="Settings"
      description="Workspace, members and security."
      phase={2}
      detail="Workspace and member management arrive with authentication in Phase 2."
    />
  );
}
