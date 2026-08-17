import { cn } from "@/lib/utils";

/**
 * Record values.
 *
 * Source payloads and current values were rendered as pretty-printed JSON in a
 * `<pre>`. That is the right rendering for a nested structure and the wrong one
 * for `{"quantity": 175, "location": "DOCK-A"}`, where braces and quotes are
 * three quarters of the characters and none of the information.
 *
 * So: a flat object of scalars becomes a key/value grid, and anything with
 * nesting keeps the `<pre>`, because inventing a bespoke rendering for
 * arbitrary nested data is how a value quietly stops matching what the source
 * actually said.
 *
 * Values are never reformatted. A string renders as its characters, a number as
 * its digits, `null` as the word — displayed distinctly, because a null the
 * source stated and a field the source omitted are different facts.
 */

function isFlatScalarObject(value: unknown): value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  const entries = Object.entries(value);
  if (entries.length === 0 || entries.length > 12) return false;
  return entries.every(
    ([, item]) =>
      item === null ||
      typeof item === "string" ||
      typeof item === "number" ||
      typeof item === "boolean",
  );
}

function renderScalar(value: unknown) {
  if (value === null) {
    return <span className="italic text-muted-foreground">null</span>;
  }
  if (typeof value === "boolean") {
    return <span className="text-foreground">{value ? "true" : "false"}</span>;
  }
  return <span className="text-foreground">{String(value)}</span>;
}

export function DataView({
  value,
  className,
}: {
  value: unknown;
  className?: string;
}) {
  if (isFlatScalarObject(value)) {
    return (
      <dl
        className={cn(
          "grid grid-cols-[minmax(0,auto)_minmax(0,1fr)] gap-x-4 gap-y-0 overflow-hidden rounded-md border border-border",
          className,
        )}
      >
        {Object.entries(value).map(([key, item], index) => (
          <div
            key={key}
            className={cn(
              "col-span-2 grid grid-cols-subgrid items-baseline px-3.5 py-2",
              index % 2 === 1 && "bg-muted/40",
            )}
          >
            <dt className="text-xs text-muted-foreground">{key}</dt>
            <dd className="tabular min-w-0 break-words text-xs">
              {renderScalar(item)}
            </dd>
          </div>
        ))}
      </dl>
    );
  }

  return (
    <pre
      className={cn(
        "tabular overflow-x-auto rounded-md border border-border bg-muted/40 px-3.5 py-2.5 text-xs text-muted-foreground",
        className,
      )}
    >
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}
