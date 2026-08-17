import { cva, type VariantProps } from "class-variance-authority";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Badge.
 *
 * One badge component, product-wide. Before this there were three ways to
 * render the same idea — a `StatusDot`, a bare `rounded-full` span, and plain
 * text — which meant "open", "connected" and "critical" all looked like
 * different kinds of thing when they are the same kind of thing.
 *
 * Tone carries meaning and is never the only carrier: the label is always
 * text, and `dot` adds a shape as a second channel for anyone who cannot
 * separate the hues.
 */

const badgeVariants = cva(
  "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-xs font-medium",
  {
    variants: {
      tone: {
        neutral: "border-border bg-muted text-muted-foreground",
        healthy:
          "border-status-healthy/30 bg-status-healthy/10 text-status-healthy",
        degraded:
          "border-status-degraded/30 bg-status-degraded/10 text-status-degraded",
        down: "border-status-down/30 bg-status-down/10 text-status-down",
        accent: "border-accent-cyan/30 bg-accent-cyan/10 text-accent-cyan",
        outline: "border-border-strong bg-transparent text-foreground",
      },
      size: {
        sm: "px-2 py-0 text-[0.6875rem]",
        md: "px-2.5 py-0.5 text-xs",
      },
    },
    defaultVariants: { tone: "neutral", size: "md" },
  },
);

const DOT_CLASS: Record<string, string> = {
  neutral: "bg-status-unknown",
  healthy: "bg-status-healthy",
  degraded: "bg-status-degraded",
  down: "bg-status-down",
  accent: "bg-accent-cyan",
  outline: "bg-muted-foreground",
};

export type BadgeTone = NonNullable<VariantProps<typeof badgeVariants>["tone"]>;

export function Badge({
  tone = "neutral",
  size,
  dot = false,
  pulse = false,
  icon,
  className,
  children,
}: {
  tone?: BadgeTone;
  size?: "sm" | "md";
  /** Render a coloured dot before the label as a non-colour channel. */
  dot?: boolean;
  /** Animate the dot. Only for genuinely in-progress states. */
  pulse?: boolean;
  icon?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span className={cn(badgeVariants({ tone, size }), className)}>
      {dot ? (
        <span className="relative flex h-1.5 w-1.5 shrink-0" aria-hidden="true">
          {pulse ? (
            <span
              className={cn(
                "absolute inline-flex h-full w-full animate-ping rounded-full opacity-60",
                DOT_CLASS[tone],
              )}
            />
          ) : null}
          <span
            className={cn(
              "relative inline-flex h-1.5 w-1.5 rounded-full",
              DOT_CLASS[tone],
            )}
          />
        </span>
      ) : null}
      {icon ? (
        <span className="shrink-0 [&>svg]:h-3 [&>svg]:w-3" aria-hidden="true">
          {icon}
        </span>
      ) : null}
      {children}
    </span>
  );
}
