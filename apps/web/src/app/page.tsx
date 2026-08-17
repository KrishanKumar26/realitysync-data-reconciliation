"use client";

import {
  Activity,
  AlertTriangle,
  Boxes,
  Database,
  GitCompareArrows,
  Gauge,
  Inbox,
  Plug,
  RefreshCw,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { SourceStatusBadge } from "@/components/sources/status-badge";
import { Badge } from "@/components/ui/badge";
import { BarRows, Donut, RatioBar, type Slice } from "@/components/ui/chart";
import { Metric, MetricGrid } from "@/components/ui/metric";
import { PageHeader } from "@/components/ui/page-header";
import {
  Panel,
  PanelBody,
  PanelFooter,
  PanelHeader,
} from "@/components/ui/panel";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import {
  Table,
  TableContainer,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui/table";
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
 * actually contacted, records that were actually received, conflicts that were
 * actually detected.
 *
 * Confidence is the exception, and it is handled as an exception. The approved
 * confidence specification is unavailable, so the panel says so and shows what
 * is blocking it. It does not render a gauge at zero: that would claim we are
 * certain of nothing, when the truth is that nobody has told us how to measure.
 *
 * Layout answers the operator's first question — "is anything wrong right
 * now?" — before it answers any other. Four metrics lead, and the two that can
 * be bad (failing sources, open conflicts) change colour when they are. The
 * detail follows underneath for whoever needs it.
 */
export default function OverviewPage() {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [refreshing, setRefreshing] = useState(false);

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

  async function refresh() {
    setRefreshing(true);
    try {
      await load();
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Overview"
        description="Know what is actually happening."
        actions={
          state.kind === "ready" ? (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void refresh()}
              disabled={refreshing}
            >
              <RefreshCw
                className={cn(refreshing && "animate-spin")}
                aria-hidden="true"
              />
              Refresh
            </Button>
          ) : undefined
        }
      />

      {state.kind === "loading" ? (
        <div className="space-y-6" data-testid="overview-loading">
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Skeleton className="h-32 w-full" />
            <Skeleton className="h-32 w-full" />
            <Skeleton className="h-32 w-full" />
            <Skeleton className="h-32 w-full" />
          </div>
          <div className="grid gap-4 lg:grid-cols-3">
            <Skeleton className="h-64 w-full lg:col-span-2" />
            <Skeleton className="h-64 w-full" />
          </div>
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
              icon={<Plug />}
              title="Nothing connected yet"
              description="RealitySync reports state only from real connected sources. Connect a PostgreSQL or MySQL database to begin — everything on this page is derived from what those sources actually say."
              action={
                <Link href="/sources">
                  <Button>
                    <Database aria-hidden="true" />
                    Connect a source
                  </Button>
                </Link>
              }
            />
          </PanelBody>
        </Panel>
      ) : null}

      {state.kind === "ready" && !state.dashboard.is_empty ? (
        <>
          <KeyMetrics dashboard={state.dashboard} />

          <div className="grid gap-4 lg:grid-cols-3">
            <ConfidencePanel dashboard={state.dashboard} />
            <SourceCompositionPanel dashboard={state.dashboard} />
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <SourceHealthPanel dashboard={state.dashboard} />
            <ConflictPanel dashboard={state.dashboard} />
          </div>

          <ActivityPanel
            activity={state.dashboard.activity}
            window={state.dashboard.window_days}
          />
        </>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------------ */

/**
 * The four figures worth reading from across a room.
 *
 * Chosen because each one can prompt an action: a failing source needs
 * credentials fixed, an open conflict needs a decision, an item with nothing
 * linked to it needs a source row. Records is the one pure volume measure, and
 * it is here because "is data still arriving" is the question a quiet pipeline
 * fails silently on.
 */
function KeyMetrics({ dashboard }: { dashboard: Dashboard }) {
  const { sources, ingestion, conflicts, window_days } = dashboard;
  const attention = sources.errored + sources.never_tested;

  return (
    <MetricGrid columns={4}>
      <Metric
        label="Sources"
        value={sources.total}
        icon={<Database />}
        tone={sources.errored > 0 ? "down" : "default"}
        hint={
          attention > 0
            ? `${sources.errored} failing · ${sources.never_tested} untested`
            : "All connected and reachable."
        }
        footer={
          <RatioBar
            label="Connected"
            value={sources.connected}
            total={sources.total}
            tone={sources.errored > 0 ? "degraded" : "healthy"}
          />
        }
      />

      <Metric
        label="Records"
        value={ingestion.observation_count.toLocaleString()}
        icon={<Inbox />}
        tone="accent"
        hint={`${ingestion.observations_in_window.toLocaleString()} received in the last ${window_days} days.`}
        footer={
          <RatioBar
            label={`Syncs succeeded (${window_days}d)`}
            value={ingestion.syncs_in_window - ingestion.failed_syncs_in_window}
            total={ingestion.syncs_in_window}
            tone={ingestion.failed_syncs_in_window > 0 ? "degraded" : "healthy"}
          />
        }
      />

      <Metric
        label="Open conflicts"
        value={conflicts.open}
        icon={<GitCompareArrows />}
        tone={conflicts.open > 0 ? "down" : "healthy"}
        hint={
          conflicts.open > 0
            ? "Sources disagree. Each needs a decision."
            : "No source is currently contradicting another."
        }
        footer={
          <Link
            href="/conflicts"
            className="text-xs font-medium text-foreground underline-offset-4 hover:underline"
          >
            Review conflicts →
          </Link>
        }
      />

      <Metric
        label="Items tracked"
        value={ingestion.entity_count}
        icon={<Boxes />}
        tone={ingestion.unmapped_entity_count > 0 ? "degraded" : "default"}
        hint={
          ingestion.unmapped_entity_count > 0
            ? `${ingestion.unmapped_entity_count} have no source rows linked, so nothing is compared for them.`
            : "Every item has at least one source row linked."
        }
        footer={
          <RatioBar
            label="With linked data"
            value={ingestion.mapped_entity_count}
            total={ingestion.entity_count}
            tone={ingestion.unmapped_entity_count > 0 ? "degraded" : "healthy"}
          />
        }
      />
    </MetricGrid>
  );
}

/* ------------------------------------------------------------------------ */

/**
 * Confidence.
 *
 * The one panel that may have nothing to show. When the specification is
 * unavailable it states that plainly and lists what is missing, so the gap is
 * legible rather than mysterious.
 */
function ConfidencePanel({ dashboard }: { dashboard: Dashboard }) {
  const { confidence } = dashboard;

  if (!confidence.available) {
    return (
      <Panel className="lg:col-span-2">
        <PanelHeader
          icon={<Gauge />}
          title="Confidence"
          description="Not available — and deliberately not guessed."
          action={<Badge tone="degraded">Unavailable</Badge>}
        />
        <PanelBody className="space-y-5">
          <div className="flex flex-col gap-5 sm:flex-row sm:items-center">
            {/* An em dash where the number goes. A gauge at 0% would read as
                "we are certain of nothing", which is a measurement — and no
                measurement has been made. */}
            <div className="flex h-28 w-28 shrink-0 items-center justify-center rounded-full border-[10px] border-dashed border-border">
              <span className="text-3xl font-semibold text-muted-foreground">
                —
              </span>
            </div>
            <p className="text-sm leading-relaxed text-muted-foreground">
              {confidence.blocked_reason}
            </p>
          </div>

          <div className="grid gap-4 border-t border-border pt-4 sm:grid-cols-2">
            <div>
              <p className="text-xs uppercase tracking-wide text-muted-foreground">
                Scored fields
              </p>
              <p className="tabular mt-1 text-xl font-semibold text-muted-foreground">
                {confidence.scored_state_count.toLocaleString()}
              </p>
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-muted-foreground">
                Awaiting a score
              </p>
              <p className="tabular mt-1 text-xl font-semibold text-foreground">
                {confidence.unscored_attribute_count.toLocaleString()}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                Received and ready to score.
              </p>
            </div>
          </div>

          {confidence.missing_specifications.length > 0 ? (
            <details className="group rounded-md border border-border bg-muted/40 px-4 py-3">
              <summary className="cursor-pointer list-none text-sm font-medium text-foreground marker:content-none">
                <span className="inline-flex items-center gap-2">
                  <AlertTriangle
                    className="h-3.5 w-3.5 text-status-degraded"
                    aria-hidden="true"
                  />
                  {confidence.missing_specifications.length} specifications
                  required
                </span>
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
        </PanelBody>
        <PanelFooter>
          <span className="tabular">
            Algorithm: {confidence.algorithm_version}
          </span>
        </PanelFooter>
      </Panel>
    );
  }

  return (
    <Panel className="lg:col-span-2">
      <PanelHeader
        icon={<Gauge />}
        title="Confidence"
        description="Across every scored field in this workspace."
      />
      <PanelBody>
        <div className="grid gap-6 sm:grid-cols-[auto_1fr] sm:items-center">
          <ConfidenceDial value={confidence.average_confidence ?? 0} />
          <div className="space-y-3">
            <RatioBar
              label="Lowest"
              value={confidence.lowest_confidence ?? 0}
              total={100}
              valueLabel={`${confidence.lowest_confidence?.toFixed(1)}%`}
              tone="degraded"
            />
            <RatioBar
              label="Highest"
              value={confidence.highest_confidence ?? 0}
              total={100}
              valueLabel={`${confidence.highest_confidence?.toFixed(1)}%`}
              tone="healthy"
            />
            <div className="flex items-baseline justify-between gap-3 border-t border-border pt-3">
              <span className="text-xs text-muted-foreground">
                Scored fields
              </span>
              <span className="tabular text-xs font-medium text-foreground">
                {confidence.scored_state_count.toLocaleString()}
              </span>
            </div>
          </div>
        </div>
      </PanelBody>
    </Panel>
  );
}

/**
 * Average confidence as an arc.
 *
 * Colour follows the product-wide confidence scale defined in globals.css and
 * is not chosen here — every surface that renders a confidence must agree, or
 * the colour stops meaning anything.
 */
function ConfidenceDial({ value }: { value: number }) {
  const size = 132;
  const thickness = 12;
  const radius = (size - thickness) / 2;
  const circumference = 2 * Math.PI * radius;
  const filled = (Math.min(Math.max(value, 0), 100) / 100) * circumference;

  const colour =
    value >= 90
      ? "var(--color-confidence-high)"
      : value >= 70
        ? "var(--color-confidence-good)"
        : value >= 50
          ? "var(--color-confidence-fair)"
          : "var(--color-confidence-low)";

  return (
    <div className="relative flex h-[132px] w-[132px] shrink-0 items-center justify-center">
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        className="-rotate-90"
        role="img"
        aria-label={`Average confidence ${value.toFixed(1)} percent`}
      >
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--muted)"
          strokeWidth={thickness}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={colour}
          strokeWidth={thickness}
          strokeLinecap="round"
          strokeDasharray={`${filled} ${circumference - filled}`}
          className="transition-[stroke-dasharray] duration-700 ease-out"
        />
      </svg>
      <div className="absolute text-center">
        <p className="tabular text-2xl font-semibold tracking-tight text-foreground">
          {value.toFixed(1)}%
        </p>
        <p className="text-xs text-muted-foreground">average</p>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------ */

/** How the connected sources are split by state. */
function SourceCompositionPanel({ dashboard }: { dashboard: Dashboard }) {
  const { sources } = dashboard;

  const slices: Slice[] = [
    {
      label: "Connected",
      value: sources.connected,
      color: "var(--color-status-healthy)",
    },
    {
      label: "Not yet tested",
      value: sources.never_tested,
      color: "var(--color-status-unknown)",
    },
    {
      label: "Failing",
      value: sources.errored,
      color: "var(--color-status-down)",
    },
    {
      label: "Disabled",
      value: sources.disabled,
      color: "var(--color-status-degraded)",
    },
  ].filter((slice) => slice.value > 0);

  return (
    <Panel>
      <PanelHeader
        icon={<Plug />}
        title="Source status"
        description="From the last real connection attempt."
      />
      <PanelBody>
        <Donut
          slices={slices}
          total={sources.total}
          caption={sources.total === 1 ? "source" : "sources"}
        />
        {sources.never_tested > 0 ? (
          <p className="mt-4 border-t border-border pt-3.5 text-xs leading-relaxed text-muted-foreground">
            &ldquo;Not yet tested&rdquo; is not a failure: credentials stored,
            connection unproven.
          </p>
        ) : null}
      </PanelBody>
    </Panel>
  );
}

/* ------------------------------------------------------------------------ */

function SourceHealthPanel({ dashboard }: { dashboard: Dashboard }) {
  const { sources } = dashboard;

  return (
    <Panel className="lg:col-span-2">
      <PanelHeader
        icon={<Database />}
        title="Sources"
        description="Nothing is dialled to render this page — these are the last real results."
        action={
          <Link href="/sources">
            <Button variant="secondary" size="sm">
              Manage
            </Button>
          </Link>
        }
      />
      <PanelBody className="p-0">
        {sources.sources.length === 0 ? (
          <EmptyState
            icon={<Database />}
            title="No sources yet"
            className="py-10"
          />
        ) : (
          <TableContainer>
            <Table>
              <THead>
                <TH>Source</TH>
                <TH>Status</TH>
                <TH align="right">Tables</TH>
                <TH align="right">Records</TH>
                <TH>Last sync</TH>
              </THead>
              <TBody>
                {sources.sources.map((source) => (
                  <TR key={source.source_id}>
                    <TD>
                      <Link
                        href={`/sources/${source.source_id}`}
                        className="font-medium text-foreground underline-offset-4 hover:underline"
                      >
                        {source.name}
                      </Link>
                      {source.last_error ? (
                        <p className="mt-0.5 max-w-xs truncate text-xs text-status-down">
                          {source.last_error}
                        </p>
                      ) : null}
                    </TD>
                    <TD>
                      <SourceStatusBadge status={source.status} />
                    </TD>
                    <TD numeric align="right">
                      {source.stream_count}
                    </TD>
                    <TD numeric align="right">
                      {source.observation_count.toLocaleString()}
                    </TD>
                    <TD numeric className="text-muted-foreground">
                      {source.last_synced_at
                        ? relativeTime(source.last_synced_at)
                        : "Never"}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </TableContainer>
        )}
      </PanelBody>
    </Panel>
  );
}

/* ------------------------------------------------------------------------ */

function ConflictPanel({ dashboard }: { dashboard: Dashboard }) {
  const { conflicts } = dashboard;

  const SEVERITY_COLOUR: Record<string, string> = {
    critical: "var(--color-status-down)",
    high: "var(--color-status-down)",
    medium: "var(--color-status-degraded)",
    low: "var(--color-status-unknown)",
  };

  const graded = Object.entries(conflicts.by_severity)
    .filter(([, count]) => count > 0)
    .map(([severity, count]) => ({
      label: severity[0]!.toUpperCase() + severity.slice(1),
      value: count,
      color: SEVERITY_COLOUR[severity] ?? "var(--color-status-unknown)",
    }));

  return (
    <Panel>
      <PanelHeader
        icon={<GitCompareArrows />}
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
        <div className="grid grid-cols-2 gap-3">
          {[
            { label: "Open", value: conflicts.open, danger: true },
            { label: "Acknowledged", value: conflicts.acknowledged },
            { label: "Resolved", value: conflicts.resolved },
            { label: "Not graded", value: conflicts.ungraded },
          ].map((item) => (
            <div
              key={item.label}
              className="rounded-md border border-border p-3"
            >
              <p
                className={cn(
                  "tabular text-xl font-semibold",
                  item.danger && item.value > 0
                    ? "text-status-down"
                    : "text-foreground",
                )}
              >
                {item.value}
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {item.label}
              </p>
            </div>
          ))}
        </div>

        {graded.length > 0 ? (
          <div className="border-t border-border pt-4">
            <p className="mb-2.5 text-xs uppercase tracking-wide text-muted-foreground">
              By severity
            </p>
            <BarRows rows={graded} />
          </div>
        ) : null}

        {conflicts.ungraded > 0 ? (
          <p className="rounded-md border border-status-degraded/25 bg-status-degraded/5 px-3.5 py-2.5 text-xs leading-relaxed text-muted-foreground">
            <span className="tabular font-medium text-foreground">
              {conflicts.ungraded}
            </span>{" "}
            detected but not graded. Assigning a severity needs the confidence
            specification, so they are shown ungraded rather than assumed
            harmless.
          </p>
        ) : null}
      </PanelBody>
    </Panel>
  );
}

/* ------------------------------------------------------------------------ */

const ACTIVITY_ICON = {
  audit: Activity,
  sync: RefreshCw,
  conflict: GitCompareArrows,
} as const;

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
        icon={<Activity />}
        title="Recent activity"
        description={`What has happened in the last ${window} days.`}
      />
      <PanelBody className={activity.length > 0 ? "p-0" : undefined}>
        {activity.length === 0 ? (
          <EmptyState
            icon={<Activity />}
            title="Nothing has happened in this window"
            description="Syncs, connection tests and conflict decisions will appear here as they occur."
            className="py-10"
          />
        ) : (
          <ol className="divide-y divide-border">
            {activity.map((item, index) => {
              const Icon = ACTIVITY_ICON[item.kind] ?? Activity;
              const isError = item.severity === "error";

              return (
                <li
                  key={`${item.occurred_at}-${index}`}
                  className="flex items-start gap-3.5 px-5 py-3.5"
                >
                  <span
                    aria-hidden="true"
                    className={cn(
                      "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border",
                      isError
                        ? "border-status-down/25 bg-status-down/10 text-status-down"
                        : "border-border bg-muted text-muted-foreground",
                    )}
                  >
                    <Icon className="h-3.5 w-3.5" />
                  </span>

                  <div className="min-w-0 flex-1">
                    <p
                      className={cn(
                        "text-sm",
                        isError ? "text-status-down" : "text-foreground",
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

                  <time
                    dateTime={item.occurred_at}
                    title={new Date(item.occurred_at).toLocaleString()}
                    className="tabular shrink-0 text-xs text-muted-foreground"
                  >
                    {relativeTime(item.occurred_at)}
                  </time>
                </li>
              );
            })}
          </ol>
        )}
      </PanelBody>
    </Panel>
  );
}

/* ------------------------------------------------------------------------ */

/**
 * "4m ago", "3d ago".
 *
 * Operational screens are read to answer "is this current?", and a relative
 * age answers that without arithmetic. The absolute timestamp is kept in
 * `title`/`dateTime` so precision is one hover away and machine-readable.
 */
function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";

  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 0) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 2_592_000) return `${Math.floor(seconds / 86_400)}d ago`;
  return new Date(iso).toLocaleDateString();
}
