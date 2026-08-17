"use client";

import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Field, Input, PasswordInput } from "@/components/ui/field";
import { Select } from "@/components/ui/select";
import { ApiError } from "@/lib/api";
import {
  updateSource,
  type DataSource,
  type SslMode,
  type UpdateSourceInput,
} from "@/lib/sources";

/**
 * Editing a source.
 *
 * There was no way to change one. A provider resetting a password meant
 * deleting the source — and with it every configured table, every sync run and
 * every record ever read through it — and rebuilding it from scratch. That is
 * a destructive answer to a routine event.
 *
 * The password field is empty and stays empty. The API never returns a stored
 * password, so there is nothing to prefill, and a placeholder of dots would
 * imply a value that could be submitted unchanged. Blank means "keep the one
 * you have"; typing replaces it.
 *
 * Only changed fields are sent. That keeps a rename a rename, rather than a
 * full resubmission that happens to contain the same values — which matters,
 * because the API drops the status back to unverified whenever a connection
 * parameter actually changes.
 */

const SSL_MODES: { value: SslMode; label: string }[] = [
  { value: "require", label: "require — encrypted, certificate not verified" },
  { value: "verify-ca", label: "verify-ca — chain verified" },
  { value: "verify-full", label: "verify-full — chain and hostname verified" },
];

function mapValidationErrors(details: unknown): Record<string, string> {
  if (!Array.isArray(details)) return {};
  const mapped: Record<string, string> = {};
  for (const entry of details) {
    if (typeof entry !== "object" || entry === null) continue;
    const { loc, msg } = entry as { loc?: unknown; msg?: unknown };
    if (!Array.isArray(loc) || typeof msg !== "string") continue;
    const field = loc[loc.length - 1];
    if (typeof field === "string") {
      mapped[field] = msg.replace(/^Value error,\s*/, "");
    }
  }
  return mapped;
}

export function EditSourceForm({
  source,
  onCancel,
  onSaved,
}: {
  source: DataSource;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(source.name);
  const [host, setHost] = useState(source.connection.host);
  const [port, setPort] = useState(String(source.connection.port));
  const [database, setDatabase] = useState(source.connection.database);
  const [username, setUsername] = useState(source.connection.username);
  const [sslMode, setSslMode] = useState<SslMode>(source.connection.ssl_mode);
  const [password, setPassword] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  function buildPayload(): UpdateSourceInput {
    const payload: UpdateSourceInput = {};
    if (name.trim() !== source.name) payload.name = name.trim();

    const connection: NonNullable<UpdateSourceInput["connection"]> = {};
    if (host.trim() !== source.connection.host) connection.host = host.trim();
    if (Number(port) !== source.connection.port) connection.port = Number(port);
    if (database.trim() !== source.connection.database) {
      connection.database = database.trim();
    }
    if (username.trim() !== source.connection.username) {
      connection.username = username.trim();
    }
    if (sslMode !== source.connection.ssl_mode) connection.ssl_mode = sslMode;
    if (password !== "") connection.password = password;

    if (Object.keys(connection).length > 0) payload.connection = connection;
    return payload;
  }

  const payload = buildPayload();
  const nothingChanged = Object.keys(payload).length === 0;
  const connectionChanged = payload.connection !== undefined;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setFieldErrors({});
    setSubmitting(true);
    try {
      await updateSource(source.id, buildPayload());
      onSaved();
    } catch (caught) {
      if (caught instanceof ApiError) {
        setFieldErrors(mapValidationErrors(caught.details));
        setError(caught.message);
      } else {
        setError("Could not save the changes.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-5" noValidate>
      <Field label="Source name" error={fieldErrors.name}>
        {({ inputId, describedBy }) => (
          <Input
            id={inputId}
            aria-describedby={describedBy}
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
          />
        )}
      </Field>

      <div className="grid gap-5 sm:grid-cols-3">
        <Field label="Host" error={fieldErrors.host} className="sm:col-span-2">
          {({ inputId, describedBy }) => (
            <Input
              id={inputId}
              aria-describedby={describedBy}
              value={host}
              onChange={(event) => setHost(event.target.value)}
              required
            />
          )}
        </Field>

        <Field label="Port" error={fieldErrors.port}>
          {({ inputId, describedBy }) => (
            <Input
              id={inputId}
              aria-describedby={describedBy}
              type="number"
              value={port}
              onChange={(event) => setPort(event.target.value)}
              required
            />
          )}
        </Field>
      </div>

      <div className="grid gap-5 sm:grid-cols-2">
        <Field label="Database" error={fieldErrors.database}>
          {({ inputId, describedBy }) => (
            <Input
              id={inputId}
              aria-describedby={describedBy}
              value={database}
              onChange={(event) => setDatabase(event.target.value)}
              required
            />
          )}
        </Field>

        <Field label="Username" error={fieldErrors.username}>
          {({ inputId, describedBy }) => (
            <Input
              id={inputId}
              aria-describedby={describedBy}
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              required
            />
          )}
        </Field>
      </div>

      <Field
        label="New password"
        hint="Leave blank to keep the stored password. RealitySync never returns it, so there is nothing to show here."
        error={fieldErrors.password}
      >
        {({ inputId, describedBy }) => (
          <PasswordInput
            id={inputId}
            aria-describedby={describedBy}
            autoComplete="new-password"
            placeholder="Unchanged"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        )}
      </Field>

      <Select
        label="TLS mode"
        value={sslMode}
        onChange={(event) => setSslMode(event.target.value as SslMode)}
        containerClassName="sm:max-w-md"
      >
        {SSL_MODES.map((mode) => (
          <option key={mode.value} value={mode.value}>
            {mode.label}
          </option>
        ))}
      </Select>

      {connectionChanged ? (
        <p className="rounded-md border border-status-degraded/25 bg-status-degraded/5 px-3.5 py-2.5 text-xs leading-relaxed text-muted-foreground">
          Changing a connection detail marks this source unverified again, and
          the connection is not tested on save. A successful connection to the
          previous target says nothing about the new one — press Test connection
          afterwards.
        </p>
      ) : null}

      {error ? (
        <p role="alert" className="text-sm text-status-down">
          {error}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-2.5">
        <Button type="submit" disabled={submitting || nothingChanged}>
          {submitting ? "Saving…" : "Save changes"}
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        {nothingChanged ? (
          <span className="text-xs text-muted-foreground">
            Nothing changed yet.
          </span>
        ) : null}
      </div>
    </form>
  );
}
