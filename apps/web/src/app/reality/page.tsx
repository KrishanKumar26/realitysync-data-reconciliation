"use client";

import { useCallback, useEffect, useState } from "react";

import { EntitySetup } from "@/components/reality/entity-setup";
import { EvidenceTrail } from "@/components/reality/evidence-trail";
import { Button } from "@/components/ui/button";
import { Panel, PanelBody, PanelHeader } from "@/components/ui/panel";
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
 * Reality.
 *
 * Shows what RealitySync believes about an entity, and — when it cannot
 * believe anything — why.
 *
 * While the approved confidence specification is unavailable the engine
 * produces no reality states, so this page will usually show the blocked
 * result from a recalculation rather than a list of values. That is the honest
 * rendering: an empty list with no explanation would read as "nothing to say",
 * when in fact there is plenty to say and no sanctioned way to score it.
 */
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
            : "Could not load reality states.",
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
      <header>
        <h1 className="text-xl font-semibold tracking-tight">Reality</h1>
        <p className="mt-1.5 text-sm text-muted-foreground">
          Entities, current state and the evidence behind it.
        </p>
      </header>

      {state.kind === "loading" ? (
        <div className="space-y-3" data-testid="reality-loading">
          <Skeleton className="h-10 w-64" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : null}

      {state.kind === "error" ? (
        <Panel>
          <PanelBody className="p-0">
            <ErrorState
              title="Could not load reality"
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
              title="No entities yet"
              description="A reality state is what RealitySync believes about one thing. Create an entity and map a synced table to it, and the engine has something to reason about."
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
                onChange={(event) => {
                  setEntityId(event.target.value);
                  setLastRun(null);
                }}
                className="mt-1.5 h-10 w-full rounded-md border border-border-strong bg-background px-3 text-sm text-foreground focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/35"
              >
                {state.entities.map((entity) => (
                  <option key={entity.id} value={entity.id}>
                    {entity.natural_key} ({entity.observation_count})
                  </option>
                ))}
              </select>
            </div>
            <Button onClick={() => void runRecalculation()} disabled={running}>
              {running ? "Recalculating…" : "Recalculate"}
            </Button>
          </div>

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
            <Panel>
              <PanelHeader
                title="States written without confidence scores"
                description="The engine ran and reported precisely what is missing."
              />
              <PanelBody className="space-y-4">
                <p className="text-sm leading-relaxed text-muted-foreground">
                  The Reality Engine read{" "}
                  <span className="tabular text-foreground">
                    {lastRun.attributes_considered}
                  </span>{" "}
                  {lastRun.attributes_considered === 1
                    ? "attribute"
                    : "attributes"}{" "}
                  and wrote{" "}
                  <span className="tabular text-foreground">
                    {lastRun.states_written}
                  </span>{" "}
                  {lastRun.states_written === 1 ? "state" : "states"}, of which{" "}
                  <span className="tabular text-foreground">
                    {lastRun.states_unscored}
                  </span>{" "}
                  {lastRun.states_unscored === 1 ? "carries" : "carry"} no
                  confidence score, because the approved confidence
                  specification is unavailable. Everything that follows from the
                  observations alone — the values, the evidence, the
                  disagreements — is recorded.{" "}
                  <span className="tabular text-foreground">
                    {lastRun.conflicts_written}
                  </span>{" "}
                  {lastRun.conflicts_written === 1
                    ? "conflict was"
                    : "conflicts were"}{" "}
                  recorded, which needs no formula.
                </p>

                {lastRun.blocked_on.length > 0 ? (
                  <p className="tabular text-xs text-muted-foreground">
                    Blocked on: {lastRun.blocked_on.join(", ")}
                  </p>
                ) : null}

                {lastRun.missing_specifications.length > 0 ? (
                  <details className="rounded-md border border-border bg-muted px-4 py-3">
                    <summary className="cursor-pointer text-sm text-foreground">
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
            <p role="status" className="text-sm text-muted-foreground">
              Wrote {lastRun.states_written} reality{" "}
              {lastRun.states_written === 1 ? "state" : "states"} and{" "}
              {lastRun.conflicts_written} conflicts.
            </p>
          ) : null}

          <Panel>
            <PanelHeader
              title="Reality states"
              description="One per attribute, with its evidence and the reason it was selected."
            />
            <PanelBody className={state.states.length > 0 ? "p-0" : undefined}>
              {state.states.length === 0 ? (
                <EmptyState
                  title="No reality states"
                  description="Run a recalculation to see what the engine can establish. While the confidence specification is unavailable states are still written, with no score and the reason stated."
                  className="py-10"
                />
              ) : (
                <ul className="divide-y divide-border">
                  {state.states.map((realityState) => (
                    <li key={realityState.id} className="px-5 py-4">
                      <div className="flex flex-wrap items-baseline justify-between gap-2">
                        <span className="tabular text-sm text-foreground">
                          {realityState.attribute}
                        </span>
                        <span className="tabular text-sm text-foreground">
                          {/* Never "0%" and never "null%". An unavailable score
                              is stated as unavailable — rendering a number here
                              would be the single most misleading thing this
                              page could do. */}
                          {realityState.confidence_available
                            ? `${realityState.confidence}%`
                            : "confidence unavailable"}{" "}
                          · {realityState.status}
                        </span>
                      </div>
                      {realityState.value_selected ? (
                        <pre className="tabular mt-2 overflow-x-auto rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
                          {JSON.stringify(realityState.value, null, 2)}
                        </pre>
                      ) : (
                        /* No value was selected. Showing "null" in a value box
                           would read as "the value is null", which is a
                           different and false claim. */
                        <p className="mt-2 rounded-md border border-dashed border-border px-3 py-2 text-xs text-muted-foreground">
                          No value selected
                        </p>
                      )}
                      <p className="mt-2 text-xs text-muted-foreground">
                        {realityState.selection_reason}
                      </p>
                      <p className="tabular mt-1 text-xs text-muted-foreground">
                        {realityState.supporting_count} supporting ·{" "}
                        {realityState.dissenting_count} dissenting ·{" "}
                        {realityState.source_count} sources ·{" "}
                        {realityState.algorithm_version}
                      </p>

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
