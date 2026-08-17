import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  THEME_STORAGE_KEY,
  ThemeToggle,
} from "@/components/shell/theme-toggle";

describe("ThemeToggle", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });

  afterEach(() => {
    document.documentElement.removeAttribute("data-theme");
  });

  it("offers light, dark and system", () => {
    render(<ThemeToggle />);

    for (const name of ["Light", "Dark", "System"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
  });

  it("starts on system, which sets no attribute at all", () => {
    render(<ThemeToggle />);

    expect(screen.getByRole("button", { name: "System" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
  });

  it("applies and remembers an explicit choice", async () => {
    const user = userEvent.setup({ delay: null });
    render(<ThemeToggle />);

    await user.click(screen.getByRole("button", { name: "Dark" }));

    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
  });

  it("removes the override when returning to system", async () => {
    // Not "sets it to light": an explicit light would stop the app following
    // a machine that switches to dark in the evening.
    const user = userEvent.setup({ delay: null });
    render(<ThemeToggle />);

    await user.click(screen.getByRole("button", { name: "Dark" }));
    await user.click(screen.getByRole("button", { name: "System" }));

    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("system");
  });

  it("restores a stored choice on mount", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "light");

    render(<ThemeToggle />);

    expect(screen.getByRole("button", { name: "Light" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });
});
