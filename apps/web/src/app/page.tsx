"use client";

import { Button } from "@/components/ui/button";
import { Panel, PanelBody, PanelHeader } from "@/components/ui/panel";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { useApiStatus } from "@/components/shell/api-status";

/**
 * Overview.
 *
 * Deliberately carries no metrics. The dashboard described in the product
 * specification arrives in Phase 6, once there are real observations behind
 * it. Showing a confidence gauge now would mean inventing a number, and a
 * fabricated metric in an operational tool is worse than an empty panel.
 */
export default function OverviewPage() {
  const { state, refresh } = useApiStatus();

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">Overview</h1>
        <p className="mt-1.5 text-sm text-muted-foreground">
          Know what is actually happening.
        </p>
      </header>

      <Panel>
        <PanelHeader
          title="Backend connection"
          description="Live result of the API liveness probe."
          action={
            <Button variant="secondary" size="sm" onClick={refresh}>
              Re-check
            </Button>
          }
        />
        <PanelBody>
          {state.kind === "loading" ? (
            <div className="space-y-2.5" data-testid="api-status-loading">
              <Skeleton className="h-4 w-52" />
              <Skeleton className="h-4 w-36" />
            </div>
          ) : null}

          {state.kind === "connected" ? (
            <dl
              className="grid gap-x-8 gap-y-3 sm:grid-cols-3"
              data-testid="api-status-connected"
            >
              {[
                ["Status", state.health.status],
                ["Version", state.health.version],
                ["Environment", state.health.environment],
              ].map(([label, value]) => (
                <div key={label}>
                  <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                    {label}
                  </dt>
                  <dd className="tabular mt-1 text-sm text-foreground">{value}</dd>
                </div>
              ))}
            </dl>
          ) : null}

          {state.kind === "unavailable" ? (
            <ErrorState
              title="API unavailable"
              description={`${state.message} Start the backend with "docker compose up" and re-check.`}
              action={
                <Button variant="secondary" size="sm" onClick={refresh}>
                  Retry
                </Button>
              }
              className="py-8"
            />
          ) : null}
        </PanelBody>
      </Panel>

      <Panel>
        <PanelHeader title="Reality state" />
        <PanelBody className="p-0">
          <EmptyState
            title="No sources connected"
            description="RealitySync reports state only from real connected sources. Connect a PostgreSQL database to begin producing observations — available from Phase 3."
          />
        </PanelBody>
      </Panel>
    </div>
  );
}
