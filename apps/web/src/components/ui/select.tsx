"use client";

import { ChevronDown } from "lucide-react";
import { useId, type SelectHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/**
 * Labelled select.
 *
 * The same forty-character class string was pasted into four screens, so the
 * dropdowns had already begun to drift apart. The native `<select>` is kept —
 * it is keyboard-accessible, screen-reader-correct and renders as the platform
 * picker on a phone, none of which a hand-built listbox gets for free.
 *
 * The chevron is decorative and `pointer-events-none`, so clicks reach the
 * select underneath it.
 */
export function Select({
  label,
  hint,
  className,
  containerClassName,
  id,
  children,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement> & {
  label: string;
  hint?: string;
  containerClassName?: string;
}) {
  const generatedId = useId();
  const selectId = id ?? generatedId;
  const hintId = hint ? `${selectId}-hint` : undefined;

  return (
    <div className={cn("min-w-0", containerClassName)}>
      <label
        htmlFor={selectId}
        className="block text-xs font-medium uppercase tracking-wide text-muted-foreground"
      >
        {label}
      </label>
      <div className="relative mt-1.5">
        <select
          id={selectId}
          aria-describedby={hintId}
          className={cn(
            "h-10 w-full appearance-none rounded-md border border-border-strong bg-panel px-3 pr-9 text-sm text-foreground",
            "transition-colors duration-150 hover:border-ring/60",
            "focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/35",
            "disabled:cursor-not-allowed disabled:opacity-60",
            className,
          )}
          {...props}
        >
          {children}
        </select>
        <ChevronDown
          aria-hidden="true"
          className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
        />
      </div>
      {hint ? (
        <p id={hintId} className="mt-1.5 text-xs text-muted-foreground">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
