"use client";

import {
  CheckCircle2,
  Inbox,
  Link2,
  Pencil,
  Play,
  Plug,
  RefreshCw,
  Search,
  Table2,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { EditSourceForm } from "@/components/sources/edit-source-form";
import { SchemaExplorer } from "@/components/sources/schema-explorer";
import {
  SourceStatusBadge,
  SyncStatusBadge,
} from "@/components/sources/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmAction } from "@/components/ui/confirm-action";
import { DataView } from "@/components/ui/data-view";
import { PageHeader } from "@/components/ui/page-header";
import {
  Panel,
  PanelBody,
  PanelFooter,
  PanelHeader,
} from "@/components/ui/panel";
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
 * Source detail: connection, tables, sync history, records.
 *
 * The page is ordered the way the work happens — connect, find tables, add
 * them, sync, look at what arrived — so someone setting up a source can work
 * straight down the screen without being told the sequence.
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
  const [editing, setEditing] = useState(false);

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
        <Skeleton className="h-9 w-56" />
        <Skeleton className="h-44 w-full" />
        <Skeleton className="h-44 w-full" />
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
      <PageHeader
        back={{ href: "/sources", label: "Sources" }}
        title={source.name}
        description={`${source.connection.host}:${source.connection.port}/${source.connection.database}`}
        actions={
          <>
            <SourceStatusBadge status={source.status} />
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void handleTest()}
              disabled={testing}
            >
              <Plug aria-hidden="true" />
              {testing ? "Testing…" : "Test connection"}
            </Button>
            <Button
              size="sm"
              onClick={() => void handleSync()}
              disabled={syncing || enabledStreams.length === 0}
            >
              {syncing ? (
                <RefreshCw className="animate-spin" aria-hidden="true" />
              ) : (
                <Play aria-hidden="true" />
              )}
              {syncing ? "Syncing…" : "Sync now"}
            </Button>
          </>
        }
      />

      {/* Sync feedback rides at the top, next to the button that caused it,
          rather than inside a panel further down the page. */}
      {syncError ? (
        <p
          role="alert"
          className="rounded-md border border-status-down/25 bg-status-down/5 px-4 py-3 text-sm text-status-down"
        >
          {syncError}
        </p>
      ) : null}

      {lastRun ? (
        <div
          role="status"
          className="animate-rise flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border border-status-healthy/25 bg-status-healthy/5 px-4 py-3 text-sm"
        >
          <CheckCircle2
            className="h-4 w-4 shrink-0 text-status-healthy"
            aria-hidden="true"
          />
          <span className="text-foreground">
            <span className="tabular font-medium">{lastRun.rows_created}</span>{" "}
            new {lastRun.rows_created === 1 ? "record" : "records"} from{" "}
            <span className="tabular font-medium">{lastRun.rows_seen}</span>{" "}
            {lastRun.rows_seen === 1 ? "row" : "rows"}
            {lastRun.rows_skipped > 0
              ? ` · ${lastRun.rows_skipped} already recorded`
              : ""}
          </span>
        </div>
      ) : null}

      {/* --- Connection --- */}
      <Panel>
        <PanelHeader
          icon={<Plug />}
          title="Connection"
          description="Your password is stored scrambled and is never shown again — not on this page, not anywhere."
          action={
            !editing ? (
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setEditing(true)}
              >
                <Pencil aria-hidden="true" />
                Edit
              </Button>
            ) : undefined
          }
        />
        <PanelBody>
          {editing ? (
            <EditSourceForm
              source={source}
              onCancel={() => setEditing(false)}
              onSaved={() => {
                setEditing(false);
                setTestResult(null);
                void load();
              }}
            />
          ) : (
            <>
              <dl className="grid gap-x-8 gap-y-4 sm:grid-cols-2 lg:grid-cols-3">
                {[
                  [
                    "Host",
                    `${source.connection.host}:${source.connection.port}`,
                  ],
                  ["Database", source.connection.database],
                  ["Username", source.connection.username],
                  ["Encryption", source.connection.ssl_mode],
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
                  className={
                    testResult.status === "connected"
                      ? "animate-rise mt-5 rounded-md border border-status-healthy/25 bg-status-healthy/5 px-4 py-3"
                      : "animate-rise mt-5 rounded-md border border-status-down/25 bg-status-down/5 px-4 py-3"
                  }
                >
                  {testResult.status === "connected" ? (
                    <>
                      <p className="flex items-center gap-2 text-sm font-medium text-status-healthy">
                        <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                        Connected over {testResult.tls_version ?? "TLS"}
                      </p>
                      <p className="tabular mt-1.5 text-xs text-muted-foreground">
                        {testResult.server_version} · as{" "}
                        {testResult.connected_as} · {testResult.latency_ms}ms
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
                <p className="mt-4 rounded-md border border-status-down/25 bg-status-down/5 px-4 py-3 text-sm text-status-down">
                  {source.last_error}
                </p>
              ) : null}
            </>
          )}
        </PanelBody>
      </Panel>

      {/* --- Tables in use --- */}
      <Panel>
        <PanelHeader
          icon={<Table2 />}
          title="Tables"
          description="The tables RealitySync reads, and how often it checks them."
          action={
            <Badge tone={enabledStreams.length > 0 ? "healthy" : "neutral"}>
              {enabledStreams.length} of {streams.length} enabled
            </Badge>
          }
        />
        <PanelBody className={streams.length > 0 ? "p-0" : undefined}>
          {streams.length === 0 ? (
            <EmptyState
              icon={<Table2 />}
              title="No tables added yet"
              description="Find tables below and add the ones you want RealitySync to read."
              className="py-10"
            />
          ) : (
            <TableContainer>
              <Table>
                <THead>
                  <TH>Table</TH>
                  <TH>Row id</TH>
                  <TH>Date column</TH>
                  <TH align="right">Records</TH>
                  <TH>Schedule</TH>
                  <TH align="right" />
                </THead>
                <TBody>
                  {streams.map((stream) => (
                    <TR key={stream.id}>
                      <TD numeric className="font-medium">
                        {stream.qualified_name}
                      </TD>
                      <TD numeric className="text-muted-foreground">
                        {stream.primary_key_columns.join(", ")}
                      </TD>
                      <TD numeric className="text-muted-foreground">
                        {stream.event_time_column ?? "when read"}
                        <span className="ml-1 text-xs">
                          ({stream.event_time_semantics})
                        </span>
                      </TD>
                      <TD numeric align="right">
                        {stream.observation_count.toLocaleString()}
                      </TD>
                      <TD>
                        {/* A disabled table is not polled at all, and says so
                            rather than showing an interval it does not follow. */}
                        {stream.enabled ? (
                          <Badge tone="healthy" size="sm" dot>
                            every {formatInterval(stream.poll_interval_seconds)}
                          </Badge>
                        ) : (
                          <Badge tone="neutral" size="sm">
                            not checked automatically
                          </Badge>
                        )}
                      </TD>
                      <TD align="right">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => void toggleStream(stream)}
                        >
                          {stream.enabled ? "Disable" : "Enable"}
                        </Button>
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableContainer>
          )}
        </PanelBody>
        {enabledStreams.length === 0 && streams.length > 0 ? (
          <PanelFooter>
            <span>Enable at least one table before syncing.</span>
          </PanelFooter>
        ) : null}
      </Panel>

      {/* --- Schema discovery --- */}
      <Panel>
        <PanelHeader
          icon={<Search />}
          title="Available tables"
          description="This is the list of tables your database reports. Nothing inside them is read yet."
        />
        <PanelBody className="p-0">
          <SchemaExplorer
            sourceId={sourceId}
            onStreamCreated={() => void load()}
          />
        </PanelBody>
      </Panel>

      {/* --- Sync history --- */}
      <Panel>
        <PanelHeader
          icon={<RefreshCw />}
          title="Sync history"
          description="Each run reads the tables you turned on and saves anything it has not seen before."
        />
        <PanelBody className={runs.length > 0 ? "p-0" : undefined}>
          {runs.length === 0 ? (
            <EmptyState
              icon={<RefreshCw />}
              title="No syncs have run yet"
              description={
                enabledStreams.length === 0
                  ? "Add and enable at least one table, then run a sync."
                  : "Press Sync now to read the enabled tables."
              }
              className="py-10"
            />
          ) : (
            <TableContainer>
              <Table>
                <THead>
                  <TH>Status</TH>
                  <TH>Started</TH>
                  <TH align="right">Duration</TH>
                  <TH align="right">Rows read</TH>
                  <TH align="right">New</TH>
                  <TH align="right">Skipped</TH>
                </THead>
                <TBody>
                  {runs.map((run) => (
                    <TR key={run.id}>
                      <TD>
                        <SyncStatusBadge status={run.status} />
                        {run.error_message ? (
                          <p className="mt-1 max-w-xs text-xs text-status-down">
                            {run.error_message}
                          </p>
                        ) : null}
                      </TD>
                      <TD numeric className="text-muted-foreground">
                        {new Date(run.started_at).toLocaleString()}
                      </TD>
                      <TD
                        numeric
                        align="right"
                        className="text-muted-foreground"
                      >
                        {run.duration_ms !== null
                          ? `${run.duration_ms}ms`
                          : "—"}
                      </TD>
                      <TD numeric align="right">
                        {run.rows_seen}
                      </TD>
                      <TD numeric align="right">
                        {run.rows_created}
                      </TD>
                      <TD
                        numeric
                        align="right"
                        className="text-muted-foreground"
                      >
                        {run.rows_skipped}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableContainer>
          )}
        </PanelBody>
      </Panel>

      {/* --- Records --- */}
      <Panel>
        <PanelHeader
          icon={<Inbox />}
          title="Records"
          description="Exactly what this database said, newest first. These are never changed afterwards."
          action={
            observations.length > 0 ? (
              <Badge tone="neutral">
                {observations.length > 20
                  ? `20 of ${observations.length}`
                  : `${observations.length}`}
              </Badge>
            ) : undefined
          }
        />
        <PanelBody className={observations.length > 0 ? "p-0" : undefined}>
          {observations.length === 0 ? (
            <EmptyState
              icon={<Inbox />}
              title="No records yet"
              description="Run a sync to read rows from the source. Nothing is shown here until real data has been read."
              className="py-10"
            />
          ) : (
            <ul className="divide-y divide-border">
              {observations.slice(0, 20).map((observation) => (
                <li key={observation.id} className="px-5 py-4">
                  <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5">
                    <span className="tabular inline-flex items-center gap-1.5 text-sm font-medium text-foreground">
                      <Link2
                        className="h-3.5 w-3.5 text-muted-foreground"
                        aria-hidden="true"
                      />
                      {observation.external_id}
                    </span>
                    <span className="tabular text-xs text-muted-foreground">
                      true at{" "}
                      {new Date(observation.event_time).toLocaleString()} ·
                      received{" "}
                      {new Date(observation.ingested_at).toLocaleString()}
                    </span>
                  </div>
                  <DataView value={observation.payload} className="mt-2.5" />
                </li>
              ))}
            </ul>
          )}
        </PanelBody>
      </Panel>

      {/* --- Danger zone --- */}
      <Panel className="border-status-down/25">
        <PanelHeader
          icon={<Trash2 />}
          title="Remove this source"
          description="Removes this database, its saved password, the tables you picked and every record read from it. This cannot be undone."
          action={
            <ConfirmAction
              label="Delete source"
              confirmLabel="Yes, delete permanently"
              pendingLabel="Deleting…"
              onConfirm={handleDelete}
            />
          }
        />
      </Panel>
    </div>
  );
}
