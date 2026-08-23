"use client";

import { History, RotateCcw } from "lucide-react";
import { useState, type FormEvent } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DataView } from "@/components/ui/data-view";
import { Panel, PanelBody, PanelHeader } from "@/components/ui/panel";
import { ApiError } from "@/lib/api";
import { fetchRealityAsOf, type RealityAsOf } from "@/lib/reality";
import { cn } from "@/lib/utils";

/**
 * "What did we know on the 15th?"
 *
 * The question the bitemporal model was built to answer and nothing asked.
 * Every record already carries both an event time and an ingestion time; this
 * uses the second one, so the answer reflects what had actually reached us by
 * the chosen moment rather than what turned out to be true.
 *
 * The result is never written. It is a view of a past belief, not a state to
 * return to — saving it would overwrite the present with the past.
 *
 * `observations_since` is the number worth reading: when a past answer differs
 * from today's and no source changed its mind, that count is the explanation.
 */

const STATUS_LABEL: Record<string, string> = {
  confirmed: "sources agree",
  contested: "sources disagree",
  stale: "out of date",
  provisional: "not final",
  unknown: "unknown",
};

/** `datetime-local` wants a local, second-less string. */
function toLocalInput(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

export function TimeTravel({ entityId }: { entityId: string }) {
  const [when, setWhen] = useState(() => {
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    return toLocalInput(yesterday);
  });
  const [result, setResult] = useState<RealityAsOf | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const at = new Date(when);
    if (Number.isNaN(at.getTime())) {
      setError("Pick a date and time first.");
      return;
    }
    setError(null);
    setLoading(true);
    try {
      setResult(await fetchRealityAsOf(entityId, at));
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not work out what we knew then.",
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <Panel>
      <PanelHeader
        icon={<History />}
        title="What did we know back then?"
        description="Answers using only the records that had arrived by the moment you pick. Nothing is changed or saved."
        action={
          result ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setResult(null);
                setError(null);
              }}
            >
              <RotateCcw aria-hidden="true" />
              Back to now
            </Button>
          ) : undefined
        }
      />
      <PanelBody className="space-y-5">
        <form
          onSubmit={handleSubmit}
          className="flex flex-wrap items-end gap-3"
        >
          <div className="min-w-0">
            <label
              htmlFor="time-travel-at"
              className="block text-xs font-medium uppercase tracking-wide text-muted-foreground"
            >
              Go back to
            </label>
            <input
              id="time-travel-at"
              type="datetime-local"
              value={when}
              onChange={(event) => setWhen(event.target.value)}
              className="mt-1.5 h-10 rounded-md border border-border-strong bg-panel px-3 text-sm text-foreground focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/35"
            />
          </div>
          <Button type="submit" variant="secondary" disabled={loading}>
            {loading ? "Working it out…" : "Show me"}
          </Button>
        </form>

        {error ? (
          <p role="alert" className="text-sm text-status-down">
            {error}
          </p>
        ) : null}

        {result ? (
          <div className="animate-rise space-y-4">
            <div className="flex flex-wrap items-center gap-2 rounded-md border border-accent-violet/25 bg-accent-violet/5 px-3.5 py-2.5">
              <Badge tone="accent">
                {new Date(result.known_at).toLocaleString()}
              </Badge>
              <span className="text-xs leading-relaxed text-muted-foreground">
                {result.observations_since > 0 ? (
                  <>
                    Based on{" "}
                    <span className="tabular font-medium text-foreground">
                      {result.observations_known}
                    </span>{" "}
                    records.{" "}
                    <span className="tabular font-medium text-foreground">
                      {result.observations_since}
                    </span>{" "}
                    more have arrived since — if this differs from today, that
                    is why.
                  </>
                ) : (
                  <>
                    Based on all{" "}
                    <span className="tabular font-medium text-foreground">
                      {result.observations_known}
                    </span>{" "}
                    records. Nothing has arrived since, so this matches today.
                  </>
                )}
              </span>
            </div>

            {result.attributes.length === 0 ? (
              <p className="rounded-md border border-dashed border-border px-3.5 py-3 text-sm text-muted-foreground">
                Nothing had reached us by then, so there was nothing to say
                about this item.
              </p>
            ) : (
              <ul className="divide-y divide-border rounded-md border border-border">
                {result.attributes.map((attribute) => (
                  <li key={attribute.attribute} className="px-4 py-3.5">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="tabular text-sm font-medium text-foreground">
                        {attribute.attribute}
                      </span>
                      <Badge
                        tone={
                          attribute.status === "confirmed"
                            ? "healthy"
                            : attribute.status === "contested"
                              ? "down"
                              : "neutral"
                        }
                        dot
                      >
                        {STATUS_LABEL[attribute.status] ?? attribute.status}
                      </Badge>
                    </div>

                    <div className={cn("mt-2.5")}>
                      {attribute.value_selected ? (
                        <DataView value={attribute.value} />
                      ) : (
                        <p className="rounded-md border border-dashed border-border px-3 py-2 text-xs text-muted-foreground">
                          No value chosen back then either.
                        </p>
                      )}
                    </div>

                    <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                      {attribute.selection_reason}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : null}
      </PanelBody>
    </Panel>
  );
}
