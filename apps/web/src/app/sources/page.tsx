"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AddSourceForm } from "@/components/sources/add-source-form";
import { SourceStatusBadge } from "@/components/sources/status-badge";
import { Button } from "@/components/ui/button";
import { Panel, PanelBody, PanelHeader } from "@/components/ui/panel";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { ApiError } from "@/lib/api";
import {
  listSources,
  type DataSource,
  SOURCE_KIND_LABELS,
} from "@/lib/sources";

type State =
  | { kind: "loading" }
  | { kind: "ready"; sources: DataSource[] }
  | { kind: "error"; message: string };

/**
 * Sources.
 *
 * Every number here is real: stream and observation counts come from the
 * database, and status reflects the last actual connection attempt. A source
 * that has never been tested says so rather than showing a green dot.
 */
export default function SourcesPage() {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    try {
      setState({ kind: "ready", sources: await listSources() });
    } catch (error) {
      setState({
        kind: "error",
        message:
          error instanceof ApiError
            ? error.message
            : "Could not load data sources.",
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
          <h1 className="text-xl font-semibold tracking-tight">Sources</h1>
          <p className="mt-1.5 text-sm text-muted-foreground">
            Databases RealitySync reads observations from.
          </p>
        </div>
        {state.kind === "ready" && state.sources.length > 0 && !adding ? (
          <Button onClick={() => setAdding(true)}>Add source</Button>
        ) : null}
      </header>

      {adding ? (
        <Panel className="animate-rise">
          <PanelHeader
            title="Connect a database"
            description="RealitySync connects outbound over TLS and reads only what you configure."
          />
          <PanelBody>
            <AddSourceForm
              onCancel={() => setAdding(false)}
              onCreated={() => {
                setAdding(false);
                void load();
              }}
            />
          </PanelBody>
        </Panel>
      ) : null}

      {state.kind === "loading" ? (
        <div className="space-y-2.5" data-testid="sources-loading">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : null}

      {state.kind === "error" ? (
        <Panel>
          <PanelBody className="p-0">
            <ErrorState
              title="Could not load sources"
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

      {state.kind === "ready" && state.sources.length === 0 && !adding ? (
        <Panel>
          <PanelBody className="p-0">
            <EmptyState
              title="No sources connected"
              description="RealitySync reports state only from real connected sources. Connect a PostgreSQL or MySQL database to start receiving records."
              action={
                <Button onClick={() => setAdding(true)}>Add source</Button>
              }
            />
          </PanelBody>
        </Panel>
      ) : null}

      {state.kind === "ready" && state.sources.length > 0 ? (
        <ul className="space-y-2.5">
          {state.sources.map((source) => (
            <li key={source.id}>
              <Link
                href={`/sources/${source.id}`}
                className="surface block px-5 py-4 transition-colors duration-150 hover:border-border-strong"
              >
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-foreground">
                      {source.name}
                    </p>
                    <p className="tabular mt-1 truncate text-xs text-muted-foreground">
                      {/* The source type leads the line: with more than one
                          kind connected, "which system is this" is the first
                          thing an operator needs, and host:port alone does
                          not answer it. */}
                      {SOURCE_KIND_LABELS[source.kind]} ·{" "}
                      {source.connection.host}:{source.connection.port}/
                      {source.connection.database} ·{" "}
                      {source.connection.ssl_mode}
                    </p>
                  </div>
                  <SourceStatusBadge status={source.status} />
                </div>

                <dl className="mt-3 flex flex-wrap gap-x-8 gap-y-1.5 text-xs">
                  <div className="flex gap-1.5">
                    <dt className="text-muted-foreground">Streams</dt>
                    <dd className="tabular text-foreground">
                      {source.stream_count}
                    </dd>
                  </div>
                  <div className="flex gap-1.5">
                    <dt className="text-muted-foreground">Observations</dt>
                    <dd className="tabular text-foreground">
                      {source.observation_count.toLocaleString()}
                    </dd>
                  </div>
                  <div className="flex gap-1.5">
                    <dt className="text-muted-foreground">Last sync</dt>
                    <dd className="text-foreground">
                      {source.last_synced_at
                        ? new Date(source.last_synced_at).toLocaleString()
                        : "Never"}
                    </dd>
                  </div>
                </dl>

                {source.last_error ? (
                  <p className="mt-2.5 text-xs text-status-down">
                    {source.last_error}
                  </p>
                ) : null}
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
