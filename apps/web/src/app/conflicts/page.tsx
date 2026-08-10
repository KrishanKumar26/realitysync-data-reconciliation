"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Panel, PanelBody, PanelHeader } from "@/components/ui/panel";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { StatusDot, type StatusTone } from "@/components/ui/status-dot";
import { ApiError } from "@/lib/api";
import {
  listConflicts,
  updateConflict,
  type Conflict,
  type ConflictSeverity,
  type ConflictStatus,
} from "@/lib/reality";
import { cn } from "@/lib/utils";

type State =
  | { kind: "loading" }
  | { kind: "ready"; conflicts: Conflict[] }
  | { kind: "error"; message: string };

const FILTERS: { value: ConflictStatus | "all"; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "acknowledged", label: "Acknowledged" },
  { value: "resolved", label: "Resolved" },
  { value: "dismissed", label: "Dismissed" },
  { value: "all", label: "All" },
];

const SEVERITY_TONE: Record<ConflictSeverity, StatusTone> = {
  critical: "down",
  high: "down",
  medium: "degraded",
  low: "unknown",
  // Not graded. Deliberately not shown as "low", which would read as
  // "harmless" when in fact nothing has assessed it.
  unspecified: "unknown",
};

const TYPE_LABEL: Record<string, string> = {
  value_conflict: "Value conflict",
  source_disagreement: "Source disagreement",
  contested_state: "Contested state",
};

/**
 * Conflicts.
 *
 * Every conflict shown is real: detected by the engine from actual observations
 * where sources disagreed. Detection is categorical, so this page works today
 * even though confidence scoring is blocked on the missing specification —
 * which is why severity may read "not graded" rather than a level.
 */
