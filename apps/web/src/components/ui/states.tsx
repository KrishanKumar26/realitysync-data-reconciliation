import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Empty and error states.
 *
 * Every list and panel in RealitySync must have a designed empty state and a
 * designed error state. "Nothing here" and "something broke" are different
 * situations that need different words and different actions — a spinner that
 * never resolves is not an acceptable answer to either.
 */

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center px-6 py-14 text-center",
        className,
      )}
    >
      {icon ? (
        <div
          className="mb-4 flex h-11 w-11 items-center justify-center rounded-full border border-border bg-muted text-muted-foreground"
          aria-hidden="true"
        >
          {icon}
        </div>
      ) : null}
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      {description ? (
        <p className="mt-2 max-w-md text-sm leading-relaxed text-muted-foreground">
          {description}
        </p>
      ) : null}
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}

export function ErrorState({
  title = "Something went wrong",
  description,
  requestId,
  action,
  className,
}: {
  title?: string;
  description?: string;
  requestId?: string | null;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center justify-center px-6 py-14 text-center",
        className,
      )}
    >
      <h3 className="text-sm font-semibold text-status-down">{title}</h3>
      {description ? (
        <p className="mt-2 max-w-md text-sm leading-relaxed text-muted-foreground">
          {description}
        </p>
      ) : null}
      {requestId ? (
        <p className="tabular mt-3 text-xs text-muted-foreground">
          Request ID: {requestId}
        </p>
      ) : null}
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}
