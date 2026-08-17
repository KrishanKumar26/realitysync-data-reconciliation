import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Data table.
 *
 * Most of this product's lists are genuinely tabular — a sync run has a status,
 * a start time, a duration and three row counts, and reading six of those as a
 * run-on sentence is harder than reading them in columns. Where the data is
 * tabular, it is now a table: aligned columns, a real header, and numbers that
 * line up so an outlier is visible without being read.
 *
 * Narrow screens are handled by horizontal scroll inside the table's own
 * container rather than by a separate card layout. A second layout is a second
 * thing to keep correct, and a table that scrolls still has its header, its
 * alignment and its scan lines — a stack of cards loses all three.
 */

export function TableContainer({
  className,
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn("w-full overflow-x-auto", className)}>{children}</div>
  );
}

export function Table({
  className,
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return (
    <table
      className={cn("w-full min-w-max border-collapse text-sm", className)}
    >
      {children}
    </table>
  );
}

export function THead({ children }: { children: ReactNode }) {
  return (
    <thead className="border-b border-border">
      <tr>{children}</tr>
    </thead>
  );
}

export function TH({
  align = "left",
  className,
  children,
}: {
  align?: "left" | "right" | "center";
  className?: string;
  children?: ReactNode;
}) {
  return (
    <th
      scope="col"
      className={cn(
        "px-5 py-2.5 text-xs font-medium uppercase tracking-wide text-muted-foreground",
        align === "right" && "text-right",
        align === "center" && "text-center",
        align === "left" && "text-left",
        className,
      )}
    >
      {children}
    </th>
  );
}

export function TBody({ children }: { children: ReactNode }) {
  return <tbody className="divide-y divide-border">{children}</tbody>;
}

export function TR({
  className,
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return (
    <tr
      className={cn(
        "transition-colors duration-150 hover:bg-muted/50",
        className,
      )}
    >
      {children}
    </tr>
  );
}

export function TD({
  align = "left",
  /** Monospace + tabular figures. Use for numbers, ids and timestamps. */
  numeric = false,
  className,
  children,
}: {
  align?: "left" | "right" | "center";
  numeric?: boolean;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <td
      className={cn(
        "px-5 py-3 align-middle text-foreground",
        numeric && "tabular",
        align === "right" && "text-right",
        align === "center" && "text-center",
        className,
      )}
    >
      {children}
    </td>
  );
}
