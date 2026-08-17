"use client";

import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { ApiError } from "@/lib/api";
import {
  createEntity,
  createMapping,
  listMappings,
  type Entity,
  type EntityMapping,
} from "@/lib/reality";
import {
  listObservations,
  listSources,
  listStreams,
  type DataSource,
  type Observation,
  type SourceStream,
} from "@/lib/sources";

/**
 * Creating an entity, and binding source rows to it.
 *
 * This is the step the Reality page used to describe and not provide. The API
 * has always had both endpoints; the interface simply never called them, so a
 * workspace could connect a source, sync real rows, and then have nowhere to
 * go. Everything downstream — reality states, conflicts, evidence, the
 * timeline — depends on an entity existing and knowing which rows describe it.
 *
 * The binding is *declared*, never inferred. Deciding that a warehouse row and
 * an ERP row describe the same laptop is a judgement about the world, and a
 * wrong guess merges two real things in a way no later calculation can undo.
 * So the form asks rather than matching on a similar-looking key.
 *
 * The external id is offered as a list read from observations already ingested,
 * not typed freehand. Its format (`sku_id=1`) is an internal convention, and
 * asking someone to reproduce it from memory would turn a routine step into a
 * guessing game with silent failure as the reward.
 */

function errorMessage(caught: unknown, fallback: string): string {
  return caught instanceof ApiError ? caught.message : fallback;
}

export function EntitySetup({
  entity,
  onEntityCreated,
  onMappingCreated,
}: {
  entity: Entity | null;
  onEntityCreated: (entity: Entity) => void;
  onMappingCreated: () => void;
}) {
  return (
    <div className="space-y-4">
      <CreateEntityForm onCreated={onEntityCreated} />
      {entity ? (
        <MapRowForm entity={entity} onMapped={onMappingCreated} />
      ) : null}
    </div>
  );
}

