"use client";

import { useEffect, useRef, useState } from "react";

import { useSession } from "@/components/auth/session-provider";
import { cn } from "@/lib/utils";

/**
 * Active organization selector.
 *
 * Switching is a server operation, not a client filter: it changes which
 * organization the session is acting in, so every subsequent request returns a
 * different tenant's data. Nothing is filtered in the browser, because the
 * browser never receives another tenant's data to filter.
 */
export function OrganizationSwitcher() {
  const { organizations, activeOrganization, switchOrganization } =
    useSession();
  const [open, setOpen] = useState(false);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;

    function onPointerDown(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }

    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  if (!activeOrganization) return null;

  async function select(organizationId: string) {
    if (organizationId === activeOrganization?.id) {
      setOpen(false);
      return;
    }

    setPendingId(organizationId);
    setError(null);
    try {
      await switchOrganization(organizationId);
      setOpen(false);
    } catch {
      setError("Could not switch workspace.");
    } finally {
      setPendingId(null);
    }
  }

  // A single organization is not a choice. Show it as a label rather than a
  // menu that does nothing when opened.
  if (organizations.length < 2) {
    return (
      <div className="min-w-0">
        <p className="truncate text-sm font-medium text-foreground">
          {activeOrganization.name}
        </p>
        <p className="text-xs capitalize text-muted-foreground">
          {activeOrganization.role}
        </p>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={cn(
          "flex w-full items-center justify-between gap-2 rounded-md px-2.5 py-2 text-left",
          "transition-colors duration-150 hover:bg-muted",
          open && "bg-muted",
        )}
      >
        <span className="min-w-0">
          <span className="block truncate text-sm font-medium text-foreground">
            {activeOrganization.name}
          </span>
          <span className="block text-xs capitalize text-muted-foreground">
            {activeOrganization.role}
          </span>
        </span>
        <svg
          viewBox="0 0 16 16"
          className={cn(
            "h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform duration-150",
            open && "rotate-180",
          )}
          aria-hidden="true"
        >
          <path
            d="M4 6l4 4 4-4"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>

      {open ? (
        <div
          role="listbox"
          aria-label="Switch workspace"
          className="animate-rise absolute left-0 right-0 top-full z-20 mt-1 overflow-hidden rounded-md border border-border bg-panel py-1 shadow-lg"
        >
          {organizations.map((organization) => {
            const active = organization.id === activeOrganization.id;
            return (
              <button
                key={organization.id}
                type="button"
                role="option"
                aria-selected={active}
                disabled={pendingId !== null}
                onClick={() => void select(organization.id)}
                className={cn(
                  "flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm",
                  "transition-colors duration-150 hover:bg-muted disabled:opacity-60",
                  active && "bg-muted",
                )}
              >
                <span className="min-w-0">
                  <span className="block truncate text-foreground">
                    {organization.name}
                  </span>
                  <span className="block text-xs capitalize text-muted-foreground">
                    {organization.role}
                  </span>
                </span>
                {pendingId === organization.id ? (
                  <span className="text-xs text-muted-foreground">
                    Switching…
                  </span>
                ) : active ? (
                  <svg
                    viewBox="0 0 16 16"
                    className="h-3.5 w-3.5 shrink-0 text-accent-cyan"
                    aria-hidden="true"
                  >
                    <path
                      d="M3 8.5l3.5 3.5L13 5"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.75"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                ) : null}
              </button>
            );
          })}
        </div>
      ) : null}

      {error ? (
        <p role="alert" className="mt-1 px-2.5 text-xs text-status-down">
          {error}
        </p>
      ) : null}
    </div>
  );
}
