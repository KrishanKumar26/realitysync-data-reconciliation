import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * A single figure on the Overview.
 *
 * `value` is `ReactNode` rather than `number` so a stat can render an honest
 * dash for an unavailable measure. That matters: the alternative — defaulting a
 * missing value to `0` — would put a confident-looking number on a dashboard
 * for something nobody has measured, which is exactly the failure mode this
 * product exists to prevent.
 */
export function Stat({
  label,
  value,
  hint,
  tone = "default",
  className,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  tone?: "default" | "warning" | "danger" | "muted";
  className?: string;
}) {
  const toneClass = {
    default: "text-foreground",
    warning: "text-status-degraded",
    danger: "text-status-down",
    muted: "text-muted-foreground",
  }[tone];

  return (
    <div className={cn("min-w-0", className)}>
      <dt className="text-xs uppercase tracking-wide text-muted-foreground">
        {label}
      </dt>
      <dd className={cn("tabular mt-1.5 text-2xl font-semibold tracking-tight", toneClass)}>
        {value}
      </dd>
      {hint ? (
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  );
}

/** Grid wrapper so stat rows line up consistently across panels. */
export function StatGrid({
  columns = 4,
  children,
}: {
  columns?: 2 | 3 | 4;
  children: ReactNode;
}) {
  return (
    <dl
      className={cn(
        "grid gap-x-8 gap-y-5",
        columns === 2 && "sm:grid-cols-2",
        columns === 3 && "sm:grid-cols-3",
        columns === 4 && "grid-cols-2 lg:grid-cols-4",
      )}
    >
      {children}
    </dl>
  );
}
