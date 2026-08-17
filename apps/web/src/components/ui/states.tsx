import { AlertTriangle, Inbox } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Empty and error states.
 *
 * Every list and panel in RealitySync must have a designed empty state and a
 * designed error state. "Nothing here" and "something broke" are different
 * situations that need different words and different actions — a spinner that
 * never resolves is not an acceptable answer to either.
 *
 * Both now carry an icon by default. An empty panel with only grey text reads
 * as a page that failed to finish loading; a bordered glyph reads as a place
 * that is deliberately empty, which is what it is.
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
      <div
        className="mb-4 flex h-12 w-12 items-center justify-center rounded-xl border border-border bg-muted text-muted-foreground [&>svg]:h-5 [&>svg]:w-5"
        aria-hidden="true"
      >
        {icon ?? <Inbox />}
      </div>
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
      <div
        className="mb-4 flex h-12 w-12 items-center justify-center rounded-xl border border-status-down/25 bg-status-down/10 text-status-down [&>svg]:h-5 [&>svg]:w-5"
        aria-hidden="true"
      >
        <AlertTriangle />
      </div>
      <h3 className="text-sm font-semibold text-status-down">{title}</h3>
      {description ? (
        <p className="mt-2 max-w-md text-sm leading-relaxed text-muted-foreground">
          {description}
        </p>
      ) : null}
      {requestId ? (
        <p className="tabular mt-3 rounded-md border border-border bg-muted px-2.5 py-1 text-xs text-muted-foreground">
          Request ID: {requestId}
        </p>
      ) : null}
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}
