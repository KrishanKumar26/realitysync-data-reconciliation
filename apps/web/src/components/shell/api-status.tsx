"use client";

import { useCallback, useEffect, useState } from "react";

import { StatusDot, type StatusTone } from "@/components/ui/status-dot";
import { ApiError, fetchHealth, type HealthResponse } from "@/lib/api";

const POLL_INTERVAL_MS = 15_000;

export type ApiConnectionState =
  | { kind: "loading" }
  | { kind: "connected"; health: HealthResponse }
  | { kind: "unavailable"; message: string };

/**
 * Live API connectivity indicator.
 *
 * This is the only value in the Phase 1 interface that reflects real backend
 * state, and it is genuinely real: it reports what /health returned, or that
 * the call failed. It never claims a connection it does not have.
 */
export function useApiStatus(): {
  state: ApiConnectionState;
  refresh: () => void;
} {
  const [state, setState] = useState<ApiConnectionState>({ kind: "loading" });

  const check = useCallback(async () => {
    try {
      const health = await fetchHealth();
      setState({ kind: "connected", health });
    } catch (error) {
      const message =
        error instanceof ApiError ? error.message : "Could not reach the API.";
      setState({ kind: "unavailable", message });
    }
  }, []);

  useEffect(() => {
    let active = true;

    const run = () => {
      if (active) void check();
    };

    run();
    const timer = setInterval(run, POLL_INTERVAL_MS);

    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [check]);

  const refresh = useCallback(() => {
    setState({ kind: "loading" });
    void check();
  }, [check]);

  return { state, refresh };
}

const TONE: Record<ApiConnectionState["kind"], StatusTone> = {
  loading: "pending",
  connected: "healthy",
  unavailable: "down",
};

export function ApiStatusIndicator({ state }: { state: ApiConnectionState }) {
  const label =
    state.kind === "loading"
      ? "Checking API…"
      : state.kind === "connected"
        ? "API connected"
        : "API unavailable";

  return (
    <StatusDot
      tone={TONE[state.kind]}
      label={label}
      pulse={state.kind === "loading"}
    />
  );
}
