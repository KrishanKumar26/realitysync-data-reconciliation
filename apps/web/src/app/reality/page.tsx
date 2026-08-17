"use client";

import { AlertTriangle, Boxes, Calculator, Target } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { EntitySetup } from "@/components/reality/entity-setup";
import { EvidenceTrail } from "@/components/reality/evidence-trail";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DataView } from "@/components/ui/data-view";
import { PageHeader } from "@/components/ui/page-header";
import { Panel, PanelBody, PanelHeader } from "@/components/ui/panel";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { ApiError } from "@/lib/api";
import {
  listEntities,
  listRealityStates,
  recalculate,
  type Entity,
  type RealityState,
  type RecalculateResult,
} from "@/lib/reality";

type State =
  | { kind: "loading" }
  | { kind: "no-entities" }
  | { kind: "ready"; entities: Entity[]; states: RealityState[] }
  | { kind: "error"; message: string };

/**
 * Current State.
 *
 * Shows what RealitySync believes about an item, and — when it cannot believe
 * anything — why.
 *
 * While the approved confidence specification is unavailable, values are still
 * established and recorded but carry no score. That is stated on each value
 * rather than hidden: a blank where a percentage belongs would read as "we
 * forgot", when in fact nobody has defined how to measure.
 */

/** How a status reads, and how confident the reading is. */
const STATUS_TONE: Record<string, BadgeTone> = {
  confirmed: "healthy",
  contested: "down",
  stale: "degraded",
  provisional: "neutral",
  unknown: "neutral",
};

