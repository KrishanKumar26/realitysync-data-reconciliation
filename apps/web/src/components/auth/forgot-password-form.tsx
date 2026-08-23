"use client";

import { MailCheck } from "lucide-react";
import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { ApiError, forgotPasswordRequest } from "@/lib/api";

/**
 * Asking for a reset link.
 *
 * The confirmation is deliberately non-committal — "if that address belongs to
 * an account" — and it is shown for **every** submission, including addresses
 * that do not exist. The API answers identically either way so that the form
 * cannot be used to find out who has an account here, and saying "we sent it"
 * or "no such user" would throw that away at the last step.
 *
 * It also does not claim the message has arrived. Where no mail sender is
 * configured the link is written to the server log instead, and an interface
 * that says "check your inbox" when nothing was sent is the kind of confident
 * falsehood this product exists to argue against.
 */
export function ForgotPasswordForm({ onBack }: { onBack: () => void }) {
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const result = await forgotPasswordRequest(email);
      setMessage(result.message);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not send a reset link.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  if (message) {
    return (
      <div className="animate-rise">
        <span
          aria-hidden="true"
          className="mb-5 flex h-11 w-11 items-center justify-center rounded-xl border border-border bg-muted text-muted-foreground"
        >
          <MailCheck className="h-5 w-5" />
        </span>
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">
          Check your email
        </h1>
        <p
          role="status"
          className="mt-2 text-sm leading-relaxed text-muted-foreground"
        >
          {message}
        </p>
        <Button variant="secondary" className="mt-6" onClick={onBack}>
          Back to sign in
        </Button>
      </div>
    );
  }

  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight text-foreground">
        Reset your password
      </h1>
      <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
        Tell us the address you signed up with and we will send a link to set a
        new password.
      </p>

      <form onSubmit={handleSubmit} className="mt-6 space-y-4" noValidate>
        <Field label="Email">
          {({ inputId, describedBy }) => (
            <Input
              id={inputId}
              aria-describedby={describedBy}
              name="email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          )}
        </Field>

        {error ? (
          <p role="alert" className="text-sm text-status-down">
            {error}
          </p>
        ) : null}

        <Button type="submit" className="w-full" disabled={submitting}>
          {submitting ? "Sending…" : "Send reset link"}
        </Button>
      </form>

      <p className="mt-6 text-sm text-muted-foreground">
        Remembered it?{" "}
        <button
          type="button"
          onClick={onBack}
          className="font-medium text-foreground underline underline-offset-4 hover:opacity-80"
        >
          Sign in
        </button>
      </p>
    </div>
  );
}
