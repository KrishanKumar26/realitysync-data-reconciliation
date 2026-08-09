import { cn } from "@/lib/utils";

export type StatusTone = "healthy" | "degraded" | "down" | "unknown" | "pending";

const TONE_CLASS: Record<StatusTone, string> = {
  healthy: "bg-status-healthy",
  degraded: "bg-status-degraded",
  down: "bg-status-down",
  unknown: "bg-status-unknown",
  pending: "bg-status-unknown",
};

/**
 * Small state indicator.
 *
 * The label is always rendered for assistive technology: colour alone must
 * never be the only carrier of meaning.
 */
export function StatusDot({
  tone,
  label,
  pulse = false,
  className,
}: {
  tone: StatusTone;
  label: string;
  pulse?: boolean;
  className?: string;
}) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <span className="relative flex h-2 w-2 shrink-0" aria-hidden="true">
        {pulse ? (
          <span
            className={cn(
              "absolute inline-flex h-full w-full animate-ping rounded-full opacity-60",
              TONE_CLASS[tone],
            )}
          />
        ) : null}
        <span
          className={cn("relative inline-flex h-2 w-2 rounded-full", TONE_CLASS[tone])}
        />
      </span>
      <span className="text-sm text-muted-foreground">{label}</span>
    </span>
  );
}
