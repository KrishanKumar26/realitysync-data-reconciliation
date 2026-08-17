"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { SchemaExplorer } from "@/components/sources/schema-explorer";
import {
  SourceStatusBadge,
  SyncStatusBadge,
} from "@/components/sources/status-badge";
import { Button } from "@/components/ui/button";
import { Panel, PanelBody, PanelHeader } from "@/components/ui/panel";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { ApiError } from "@/lib/api";
import {
  deleteSource,
  getSource,
  listObservations,
  listStreams,
  listSyncRuns,
  runSync,
  testConnection,
  updateStream,
  type ConnectionTestResult,
  type DataSource,
  type Observation,
  type SourceStream,
  type SyncRun,
} from "@/lib/sources";

interface Loaded {
  source: DataSource;
  streams: SourceStream[];
  runs: SyncRun[];
  observations: Observation[];
}

type State =
  | { kind: "loading" }
  | { kind: "ready"; data: Loaded }
  | { kind: "error"; message: string };

/**
 * Source detail: connection, schema, streams, sync history, observations.
 *
 * The whole Phase 3 vertical slice is visible on this one page, in the order
 * it happens — connect, discover, configure, sync, observe.
 */
/** "30s", "5m", "2h" — the shortest honest rendering of an interval. */
function formatInterval(seconds: number): string {
  if (seconds % 3600 === 0) return `${seconds / 3600}h`;
  if (seconds % 60 === 0) return `${seconds / 60}m`;
  return `${seconds}s`;
}

