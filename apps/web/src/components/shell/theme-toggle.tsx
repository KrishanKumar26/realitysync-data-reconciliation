"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

/**
 * Light / dark / system.
 *
 * Three states, not two. "System" is the default and it is a real choice: it
 * follows the operating system, so a machine that goes dark in the evening
 * takes the app with it. A two-way switch would silently pin the app to
 * whatever it was when someone first pressed the button.
 *
 * The stylesheet already handles all three — bare `:root` is light,
 * `prefers-color-scheme` covers system, and `[data-theme]` overrides both — so
 * this only has to set or remove one attribute.
 *
 * Reading localStorage happens in an effect, so the first render matches what
 * the server produced and hydration does not mismatch. The *applied* theme is
 * set before paint by the inline script in layout.tsx, which is why there is
 * no flash of the wrong theme while this component mounts.
 */

export const THEME_STORAGE_KEY = "realitysync-theme";

type Theme = "light" | "dark" | "system";

const OPTIONS: { value: Theme; label: string; icon: typeof Sun }[] = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "System", icon: Monitor },
];

function apply(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "system") {
    root.removeAttribute("data-theme");
  } else {
    root.setAttribute("data-theme", theme);
  }
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("system");

  useEffect(() => {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === "light" || stored === "dark" || stored === "system") {
      setTheme(stored);
    }
  }, []);

  function choose(next: Theme) {
    setTheme(next);
    apply(next);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Private browsing can refuse writes. The choice still applies to this
      // page; it simply will not survive a reload, which beats crashing.
    }
  }

  return (
    <div
      role="group"
      aria-label="Theme"
      className="inline-flex items-center gap-0.5 rounded-lg border border-border bg-muted/40 p-0.5"
    >
      {OPTIONS.map((option) => {
        const Icon = option.icon;
        const active = theme === option.value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            title={option.label}
            onClick={() => choose(option.value)}
            className={cn(
              "rounded-md p-1.5 transition-colors duration-150",
              active
                ? "bg-panel text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <Icon className="h-4 w-4" aria-hidden="true" />
            <span className="sr-only">{option.label}</span>
          </button>
        );
      })}
    </div>
  );
}