export default function RealityPage() {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [entityId, setEntityId] = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<RecalculateResult | null>(null);
  const [running, setRunning] = useState(false);

  const load = useCallback(async () => {
    try {
      const entities = await listEntities();
      if (entities.length === 0) {
        setState({ kind: "no-entities" });
        return;
      }
      const selected = entityId ?? entities[0]!.id;
      const states = await listRealityStates(selected);
      setEntityId(selected);
      setState({ kind: "ready", entities, states });
    } catch (error) {
      setState({
        kind: "error",
        message:
          error instanceof ApiError
            ? error.message
            : "Could not load the current values.",
      });
    }
  }, [entityId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function runRecalculation() {
    if (!entityId) return;
    setRunning(true);
    setLastRun(null);
    try {
      setLastRun(await recalculate(entityId));
      await load();
    } catch (error) {
      setState({
        kind: "error",
        message:
          error instanceof ApiError
            ? error.message
            : "The recalculation failed.",
      });
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Current State"
        description="Items, their current values and the evidence behind them."
      />

      {state.kind === "loading" ? (
        <div className="space-y-4" data-testid="reality-loading">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-48 w-full" />
        </div>
      ) : null}

      {state.kind === "error" ? (
        <Panel>
          <PanelBody className="p-0">
            <ErrorState
              title="Could not load the current values"
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
              description="An item is one real thing your sources describe — a product, a shipment, an account. Create one and link a synced table to it, and RealitySync can start comparing what each source says."
              action={
                <EntitySetup
                  entity={null}
                  onEntityCreated={() => void load()}
                  onMappingCreated={() => void load()}
                />
              }
            />
          </PanelBody>
        </Panel>
      ) : null}

      {state.kind === "ready" ? (
        <>
          {/* Item picker and the recalculate action, together: choosing an item
              and asking what is true about it is one decision, not two. */}
          <Panel>
            <PanelBody className="flex flex-wrap items-end gap-4">
              <Select
                id="entity"
                label="Item"
                containerClassName="w-full sm:w-72"
                value={entityId ?? ""}
                onChange={(event) => {
                  setEntityId(event.target.value);
                  setLastRun(null);
                }}
              >
                {state.entities.map((entity) => (
                  <option key={entity.id} value={entity.id}>
                    {entity.natural_key} ({entity.observation_count} records)
                  </option>
                ))}
              </Select>
              <Button
                onClick={() => void runRecalculation()}
                disabled={running}
              >
                <Calculator aria-hidden="true" />
                {running ? "Recalculating…" : "Recalculate"}
              </Button>
            </PanelBody>
          </Panel>

          <EntitySetup
            entity={state.entities.find((e) => e.id === entityId) ?? null}
            onEntityCreated={(created) => {
              setEntityId(created.id);
              setLastRun(null);
              void load();
            }}
            onMappingCreated={() => void load()}
          />

          {lastRun?.blocked ? (
            <Panel className="animate-rise border-status-degraded/25">
              <PanelHeader
                icon={<AlertTriangle />}
                title="Values recorded without confidence scores"
                description="RealitySync ran and reported exactly what is missing."
                action={<Badge tone="degraded">Partial</Badge>}
              />
              <PanelBody className="space-y-4">
                <div className="grid gap-3 sm:grid-cols-4">
                  {[
                    ["Fields read", lastRun.attributes_considered],
                    ["Values written", lastRun.states_written],
                    ["Without a score", lastRun.states_unscored],
                    ["Conflicts found", lastRun.conflicts_written],
                  ].map(([label, value]) => (
                    <div
                      key={label as string}
                      className="rounded-md border border-border p-3"
                    >
                      <p className="tabular text-xl font-semibold text-foreground">
                        {value}
                      </p>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        {label}
                      </p>
                    </div>
                  ))}
                </div>

                <p className="text-sm leading-relaxed text-muted-foreground">
                  Everything that follows from the records alone — the values,
                  the evidence, the disagreements — is recorded. Only the
                  confidence score is absent, because the approved specification
                  for calculating it is unavailable. Conflicts needed no
                  formula, so they were found and recorded normally.
                </p>

                {lastRun.blocked_on.length > 0 ? (
                  <p className="tabular text-xs text-muted-foreground">
                    Blocked on: {lastRun.blocked_on.join(", ")}
                  </p>
                ) : null}

                {lastRun.missing_specifications.length > 0 ? (
                  <details className="rounded-md border border-border bg-muted/40 px-4 py-3">
                    <summary className="cursor-pointer text-sm font-medium text-foreground">
                      {lastRun.missing_specifications.length} specifications
                      required
                    </summary>
                    <ul className="mt-3 space-y-2">
                      {lastRun.missing_specifications.map((spec) => (
                        <li
                          key={spec.name}
                          className="text-xs text-muted-foreground"
                        >
                          <span className="tabular text-foreground">
                            {spec.name}
                          </span>
                          {" — "}
                          {spec.description}
                        </li>
                      ))}
                    </ul>
                  </details>
                ) : null}
              </PanelBody>
            </Panel>
          ) : null}

          {lastRun && !lastRun.blocked ? (
            <p
              role="status"
              className="animate-rise rounded-md border border-status-healthy/25 bg-status-healthy/5 px-4 py-3 text-sm text-foreground"
            >
              Wrote {lastRun.states_written}{" "}
              {lastRun.states_written === 1 ? "value" : "values"} and{" "}
              {lastRun.conflicts_written} conflicts.
            </p>
          ) : null}

          <Panel>
            <PanelHeader
              icon={<Target />}
              title="Current values"
              description="One per field, with its evidence and the reason it was chosen."
              action={
                state.states.length > 0 ? (
                  <Badge tone="neutral">
                    {state.states.length}{" "}
                    {state.states.length === 1 ? "field" : "fields"}
                  </Badge>
                ) : undefined
              }
            />
            <PanelBody className={state.states.length > 0 ? "p-0" : undefined}>
              {state.states.length === 0 ? (
                <EmptyState
                  icon={<Target />}
                  title="No values yet"
                  description="Run a recalculation to see what can be established. While the confidence formula is unavailable, values are still recorded — with no score, and the reason stated."
                  className="py-10"
                />
              ) : (
                <ul className="divide-y divide-border">
                  {state.states.map((realityState) => (
                    <li key={realityState.id} className="px-5 py-5">
                      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
                        <h3 className="tabular text-sm font-semibold text-foreground">
                          {realityState.attribute}
                        </h3>
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge
                            tone={STATUS_TONE[realityState.status] ?? "neutral"}
                            dot
                          >
                            {realityState.status}
                          </Badge>
                          {/* Never "0%" and never "null%". An unavailable score
                              is stated as unavailable — rendering a number here
                              would be the single most misleading thing this
                              page could do. */}
                          {realityState.confidence_available ? (
                            <Badge tone="accent">
                              {realityState.confidence}% confident
                            </Badge>
                          ) : (
                            <Badge tone="neutral">Confidence unavailable</Badge>
                          )}
                        </div>
                      </div>

                      <div className="mt-3">
                        {realityState.value_selected ? (
                          <DataView value={realityState.value} />
                        ) : (
                          /* No value was selected. Showing "null" in a value
                             box would read as "the value is null", which is a
                             different and false claim. */
                          <p className="rounded-md border border-dashed border-border px-3.5 py-3 text-xs text-muted-foreground">
                            No value selected — the sources disagree and nothing
                            available ranks one above another.
                          </p>
                        )}
                      </div>

                      <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
                        {realityState.selection_reason}
                      </p>

                      <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                        <Badge tone="outline" size="sm">
                          {realityState.supporting_count} supporting
                        </Badge>
                        <Badge
                          tone={
                            realityState.dissenting_count > 0
                              ? "degraded"
                              : "outline"
                          }
                          size="sm"
                        >
                          {realityState.dissenting_count} dissenting
                        </Badge>
                        <Badge tone="outline" size="sm">
                          {realityState.source_count}{" "}
                          {realityState.source_count === 1
                            ? "source"
                            : "sources"}
                        </Badge>
                        <span className="tabular ml-auto text-xs text-muted-foreground">
                          {realityState.algorithm_version}
                        </span>
                      </div>

                      <EvidenceTrail
                        entityId={realityState.entity_id}
                        attribute={realityState.attribute}
                      />
                    </li>
                  ))}
                </ul>
              )}
            </PanelBody>
          </Panel>
        </>
      ) : null}
    </div>
  );
}
