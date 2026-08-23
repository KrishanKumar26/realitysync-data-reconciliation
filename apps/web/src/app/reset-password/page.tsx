"use client";

import { CheckCircle2, KeyRound } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import { Field, PasswordInput } from "@/components/ui/field";
import { ApiError, resetPasswordRequest } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Setting a new password from an emailed link.
 *
 * Rendered outside the auth gate — the whole point is that nobody is signed
 * in — which AuthGate allows by path.
 *
 * The token is read from the query string and never displayed. Putting it in a
 * visible field would invite someone to screenshot or paste it, and it is a
 * live credential until it is spent.
 *
 * On success this does **not** sign the person in. The reset revoked every
 * session for that account, deliberately, and quietly starting a new one here
 * would undo the part that makes a reset worth doing.
 */

function ResetForm() {
  const token = useSearchParams().get("token") ?? "";

  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const mismatch = confirmation !== "" && confirmation !== password;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (mismatch) return;
    setError(null);
    setSubmitting(true);
    try {
      await resetPasswordRequest({ token, password });
      setDone(true);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not change the password.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  if (!token) {
    return (
      <Shell title="That link is incomplete">
        <p className="text-sm leading-relaxed text-muted-foreground">
          It is missing the part that identifies your request. Open the link
          from your email again, or ask for a new one.
        </p>
        <Link
          href="/"
          className={cn(buttonVariants({ variant: "secondary" }), "mt-6")}
        >
          Back to sign in
        </Link>
      </Shell>
    );
  }

  if (done) {
    return (
      <Shell title="Password changed" tone="healthy">
        <p className="text-sm leading-relaxed text-muted-foreground">
          You were signed out everywhere as part of the reset. Sign in with the
          new password to continue.
        </p>
        <Link href="/" className={cn(buttonVariants(), "mt-6")}>
          Sign in
        </Link>
      </Shell>
    );
  }

  return (
    <Shell title="Choose a new password">
      <p className="text-sm leading-relaxed text-muted-foreground">
        This also signs you out everywhere else.
      </p>

      <form onSubmit={handleSubmit} className="mt-6 space-y-4" noValidate>
        <Field label="New password">
          {({ inputId, describedBy }) => (
            <PasswordInput
              id={inputId}
              aria-describedby={describedBy}
              autoComplete="new-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          )}
        </Field>

        <Field
          label="Type it again"
          error={mismatch ? "The two passwords do not match." : undefined}
        >
          {({ inputId, describedBy }) => (
            <PasswordInput
              id={inputId}
              aria-describedby={describedBy}
              autoComplete="new-password"
              required
              value={confirmation}
              onChange={(event) => setConfirmation(event.target.value)}
            />
          )}
        </Field>

        {error ? (
          <p role="alert" className="text-sm text-status-down">
            {error}
          </p>
        ) : null}

        <Button
          type="submit"
          className="w-full"
          disabled={submitting || mismatch || password === ""}
        >
          {submitting ? "Changing…" : "Change password"}
        </Button>
      </form>
    </Shell>
  );
}

function Shell({
  title,
  tone = "default",
  children,
}: {
  title: string;
  tone?: "default" | "healthy";
  children: React.ReactNode;
}) {
  return (
    <main className="flex min-h-dvh items-center justify-center px-5 py-12">
      <div className="animate-rise w-full max-w-sm">
        <span
          aria-hidden="true"
          className={cn(
            "mb-5 flex h-11 w-11 items-center justify-center rounded-xl border",
            tone === "healthy"
              ? "border-status-healthy/25 bg-status-healthy/10 text-status-healthy"
              : "border-border bg-muted text-muted-foreground",
          )}
        >
          {tone === "healthy" ? (
            <CheckCircle2 className="h-5 w-5" />
          ) : (
            <KeyRound className="h-5 w-5" />
          )}
        </span>
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">
          {title}
        </h1>
        {children}
      </div>
    </main>
  );
}

export default function ResetPasswordPage() {
  // useSearchParams needs a Suspense boundary to prerender.
  return (
    <Suspense fallback={<Shell title="Choose a new password">{null}</Shell>}>
      <ResetForm />
    </Suspense>
  );
}
