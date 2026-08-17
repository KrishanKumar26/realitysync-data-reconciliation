"use client";

import { KeyRound, Search, Table2 } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  createStream,
  discoverSchema,
  type CreateStreamInput,
  type DiscoveredTable,
  type EventTimeSemantics,
  type SchemaDiscovery,
} from "@/lib/sources";

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; discovery: SchemaDiscovery }
  | { kind: "error"; message: string };

/**
 * Schema explorer.
 *
 * Discovery reads the source's catalog, never its data. Row counts are the
 * planner's estimate and are labelled "approx." everywhere they appear — a
 * number presented as exact when it is not would be precisely the kind of
 * unverified claim this product exists to eliminate.
 */
export function SchemaExplorer({
  sourceId,
  onStreamCreated,
}: {
  sourceId: string;
  onStreamCreated: () => void;
}) {
  const [state, setState] = useState<State>({ kind: "idle" });
  const [selected, setSelected] = useState<DiscoveredTable | null>(null);

  async function discover() {
    setState({ kind: "loading" });
    setSelected(null);
    try {
      setState({ kind: "ready", discovery: await discoverSchema(sourceId) });
    } catch (error) {
      setState({
        kind: "error",
        message:
          error instanceof ApiError
            ? error.message
            : "Could not load the tables.",
      });
    }
  }

  if (state.kind === "idle") {
    return (
      <EmptyState
        icon={<Search />}
        title="Tables not loaded yet"
        description="RealitySync will ask your database what tables it has. It does not look inside them."
        action={
          <Button onClick={() => void discover()}>
            <Search aria-hidden="true" />
            Find tables
          </Button>
        }
      />
    );
  }

  if (state.kind === "loading") {
    return (
      <div className="space-y-2.5 px-5 py-4" data-testid="schema-loading">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-2/3" />
      </div>
    );
  }

  if (state.kind === "error") {
    return (
      <ErrorState
        title="Could not load the tables"
        description={state.message}
        action={
          <Button variant="secondary" onClick={() => void discover()}>
            Try again
          </Button>
        }
      />
    );
  }

  const { discovery } = state;

  if (selected) {
    return (
      <StreamConfigForm
        sourceId={sourceId}
        table={selected}
        onCancel={() => setSelected(null)}
        onCreated={() => {
          setSelected(null);
          onStreamCreated();
          void discover();
        }}
      />
    );
  }

  if (discovery.tables.length === 0) {
    return (
      <EmptyState
        icon={<Table2 />}
        title="No readable tables found"
        description={
          discovery.inaccessible_schemas.length > 0
            ? `RealitySync can see ${discovery.inaccessible_schemas.length} schema(s) it cannot read. Grant USAGE and SELECT to the connecting role.`
            : "The database has no tables this role can select from."
        }
        action={
          <Button variant="secondary" onClick={() => void discover()}>
            Refresh list
          </Button>
        }
      />
    );
  }

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-3">
        <p className="text-sm text-muted-foreground">
          {discovery.tables.length} readable{" "}
          {discovery.tables.length === 1 ? "table" : "tables"} across{" "}
          {discovery.schemas.length}{" "}
          {discovery.schemas.length === 1 ? "schema" : "schemas"}
        </p>
        <Button variant="secondary" size="sm" onClick={() => void discover()}>
          Refresh list
        </Button>
      </div>

      {discovery.inaccessible_schemas.length > 0 ? (
        <p className="border-b border-border bg-muted px-5 py-2.5 text-xs text-muted-foreground">
          Not readable by this role:{" "}
          <span className="tabular">
            {discovery.inaccessible_schemas.join(", ")}
          </span>
        </p>
      ) : null}

      <ul className="divide-y divide-border">
        {discovery.tables.map((table) => (
          <li key={table.qualified_name}>
            <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-3">
              <div className="flex min-w-0 items-start gap-3">
                <span
                  aria-hidden="true"
                  className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-border bg-muted text-muted-foreground"
                >
                  <Table2 className="h-3.5 w-3.5" />
                </span>
                <div className="min-w-0">
                  <p className="tabular truncate text-sm font-medium text-foreground">
                    {table.qualified_name}
                  </p>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5">
                    <Badge tone="outline" size="sm">
                      {table.columns.length} columns
                    </Badge>
                    {table.approximate_row_count !== null ? (
                      <Badge tone="neutral" size="sm">
                        approx. {table.approximate_row_count.toLocaleString()}{" "}
                        rows
                      </Badge>
                    ) : null}
                    {table.primary_key_columns.length > 0 ? (
                      <Badge tone="neutral" size="sm" icon={<KeyRound />}>
                        {table.primary_key_columns.join(", ")}
                      </Badge>
                    ) : null}
                  </div>
                </div>
              </div>

              {table.configured ? (
                <Badge tone="healthy" dot>
                  Added
                </Badge>
              ) : table.primary_key_columns.length === 0 ? (
                <span
                  className="shrink-0 text-xs text-muted-foreground"
                  title="This table has no unique id column, so a row cannot be tracked over time."
                >
                  No unique id
                </span>
              ) : (
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => setSelected(table)}
                >
                  Add
                </Button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

const SEMANTICS: {
  value: EventTimeSemantics;
  label: string;
  description: string;
}[] = [
  {
    value: "observed",
    label: "When it was true",
    description: "Pick this if the date says when the thing actually happened.",
  },
  {
    value: "recorded",
    label: "When the system saved it",
    description:
      "Pick this if the date says when the row was saved, not when it happened.",
  },
  {
    value: "ingest_fallback",
    label: "No date column",
    description:
      "No usable date — RealitySync will use the time it read the row.",
  },
];

/**
 * Stream configuration.
 *
 * Event-time semantics is the question that matters here, so it is asked
 * plainly rather than buried in an advanced section. "When this was true" and
 * "when this was written down" are different facts, and which one a column
 * holds cannot be inferred from its type.
 */
function StreamConfigForm({
  sourceId,
  table,
  onCancel,
  onCreated,
}: {
  sourceId: string;
  table: DiscoveredTable;
  onCancel: () => void;
  onCreated: () => void;
}) {
  const [eventTimeColumn, setEventTimeColumn] = useState<string>(
    table.temporal_columns[0] ?? "",
  );
  const [semantics, setSemantics] = useState<EventTimeSemantics>(
    table.temporal_columns.length > 0 ? "recorded" : "ingest_fallback",
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const usesColumn = semantics !== "ingest_fallback";

  async function submit() {
    setSubmitting(true);
    setError(null);
    const input: CreateStreamInput = {
      schema_name: table.schema_name,
      table_name: table.table_name,
      primary_key_columns: table.primary_key_columns,
      event_time_semantics: semantics,
      event_time_column: usesColumn ? eventTimeColumn : null,
    };
    try {
      await createStream(sourceId, input);
      onCreated();
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not configure the stream.",
      );
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-5 px-5 py-4">
      <div>
        <h3 className="text-sm font-semibold text-foreground">
          Add <span className="tabular">{table.qualified_name}</span>
        </h3>
        <p className="mt-1 text-sm text-muted-foreground">
          Each row is identified by:{" "}
          <span className="tabular">
            {table.primary_key_columns.join(", ")}
          </span>
        </p>
      </div>

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium text-foreground">
          What does this table&apos;s timestamp mean?
        </legend>
        <div className="space-y-1.5 pt-1">
          {SEMANTICS.map((option) => {
            const unavailable =
              option.value !== "ingest_fallback" &&
              table.temporal_columns.length === 0;
            return (
              <label
                key={option.value}
                className={cn(
                  "flex items-start gap-3 rounded-md border border-border px-3 py-2.5 transition-colors duration-150",
                  unavailable
                    ? "cursor-not-allowed opacity-50"
                    : "cursor-pointer hover:bg-muted has-[:checked]:border-border-strong has-[:checked]:bg-muted",
                )}
              >
                <input
                  type="radio"
                  name="semantics"
                  value={option.value}
                  disabled={unavailable}
                  checked={semantics === option.value}
                  onChange={() => setSemantics(option.value)}
                  className="mt-0.5 accent-[var(--color-accent-cyan)]"
                />
                <span className="min-w-0">
                  <span className="block text-sm text-foreground">
                    {option.label}
                  </span>
                  <span className="block text-xs text-muted-foreground">
                    {option.description}
                  </span>
                </span>
              </label>
            );
          })}
        </div>
      </fieldset>

      {usesColumn ? (
        <div className="space-y-1.5">
          <label
            htmlFor="event-time-column"
            className="block text-sm font-medium text-foreground"
          >
            Date column
          </label>
          <select
            id="event-time-column"
            value={eventTimeColumn}
            onChange={(event) => setEventTimeColumn(event.target.value)}
            className="h-10 w-full rounded-md border border-border-strong bg-background px-3 text-sm text-foreground focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/35"
          >
            {table.temporal_columns.map((column) => (
              <option key={column} value={column}>
                {column}
              </option>
            ))}
          </select>
        </div>
      ) : null}

      {error ? (
        <p role="alert" className="text-sm text-status-down">
          {error}
        </p>
      ) : null}

      <div className="flex items-center gap-2.5">
        <Button
          onClick={() => void submit()}
          disabled={submitting || (usesColumn && !eventTimeColumn)}
        >
          {submitting ? "Adding…" : "Add table"}
        </Button>
        <Button variant="ghost" onClick={onCancel} disabled={submitting}>
          Back
        </Button>
      </div>
    </div>
  );
}
