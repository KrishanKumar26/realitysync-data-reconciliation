"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";

import { ApiStatusIndicator, useApiStatus } from "@/components/shell/api-status";
import { NAV_ITEMS } from "@/components/shell/nav";
import { cn } from "@/lib/utils";

/**
 * Application chrome: sidebar navigation, header and content region.
 *
 * Responsive by construction — the sidebar becomes a disclosure panel below
 * the `lg` breakpoint rather than a separate mobile implementation.
 */
export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { state } = useApiStatus();
  const [navOpen, setNavOpen] = useState(false);

  return (
    <div className="min-h-dvh bg-background">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-panel focus:px-4 focus:py-2 focus:text-sm"
      >
        Skip to content
      </a>

      <div className="lg:grid lg:grid-cols-[15rem_1fr]">
        {/* --- Sidebar --- */}
        <aside
          className={cn(
            "border-b border-border bg-panel lg:sticky lg:top-0 lg:h-dvh lg:border-b-0 lg:border-r",
            navOpen ? "block" : "hidden lg:block",
          )}
        >
          <div className="flex h-14 items-center gap-2.5 px-5 lg:border-b lg:border-border">
            <span
              className="h-2.5 w-2.5 rounded-full bg-accent-cyan"
              aria-hidden="true"
            />
            <span className="text-sm font-semibold tracking-tight">RealitySync</span>
          </div>

          <nav aria-label="Workspace" className="px-3 py-4">
            <ul className="space-y-0.5">
              {NAV_ITEMS.map((item) => {
                const active =
                  item.href === "/"
                    ? pathname === "/"
                    : pathname.startsWith(item.href);

                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      aria-current={active ? "page" : undefined}
                      onClick={() => setNavOpen(false)}
                      className={cn(
                        "block rounded-md px-3 py-2 text-sm transition-colors duration-150",
                        active
                          ? "bg-muted font-medium text-foreground"
                          : "text-muted-foreground hover:bg-muted hover:text-foreground",
                      )}
                    >
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>
        </aside>

        {/* --- Main column --- */}
        <div className="flex min-h-dvh flex-col">
          <header className="sticky top-0 z-10 flex h-14 items-center justify-between gap-4 border-b border-border bg-background/85 px-5 backdrop-blur">
            <button
              type="button"
              className="rounded-md border border-border px-2.5 py-1.5 text-sm text-muted-foreground lg:hidden"
              aria-expanded={navOpen}
              aria-controls="workspace-nav"
              onClick={() => setNavOpen((open) => !open)}
            >
              Menu
            </button>
            <div className="hidden lg:block" />
            <ApiStatusIndicator state={state} />
          </header>

          <main id="main" className="flex-1 px-5 py-8 lg:px-8">
            <div className="mx-auto w-full max-w-5xl">{children}</div>
          </main>

          <footer className="border-t border-border px-5 py-4 lg:px-8">
            <p className="mx-auto w-full max-w-5xl text-xs text-muted-foreground">
              RealitySync — foundation build. No product data is present yet.
            </p>
          </footer>
        </div>
      </div>
    </div>
  );
}
