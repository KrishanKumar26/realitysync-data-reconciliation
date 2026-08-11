"use client";

import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { ApiError } from "@/lib/api";
import {
  createSource,
  DEFAULT_PORTS,
  SOURCE_KIND_LABELS,
  type DataSource,
  type SourceKind,
  type SslMode,
} from "@/lib/sources";

const SOURCE_KINDS: { value: SourceKind; description: string }[] = [
  { value: "postgresql", description: "Reads over TLS from a read-only role." },
  { value: "mysql", description: "Reads over TLS from a read-only account." },
];

const SSL_MODES: { value: SslMode; label: string; description: string }[] = [
  {
    value: "require",
    label: "require",
    description:
      "Encrypted. Certificate not verified — fine for a self-signed cert.",
  },
  {
    value: "verify-ca",
    label: "verify-ca",
    description: "Encrypted, certificate chain verified against a trusted CA.",
  },
  {
    value: "verify-full",
    label: "verify-full",
    description:
      "Chain verified and hostname checked. Recommended for production.",
  },
];

/**
 * Database connection form.
 *
 * The password lives in component state, is sent once, and is never read back:
 * no API response has a field for it. After saving, the interface can only
 * show that a credential exists, not what it is.
 *
 * The SSL mode selector offers no way to disable TLS. 'disable', 'allow' and
 * 'prefer' are absent rather than present-and-rejected, because an option you
 * can pick and then be told off for is worse than one that was never offered.
 */
