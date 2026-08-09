"use client";

import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import { Panel, PanelBody } from "@/components/ui/panel";
import { ErrorState } from "@/components/ui/states";

/** Route-level error boundary. */
export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Error reporting is wired in Phase 9. Until then the digest is the only
    // correlation handle, so make sure it reaches the console.
    console.error("Route error", { digest: error.digest });
  }, [error]);

  return (
    <Panel>
      <PanelBody className="p-0">
        <ErrorState
          title="This page failed to load"
          description="An unexpected error occurred while rendering."
          requestId={error.digest ?? null}
          action={
            <Button variant="secondary" size="sm" onClick={reset}>
              Try again
            </Button>
          }
        />
      </PanelBody>
    </Panel>
  );
}
