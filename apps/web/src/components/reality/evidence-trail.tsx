"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api";
import { listEvidence, type Evidence } from "@/lib/reality";

/**
 * The provenance trail for one reality state.
 *
 * Collapsed by default and fetched on open. A state usually has a handful of
 * evidence rows, but the page renders every attribute of an entity, and
 * loading all of their trails eagerly would mean one request per attribute
 * before anything at all is visible.
 *
 * Both timestamps are shown for every observation, always. Event time and
 * ingestion time answer different questions — "when was this true" and "when
 * did we learn it" — and the gap between them is precisely what a late arrival
 * looks like. Showing one would hide the thing worth seeing.
 */

const ROLE_LABELS: Record<Evidence["role"], string> = {
  supporting: "supported",
  dissenting: "disagreed",
  excluded: "excluded",
  // Deliberately not "ignored": it was considered, there was simply no
  // selected value for it to agree or disagree with.
  considered: "considered",
};

function formatInstant(value: string): string {
  return new Date(value).toLocaleString();
}

export function EvidenceTrail({
  entityId,
  attribute,
}: {
  entityId: string;
  attribute: string;
}) {
  const [state, setState] = useState<
    | { kind: "closed" }
    | { kind: "loading" }
    | { kind: "ready"; evidence: Evidence[] }
    | { kind: "error"; message: string }
  >({ kind: "closed" });

  async function toggle(open: boolean) {
    if (!open) {
      setState({ kind: "closed" });
      return;
    }
    setState({ kind: "loading" });
    try {
      setState({
        kind: "ready",
        evidence: await listEvidence(entityId, attribute),
      });
    } catch (caught) {
      setState({
        kind: "error",
        message:
          caught instanceof ApiError
            ? caught.message
            : "Could not load the evidence for this attribute.",
      });
    }
  }

  return (
    <details
      className="mt-2"
      onToggle={(event) => void toggle(event.currentTarget.open)}
    >
      <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
        Evidence
      </summary>

      {state.kind === "loading" ? (
        <p className="mt-2 text-xs text-muted-foreground">Loading evidence…</p>
      ) : null}

      {state.kind === "error" ? (
        <p role="alert" className="mt-2 text-xs text-status-down">
          {state.message}
        </p>
      ) : null}

      {state.kind === "ready" && state.evidence.length === 0 ? (
        <p className="mt-2 text-xs text-muted-foreground">
          No evidence recorded for this attribute.
        </p>
      ) : null}

      {state.kind === "ready" && state.evidence.length > 0 ? (
        <ul className="mt-2 space-y-2">
          {state.evidence.map((entry) => (
            <li
              key={entry.observation_id}
              className="rounded-md border border-border px-3 py-2"
            >
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="tabular text-xs text-foreground">
                  {entry.external_id}
                </span>
                <span className="text-xs text-muted-foreground">
                  {ROLE_LABELS[entry.role]}
                </span>
              </div>

              <p className="tabular mt-1 text-xs text-muted-foreground">
                {JSON.stringify(entry.observed_value)}
              </p>

              <p className="tabular mt-1 text-xs text-muted-foreground">
                true at {formatInstant(entry.event_time)} · learned{" "}
                {formatInstant(entry.ingested_at)}
              </p>

              {entry.exclusion_reason ? (
                <p className="mt-1 text-xs text-muted-foreground">
                  {entry.exclusion_reason.replaceAll("_", " ")}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </details>
  );
}
