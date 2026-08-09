import type { Metadata } from "next";

import { PhasePlaceholder } from "@/components/shell/phase-placeholder";

export const metadata: Metadata = { title: "Sources" };

export default function Page() {
  return (
    <PhasePlaceholder
      title="Sources"
      description="Connected databases and APIs."
      phase={3}
      detail="The PostgreSQL connector — connection testing, schema discovery and synchronisation — is implemented in Phase 3."
    />
  );
}
