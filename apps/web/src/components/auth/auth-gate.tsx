"use client";

import type { ReactNode } from "react";

import { usePathname } from "next/navigation";

import { AuthScreen } from "@/components/auth/auth-screen";
import { useSession } from "@/components/auth/session-provider";
import { AppShell } from "@/components/shell/app-shell";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ui/states";

/**
 * Decides what the application renders for the current session state.
 *
 * The states are kept distinct on purpose. Collapsing "loading" into "signed
 * out" produces the flash of a sign-in form on every reload; collapsing
 * "unreachable" into "signed out" shows a form that cannot possibly succeed
 * and blames the user for a backend outage.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { status, refresh } = useSession();

  // The reset page has to render for someone who is signed out — that is the
  // entire situation it exists for. Without this the gate shows the sign-in
  // form instead and the link in the email goes nowhere.
  //
  // Checked after the hooks, never before: an early return above them would
  // change how many hooks run between renders, which is a rule of hooks
  // violation and a genuinely nasty class of bug.
  if (pathname === "/reset-password") return <>{children}</>;

  switch (status.kind) {
    case "loading":
      return <SessionLoading />;

    case "authenticated":
      return <AppShell>{children}</AppShell>;

    case "expired":
      return <AuthScreen expired />;

    case "anonymous":
      return <AuthScreen />;

    case "unreachable":
      return (
        <div className="flex min-h-dvh items-center justify-center px-5">
          <ErrorState
            title="Cannot reach RealitySync"
            description={`${status.message} The API may still be starting. Your session has not ended.`}
            action={
              <Button
                variant="secondary"
                size="sm"
                onClick={() => void refresh()}
              >
                Try again
              </Button>
            }
          />
        </div>
      );
  }
}

/**
 * Shown while the session is being resolved.
 *
 * Deliberately shaped like the signed-in layout — sidebar plus content — so
 * that the transition into the application is a fill rather than a jump.
 */
function SessionLoading() {
  return (
    <div
      className="min-h-dvh bg-background lg:grid lg:grid-cols-[15rem_1fr]"
      data-testid="session-loading"
      role="status"
      aria-live="polite"
    >
      <span className="sr-only">Signing you in</span>

      <aside className="hidden border-r border-border bg-panel lg:block">
        <div className="flex h-14 items-center gap-2.5 border-b border-border px-5">
          <span
            className="h-2.5 w-2.5 rounded-full bg-accent-cyan"
            aria-hidden="true"
          />
          <span className="text-sm font-semibold tracking-tight">
            RealitySync
          </span>
        </div>
        <div className="space-y-2 px-3 py-4" aria-hidden="true">
          {[...Array(6)].map((_, index) => (
            <Skeleton key={index} className="h-8 w-full" />
          ))}
        </div>
      </aside>

      <div className="px-5 py-8 lg:px-8" aria-hidden="true">
        <div className="mx-auto w-full max-w-5xl space-y-6">
          <Skeleton className="h-7 w-40" />
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      </div>
    </div>
  );
}
