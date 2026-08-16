/**
 * Entity, conflict and timeline API client.
 *
 * Note what the types do and do not carry. `Conflict.score` is
 * `number | null` and `severity` can be `"unspecified"` — the confidence
 * specification is missing, so a conflict can be *detected* without being
 * *graded*. The interface must render that distinction rather than defaulting
 * a missing grade to something reassuring.
 */

import { apiFetch } from "@/lib/api";

export type ConflictType =
  "value_conflict" | "source_disagreement" | "contested_state";

/** "unspecified" means the severity thresholds are not available. */
export type ConflictSeverity =
  "low" | "medium" | "high" | "critical" | "unspecified";

export type ConflictStatus = "open" | "acknowledged" | "resolved" | "dismissed";

export type TimeAxis = "event" | "knowledge";

export interface Entity {
  id: string;
  entity_type: string;
  natural_key: string;
  display_name: string | null;
  mapping_count: number;
  observation_count: number;
  created_at: string;
}

export interface CompetingValue {
  value: unknown;
  weight: string;
  share: string;
  sources: string[];
  observation_count: number;
}

export interface Conflict {
  id: string;
  entity_id: string;
  entity_natural_key: string | null;
  reality_state_id: string | null;
  attribute: string;
  conflict_type: ConflictType;
  severity: ConflictSeverity;
  status: ConflictStatus;
  /** null while the conflict-score formula is unavailable. */
  score: string | null;
  summary: string;
  details: {
    competing_values?: CompetingValue[];
    divergence?: string | null;
    margin_percentage_points?: string;
    grading?: { available: boolean; reason?: string };
    [key: string]: unknown;
  };
  detected_at: string;
  last_seen_at: string;
  resolved_at: string | null;
  resolution_note: string | null;
}

export interface TimelineEvent {
  observation_id: string;
  external_id: string;
  source_id: string;
  source_name: string;
  values: Record<string, unknown>;
  /** When the fact was true, per the source. */
  event_time: string;
  /** When RealitySync learned it. */
  ingested_at: string;
  event_time_semantics: string;
  /** True when learned after it was true — the two axes diverged here. */
  arrived_late: boolean;
  lag_seconds: number;
}

export interface Timeline {
  axis: TimeAxis;
  as_of_event_time: string | null;
  as_of_knowledge_time: string | null;
  event_count: number;
  late_arrival_count: number;
  truncated: boolean;
  events: TimelineEvent[];
}

export interface RealityState {
  id: string;
  entity_id: string;
  attribute: string;
  /**
   * `null` when no value was selected. Two different situations, told apart by
   * `status`: `unknown` means no usable evidence exists, `contested` means
   * several values compete and the rule for ranking them is unavailable.
   */
  value: unknown;
  /**
   * Whether `value` is a selection. Read this rather than checking `value` for
   * null — a source can legitimately assert null, and the two must not be
   * rendered the same way.
   */
  value_selected: boolean;
  /** `null` while the confidence specification is unavailable. Never render 0. */
  confidence: string | null;
  /** Explicit flag, so the UI branches on intent rather than on a null. */
  confidence_available: boolean;
  status: string;
  /** The score's inputs when it exists; otherwise why it does not. */
  confidence_breakdown: Record<string, unknown>;
  selection_reason: string;
  valid_from: string;
  calculated_at: string;
  algorithm_version: string;
  supporting_count: number;
  dissenting_count: number;
  source_count: number;
}

/** One observation's role in a state. The provenance trail. */
export interface Evidence {
  observation_id: string;
  source_id: string;
  stream_id: string;
  external_id: string;
  role: "supporting" | "dissenting" | "excluded" | "considered";
  weight: string;
  observed_value: unknown;
  /** When the source says it was true. */
  event_time: string;
  /** When RealitySync learned it. Deliberately separate from `event_time`. */
  ingested_at: string;
  exclusion_reason: string | null;
}

export interface RecalculateResult {
  entity_id: string;
  attributes_considered: number;
  states_written: number;
  conflicts_written: number;
  calculated_at: string;
  /** How many written states carry no confidence score. */
  states_unscored: number;
  unscored_attributes: { attribute: string; blocked_on: string }[];
  /**
   * True when nothing could be scored. It does NOT mean nothing was written —
   * states are written either way now, with a null confidence.
   */
  blocked: boolean;
  blocked_on: string[];
  missing_specifications: { name: string; description: string }[];
}

export function listEntities(): Promise<Entity[]> {
  return apiFetch<Entity[]>("/api/entities", { cache: "no-store" });
}

export function createEntity(input: {
  entity_type: string;
  natural_key: string;
  display_name?: string;
}): Promise<Entity> {
  return apiFetch<Entity>("/api/entities", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function listConflicts(
  params: { status?: ConflictStatus } = {},
): Promise<Conflict[]> {
  const query = params.status ? `?status=${params.status}` : "";
  return apiFetch<Conflict[]>(`/api/conflicts${query}`, { cache: "no-store" });
}

export function updateConflict(
  id: string,
  input: { status: "acknowledged" | "resolved" | "dismissed"; note?: string },
): Promise<Conflict> {
  return apiFetch<Conflict>(`/api/conflicts/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function fetchTimeline(
  entityId: string,
  params: {
    axis?: TimeAxis;
    as_of_event_time?: string;
    as_of_knowledge_time?: string;
  } = {},
): Promise<Timeline> {
  const query = new URLSearchParams();
  if (params.axis) query.set("axis", params.axis);
  if (params.as_of_event_time)
    query.set("as_of_event_time", params.as_of_event_time);
  if (params.as_of_knowledge_time)
    query.set("as_of_knowledge_time", params.as_of_knowledge_time);

  const suffix = query.toString() ? `?${query}` : "";
  return apiFetch<Timeline>(`/api/entities/${entityId}/timeline${suffix}`, {
    cache: "no-store",
  });
}

export function listRealityStates(entityId: string): Promise<RealityState[]> {
  return apiFetch<RealityState[]>(`/api/entities/${entityId}/reality`, {
    cache: "no-store",
  });
}

export function listEvidence(
  entityId: string,
  attribute: string,
): Promise<Evidence[]> {
  return apiFetch<Evidence[]>(
    `/api/entities/${entityId}/reality/${encodeURIComponent(attribute)}/evidence`,
    { method: "GET" },
  );
}

export function recalculate(entityId: string): Promise<RecalculateResult> {
  return apiFetch<RecalculateResult>(`/api/entities/${entityId}/recalculate`, {
    method: "POST",
    timeoutMs: 60_000,
  });
}
