"use client";

import { Check, GitCompareArrows, ShieldQuestion, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Badge, type BadgeTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import {
  Panel,
  PanelBody,
  PanelFooter,
  PanelHeader,
} from "@/components/ui/panel";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
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

const SEVERITY_TONE: Record<ConflictSeverity, BadgeTone> = {
  critical: "down",
  high: "down",
  medium: "degraded",
  low: "neutral",
  // Not graded. Deliberately not shown as "low", which would read as
  // "harmless" when in fact nothing has assessed it.
  unspecified: "neutral",
};

const TYPE_LABEL: Record<string, string> = {
  value_conflict: "Different values",
  source_disagreement: "Sources disagree",
  contested_state: "No agreed value",
};

/**
 * Conflicts.
 *
 * Every conflict shown is real: detected from actual records where sources
 * disagreed. Detection is categorical, so this page works today even though
 * confidence scoring is blocked on the missing specification — which is why
 * severity may read "not graded" rather than a level.
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
          error instanceof ApiError
            ? error.message
            : "Could not load conflicts.",
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
      <PageHeader
        title="Conflicts"
        description="Where two sources describe the same thing differently."
      />

      {/* Segmented filter. Reads as one control rather than five buttons that
          happen to sit next to each other. */}
      <div
        className="inline-flex flex-wrap gap-1 rounded-lg border border-border bg-muted/40 p-1"
        role="group"
        aria-label="Filter by status"
      >
        {FILTERS.map((option) => (
          <button
            key={option.value}
            type="button"
            aria-pressed={filter === option.value}
            onClick={() => setFilter(option.value)}
            className={cn(
              "rounded-md px-3 py-1.5 text-sm transition-colors duration-150",
              filter === option.value
                ? "bg-panel font-medium text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {option.label}
          </button>
        ))}
      </div>

      {state.kind === "loading" ? (
        <div className="space-y-4" data-testid="conflicts-loading">
          <Skeleton className="h-56 w-full" />
          <Skeleton className="h-56 w-full" />
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
              icon={<GitCompareArrows />}
              title={
                filter === "open"
                  ? "No open conflicts"
                  : `No ${filter === "all" ? "" : filter} conflicts`
              }
              description="A conflict appears when two sources state different values for the same field of the same item. Nothing is shown here until that actually happens."
            />
          </PanelBody>
        </Panel>
      ) : null}

      {state.kind === "ready" && state.conflicts.length > 0 ? (
        <ul className="space-y-4">
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
  const mostSupported = Math.max(
    ...competing.map((candidate) => candidate.observation_count),
    0,
  );

  return (
    <Panel>
      <PanelHeader
        icon={<GitCompareArrows />}
        title={TYPE_LABEL[conflict.conflict_type] ?? conflict.conflict_type}
        description={conflict.summary}
        action={
          <Badge tone={SEVERITY_TONE[conflict.severity]} dot>
            {graded ? conflict.severity : "Not graded"}
          </Badge>
        }
      />
      <PanelBody className="space-y-5">
        <dl className="grid gap-x-8 gap-y-3 sm:grid-cols-2 lg:grid-cols-4">
          {[
            [
              "Item",
              conflict.entity_natural_key ?? conflict.entity_id.slice(0, 8),
            ],
            ["Field", conflict.attribute],
            ["Divergence", conflict.details.divergence ?? "—"],
            ["Score", conflict.score ?? "Not scored"],
          ].map(([label, value]) => (
            <div key={label as string}>
              <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                {label}
              </dt>
              <dd className="tabular mt-1 break-words text-sm text-foreground">
                {value}
              </dd>
            </div>
          ))}
        </dl>

        {competing.length > 0 ? (
          <div>
            <p className="text-xs uppercase tracking-wide text-muted-foreground">
              What each side says
            </p>
            <ul className="mt-2.5 grid gap-2.5 sm:grid-cols-2">
              {competing.map((candidate, index) => (
                <li
                  key={index}
                  className="rounded-lg border border-border bg-muted/30 p-4"
                >
                  <p className="tabular break-words text-base font-semibold text-foreground">
                    {JSON.stringify(candidate.value)}
                  </p>
                  <p className="mt-2 text-xs text-muted-foreground">
                    {candidate.observation_count}{" "}
                    {candidate.observation_count === 1 ? "record" : "records"}{" "}
                    from {candidate.sources.length}{" "}
                    {candidate.sources.length === 1 ? "source" : "sources"}
                  </p>
                  {/* Bar length is share of the best-supported candidate — a
                      count comparison, never a claim about which is right. */}
                  <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-accent-cyan/70"
                      style={{
                        width: `${
                          mostSupported > 0
                            ? (candidate.observation_count / mostSupported) *
                              100
                            : 0
                        }%`,
                      }}
                    />
                  </div>
                  {candidate.sources.length > 0 ? (
                    <div className="mt-2.5 flex flex-wrap gap-1.5">
                      {candidate.sources.map((source) => (
                        <Badge key={String(source)} tone="outline" size="sm">
                          {String(source)}
                        </Badge>
                      ))}
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {!graded ? (
          <p className="flex gap-2.5 rounded-md border border-border bg-muted/40 px-3.5 py-3 text-xs leading-relaxed text-muted-foreground">
            <ShieldQuestion
              className="h-4 w-4 shrink-0 text-status-degraded"
              aria-hidden="true"
            />
            <span>
              This disagreement was detected from the evidence, which needs no
              formula. Grading it — a severity and a 0–1 score — requires the
              confidence specification, which is not yet available. It is shown
              ungraded rather than assumed harmless.
            </span>
          </p>
        ) : null}

        {conflict.resolution_note ? (
          <p className="text-sm text-muted-foreground">
            <span className="text-foreground">Note:</span>{" "}
            {conflict.resolution_note}
          </p>
        ) : null}

        {conflict.status === "open" ? (
          <div className="flex flex-wrap items-center gap-2">
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
              <Check aria-hidden="true" />
              Mark resolved
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={pending}
              onClick={() => onAct(conflict, "dismissed")}
            >
              <X aria-hidden="true" />
              Dismiss
            </Button>
          </div>
        ) : null}
      </PanelBody>

      <PanelFooter>
        <span className="capitalize">
          {conflict.status}
          {conflict.resolved_at
            ? ` · ${new Date(conflict.resolved_at).toLocaleString()}`
            : ""}
        </span>
        <span className="tabular">
          Detected {new Date(conflict.detected_at).toLocaleString()}
        </span>
      </PanelFooter>
    </Panel>
  );
}
