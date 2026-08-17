import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Metric card.
 *
 * The headline figures at the top of a screen. Distinct from `Stat`, which is a
 * figure inside a panel: a metric is its own surface and carries an icon, a
 * tone and a supporting line, because it is meant to be read at a glance from
 * across a desk.
 *
 * `value` is `ReactNode`, not `number`, for the same reason it is on `Stat`: a
 * measure nobody has taken renders as an em dash. Defaulting an unknown to zero
 * would put a confident number on a dashboard for something unmeasured, which
 * is the exact failure this product exists to prevent.
 */

export type MetricTone = "default" | "healthy" | "degraded" | "down" | "accent";

const ICON_TONE: Record<MetricTone, string> = {
  default: "border-border bg-muted text-muted-foreground",
  healthy: "border-status-healthy/25 bg-status-healthy/10 text-status-healthy",
  degraded:
    "border-status-degraded/25 bg-status-degraded/10 text-status-degraded",
  down: "border-status-down/25 bg-status-down/10 text-status-down",
  accent: "border-accent-cyan/25 bg-accent-cyan/10 text-accent-cyan",
};

const VALUE_TONE: Record<MetricTone, string> = {
  default: "text-foreground",
  healthy: "text-foreground",
  degraded: "text-status-degraded",
  down: "text-status-down",
  accent: "text-foreground",
};

export function Metric({
  label,
  value,
  hint,
  icon,
  tone = "default",
  footer,
  className,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  icon?: ReactNode;
  tone?: MetricTone;
  /** Rendered under a divider — a ratio bar, a badge row, a link. */
  footer?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "surface flex flex-col p-5 transition-colors duration-150 hover:border-border-strong",
        className,
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {label}
        </p>
        {icon ? (
          <span
            aria-hidden="true"
            className={cn(
              "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border [&>svg]:h-4 [&>svg]:w-4",
              ICON_TONE[tone],
            )}
          >
            {icon}
          </span>
        ) : null}
      </div>

      <p
        className={cn(
          "tabular mt-3 text-3xl font-semibold leading-none tracking-tight",
          VALUE_TONE[tone],
        )}
      >
        {value}
      </p>

      {hint ? (
        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
          {hint}
        </p>
      ) : null}

      {footer ? (
        <div className="mt-auto border-t border-border pt-3.5 [&:not(:first-child)]:mt-4">
          {footer}
        </div>
      ) : null}
    </div>
  );
}

/** Responsive grid for a row of metrics. */
export function MetricGrid({
  columns = 4,
  className,
  children,
}: {
  columns?: 2 | 3 | 4;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "grid gap-4",
        columns === 2 && "sm:grid-cols-2",
        columns === 3 && "sm:grid-cols-2 lg:grid-cols-3",
        columns === 4 && "sm:grid-cols-2 xl:grid-cols-4",
        className,
      )}
    >
      {children}
    </div>
  );
}