export default function SourceDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const sourceId = params.id;

  const [state, setState] = useState<State>({ kind: "loading" });
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(
    null,
  );
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<SyncRun | null>(null);

  const load = useCallback(async () => {
    try {
      const [source, streams, runs, observations] = await Promise.all([
        getSource(sourceId),
        listStreams(sourceId),
        listSyncRuns(sourceId),
        listObservations(sourceId),
      ]);
      setState({
        kind: "ready",
        data: { source, streams, runs, observations },
      });
    } catch (error) {
      setState({
        kind: "error",
        message:
          error instanceof ApiError
            ? error.message
            : "Could not load this source.",
      });
    }
  }, [sourceId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleTest() {
    setTesting(true);
    setTestResult(null);
    try {
      setTestResult(await testConnection(sourceId));
    } catch (error) {
      setTestResult({
        status: "failed",
        database: null,
        server_version: null,
        latency_ms: null,
        tls_version: null,
        connected_as: null,
        can_discover_schema: false,
        warnings: [],
        error_code: null,
        error_message:
          error instanceof ApiError
            ? error.message
            : "The connection test failed.",
        remediation: null,
      });
    } finally {
      setTesting(false);
      void load();
    }
  }

  async function handleSync() {
    setSyncing(true);
    setSyncError(null);
    setLastRun(null);
    try {
      setLastRun(await runSync(sourceId));
    } catch (error) {
      setSyncError(
        error instanceof ApiError ? error.message : "The sync failed.",
      );
    } finally {
      setSyncing(false);
      void load();
    }
  }

  async function toggleStream(stream: SourceStream) {
    await updateStream(sourceId, stream.id, { enabled: !stream.enabled });
    void load();
  }

  async function handleDelete() {
    await deleteSource(sourceId);
    router.push("/sources");
  }

  if (state.kind === "loading") {
    return (
      <div className="space-y-4" data-testid="source-loading">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  if (state.kind === "error") {
    return (
      <ErrorState
        title="Could not load this source"
        description={state.message}
        action={
          <Link href="/sources">
            <Button variant="secondary">Back to sources</Button>
          </Link>
        }
      />
    );
  }

  const { source, streams, runs, observations } = state.data;
  const enabledStreams = streams.filter((s) => s.enabled);

  return (
    <div className="space-y-6">
      <header className="space-y-3">
        <Link
          href="/sources"
          className="text-xs text-muted-foreground hover:text-foreground"
        >
          ← Sources
        </Link>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="truncate text-xl font-semibold tracking-tight">
              {source.name}
            </h1>
            <p className="tabular mt-1.5 truncate text-sm text-muted-foreground">
              {source.connection.host}:{source.connection.port}/
              {source.connection.database}
            </p>
          </div>
          <SourceStatusBadge status={source.status} />
        </div>
      </header>

      {/* --- Connection --- */}
      <Panel>
        <PanelHeader
          title="Connection"
          description="Credentials are encrypted at rest and are never returned by the API."
          action={
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void handleTest()}
              disabled={testing}
            >
              {testing ? "Testing…" : "Test connection"}
            </Button>
          }
        />
        <PanelBody>
          <dl className="grid gap-x-8 gap-y-3 sm:grid-cols-3">
            {[
              ["Host", `${source.connection.host}:${source.connection.port}`],
              ["Database", source.connection.database],
              ["Username", source.connection.username],
              ["TLS mode", source.connection.ssl_mode],
              ["Password", "•••••••• stored"],
              [
                "Last connected",
                source.last_connected_at
                  ? new Date(source.last_connected_at).toLocaleString()
                  : "Never",
              ],
            ].map(([label, value]) => (
              <div key={label}>
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                  {label}
                </dt>
                <dd className="tabular mt-1 break-all text-sm text-foreground">
                  {value}
                </dd>
              </div>
            ))}
          </dl>

          {testResult ? (
            <div
              role="status"
              className="mt-5 rounded-md border border-border bg-muted px-4 py-3"
            >
              {testResult.status === "connected" ? (
                <>
                  <p className="text-sm font-medium text-status-healthy">
                    Connected over {testResult.tls_version ?? "TLS"}
                  </p>
                  <p className="tabular mt-1.5 text-xs text-muted-foreground">
                    {testResult.server_version} · as {testResult.connected_as} ·{" "}
                    {testResult.latency_ms}ms
                  </p>
                  {testResult.warnings.map((warning) => (
                    <p
                      key={warning}
                      className="mt-2 text-xs text-status-degraded"
                    >
                      {warning}
                    </p>
                  ))}
                </>
              ) : (
                <>
                  <p className="text-sm font-medium text-status-down">
                    {testResult.error_message}
                  </p>
                  {testResult.remediation ? (
                    <p className="mt-1.5 text-xs text-muted-foreground">
                      {testResult.remediation}
                    </p>
                  ) : null}
                </>
              )}
            </div>
          ) : null}

          {!testResult && source.last_error ? (
            <p className="mt-4 text-sm text-status-down">{source.last_error}</p>
          ) : null}
        </PanelBody>
      </Panel>

      {/* --- Streams --- */}
      <Panel>
        <PanelHeader
          title="Tables"
          description="Tables RealitySync reads from."
        />
        <PanelBody className={streams.length > 0 ? "p-0" : undefined}>
          {streams.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No tables added yet. Find tables below to choose one.
            </p>
          ) : (
            <ul className="divide-y divide-border">
              {streams.map((stream) => (
                <li
                  key={stream.id}
                  className="flex flex-wrap items-center justify-between gap-3 px-5 py-3"
                >
                  <div className="min-w-0">
                    <p className="tabular truncate text-sm text-foreground">
                      {stream.qualified_name}
                    </p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      key: {stream.primary_key_columns.join(", ")} · time:{" "}
                      {stream.event_time_column ?? "when read"} (
                      {stream.event_time_semantics}) ·{" "}
                      {stream.observation_count.toLocaleString()} records ·{" "}
                      {/* The poll interval is honoured by the scheduler, so it
                          has to be visible: an operator cannot otherwise tell
                          how fresh this stream is meant to be. A disabled
                          stream is not polled at all, and says so rather than
                          showing an interval it does not follow. */}
                      {stream.enabled
                        ? `every ${formatInterval(stream.poll_interval_seconds)}`
                        : "not scheduled"}
                    </p>
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => void toggleStream(stream)}
                  >
                    {stream.enabled ? "Disable" : "Enable"}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </PanelBody>
      </Panel>

      {/* --- Schema --- */}
      <Panel>
        <PanelHeader
          title="Available tables"
          description="Read from the database's own catalogue. No table data is read."
        />
        <PanelBody className="p-0">
          <SchemaExplorer
            sourceId={sourceId}
            onStreamCreated={() => void load()}
          />
        </PanelBody>
      </Panel>

      {/* --- Sync --- */}
      <Panel>
        <PanelHeader
          title="Sync"
          description="Reads the tables above and adds new records."
          action={
            <Button
              size="sm"
              onClick={() => void handleSync()}
              disabled={syncing || enabledStreams.length === 0}
            >
              {syncing ? "Syncing…" : "Sync now"}
            </Button>
          }
        />
        <PanelBody className={runs.length > 0 ? "p-0" : undefined}>
          {enabledStreams.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Add and enable at least one table before syncing.
            </p>
          ) : null}

          {syncError ? (
            <p role="alert" className="px-5 py-3 text-sm text-status-down">
              {syncError}
            </p>
          ) : null}

          {lastRun ? (
            <div
              role="status"
              className="mx-5 mt-4 rounded-md border border-border bg-muted px-4 py-3"
            >
              <p className="text-sm text-foreground">
                {lastRun.rows_created} new{" "}
                {lastRun.rows_created === 1 ? "record" : "records"} from{" "}
                {lastRun.rows_seen} {lastRun.rows_seen === 1 ? "row" : "rows"}
                {lastRun.rows_skipped > 0
                  ? ` · ${lastRun.rows_skipped} already recorded`
                  : ""}
              </p>
            </div>
          ) : null}

          {runs.length > 0 ? (
            <ul className="divide-y divide-border">
              {runs.map((run) => (
                <li
                  key={run.id}
                  className="flex flex-wrap items-center justify-between gap-3 px-5 py-3"
                >
                  <div className="min-w-0">
                    <SyncStatusBadge status={run.status} />
                    <p className="tabular mt-1 text-xs text-muted-foreground">
                      {new Date(run.started_at).toLocaleString()}
                      {run.duration_ms !== null
                        ? ` · ${run.duration_ms}ms`
                        : ""}
                    </p>
                    {run.error_message ? (
                      <p className="mt-1 text-xs text-status-down">
                        {run.error_message}
                      </p>
                    ) : null}
                  </div>
                  <p className="tabular shrink-0 text-xs text-muted-foreground">
                    {run.rows_seen} seen · {run.rows_created} new ·{" "}
                    {run.rows_skipped} skipped
                  </p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="px-5 pb-4 pt-2 text-sm text-muted-foreground">
              No syncs have run yet.
            </p>
          )}
        </PanelBody>
      </Panel>

      {/* --- Observations --- */}
      <Panel>
        <PanelHeader
          title="Records"
          description="What this source stated, most recently received first. These are never edited."
        />
        <PanelBody className={observations.length > 0 ? "p-0" : undefined}>
          {observations.length === 0 ? (
            <EmptyState
              title="No records yet"
              description="Run a sync to read rows from the source. Nothing is shown here until real data has been read."
              className="py-10"
            />
          ) : (
            <ul className="divide-y divide-border">
              {observations.slice(0, 20).map((observation) => (
                <li key={observation.id} className="px-5 py-3">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <p className="tabular text-sm text-foreground">
                      {observation.external_id}
                    </p>
                    <p className="tabular text-xs text-muted-foreground">
                      event {new Date(observation.event_time).toLocaleString()}{" "}
                      · ingested{" "}
                      {new Date(observation.ingested_at).toLocaleString()}
                    </p>
                  </div>
                  <pre className="tabular mt-2 overflow-x-auto rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
                    {JSON.stringify(observation.payload, null, 2)}
                  </pre>
                </li>
              ))}
            </ul>
          )}
        </PanelBody>
      </Panel>

      {/* --- Danger zone --- */}
      <Panel>
        <PanelHeader
          title="Remove source"
          description="Deletes the source, its credentials, tables and records. This cannot be undone."
          action={
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void handleDelete()}
            >
              Delete source
            </Button>
          }
        />
      </Panel>
    </div>
  );
}
