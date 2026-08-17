"use client";

import { useState, type FormEvent } from "react";

import { useSession } from "@/components/auth/session-provider";
import { Check } from "lucide-react";
import type { CSSProperties } from "react";

import { Button } from "@/components/ui/button";
import { Field, Input, PasswordInput } from "@/components/ui/field";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

type Mode = "signin" | "signup";

/** Mirrors the API's policy so the client can say so before a round trip. */
const PASSWORD_MIN_LENGTH = 12;

/**
 * Sign-in and sign-up.
 *
 * One screen with two modes rather than two routes: the fields overlap almost
 * entirely, and switching is instant instead of a navigation. Everything shown
 * here is real — there is no product data on this screen to fake.
 */
export function AuthScreen({ expired = false }: { expired?: boolean }) {
  const { login, register } = useSession();

  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [organizationName, setOrganizationName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  const isSignUp = mode === "signup";

  function switchMode(next: Mode) {
    setMode(next);
    setError(null);
    setFieldErrors({});
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setFieldErrors({});

    if (isSignUp && password.length < PASSWORD_MIN_LENGTH) {
      // Caught client-side purely for speed of feedback. The API enforces the
      // same rule, and the API is the one that decides.
      setFieldErrors({
        password: `Use at least ${PASSWORD_MIN_LENGTH} characters.`,
      });
      return;
    }

    setSubmitting(true);
    try {
      if (isSignUp) {
        await register({
          email,
          password,
          full_name: fullName,
          organization_name: organizationName,
        });
      } else {
        await login({ email, password });
      }
    } catch (caught) {
      if (caught instanceof ApiError) {
        if (caught.code === "VALIDATION_ERROR") {
          setFieldErrors(mapValidationErrors(caught.details));
          setError("Check the highlighted fields.");
        } else {
          setError(caught.message);
        }
      } else {
        setError("Something went wrong. Try again.");
      }
      setSubmitting(false);
      return;
    }
    // Deliberately no setSubmitting(false) on success: the provider swaps this
    // screen out, and re-enabling a button on an unmounting component is both
    // pointless and a React warning waiting to happen.
  }

  return (
    <div className="grid min-h-dvh lg:grid-cols-[1.1fr_1fr]">
      <BrandPanel />

      <main className="flex items-center justify-center px-5 py-12 sm:px-8">
        <div className="w-full max-w-sm">
          <div className="animate-rise-stagger mb-8 lg:hidden">
            <Wordmark />
          </div>

          <h1
            className="animate-rise-stagger text-2xl font-semibold tracking-tight text-foreground"
            style={{ "--stagger": 1 } as CSSProperties}
          >
            {isSignUp ? "Create your workspace" : "Sign in"}
          </h1>
          <p
            className="animate-rise-stagger mt-2 text-sm leading-relaxed text-muted-foreground"
            style={{ "--stagger": 2 } as CSSProperties}
          >
            {isSignUp
              ? "Your workspace is where connected sources and reconciled state live."
              : "Continue to your RealitySync workspace."}
          </p>

          {expired ? (
            <div
              role="status"
              className="mt-5 rounded-md border border-border bg-muted px-3.5 py-2.5 text-sm text-muted-foreground"
            >
              Your session ended. Sign in to continue.
            </div>
          ) : null}

          <form
            onSubmit={handleSubmit}
            className="animate-rise-stagger mt-6 space-y-4"
            style={{ "--stagger": 3 } as CSSProperties}
            noValidate
          >
            {isSignUp ? (
              <>
                <Field label="Your name" error={fieldErrors.full_name}>
                  {({ inputId, describedBy }) => (
                    <Input
                      id={inputId}
                      aria-describedby={describedBy}
                      aria-invalid={Boolean(fieldErrors.full_name)}
                      name="full_name"
                      autoComplete="name"
                      required
                      value={fullName}
                      onChange={(event) => setFullName(event.target.value)}
                    />
                  )}
                </Field>

                <Field
                  label="Workspace name"
                  error={fieldErrors.organization_name}
                >
                  {({ inputId, describedBy }) => (
                    <Input
                      id={inputId}
                      aria-describedby={describedBy}
                      aria-invalid={Boolean(fieldErrors.organization_name)}
                      name="organization_name"
                      autoComplete="organization"
                      required
                      value={organizationName}
                      onChange={(event) =>
                        setOrganizationName(event.target.value)
                      }
                    />
                  )}
                </Field>
              </>
            ) : null}

            <Field label="Email" error={fieldErrors.email}>
              {({ inputId, describedBy }) => (
                <Input
                  id={inputId}
                  aria-describedby={describedBy}
                  aria-invalid={Boolean(fieldErrors.email)}
                  name="email"
                  type="email"
                  autoComplete="email"
                  required
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                />
              )}
            </Field>

            <Field
              label="Password"
              hint={
                isSignUp
                  ? `At least ${PASSWORD_MIN_LENGTH} characters.`
                  : undefined
              }
              error={fieldErrors.password}
            >
              {({ inputId, describedBy }) => (
                <PasswordInput
                  id={inputId}
                  aria-describedby={describedBy}
                  aria-invalid={Boolean(fieldErrors.password)}
                  name="password"
                  autoComplete={isSignUp ? "new-password" : "current-password"}
                  required
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
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
              size="md"
              className="h-10 w-full"
              disabled={submitting}
            >
              {submitting
                ? isSignUp
                  ? "Creating workspace…"
                  : "Signing in…"
                : isSignUp
                  ? "Create workspace"
                  : "Sign in"}
            </Button>
          </form>

          <p
            className="animate-rise-stagger mt-6 text-sm text-muted-foreground"
            style={{ "--stagger": 4 } as CSSProperties}
          >
            {isSignUp ? "Already have an account? " : "No account yet? "}
            <button
              type="button"
              onClick={() => switchMode(isSignUp ? "signin" : "signup")}
              className="font-medium text-foreground underline underline-offset-4 hover:opacity-80"
            >
              {isSignUp ? "Sign in" : "Create one"}
            </button>
          </p>
        </div>
      </main>
    </div>
  );
}

function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <span
        aria-hidden="true"
        className="relative flex h-5 w-5 shrink-0 items-center justify-center"
      >
        <span className="absolute left-0 h-3.5 w-3.5 rounded-full border-[1.5px] border-accent-cyan" />
        <span className="absolute right-0 h-3.5 w-3.5 rounded-full border-[1.5px] border-accent-violet" />
      </span>
      <span className="text-sm font-semibold tracking-tight">RealitySync</span>
    </span>
  );
}

/**
 * The left-hand panel.
 *
 * States what the product does. No dashboard screenshot, no sample metrics —
 * a fabricated confidence score on the sign-in screen would be a lie told
 * before anyone has even signed in.
 */
function BrandPanel() {
  return (
    <aside className="relative hidden overflow-hidden border-r border-border bg-panel lg:flex lg:flex-col lg:justify-between lg:p-12">
      <div aria-hidden="true" className="bg-grid absolute inset-0 opacity-40" />

      <div
        aria-hidden="true"
        className="animate-drift pointer-events-none absolute -left-24 -top-24 h-96 w-96 rounded-full opacity-[0.18] blur-3xl"
        style={{
          background:
            "radial-gradient(circle, var(--color-accent-cyan), transparent 70%)",
        }}
      />
      <div
        aria-hidden="true"
        className="animate-drift-slow pointer-events-none absolute -bottom-32 -right-16 h-96 w-96 rounded-full opacity-[0.14] blur-3xl"
        style={{
          background:
            "radial-gradient(circle, var(--color-accent-violet), transparent 70%)",
        }}
      />

      <Wordmark className="animate-rise-stagger relative" />

      <div className="relative max-w-md">
        <ConvergingRings />

        <p
          className="animate-rise-stagger mt-10 text-3xl font-semibold leading-[1.15] tracking-tight text-foreground"
          style={{ "--stagger": 1 } as CSSProperties}
        >
          Know what is actually happening.
        </p>
        <p
          className="animate-rise-stagger mt-4 text-sm leading-relaxed text-muted-foreground"
          style={{ "--stagger": 2 } as CSSProperties}
        >
          RealitySync reads every connected source, compares what each one says,
          and shows you the current value together with the evidence behind it —
          including where they disagree.
        </p>
      </div>

      <div className="relative space-y-3">
        <ul className="space-y-2.5">
          {[
            "Connects to PostgreSQL and MySQL over TLS, read-only.",
            "Records what each source said, and when it said it.",
            "Flags disagreements instead of silently picking a winner.",
          ].map((line, index) => (
            <li
              key={line}
              className="animate-rise-stagger flex items-start gap-2.5 text-sm text-muted-foreground"
              style={{ "--stagger": 3 + index } as CSSProperties}
            >
              <Check
                className="mt-0.5 h-4 w-4 shrink-0 text-accent-cyan"
                aria-hidden="true"
              />
              {line}
            </li>
          ))}
        </ul>
        <p
          className="animate-rise-stagger border-t border-border pt-3 text-xs text-muted-foreground"
          style={{ "--stagger": 6 } as CSSProperties}
        >
          Every value comes from a real source. Nothing is estimated to fill a
          gap.
        </p>
      </div>
    </aside>
  );
}

/**
 * Two sources, and the part where they agree.
 *
 * The one picture on the screen, and it is the product's argument rather than
 * decoration: two rings drift towards each other and apart, overlapping but
 * never coinciding. Purely decorative to a screen reader — the sentence
 * underneath says the same thing in words.
 */
function ConvergingRings() {
  return (
    <div
      aria-hidden="true"
      className="animate-rise-stagger relative h-24 w-44"
      style={{ "--stagger": 0 } as CSSProperties}
    >
      <span className="absolute left-2 top-2 h-20 w-20 rounded-full border border-accent-cyan/50" />
      <span className="animate-converge absolute left-2 top-2 h-20 w-20 rounded-full border border-accent-violet/50" />
      <span className="absolute left-2 top-2 h-20 w-20 rounded-full bg-accent-cyan/[0.07] blur-md" />
    </div>
  );
}

/** Turn the API's validation details into per-field messages. */
function mapValidationErrors(details: unknown): Record<string, string> {
  if (!Array.isArray(details)) return {};

  const mapped: Record<string, string> = {};
  for (const entry of details) {
    if (typeof entry !== "object" || entry === null) continue;
    const { loc, msg } = entry as { loc?: unknown; msg?: unknown };
    if (!Array.isArray(loc) || typeof msg !== "string") continue;

    // loc is ["body", "<field>"]; the field name is the last segment.
    const field = loc[loc.length - 1];
    if (typeof field === "string") {
      mapped[field] = msg.replace(/^Value error,\s*/, "");
    }
  }
  return mapped;
}