function CreateEntityForm({
  onCreated,
}: {
  onCreated: (entity: Entity) => void;
}) {
  const [open, setOpen] = useState(false);
  const [entityType, setEntityType] = useState("sku");
  const [naturalKey, setNaturalKey] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const created = await createEntity({
        entity_type: entityType.trim(),
        natural_key: naturalKey.trim(),
      });
      setNaturalKey("");
      setOpen(false);
      onCreated(created);
    } catch (caught) {
      setError(errorMessage(caught, "Could not create the item."));
    } finally {
      setSubmitting(false);
    }
  }

  if (!open) {
    return (
      <Button variant="secondary" onClick={() => setOpen(true)}>
        New item
      </Button>
    );
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="surface space-y-4 px-5 py-4"
      noValidate
    >
      <div>
        <h3 className="text-sm font-medium text-foreground">New item</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          One real thing your sources describe — a product, a shipment, an
          account. Values are tracked per item.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Type" hint="How you group these, e.g. product or asset.">
          {({ inputId, describedBy }) => (
            <Input
              id={inputId}
              aria-describedby={describedBy}
              value={entityType}
              onChange={(event) => setEntityType(event.target.value)}
              required
            />
          )}
        </Field>

        <Field label="Reference ID" hint="How you refer to it, e.g. LAPTOP-13.">
          {({ inputId, describedBy }) => (
            <Input
              id={inputId}
              aria-describedby={describedBy}
              value={naturalKey}
              onChange={(event) => setNaturalKey(event.target.value)}
              placeholder="LAPTOP-13"
              required
            />
          )}
        </Field>
      </div>

      {error ? (
        <p role="alert" className="text-sm text-status-down">
          {error}
        </p>
      ) : null}

      <div className="flex items-center gap-2.5">
        <Button type="submit" disabled={submitting || !naturalKey.trim()}>
          {submitting ? "Creating…" : "Create item"}
        </Button>
        <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function MapRowForm({
  entity,
  onMapped,
}: {
  entity: Entity;
  onMapped: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [sources, setSources] = useState<DataSource[]>([]);
  const [streams, setStreams] = useState<SourceStream[]>([]);
  const [externalIds, setExternalIds] = useState<string[]>([]);
  const [mappings, setMappings] = useState<EntityMapping[]>([]);

  const [sourceId, setSourceId] = useState("");
  const [streamId, setStreamId] = useState("");
  const [externalId, setExternalId] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void listMappings(entity.id)
      .then(setMappings)
      .catch(() => setMappings([]));
  }, [entity.id]);

  useEffect(() => {
    if (!open) return;
    void listSources()
      .then((loaded) => {
        setSources(loaded);
        if (loaded.length > 0 && !sourceId) setSourceId(loaded[0]!.id);
      })
      .catch(() => setError("Could not load sources."));
    // sourceId is deliberately absent: this seeds the first selection once, and
    // re-running on every change would fight the user's own selection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!sourceId) return;
    setStreamId("");
    setExternalId("");
    void Promise.all([listStreams(sourceId), listObservations(sourceId)])
      .then(
        ([loadedStreams, observations]: [SourceStream[], Observation[]]) => {
          setStreams(loadedStreams);
          if (loadedStreams.length > 0) setStreamId(loadedStreams[0]!.id);
          // Offered rather than typed: the format is an internal convention, and
          // a typo produces a mapping that matches nothing and reports no error.
          const ids = [
            ...new Set(observations.map((o) => o.external_id)),
          ].sort();
          setExternalIds(ids);
          if (ids.length > 0) setExternalId(ids[0]!);
        },
      )
      .catch(() => setError("Could not load that source's tables."));
  }, [sourceId]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await createMapping(entity.id, {
        stream_id: streamId,
        external_id: externalId,
      });
      setMappings(await listMappings(entity.id));
      setOpen(false);
      onMapped();
    } catch (caught) {
      setError(errorMessage(caught, "Could not link that row."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="surface px-5 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-medium text-foreground">
            Linked data for {entity.natural_key}
          </h3>
          <p className="mt-1 text-xs text-muted-foreground">
            {mappings.length === 0
              ? "None yet. Link at least two sources to see whether they agree."
              : `${mappings.length} linked. All of them are compared.`}
          </p>
        </div>
        {!open ? (
          <Button variant="secondary" size="sm" onClick={() => setOpen(true)}>
            Link a table row
          </Button>
        ) : null}
      </div>

      {open ? (
        <form onSubmit={handleSubmit} className="mt-4 space-y-4" noValidate>
          <div className="grid gap-4 sm:grid-cols-3">
            <label className="block">
              <span className="block text-xs uppercase tracking-wide text-muted-foreground">
                Source
              </span>
              <select
                value={sourceId}
                onChange={(event) => setSourceId(event.target.value)}
                className="mt-1.5 h-10 w-full rounded-md border border-border-strong bg-background px-3 text-sm text-foreground"
              >
                {sources.map((source) => (
                  <option key={source.id} value={source.id}>
                    {source.name}
                  </option>
                ))}
              </select>
            </label>

            <label className="block">
              <span className="block text-xs uppercase tracking-wide text-muted-foreground">
                Table
              </span>
              <select
                value={streamId}
                onChange={(event) => setStreamId(event.target.value)}
                className="mt-1.5 h-10 w-full rounded-md border border-border-strong bg-background px-3 text-sm text-foreground"
              >
                {streams.map((stream) => (
                  <option key={stream.id} value={stream.id}>
                    {stream.qualified_name}
                  </option>
                ))}
              </select>
            </label>

            <label className="block">
              <span className="block text-xs uppercase tracking-wide text-muted-foreground">
                Row
              </span>
              <select
                value={externalId}
                onChange={(event) => setExternalId(event.target.value)}
                className="mt-1.5 h-10 w-full rounded-md border border-border-strong bg-background px-3 text-sm text-foreground"
              >
                {externalIds.map((id) => (
                  <option key={id} value={id}>
                    {id}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {externalIds.length === 0 && sourceId ? (
            <p className="text-xs text-muted-foreground">
              This source has no data yet. Sync it first — there is nothing to
              link until it has read some rows.
            </p>
          ) : null}

          {error ? (
            <p role="alert" className="text-sm text-status-down">
              {error}
            </p>
          ) : null}

          <div className="flex items-center gap-2.5">
            <Button
              type="submit"
              size="sm"
              disabled={submitting || !streamId || !externalId}
            >
              {submitting ? "Linking…" : "Link row"}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setOpen(false)}
            >
              Cancel
            </Button>
          </div>
        </form>
      ) : null}

      {mappings.length > 0 ? (
        <ul className="mt-3 space-y-1">
          {mappings.map((mapping) => (
            <li
              key={mapping.id}
              className="tabular text-xs text-muted-foreground"
            >
              {mapping.external_id}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
