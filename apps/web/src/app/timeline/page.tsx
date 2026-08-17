"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Panel, PanelBody, PanelHeader } from "@/components/ui/panel";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { ApiError } from "@/lib/api";
import {
  fetchTimeline,
  listEntities,
  type Entity,
  type Timeline,
  type TimeAxis,
} from "@/lib/reality";
import { cn } from "@/lib/utils";

type State =
  | { kind: "loading" }
  | { kind: "no-entities" }
  | { kind: "ready"; entities: Entity[]; timeline: Timeline }
  | { kind: "error"; message: string };

const AXES: { value: TimeAxis; label: string; question: string }[] = [
  {
    value: "event",
    label: "What was true",
    question: "Ordered by when each fact was true, according to the source.",
  },
  {
    value: "knowledge",
    label: "What we knew",
    question:
      "Ordered by when RealitySync learned each fact. Differs from the other view exactly where something arrived late.",
  },
];

/**
 * Timeline.
 *
 * The screen that makes the two time axes visible. "What was true at T" and
 * "what did we know at T" are different questions with different answers, and
 * the difference is precisely the late-arriving observations — which are
 * flagged rather than left for the reader to infer.
 *
 * Every event is a real observation. Nothing here depends on the missing
 * confidence specification: this reports what sources said and when, and
 * asserts nothing about which is right.
 */
export default function TimelinePage() {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [entityId, setEntityId] = useState<string | null>(null);
  const [axis, setAxis] = useState<TimeAxis>("event");

  const load = useCallback(async () => {
    try {
      const entities = await listEntities();
      if (entities.length === 0) {
        setState({ kind: "no-entities" });
        return;
      }

      const selected = entityId ?? entities[0]!.id;
      const timeline = await fetchTimeline(selected, { axis });

      setEntityId(selected);
      setState({ kind: "ready", entities, timeline });
    } catch (error) {
      setState({
        kind: "error",
        message:
          error instanceof ApiError
            ? error.message
            : "Could not load the timeline.",
      });
    }
  }, [entityId, axis]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">Timeline</h1>
        <p className="mt-1.5 text-sm text-muted-foreground">
          Observations, events and state changes over time.
        </p>
      </header>

      {state.kind === "loading" ? (
        <div className="space-y-2.5" data-testid="timeline-loading">
          <Skeleton className="h-10 w-64" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : null}

      {state.kind === "error" ? (
        <Panel>
          <PanelBody className="p-0">
            <ErrorState
              title="Could not load the timeline"
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

      {state.kind === "no-entities" ? (
        <Panel>
          <PanelBody className="p-0">
            <EmptyState
              title="No items yet"
              description="A timeline is the history of one thing. Create an item and link a synced table to it, and every record already received will appear here."
            />
          </PanelBody>
        </Panel>
      ) : null}

      {state.kind === "ready" ? (
        <>
          <div className="flex flex-wrap items-end gap-4">
            <div className="min-w-56">
              <label
                htmlFor="entity"
                className="block text-xs uppercase tracking-wide text-muted-foreground"
              >
                Entity
              </label>
              <select
                id="entity"
                value={entityId ?? ""}
                onChange={(event) => setEntityId(event.target.value)}
                className="mt-1.5 h-10 w-full rounded-md border border-border-strong bg-background px-3 text-sm text-foreground focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/35"
              >
                {state.entities.map((entity) => (
                  <option key={entity.id} value={entity.id}>
                    {entity.natural_key} ({entity.observation_count})
                  </option>
                ))}
              </select>
            </div>

            <div className="flex gap-1.5" role="group" aria-label="View by">
              {AXES.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  aria-pressed={axis === option.value}
                  title={option.question}
                  onClick={() => setAxis(option.value)}
                  className={cn(
                    "rounded-md border px-3 py-2 text-sm transition-colors duration-150",
                    axis === option.value
                      ? "border-border-strong bg-muted text-foreground"
                      : "border-border text-muted-foreground hover:bg-muted hover:text-foreground",
                  )}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          <p className="text-sm text-muted-foreground">
            {AXES.find((a) => a.value === axis)?.question}
          </p>

          <Panel>
            <PanelHeader
              title={`${state.timeline.event_count} ${
                state.timeline.event_count === 1 ? "record" : "records"
              }`}
              description={
                state.timeline.late_arrival_count > 0
                  ? `${state.timeline.late_arrival_count} arrived after the fact was true — the two views differ for those.`
                  : "Every observation arrived as its fact became true, so both views agree."
              }
            />
            <PanelBody
              className={state.timeline.events.length > 0 ? "p-0" : undefined}
            >
              {state.timeline.events.length === 0 ? (
                <EmptyState
                  title="Nothing recorded yet"
                  description="No data is linked to this item yet."
                  className="py-10"
                />
              ) : (
                <ol className="divide-y divide-border">
                  {state.timeline.events.map((event) => (
                    <li key={event.observation_id} className="px-5 py-3.5">
                      <div className="flex flex-wrap items-baseline justify-between gap-2">
                        <span className="text-sm font-medium text-foreground">
                          {event.source_name}
                        </span>
                        {event.arrived_late ? (
                          <span className="rounded-full border border-border px-2.5 py-0.5 text-xs text-status-degraded">
                            arrived {formatLag(event.lag_seconds)} late
                          </span>
                        ) : null}
                      </div>

                      <dl className="tabular mt-1.5 flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted-foreground">
                        <div className="flex gap-1.5">
                          <dt>true at</dt>
                          <dd className="text-foreground">
                            {new Date(event.event_time).toLocaleString()}
                          </dd>
                        </div>
                        <div className="flex gap-1.5">
                          <dt>learned at</dt>
                          <dd className="text-foreground">
                            {new Date(event.ingested_at).toLocaleString()}
                          </dd>
                        </div>
                        <div className="flex gap-1.5">
                          <dt>semantics</dt>
                          <dd>{event.event_time_semantics}</dd>
                        </div>
                      </dl>

                      <pre className="tabular mt-2 overflow-x-auto rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
                        {JSON.stringify(event.values, null, 2)}
                      </pre>
                    </li>
                  ))}
                </ol>
              )}
            </PanelBody>
          </Panel>

          {state.timeline.truncated ? (
            <p className="text-xs text-muted-foreground">
              Showing the most recent {state.timeline.event_count} observations.
              More exist.
            </p>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

function formatLag(seconds: number): string {
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86_400) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86_400)}d`;
}
