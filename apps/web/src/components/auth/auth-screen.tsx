"use client";

import { useState, type FormEvent } from "react";

import { useSession } from "@/components/auth/session-provider";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
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
        <div className="animate-rise w-full max-w-sm">
          <div className="mb-8 lg:hidden">
            <Wordmark />
          </div>

          <h1 className="text-2xl font-semibold tracking-tight text-foreground">
            {isSignUp ? "Create your workspace" : "Sign in"}
          </h1>
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
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

          <form onSubmit={handleSubmit} className="mt-6 space-y-4" noValidate>
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
                <Input
                  id={inputId}
                  aria-describedby={describedBy}
                  aria-invalid={Boolean(fieldErrors.password)}
                  name="password"
                  type="password"
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

            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting
                ? isSignUp
                  ? "Creating workspace…"
                  : "Signing in…"
                : isSignUp
                  ? "Create workspace"
                  : "Sign in"}
            </Button>
          </form>

          <p className="mt-6 text-sm text-muted-foreground">
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
        className="h-2.5 w-2.5 rounded-full bg-accent-cyan"
        aria-hidden="true"
      />
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
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -left-24 -top-24 h-96 w-96 rounded-full opacity-[0.18] blur-3xl"
        style={{
          background:
            "radial-gradient(circle, var(--color-accent-cyan), transparent 70%)",
        }}
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -bottom-32 -right-16 h-96 w-96 rounded-full opacity-[0.14] blur-3xl"
        style={{
          background:
            "radial-gradient(circle, var(--color-accent-violet), transparent 70%)",
        }}
      />

      <Wordmark className="relative" />

      <div className="relative max-w-md">
        <p className="text-2xl font-semibold leading-snug tracking-tight text-foreground">
          Know what is actually happening.
        </p>
        <p className="mt-4 text-sm leading-relaxed text-muted-foreground">
          RealitySync reconciles observations from every connected source into
          one continuously verified state — and shows the evidence behind each
          conclusion.
        </p>
      </div>

      <p className="relative text-xs text-muted-foreground">
        Every value in RealitySync comes from a real source. Nothing is
        estimated to fill a gap.
      </p>
    </aside>
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
