/**
 * Data source API client.
 *
 * Note what is missing: no type here carries a password except
 * {@link CreateSourceInput}, which is a *request* shape. Nothing the API
 * returns has a field for one, so a credential cannot arrive at the browser
 * even by accident.
 */

import { apiFetch } from "@/lib/api";

export type SourceKind = "postgresql" | "mysql";

/** Default port per source type, so the form never offers the wrong one. */
export const DEFAULT_PORTS: Record<SourceKind, number> = {
  postgresql: 5432,
  mysql: 3306,
};

/** How each type is named in the interface. */
export const SOURCE_KIND_LABELS: Record<SourceKind, string> = {
  postgresql: "PostgreSQL",
  mysql: "MySQL",
};
export type SourceStatus = "configured" | "connected" | "error" | "disabled";
export type SslMode = "require" | "verify-ca" | "verify-full";
export type EventTimeSemantics = "observed" | "recorded" | "ingest_fallback";
export type SyncStatus =
  "pending" | "running" | "completed" | "failed" | "skipped";

/** What the API returns about a connection — never the password. */
export interface ConnectionSummary {
  host: string;
  port: number;
  database: string;
  username: string;
  ssl_mode: SslMode;
  password_set: boolean;
}

export interface DataSource {
  id: string;
  name: string;
  kind: SourceKind;
  status: SourceStatus;
  connection: ConnectionSummary;
  last_connected_at: string | null;
  last_connection_latency_ms: number | null;
  last_synced_at: string | null;
  last_error: string | null;
  last_error_at: string | null;
  stream_count: number;
  observation_count: number;
  created_at: string;
}

export interface ConnectionTestResult {
  status: "connected" | "failed";
  database: string | null;
  server_version: string | null;
  latency_ms: number | null;
  tls_version: string | null;
  connected_as: string | null;
  can_discover_schema: boolean;
  warnings: string[];
  error_code: string | null;
  error_message: string | null;
  remediation: string | null;
}

export interface DiscoveredColumn {
  name: string;
  data_type: string;
  nullable: boolean;
  is_primary_key: boolean;
  is_temporal: boolean;
}

export interface DiscoveredTable {
  schema_name: string;
  table_name: string;
  qualified_name: string;
  kind: string;
  /** Planner estimate. Always labelled approximate — it is not a row count. */
  approximate_row_count: number | null;
  columns: DiscoveredColumn[];
  primary_key_columns: string[];
  temporal_columns: string[];
  configured: boolean;
}

export interface SchemaDiscovery {
  schemas: string[];
  tables: DiscoveredTable[];
  inaccessible_schemas: string[];
  discovered_at: string | null;
}

export interface SourceStream {
  id: string;
  data_source_id: string;
  schema_name: string;
  table_name: string;
  qualified_name: string;
  primary_key_columns: string[];
  event_time_column: string | null;
  event_time_semantics: EventTimeSemantics;
  selected_columns: string[];
  enabled: boolean;
  poll_interval_seconds: number;
  last_synced_at: string | null;
  last_event_time: string | null;
  observation_count: number;
  created_at: string;
}

export interface SyncStreamDetail {
  stream_id: string | null;
  table: string | null;
  rows_seen: number;
  rows_created: number;
  rows_skipped: number;
  error_code: string | null;
  error_message: string | null;
}

export interface SyncRun {
  id: string;
  source_id: string;
  stream_id: string | null;
  status: SyncStatus;
  started_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  rows_seen: number;
  rows_created: number;
  rows_skipped: number;
  error_code: string | null;
  error_message: string | null;
  streams: SyncStreamDetail[];
}

export interface Observation {
  id: string;
  source_id: string;
  stream_id: string;
  external_id: string;
  entity_mapping_state: string;
  payload: Record<string, unknown>;
  event_time: string;
  event_time_semantics: string;
  ingested_at: string;
  fingerprint: string;
  provenance: Record<string, unknown>;
}

export interface CreateSourceInput {
  name: string;
  kind: SourceKind;
  connection: {
    host: string;
    port: number;
    database: string;
    username: string;
    /** Held in form state only, sent once, never returned. */
    password: string;
    ssl_mode: SslMode;
  };
}

export interface CreateStreamInput {
  schema_name: string;
  table_name: string;
  primary_key_columns: string[];
  event_time_column?: string | null;
  event_time_semantics: EventTimeSemantics;
  selected_columns?: string[];
  enabled?: boolean;
  poll_interval_seconds?: number;
}

const base = "/api/data-sources";

export function listSources(): Promise<DataSource[]> {
  return apiFetch<DataSource[]>(base, { cache: "no-store" });
}

export function getSource(id: string): Promise<DataSource> {
  return apiFetch<DataSource>(`${base}/${id}`, { cache: "no-store" });
}

export function createSource(input: CreateSourceInput): Promise<DataSource> {
  return apiFetch<DataSource>(base, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function deleteSource(id: string): Promise<void> {
  return apiFetch<void>(`${base}/${id}`, { method: "DELETE" });
}

export function testConnection(id: string): Promise<ConnectionTestResult> {
  // Longer timeout than the default: this deliberately dials a remote
  // database, and the connector's own connect timeout should be what fails
  // first, not the browser giving up early with a less useful message.
  return apiFetch<ConnectionTestResult>(`${base}/${id}/test-connection`, {
    method: "POST",
    timeoutMs: 30_000,
  });
}

export function discoverSchema(id: string): Promise<SchemaDiscovery> {
  return apiFetch<SchemaDiscovery>(`${base}/${id}/discover-schema`, {
    method: "POST",
    timeoutMs: 45_000,
  });
}

export function listStreams(id: string): Promise<SourceStream[]> {
  return apiFetch<SourceStream[]>(`${base}/${id}/streams`, {
    cache: "no-store",
  });
}

export function createStream(
  id: string,
  input: CreateStreamInput,
): Promise<SourceStream> {
  return apiFetch<SourceStream>(`${base}/${id}/streams`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateStream(
  id: string,
  streamId: string,
  input: Partial<Pick<SourceStream, "enabled" | "poll_interval_seconds">>,
): Promise<SourceStream> {
  return apiFetch<SourceStream>(`${base}/${id}/streams/${streamId}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function runSync(
  id: string,
  options: { full_refresh?: boolean; stream_id?: string } = {},
): Promise<SyncRun> {
  // A sync reads a remote database inline, so it can legitimately take a
  // while on a large table.
  return apiFetch<SyncRun>(`${base}/${id}/sync`, {
    method: "POST",
    body: JSON.stringify(options),
    timeoutMs: 120_000,
  });
}

export function listSyncRuns(id: string): Promise<SyncRun[]> {
  return apiFetch<SyncRun[]>(`${base}/${id}/sync-runs`, { cache: "no-store" });
}

export function listObservations(id: string): Promise<Observation[]> {
  return apiFetch<Observation[]>(`${base}/${id}/observations`, {
    cache: "no-store",
  });
}