export function AddSourceForm({
  onCreated,
  onCancel,
}: {
  onCreated: (source: DataSource) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<SourceKind>("postgresql");
  const [host, setHost] = useState("");
  const [port, setPort] = useState(String(DEFAULT_PORTS.postgresql));
  // Tracks whether the operator has typed their own port. Switching source
  // type should move the default, but must never overwrite a port someone
  // deliberately entered.
  const [portEdited, setPortEdited] = useState(false);

  function selectKind(next: SourceKind) {
    setKind(next);
    if (!portEdited) setPort(String(DEFAULT_PORTS[next]));
  }
  const [database, setDatabase] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [sslMode, setSslMode] = useState<SslMode>("require");

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setFieldErrors({});
    setSubmitting(true);

    try {
      const source = await createSource({
        name,
        kind,
        connection: {
          host: host.trim(),
          port: Number(port) || DEFAULT_PORTS[kind],
          database: database.trim(),
          username: username.trim(),
          password,
          ssl_mode: sslMode,
        },
      });
      // Clear the credential from memory as soon as it has been sent. It is
      // not needed again, and React state is readable from a devtools session.
      setPassword("");
      onCreated(source);
    } catch (caught) {
      if (caught instanceof ApiError) {
        if (
          caught.code === "VALIDATION_ERROR" &&
          Array.isArray(caught.details)
        ) {
          const mapped: Record<string, string> = {};
          for (const entry of caught.details as {
            loc?: string[];
            msg?: string;
          }[]) {
            const field = entry.loc?.[entry.loc.length - 1];
            if (field && entry.msg) {
              mapped[field] = entry.msg.replace(/^Value error,\s*/, "");
            }
          }
          setFieldErrors(mapped);
          setError("Check the highlighted fields.");
        } else {
          setError(caught.message);
        }
      } else {
        setError("Could not create the data source.");
      }
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-5" noValidate>
      <Field
        label="Source name"
        hint="How this database appears in RealitySync."
      >
        {({ inputId, describedBy }) => (
          <Input
            id={inputId}
            aria-describedby={describedBy}
            aria-invalid={Boolean(fieldErrors.name)}
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Production warehouse"
            required
          />
        )}
      </Field>

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium text-foreground">
          Source type
        </legend>
        <div className="grid gap-1.5 pt-1 sm:grid-cols-2">
          {SOURCE_KINDS.map((option) => (
            <label
              key={option.value}
              className="flex cursor-pointer items-start gap-3 rounded-md border border-border px-3 py-2.5 transition-colors duration-150 hover:bg-muted has-[:checked]:border-border-strong has-[:checked]:bg-muted"
            >
              <input
                type="radio"
                name="kind"
                value={option.value}
                checked={kind === option.value}
                onChange={() => selectKind(option.value)}
                className="mt-0.5 accent-[var(--color-accent-cyan)]"
              />
              <span className="min-w-0">
                <span className="block text-sm text-foreground">
                  {SOURCE_KIND_LABELS[option.value]}
                </span>
                <span className="block text-xs text-muted-foreground">
                  {option.description}
                </span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      <div className="grid gap-4 sm:grid-cols-[1fr_8rem]">
        <Field label="Host" error={fieldErrors.host}>
          {({ inputId, describedBy }) => (
            <Input
              id={inputId}
              aria-describedby={describedBy}
              aria-invalid={Boolean(fieldErrors.host)}
              value={host}
              onChange={(event) => setHost(event.target.value)}
              placeholder="db.example.com"
              autoComplete="off"
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
              inputMode="numeric"
              min={1}
              max={65535}
              value={port}
              onChange={(event) => {
                setPortEdited(true);
                setPort(event.target.value);
              }}
              required
            />
          )}
        </Field>
      </div>

      <Field label="Database" error={fieldErrors.database}>
        {({ inputId, describedBy }) => (
          <Input
            id={inputId}
            aria-describedby={describedBy}
            aria-invalid={Boolean(fieldErrors.database)}
            value={database}
            onChange={(event) => setDatabase(event.target.value)}
            autoComplete="off"
            required
          />
        )}
      </Field>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field
          label="Username"
          hint="A read-only role is strongly recommended."
          error={fieldErrors.username}
        >
          {({ inputId, describedBy }) => (
            <Input
              id={inputId}
              aria-describedby={describedBy}
              aria-invalid={Boolean(fieldErrors.username)}
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="off"
              required
            />
          )}
        </Field>

        <Field
          label="Password"
          hint="Encrypted at rest. Never shown again."
          error={fieldErrors.password}
        >
          {({ inputId, describedBy }) => (
            <Input
              id={inputId}
              aria-describedby={describedBy}
              aria-invalid={Boolean(fieldErrors.password)}
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="new-password"
              required
            />
          )}
        </Field>
      </div>

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium text-foreground">
          TLS mode
        </legend>
        <p className="text-xs text-muted-foreground">
          RealitySync will not connect to a database without encryption, so
          there is no option to disable TLS.
        </p>
        <div className="space-y-1.5 pt-1">
          {SSL_MODES.map((mode) => (
            <label
              key={mode.value}
              className="flex cursor-pointer items-start gap-3 rounded-md border border-border px-3 py-2.5 transition-colors duration-150 hover:bg-muted has-[:checked]:border-border-strong has-[:checked]:bg-muted"
            >
              <input
                type="radio"
                name="ssl_mode"
                value={mode.value}
                checked={sslMode === mode.value}
                onChange={() => setSslMode(mode.value)}
                className="mt-0.5 accent-[var(--color-accent-cyan)]"
              />
              <span className="min-w-0">
                <span className="tabular block text-sm text-foreground">
                  {mode.label}
                </span>
                <span className="block text-xs text-muted-foreground">
                  {mode.description}
                </span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      {error ? (
        <p role="alert" className="text-sm text-status-down">
          {error}
        </p>
      ) : null}

      <div className="flex items-center gap-2.5">
        <Button type="submit" disabled={submitting}>
          {submitting ? "Saving…" : "Save source"}
        </Button>
        <Button
          type="button"
          variant="ghost"
          onClick={onCancel}
          disabled={submitting}
        >
          Cancel
        </Button>
      </div>

      <p className="text-xs text-muted-foreground">
        Saving stores the credentials encrypted. The connection is not tested
        until you ask for it — RealitySync will not claim a connection it has
        not made.
      </p>
    </form>
  );
}
