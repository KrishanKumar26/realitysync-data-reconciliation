/**
 * Overview API client.
 *
 * Note the shape of `Confidence`: every number is `number | null` and there is
 * an `available` flag. That is not defensive typing — it is the API telling the
 * truth. While the approved confidence specification is missing there is no
 * score to report, and `null` forces the interface to handle that rather than
 * rendering a zero that would read as "we are certain of nothing".
 */

import { apiFetch } from "@/lib/api";
import type { SourceStatus } from "@/lib/sources";

export interface SourceHealth {
  source_id: string;
  name: string;
  kind: string;
  status: SourceStatus;
  stream_count: number;
  observation_count: number;
  last_connected_at: string | null;
  last_synced_at: string | null;
  last_error: string | null;
  last_error_at: string | null;
  /** Credentials stored but never verified — distinct from unhealthy. */
  never_tested: boolean;
}

export interface SourceSummary {
  total: number;
  connected: number;
  never_tested: number;
  errored: number;
  disabled: number;
  sources: SourceHealth[];
}

export interface IngestionSummary {
  observation_count: number;
  observations_in_window: number;
  entity_count: number;
  mapped_entity_count: number;
  unmapped_entity_count: number;
  stream_count: number;
  enabled_stream_count: number;
  last_sync_at: string | null;
  syncs_in_window: number;
  failed_syncs_in_window: number;
}

export interface ConflictSummary {
  open: number;
  acknowledged: number;
  resolved: number;
  dismissed: number;
  outstanding: number;
  /** Graded buckets only. */
  by_severity: Record<string, number>;
  /** Detected but not assessed — never folded in with "low". */
  ungraded: number;
  newest_open_at: string | null;
}

export interface Confidence {
  available: boolean;
  scored_state_count: number;
  unscored_attribute_count: number;
  average_confidence: number | null;
  lowest_confidence: number | null;
  highest_confidence: number | null;
  algorithm_version: string;
  blocked_reason: string | null;
  missing_specifications: { name: string; description: string }[];
}

export interface ActivityItem {
  kind: "audit" | "sync" | "conflict";
  occurred_at: string;
  summary: string;
  detail: string | null;
  resource_type: string | null;
  resource_id: string | null;
  severity: string | null;
}

export interface Dashboard {
  organization_id: string;
  generated_at: string;
  window_days: number;
  /** True when nothing is connected yet — onboarding, not "quiet". */
  is_empty: boolean;
  sources: SourceSummary;
  ingestion: IngestionSummary;
  conflicts: ConflictSummary;
  confidence: Confidence;
  activity: ActivityItem[];
}

export function fetchDashboard(): Promise<Dashboard> {
  return apiFetch<Dashboard>("/api/dashboard", { cache: "no-store" });
}

export function fetchActivity(limit = 50): Promise<ActivityItem[]> {
  return apiFetch<ActivityItem[]>(`/api/activity?limit=${limit}`, {
    cache: "no-store",
  });
}
