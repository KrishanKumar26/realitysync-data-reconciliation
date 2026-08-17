"use client";

import { Boxes, Clock, History } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DataView } from "@/components/ui/data-view";
import { PageHeader } from "@/components/ui/page-header";
import {
  Panel,
  PanelBody,
  PanelFooter,
  PanelHeader,
} from "@/components/ui/panel";
import { Select } from "@/components/ui/select";
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

/** What the source's date column actually means, in words. */
const DATE_MEANING: Record<string, string> = {
  observed: "when it was true",
  recorded: "when the system wrote it",
  ingest_fallback: "when we read it",
};

const AXES: { value: TimeAxis; label: string; question: string }[] = [
  {
    value: "event",
    label: "What was true",
    question:
      "In the order things actually happened, according to your systems.",
  },
  {
    value: "knowledge",
    label: "What we knew",
    question:
      "In the order RealitySync found out. This differs from the other view wherever news reached us late.",
  },
];

/**
 * Timeline.
 *
 * The screen that makes the two time axes visible. "What was true at T" and
 * "what did we know at T" are different questions with different answers, and
 * the difference is precisely the late-arriving records — which are flagged
 * rather than left for the reader to infer.
 *
 * Rendered as a rail with markers rather than a list of rows. The point of the
 * screen is sequence, and a vertical line is the cheapest way to say "these
 * things happened in this order" without the reader having to compare
 * timestamps.
 *
 * Every event is a real record. Nothing here depends on the missing confidence
 * specification: this reports what sources said and when, and asserts nothing
 * about which is right.
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
      <PageHeader
        title="Timeline"
        description="What each system said, and when it said it."
      />

      {state.kind === "loading" ? (
        <div className="space-y-4" data-testid="timeline-loading">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-48 w-full" />
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
              icon={<Boxes />}
              title="No items yet"
              description="A timeline is the history of one thing. Create an item and link a synced table to it, and every record already received will appear here."
            />
          </PanelBody>
        </Panel>
      ) : null}

      {state.kind === "ready" ? (
        <>
          <Panel>
            <PanelBody className="flex flex-wrap items-end gap-4">
              <Select
                id="entity"
                label="Item"
                containerClassName="w-full sm:w-72"
                value={entityId ?? ""}
                onChange={(event) => setEntityId(event.target.value)}
              >
                {state.entities.map((entity) => (
                  <option key={entity.id} value={entity.id}>
                    {entity.natural_key} ({entity.observation_count} records)
                  </option>
                ))}
              </Select>

              <div>
                <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  View by
                </p>
                <div
                  className="inline-flex gap-1 rounded-lg border border-border bg-muted/40 p-1"
                  role="group"
                  aria-label="View by"
                >
                  {AXES.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      aria-pressed={axis === option.value}
                      title={option.question}
                      onClick={() => setAxis(option.value)}
                      className={cn(
                        "rounded-md px-3 py-1.5 text-sm transition-colors duration-150",
                        axis === option.value
                          ? "bg-panel font-medium text-foreground shadow-sm"
                          : "text-muted-foreground hover:text-foreground",
                      )}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              </div>

              <p className="w-full text-sm text-muted-foreground sm:w-auto sm:flex-1">
                {AXES.find((a) => a.value === axis)?.question}
              </p>
            </PanelBody>
          </Panel>

          <Panel>
            <PanelHeader
              icon={<History />}
              title={`${state.timeline.event_count} ${
                state.timeline.event_count === 1 ? "record" : "records"
              }`}
              description={
                state.timeline.late_arrival_count > 0
                  ? `${state.timeline.late_arrival_count} reached us after they had already happened. The two views differ for those.`
                  : "Nothing reached us late, so both views show the same order."
              }
              action={
                state.timeline.late_arrival_count > 0 ? (
                  <Badge tone="degraded" dot>
                    {state.timeline.late_arrival_count} reached us late
                  </Badge>
                ) : (
                  <Badge tone="healthy" dot>
                    In step
                  </Badge>
                )
              }
            />
            <PanelBody
              className={state.timeline.events.length > 0 ? "p-0" : undefined}
            >
              {state.timeline.events.length === 0 ? (
                <EmptyState
                  icon={<History />}
                  title="Nothing recorded yet"
                  description="No data is linked to this item yet."
                  className="py-10"
                />
              ) : (
                <ol className="relative px-5 py-5">
                  {/* The rail. Decorative — order is already conveyed by the
                      document order of the list. */}
                  <span
                    aria-hidden="true"
                    className="absolute bottom-6 left-[1.9375rem] top-7 w-px bg-border"
                  />
                  {state.timeline.events.map((event) => (
                    <li
                      key={event.observation_id}
                      className="relative flex gap-4 pb-6 last:pb-0"
                    >
                      <span
                        aria-hidden="true"
                        className={cn(
                          "relative z-10 mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border-2 bg-panel",
                          event.arrived_late
                            ? "border-status-degraded text-status-degraded"
                            : "border-border text-muted-foreground",
                        )}
                      >
                        <Clock className="h-3 w-3" />
                      </span>

                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5">
                          <span className="text-sm font-medium text-foreground">
                            {event.source_name}
                          </span>
                          {event.arrived_late ? (
                            <Badge tone="degraded" size="sm">
                              told to us {formatLag(event.lag_seconds)} late
                            </Badge>
                          ) : null}
                        </div>

                        <dl className="tabular mt-1.5 flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted-foreground">
                          <div className="flex gap-1.5">
                            <dt>happened</dt>
                            <dd className="text-foreground">
                              {new Date(event.event_time).toLocaleString()}
                            </dd>
                          </div>
                          <div className="flex gap-1.5">
                            <dt>we found out</dt>
                            <dd className="text-foreground">
                              {new Date(event.ingested_at).toLocaleString()}
                            </dd>
                          </div>
                          <div className="flex gap-1.5">
                            <dt>date means</dt>
                            <dd>
                              {DATE_MEANING[event.event_time_semantics] ??
                                event.event_time_semantics}
                            </dd>
                          </div>
                        </dl>

                        <DataView value={event.values} className="mt-2.5" />
                      </div>
                    </li>
                  ))}
                </ol>
              )}
            </PanelBody>

            {state.timeline.truncated ? (
              <PanelFooter>
                <span>
                  Showing the most recent {state.timeline.event_count} records.
                  More exist.
                </span>
              </PanelFooter>
            ) : null}
          </Panel>
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
