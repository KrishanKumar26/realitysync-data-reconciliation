import { StatusDot, type StatusTone } from "@/components/ui/status-dot";
import type { SourceStatus, SyncStatus } from "@/lib/sources";

/**
 * Source status.
 *
 * "Configured" and "Connected" are shown as different things on purpose:
 * credentials being stored is not the same as a connection having been proven,
 * and conflating them would tell someone their database is reachable when
 * nothing has ever reached it.
 */
const SOURCE_TONE: Record<SourceStatus, { tone: StatusTone; label: string }> = {
  connected: { tone: "healthy", label: "Connected" },
  configured: { tone: "unknown", label: "Not yet tested" },
  error: { tone: "down", label: "Connection failed" },
  disabled: { tone: "unknown", label: "Disabled" },
};

export function SourceStatusBadge({ status }: { status: SourceStatus }) {
  const { tone, label } = SOURCE_TONE[status];
  return <StatusDot tone={tone} label={label} />;
}

const SYNC_TONE: Record<SyncStatus, { tone: StatusTone; label: string }> = {
  completed: { tone: "healthy", label: "Completed" },
  running: { tone: "pending", label: "Running" },
  pending: { tone: "pending", label: "Pending" },
  failed: { tone: "down", label: "Failed" },
  // Not an error: another sync already held the source's lock.
  skipped: { tone: "unknown", label: "Skipped" },
};

export function SyncStatusBadge({ status }: { status: SyncStatus }) {
  const { tone, label } = SYNC_TONE[status];
  return <StatusDot tone={tone} label={label} pulse={status === "running"} />;
}
