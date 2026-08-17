import { Badge, type BadgeTone } from "@/components/ui/badge";
import type { SourceStatus, SyncStatus } from "@/lib/sources";

/**
 * Source status.
 *
 * "Not yet tested" and "Connected" are shown as different things on purpose:
 * credentials being stored is not the same as a connection having been proven,
 * and conflating them would tell someone their database is reachable when
 * nothing has ever reached it.
 */

const SOURCE_TONE: Record<SourceStatus, { tone: BadgeTone; label: string }> = {
  connected: { tone: "healthy", label: "Connected" },
  configured: { tone: "neutral", label: "Not yet tested" },
  error: { tone: "down", label: "Connection failed" },
  disabled: { tone: "neutral", label: "Disabled" },
};

export function SourceStatusBadge({ status }: { status: SourceStatus }) {
  const { tone, label } = SOURCE_TONE[status];
  return (
    <Badge tone={tone} dot>
      {label}
    </Badge>
  );
}

const SYNC_TONE: Record<SyncStatus, { tone: BadgeTone; label: string }> = {
  completed: { tone: "healthy", label: "Completed" },
  running: { tone: "accent", label: "Running" },
  pending: { tone: "neutral", label: "Pending" },
  failed: { tone: "down", label: "Failed" },
  skipped: { tone: "neutral", label: "Skipped" },
};

export function SyncStatusBadge({ status }: { status: SyncStatus }) {
  const { tone, label } = SYNC_TONE[status];
  return (
    <Badge tone={tone} dot pulse={status === "running"}>
      {label}
    </Badge>
  );
}
