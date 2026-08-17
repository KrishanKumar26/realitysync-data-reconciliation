"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Stat, StatGrid } from "@/components/overview/stat";
import { SourceStatusBadge } from "@/components/sources/status-badge";
import { Button } from "@/components/ui/button";
import { Panel, PanelBody, PanelHeader } from "@/components/ui/panel";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { ApiError } from "@/lib/api";
import {
  fetchDashboard,
  type ActivityItem,
  type Dashboard,
} from "@/lib/dashboard";
import { cn } from "@/lib/utils";

type State =
  | { kind: "loading" }
  | { kind: "ready"; dashboard: Dashboard }
  | { kind: "error"; message: string };

/**
 * Overview.
 *
 * Every figure here is a real count from a real table — sources that were
 * actually contacted, observations that were actually ingested, conflicts that
 * were actually detected.
 *
 * Reality confidence is the exception, and it is handled as an exception. The
 * approved confidence specification is unavailable, so the panel says so and
 * shows what is blocking it. It does not render a gauge at zero: that would
 * claim we are certain of nothing, when the truth is that nobody has told us
 * how to measure.
 */
export default function OverviewPage() {
  const [state, setState] = useState<State>({ kind: "loading" });

  const load = useCallback(async () => {
    try {
      setState({ kind: "ready", dashboard: await fetchDashboard() });
    } catch (error) {
      setState({
        kind: "error",
        message:
          error instanceof ApiError
            ? error.message
            : "Could not load the overview.",
      });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Overview</h1>
          <p className="mt-1.5 text-sm text-muted-foreground">
            Know what is actually happening.
          </p>
        </div>
        {state.kind === "ready" ? (
          <Button variant="secondary" size="sm" onClick={() => void load()}>
            Refresh
          </Button>
        ) : null}
      </header>

      {state.kind === "loading" ? (
        <div className="space-y-4" data-testid="overview-loading">
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : null}

      {state.kind === "error" ? (
        <Panel>
          <PanelBody className="p-0">
            <ErrorState
              title="Could not load the overview"
              description={state.message}
              action={
                <Button variant="secondary" onClick={() => void load()}>
                  Retry
                </Button>
              }
            />
          </PanelBody>
        </Panel>
      ) : null}

      {state.kind === "ready" && state.dashboard.is_empty ? (
        <Panel>
          <PanelBody className="p-0">
            <EmptyState
              title="Nothing connected yet"
              description="RealitySync reports state only from real connected sources. Connect a PostgreSQL or MySQL database to start receiving records — everything on this page is derived from them."
              action={
                <Link href="/sources">
                  <Button>Connect a source</Button>
                </Link>
              }
            />
          </PanelBody>
        </Panel>
      ) : null}

      {state.kind === "ready" && !state.dashboard.is_empty ? (
        <>
          <ConfidencePanel dashboard={state.dashboard} />
          <SourceHealthPanel dashboard={state.dashboard} />
          <IngestionPanel dashboard={state.dashboard} />
          <ConflictPanel dashboard={state.dashboard} />
          <ActivityPanel
            activity={state.dashboard.activity}
            window={state.dashboard.window_days}
          />
        </>
      ) : null}
    </div>
  );
}

/**
 * Reality confidence.
 *
 * The one panel that may have nothing to show. When the specification is
 * unavailable it states that plainly and lists what is missing, so the gap is
 * legible rather than mysterious.
 */
function ConfidencePanel({ dashboard }: { dashboard: Dashboard }) {
  const { confidence } = dashboard;

  if (!confidence.available) {
    return (
      <Panel>
        <PanelHeader title="Confidence" description="Not available." />
        <PanelBody className="space-y-4">
          <p className="text-sm leading-relaxed text-muted-foreground">
            {confidence.blocked_reason}
          </p>

          <StatGrid columns={3}>
            <Stat
              label="Average"
              value="—"
              tone="muted"
              hint="No score is shown rather than an invented one."
            />
            <Stat
              label="Scored fields"
              value={confidence.scored_state_count}
              tone="muted"
            />
            <Stat
              label="Awaiting a score"
              value={confidence.unscored_attribute_count.toLocaleString()}
              hint="Records received and ready to score."
            />
          </StatGrid>

          {confidence.missing_specifications.length > 0 ? (
            <details className="rounded-md border border-border bg-muted px-4 py-3">
              <summary className="cursor-pointer text-sm text-foreground">
                {confidence.missing_specifications.length} specifications
                required
              </summary>
              <ul className="mt-3 space-y-2">
                {confidence.missing_specifications.map((spec) => (
                  <li key={spec.name} className="text-xs text-muted-foreground">
                    <span className="tabular text-foreground">{spec.name}</span>
                    {" — "}
                    {spec.description}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}

          <p className="tabular text-xs text-muted-foreground">
            Algorithm: {confidence.algorithm_version}
          </p>
        </PanelBody>
      </Panel>
    );
  }

  return (
    <Panel>
      <PanelHeader
        title="Confidence"
        description="Across every scored field in this workspace."
      />
      <PanelBody>
        <StatGrid columns={4}>
          <Stat
            label="Average"
            value={`${confidence.average_confidence?.toFixed(1)}%`}
          />
          <Stat
            label="Lowest"
            value={`${confidence.lowest_confidence?.toFixed(1)}%`}
          />
          <Stat
            label="Highest"
            value={`${confidence.highest_confidence?.toFixed(1)}%`}
          />
          <Stat label="Scored fields" value={confidence.scored_state_count} />
        </StatGrid>
      </PanelBody>
    </Panel>
  );
}

function SourceHealthPanel({ dashboard }: { dashboard: Dashboard }) {
  const { sources } = dashboard;

  return (
    <Panel>
      <PanelHeader
        title="Source health"
        description="From the last real connection attempt — nothing is dialled to render this page."
        action={
          <Link href="/sources">
            <Button variant="secondary" size="sm">
              Manage
            </Button>
          </Link>
        }
      />
      <PanelBody className="space-y-5">
        <StatGrid columns={4}>
          <Stat label="Sources" value={sources.total} />
          <Stat label="Connected" value={sources.connected} />
          <Stat
            label="Not yet tested"
            value={sources.never_tested}
            tone={sources.never_tested > 0 ? "warning" : "default"}
            hint={
              sources.never_tested > 0
                ? "Credentials stored, connection unproven."
                : undefined
            }
          />
          <Stat
            label="Failing"
            value={sources.errored}
            tone={sources.errored > 0 ? "danger" : "default"}
          />
        </StatGrid>

        {sources.sources.length > 0 ? (
          <ul className="divide-y divide-border border-t border-border">
            {sources.sources.map((source) => (
              <li
                key={source.source_id}
                className="flex flex-wrap items-center justify-between gap-3 py-3"
              >
                <div className="min-w-0">
                  <Link
                    href={`/sources/${source.source_id}`}
                    className="truncate text-sm text-foreground hover:underline"
                  >
                    {source.name}
                  </Link>
                  <p className="tabular mt-0.5 text-xs text-muted-foreground">
                    {source.stream_count}{" "}
                    {source.stream_count === 1 ? "table" : "tables"} ·{" "}
                    {source.observation_count.toLocaleString()} records
                    {source.last_synced_at
                      ? ` · synced ${new Date(source.last_synced_at).toLocaleString()}`
                      : " · never synced"}
                  </p>
                  {source.last_error ? (
                    <p className="mt-1 text-xs text-status-down">
                      {source.last_error}
                    </p>
                  ) : null}
                </div>
                <SourceStatusBadge status={source.status} />
              </li>
            ))}
          </ul>
        ) : null}
      </PanelBody>
    </Panel>
  );
}

function IngestionPanel({ dashboard }: { dashboard: Dashboard }) {
  const { ingestion, window_days } = dashboard;

  return (
    <Panel>
      <PanelHeader
        title="Data received"
        description={`What each source stated. These are never edited. Window: last ${window_days} days.`}
      />
      <PanelBody>
        <StatGrid columns={4}>
          <Stat
            label="Records"
            value={ingestion.observation_count.toLocaleString()}
            hint={`${ingestion.observations_in_window.toLocaleString()} in the last ${window_days} days`}
          />
          <Stat
            label="Items"
            value={ingestion.entity_count}
            hint={
              ingestion.unmapped_entity_count > 0
                ? `${ingestion.unmapped_entity_count} with no linked source rows`
                : undefined
            }
            tone={ingestion.unmapped_entity_count > 0 ? "warning" : "default"}
          />
          <Stat
            label="Tables"
            value={ingestion.stream_count}
            hint={`${ingestion.enabled_stream_count} enabled`}
          />
          <Stat
            label="Failed syncs"
            value={ingestion.failed_syncs_in_window}
            tone={ingestion.failed_syncs_in_window > 0 ? "danger" : "default"}
            hint={`of ${ingestion.syncs_in_window} in the window`}
          />
        </StatGrid>
      </PanelBody>
    </Panel>
  );
}

function ConflictPanel({ dashboard }: { dashboard: Dashboard }) {
  const { conflicts } = dashboard;
  const graded = Object.entries(conflicts.by_severity).filter(([, n]) => n > 0);

  return (
    <Panel>
      <PanelHeader
        title="Conflicts"
        description="Sources stating different values for the same field."
        action={
          <Link href="/conflicts">
            <Button variant="secondary" size="sm">
              Review
            </Button>
          </Link>
        }
      />
      <PanelBody className="space-y-4">
        <StatGrid columns={4}>
          <Stat
            label="Open"
            value={conflicts.open}
            tone={conflicts.open > 0 ? "danger" : "default"}
          />
          <Stat label="Acknowledged" value={conflicts.acknowledged} />
          <Stat label="Resolved" value={conflicts.resolved} />
          <Stat
            label="Not graded"
            value={conflicts.ungraded}
            tone={conflicts.ungraded > 0 ? "warning" : "default"}
            hint={
              conflicts.ungraded > 0
                ? "Detected, but severity needs the confidence specification."
                : undefined
            }
          />
        </StatGrid>

        {graded.length > 0 ? (
          <div className="flex flex-wrap gap-2 border-t border-border pt-4">
            {graded.map(([severity, count]) => (
              <span
                key={severity}
                className={cn(
                  "rounded-full border px-2.5 py-0.5 text-xs capitalize",
                  severity === "critical" || severity === "high"
                    ? "border-status-down/40 text-status-down"
                    : "border-border text-muted-foreground",
                )}
              >
                {count} {severity}
              </span>
            ))}
          </div>
        ) : null}
      </PanelBody>
    </Panel>
  );
}

function ActivityPanel({
  activity,
  window,
}: {
  activity: ActivityItem[];
  window: number;
}) {
  return (
    <Panel>
      <PanelHeader
        title="Recent activity"
        description={`What has happened in the last ${window} days.`}
      />
      <PanelBody className={activity.length > 0 ? "p-0" : undefined}>
        {activity.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Nothing has happened in this window.
          </p>
        ) : (
          <ol className="divide-y divide-border">
            {activity.map((item, index) => (
              <li
                key={`${item.occurred_at}-${index}`}
                className="flex flex-wrap items-baseline justify-between gap-2 px-5 py-3"
              >
                <div className="min-w-0">
                  <p
                    className={cn(
                      "text-sm",
                      item.severity === "error"
                        ? "text-status-down"
                        : "text-foreground",
                    )}
                  >
                    {item.summary}
                  </p>
                  {item.detail ? (
                    <p className="tabular mt-0.5 truncate text-xs text-muted-foreground">
                      {item.detail}
                    </p>
                  ) : null}
                </div>
                <time className="tabular shrink-0 text-xs text-muted-foreground">
                  {new Date(item.occurred_at).toLocaleString()}
                </time>
              </li>
            ))}
          </ol>
        )}
      </PanelBody>
    </Panel>
  );
}