export default function ConflictsPage() {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [filter, setFilter] = useState<ConflictStatus | "all">("open");
  const [pendingId, setPendingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const conflicts = await listConflicts(
        filter === "all" ? {} : { status: filter },
      );
      setState({ kind: "ready", conflicts });
    } catch (error) {
      setState({
        kind: "error",
        message:
          error instanceof ApiError ? error.message : "Could not load conflicts.",
      });
    }
  }, [filter]);

  useEffect(() => {
    setState({ kind: "loading" });
    void load();
  }, [load]);

  async function act(
    conflict: Conflict,
    status: "acknowledged" | "resolved" | "dismissed",
  ) {
    setPendingId(conflict.id);
    try {
      await updateConflict(conflict.id, { status });
      await load();
    } finally {
      setPendingId(null);
    }
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">Conflicts</h1>
        <p className="mt-1.5 text-sm text-muted-foreground">
          Disagreements detected between sources.
        </p>
      </header>

      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter by status">
        {FILTERS.map((option) => (
          <button
            key={option.value}
            type="button"
            aria-pressed={filter === option.value}
            onClick={() => setFilter(option.value)}
            className={cn(
              "rounded-md border px-3 py-1.5 text-sm transition-colors duration-150",
              filter === option.value
                ? "border-border-strong bg-muted text-foreground"
                : "border-border text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            {option.label}
          </button>
        ))}
      </div>

      {state.kind === "loading" ? (
        <div className="space-y-2.5" data-testid="conflicts-loading">
          <Skeleton className="h-28 w-full" />
          <Skeleton className="h-28 w-full" />
        </div>
      ) : null}

      {state.kind === "error" ? (
        <Panel>
          <PanelBody className="p-0">
            <ErrorState
              title="Could not load conflicts"
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

      {state.kind === "ready" && state.conflicts.length === 0 ? (
        <Panel>
          <PanelBody className="p-0">
            <EmptyState
              title={
                filter === "open"
                  ? "No open conflicts"
                  : `No ${filter === "all" ? "" : filter} conflicts`
              }
              description="A conflict appears when two sources state different values for the same attribute of the same entity. Nothing is shown here until that actually happens."
            />
          </PanelBody>
        </Panel>
      ) : null}

      {state.kind === "ready" && state.conflicts.length > 0 ? (
        <ul className="space-y-3">
          {state.conflicts.map((conflict) => (
            <li key={conflict.id}>
              <ConflictCard
                conflict={conflict}
                pending={pendingId === conflict.id}
                onAct={act}
              />
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function ConflictCard({
  conflict,
  pending,
  onAct,
}: {
  conflict: Conflict;
  pending: boolean;
  onAct: (
    conflict: Conflict,
    status: "acknowledged" | "resolved" | "dismissed",
  ) => void;
}) {
  const graded = conflict.severity !== "unspecified";
  const competing = conflict.details.competing_values ?? [];

  return (
    <Panel>
      <PanelHeader
        title={TYPE_LABEL[conflict.conflict_type] ?? conflict.conflict_type}
        description={conflict.summary}
        action={
          <StatusDot
            tone={SEVERITY_TONE[conflict.severity]}
            label={graded ? conflict.severity : "Not graded"}
          />
        }
      />
      <PanelBody className="space-y-4">
        <dl className="grid gap-x-8 gap-y-3 sm:grid-cols-4">
          <div>
            <dt className="text-xs uppercase tracking-wide text-muted-foreground">
              Entity
            </dt>
            <dd className="tabular mt-1 text-sm text-foreground">
              {conflict.entity_natural_key ?? conflict.entity_id.slice(0, 8)}
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-muted-foreground">
              Attribute
            </dt>
            <dd className="tabular mt-1 text-sm text-foreground">
              {conflict.attribute}
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-muted-foreground">
              Divergence
            </dt>
            <dd className="tabular mt-1 text-sm text-foreground">
              {conflict.details.divergence ?? "—"}
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-muted-foreground">
              Score
            </dt>
            <dd className="tabular mt-1 text-sm text-foreground">
              {conflict.score ?? "Not scored"}
            </dd>
          </div>
        </dl>

        {competing.length > 0 ? (
          <div>
            <p className="text-xs uppercase tracking-wide text-muted-foreground">
              Competing values
            </p>
            <ul className="mt-2 space-y-1.5">
              {competing.map((candidate, index) => (
                <li
                  key={index}
                  className="flex flex-wrap items-baseline justify-between gap-2 rounded-md border border-border px-3 py-2"
                >
                  <span className="tabular text-sm text-foreground">
                    {JSON.stringify(candidate.value)}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {candidate.observation_count}{" "}
                    {candidate.observation_count === 1
                      ? "observation"
                      : "observations"}{" "}
                    from {candidate.sources.length}{" "}
                    {candidate.sources.length === 1 ? "source" : "sources"}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {!graded ? (
          <p className="rounded-md border border-border bg-muted px-3.5 py-2.5 text-xs text-muted-foreground">
            This disagreement was detected from the evidence, which needs no
            formula. Grading it — a severity and a 0–1 score — requires the
            confidence specification, which is not yet available. It is shown
            ungraded rather than assumed harmless.
          </p>
        ) : null}

        {conflict.resolution_note ? (
          <p className="text-sm text-muted-foreground">
            <span className="text-foreground">Note:</span>{" "}
            {conflict.resolution_note}
          </p>
        ) : null}

        <div className="flex flex-wrap items-center gap-2">
          {conflict.status === "open" ? (
            <>
              <Button
                size="sm"
                variant="secondary"
                disabled={pending}
                onClick={() => onAct(conflict, "acknowledged")}
              >
                Acknowledge
              </Button>
              <Button
                size="sm"
                variant="secondary"
                disabled={pending}
                onClick={() => onAct(conflict, "resolved")}
              >
                Mark resolved
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={pending}
                onClick={() => onAct(conflict, "dismissed")}
              >
                Dismiss
              </Button>
            </>
          ) : (
            <span className="text-xs capitalize text-muted-foreground">
              {conflict.status}
              {conflict.resolved_at
                ? ` · ${new Date(conflict.resolved_at).toLocaleString()}`
                : ""}
            </span>
          )}
          <span className="ml-auto text-xs text-muted-foreground">
            Detected {new Date(conflict.detected_at).toLocaleString()}
          </span>
        </div>
      </PanelBody>
    </Panel>
  );
}
