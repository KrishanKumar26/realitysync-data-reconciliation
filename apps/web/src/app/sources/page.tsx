"use client";

import { Database, Plug, Plus } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AddSourceForm } from "@/components/sources/add-source-form";
import { SourceStatusBadge } from "@/components/sources/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
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
 * Every number here is real: table and record counts come from the database,
 * and status reflects the last actual connection attempt. A source that has
 * never been tested says so rather than showing a green dot.
 *
 * Cards rather than a table, unlike the Overview's source list. These rows are
 * navigation targets with four dissimilar facts each — a type, an address, two
 * counts and possibly an error — which is a description, not a column of
 * comparable values.
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
      <PageHeader
        title="Sources"
        description="Databases RealitySync reads from."
        actions={
          state.kind === "ready" && state.sources.length > 0 && !adding ? (
            <Button onClick={() => setAdding(true)}>
              <Plus aria-hidden="true" />
              Add source
            </Button>
          ) : undefined
        }
      />

      {adding ? (
        <Panel className="animate-rise">
          <PanelHeader
            icon={<Plug />}
            title="Connect a database"
            description="RealitySync only reads. It connects over an encrypted link, and only to the tables you pick."
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
        <div
          className="grid gap-4 md:grid-cols-2"
          data-testid="sources-loading"
        >
          <Skeleton className="h-36 w-full" />
          <Skeleton className="h-36 w-full" />
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
              icon={<Database />}
              title="No sources connected"
              description="RealitySync reports state only from real connected sources. Connect a PostgreSQL or MySQL database to start receiving records."
              action={
                <Button onClick={() => setAdding(true)}>
                  <Plus aria-hidden="true" />
                  Add source
                </Button>
              }
            />
          </PanelBody>
        </Panel>
      ) : null}

      {state.kind === "ready" && state.sources.length > 0 ? (
        <ul className="grid gap-4 md:grid-cols-2">
          {state.sources.map((source) => (
            <li key={source.id}>
              <Link
                href={`/sources/${source.id}`}
                className="surface group flex h-full flex-col p-5 transition-colors duration-150 hover:border-border-strong"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex min-w-0 items-start gap-3">
                    <span
                      aria-hidden="true"
                      className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-border bg-muted text-muted-foreground"
                    >
                      <Database className="h-4 w-4" />
                    </span>
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-foreground">
                        {source.name}
                      </p>
                      <p className="tabular mt-0.5 truncate text-xs text-muted-foreground">
                        {source.connection.host}:{source.connection.port}/
                        {source.connection.database}
                      </p>
                    </div>
                  </div>
                  <SourceStatusBadge status={source.status} />
                </div>

                <div className="mt-3 flex flex-wrap gap-1.5">
                  {/* The source type leads: with more than one kind connected,
                      "which system is this" is the first thing an operator
                      needs, and host:port alone does not answer it. */}
                  <Badge tone="outline" size="sm">
                    {SOURCE_KIND_LABELS[source.kind]}
                  </Badge>
                  <Badge tone="neutral" size="sm">
                    Encrypted
                  </Badge>
                </div>

                <dl className="mt-4 grid grid-cols-3 gap-3 border-t border-border pt-3.5">
                  <div>
                    <dt className="text-xs text-muted-foreground">Tables</dt>
                    <dd className="tabular mt-0.5 text-sm font-medium text-foreground">
                      {source.stream_count}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">Records</dt>
                    <dd className="tabular mt-0.5 text-sm font-medium text-foreground">
                      {source.observation_count.toLocaleString()}
                    </dd>
                  </div>
                  <div className="min-w-0">
                    <dt className="text-xs text-muted-foreground">Last sync</dt>
                    <dd
                      className="mt-0.5 truncate text-sm font-medium text-foreground"
                      title={
                        source.last_synced_at
                          ? new Date(source.last_synced_at).toLocaleString()
                          : undefined
                      }
                    >
                      {source.last_synced_at
                        ? new Date(source.last_synced_at).toLocaleDateString()
                        : "Never"}
                    </dd>
                  </div>
                </dl>

                {source.last_error ? (
                  <p className="mt-3 rounded-md border border-status-down/25 bg-status-down/5 px-3 py-2 text-xs text-status-down">
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
