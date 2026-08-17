"use client";

import { LogOut, Menu, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { OrganizationSwitcher } from "@/components/auth/organization-switcher";
import { useSession } from "@/components/auth/session-provider";
import {
  ApiStatusIndicator,
  useApiStatus,
} from "@/components/shell/api-status";
import {
  NAV_GROUP_LABELS,
  NAV_ITEMS,
  type NavGroup,
} from "@/components/shell/nav";
import { ThemeToggle } from "@/components/shell/theme-toggle";
import { cn } from "@/lib/utils";

/**
 * Application chrome: sidebar navigation, header and content region.
 *
 * Below `lg` the sidebar is a slide-over with a backdrop rather than a block
 * that pushes the page down. Pushing meant opening the menu moved the content
 * a person was reading, and closing it moved it back — on a phone that is most
 * of the screen jumping twice per navigation.
 *
 * The content column is capped at `max-w-7xl`. The previous `max-w-5xl` left
 * two thirds of a desktop monitor empty on a screen whose whole purpose is
 * showing tables of operational data side by side.
 *
 * Rendered only for an authenticated session; AuthGate decides that.
 */
export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { state } = useApiStatus();
  const [navOpen, setNavOpen] = useState(false);

  // A navigation that leaves the menu open covers the page it just moved to.
  useEffect(() => {
    setNavOpen(false);
  }, [pathname]);

  // Escape closes the slide-over. Expected of anything that overlays the page.
  useEffect(() => {
    if (!navOpen) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setNavOpen(false);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [navOpen]);

  return (
    <div className="min-h-dvh bg-background">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-panel focus:px-4 focus:py-2 focus:text-sm focus:shadow-lg"
      >
        Skip to content
      </a>

      {/* Backdrop for the mobile slide-over. */}
      {navOpen ? (
        <button
          type="button"
          aria-label="Close navigation"
          onClick={() => setNavOpen(false)}
          className="fixed inset-0 z-30 bg-overlay backdrop-blur-[2px] lg:hidden"
        />
      ) : null}

      <div className="lg:grid lg:grid-cols-[16rem_1fr]">
        {/* --- Sidebar --- */}
        <aside
          className={cn(
            "fixed inset-y-0 left-0 z-40 flex w-[17rem] flex-col border-r border-border bg-panel",
            "transition-transform duration-200 ease-out",
            "lg:sticky lg:top-0 lg:z-auto lg:h-dvh lg:w-auto lg:visible lg:translate-x-0 lg:transition-none",
            // `invisible` when closed, not just translated off-screen: an
            // off-screen element is still in the tab order, so a keyboard user
            // on a phone would tab through six hidden links before reaching the
            // page. Tailwind classes have no effect under jsdom, so this does
            // not hide the nav from the test that enumerates it.
            navOpen ? "visible translate-x-0" : "invisible -translate-x-full",
          )}
        >
          <div className="flex h-16 items-center justify-between gap-2.5 border-b border-border px-5">
            <Link href="/" className="flex items-center gap-2.5">
              <Logomark />
              <span className="text-sm font-semibold tracking-tight">
                RealitySync
              </span>
            </Link>
            <button
              type="button"
              onClick={() => setNavOpen(false)}
              className="-mr-1.5 rounded-md p-1.5 text-muted-foreground transition-colors duration-150 hover:bg-muted hover:text-foreground lg:hidden"
              aria-label="Close navigation"
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>

          <div className="border-b border-border px-3 py-3">
            <OrganizationSwitcher />
          </div>

          <nav
            id="workspace-nav"
            aria-label="Workspace"
            className="flex-1 overflow-y-auto px-3 py-4"
          >
            {(Object.keys(NAV_GROUP_LABELS) as NavGroup[]).map((group) => (
              <div key={group} className="mb-5 last:mb-0">
                <p className="px-3 pb-2 text-[0.6875rem] font-medium uppercase tracking-wider text-muted-foreground/70">
                  {NAV_GROUP_LABELS[group]}
                </p>
                <ul className="space-y-0.5">
                  {NAV_ITEMS.filter((item) => item.group === group).map(
                    (item) => {
                      const active =
                        item.href === "/"
                          ? pathname === "/"
                          : pathname.startsWith(item.href);
                      const Icon = item.icon;

                      return (
                        <li key={item.href}>
                          <Link
                            href={item.href}
                            aria-current={active ? "page" : undefined}
                            className={cn(
                              "relative flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors duration-150",
                              active
                                ? "bg-muted font-medium text-foreground"
                                : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                            )}
                          >
                            {/* Active marker. A second channel beside the fill,
                                so the current page is findable without relying
                                on a subtle background difference. */}
                            {active ? (
                              <span
                                aria-hidden="true"
                                className="absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-accent-cyan"
                              />
                            ) : null}
                            <Icon
                              className="h-4 w-4 shrink-0"
                              aria-hidden="true"
                            />
                            {item.label}
                          </Link>
                        </li>
                      );
                    },
                  )}
                </ul>
              </div>
            ))}
          </nav>

          <div className="border-t border-border p-3">
            <UserMenu />
          </div>
        </aside>

        {/* --- Main column --- */}
        <div className="flex min-h-dvh flex-col">
          <header className="sticky top-0 z-20 flex h-16 items-center justify-between gap-4 border-b border-border bg-background/85 px-5 backdrop-blur lg:px-8">
            <button
              type="button"
              className="-ml-1.5 flex items-center gap-2 rounded-md p-1.5 text-sm text-muted-foreground transition-colors duration-150 hover:bg-muted hover:text-foreground lg:hidden"
              aria-expanded={navOpen}
              aria-controls="workspace-nav"
              onClick={() => setNavOpen(true)}
            >
              <Menu className="h-5 w-5" aria-hidden="true" />
              <span className="sr-only">Menu</span>
            </button>
            <div className="hidden lg:block" />
            <div className="flex items-center gap-4">
              <ApiStatusIndicator state={state} />
              <ThemeToggle />
            </div>
          </header>

          <main id="main" className="flex-1 px-5 py-8 lg:px-8">
            <div className="mx-auto w-full max-w-7xl">{children}</div>
          </main>

          <footer className="border-t border-border px-5 py-5 lg:px-8">
            <p className="mx-auto w-full max-w-7xl text-xs text-muted-foreground">
              RealitySync — every figure on these screens is read from a
              connected source. Nothing is estimated, and anything unmeasured
              says so.
            </p>
          </footer>
        </div>
      </div>
    </div>
  );
}

/** The mark. Two offset rings: two sources, and the part where they agree. */
function Logomark() {
  return (
    <span
      aria-hidden="true"
      className="relative flex h-5 w-5 shrink-0 items-center justify-center"
    >
      <span className="absolute left-0 h-3.5 w-3.5 rounded-full border-[1.5px] border-accent-cyan" />
      <span className="absolute right-0 h-3.5 w-3.5 rounded-full border-[1.5px] border-accent-violet" />
    </span>
  );
}

/** Signed-in identity and sign-out. */
function UserMenu() {
  const { status, logout } = useSession();
  const [signingOut, setSigningOut] = useState(false);

  if (status.kind !== "authenticated") return null;
  const { user } = status.session;

  const initials = user.full_name
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2.5 rounded-md px-2 py-1.5">
        <span
          aria-hidden="true"
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-border bg-muted text-xs font-medium text-muted-foreground"
        >
          {initials}
        </span>
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-foreground">
            {user.full_name}
          </p>
          <p
            className="truncate text-xs text-muted-foreground"
            title={user.email}
          >
            {user.email}
          </p>
        </div>
      </div>
      <button
        type="button"
        disabled={signingOut}
        onClick={() => {
          setSigningOut(true);
          void logout();
        }}
        className="flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left text-sm text-muted-foreground transition-colors duration-150 hover:bg-muted hover:text-foreground disabled:opacity-60"
      >
        <LogOut className="h-4 w-4 shrink-0" aria-hidden="true" />
        {signingOut ? "Signing out…" : "Sign out"}
      </button>
    </div>
  );
}
