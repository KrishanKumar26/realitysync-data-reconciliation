import { cn } from "@/lib/utils";

/**
 * Loading placeholder.
 *
 * Skeletons must match the geometry of the content they replace so nothing
 * shifts when real data arrives.
 */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn("skeleton rounded-md", className)}
      role="status"
      aria-busy="true"
      aria-live="polite"
    >
      <span className="sr-only">Loading</span>
    </div>
  );
}
