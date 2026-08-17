import react from "@vitejs/plugin-react";
import { resolve } from "path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": resolve(import.meta.dirname, "./src") },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}"],
    // 5s is vitest's default and it is too tight for this suite: every test
    // mounts a provider, resolves a stubbed session and drives real user
    // events through jsdom. Under load — a build running alongside, or CI on
    // a shared runner — sign-in tests were timing out and passing again on a
    // rerun, which is worse than a slow suite because it teaches everyone to
    // ignore a red run. Nothing here should ever take 15s; a test that does is
    // genuinely stuck and still fails.
    testTimeout: 15_000,
  },
});
