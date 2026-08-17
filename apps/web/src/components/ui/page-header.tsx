import Link from "next/link";
import { ChevronLeft } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Page header.
 *
 * Every screen opened with its own hand-rolled header block, so the title size,
 * the gap under it and the position of the primary action all drifted. One
 * component means a person moving between screens does not have to re-find the
 * same three things each time.
 */
export function PageHeader({
  title,
  description,
  actions,
  back,
  className,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
  /** A single "up one level" link. Not a full breadcrumb trail: this product is
      two levels deep at most, and a trail of two is noise. */
  back?: { href: string; label: string };
  className?: string;
}) {
  return (
    <header className={cn("space-y-3", className)}>
      {back ? (
        <Link
          href={back.href}
          className="inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors duration-150 hover:text-foreground"
        >
          <ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
          {back.label}
        </Link>
      ) : null}

      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
        <div className="min-w-0">
          <h1 className="truncate text-2xl font-semibold tracking-tight text-foreground">
            {title}
          </h1>
          {description ? (
            <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
              {description}
            </p>
          ) : null}
        </div>
        {actions ? (
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            {actions}
          </div>
        ) : null}
      </div>
    </header>
  );
}
