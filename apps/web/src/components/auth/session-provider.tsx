"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  ApiError,
  fetchSession,
  loginRequest,
  logoutRequest,
  registerRequest,
  switchOrganizationRequest,
  type AuthenticatedSession,
  type OrganizationMembership,
  type SessionState,
} from "@/lib/api";

/**
 * Session state, resolved once on mount and shared by the whole application.
 *
 * Four distinct states, not a boolean plus a spinner:
 *
 * `loading`
 *     We have not asked the API yet. Rendering a sign-in form here would make
 *     the page flash "signed out" on every reload for an already-signed-in
 *     user — the single most common bug in cookie-based SPA auth.
 * `authenticated`
 *     A live session with its user and organizations.
 * `anonymous`
 *     Confirmed signed out. Show the sign-in form.
 * `expired`
 *     A session existed and ended. Same form, different words: the person
 *     deserves to know why they are being asked again.
 *
 * `unreachable` is a fifth: the API did not answer. That is not "signed out",
 * and treating it as such would show a sign-in form that cannot possibly work.
 */
export type SessionStatus =
  | { kind: "loading" }
  | { kind: "authenticated"; session: AuthenticatedSession }
  | { kind: "anonymous" }
  | { kind: "expired" }
  | { kind: "unreachable"; message: string };

interface SessionContextValue {
  status: SessionStatus;
  /** The signed-in user's organizations, or an empty list. */
  organizations: OrganizationMembership[];
  /** The organization the session is currently acting in. */
  activeOrganization: OrganizationMembership | null;
  login: (input: { email: string; password: string }) => Promise<void>;
  register: (input: {
    email: string;
    password: string;
    full_name: string;
    organization_name: string;
  }) => Promise<void>;
  logout: () => Promise<void>;
  switchOrganization: (organizationId: string) => Promise<void>;
  refresh: () => Promise<void>;
}

const SessionContext = createContext<SessionContextValue | null>(null);

function toStatus(state: SessionState): SessionStatus {
  if (state.authenticated) return { kind: "authenticated", session: state };
  return state.reason === "expired" ? { kind: "expired" } : { kind: "anonymous" };
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<SessionStatus>({ kind: "loading" });

  const refresh = useCallback(async () => {
    try {
      setStatus(toStatus(await fetchSession()));
    } catch (error) {
      // A network failure is not a sign-out. Saying "signed out" here would
      // be a guess presented as fact, and would hide a backend outage behind
      // a sign-in form.
      setStatus({
        kind: "unreachable",
        message:
          error instanceof ApiError
            ? error.message
            : "Could not reach the API.",
      });
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(async (input: { email: string; password: string }) => {
    // Errors propagate: the form owns the message, because it is the thing
    // that can put it next to the field the person is looking at.
    setStatus({ kind: "authenticated", session: await loginRequest(input) });
  }, []);

  const register = useCallback(
    async (input: {
      email: string;
      password: string;
      full_name: string;
      organization_name: string;
    }) => {
      setStatus({ kind: "authenticated", session: await registerRequest(input) });
    },
    [],
  );

  const logout = useCallback(async () => {
    try {
      await logoutRequest();
    } catch {
      // Swallowed, not rethrown. Signing out always succeeds from the caller's
      // point of view: if the request failed, the server has either already
      // revoked the session or is unreachable, and in both cases leaving the
      // interface looking signed in is the worse outcome. Rethrowing would
      // also produce an unhandled rejection at every call site, since none of
      // them have anything useful to do with the error.
    } finally {
      setStatus({ kind: "anonymous" });
    }
  }, []);

  const switchOrganization = useCallback(async (organizationId: string) => {
    setStatus({
      kind: "authenticated",
      session: await switchOrganizationRequest(organizationId),
    });
  }, []);

  const value = useMemo<SessionContextValue>(() => {
    const session = status.kind === "authenticated" ? status.session : null;
    const organizations = session?.organizations ?? [];
    const activeOrganization =
      organizations.find((org) => org.id === session?.active_organization_id) ??
      null;

    return {
      status,
      organizations,
      activeOrganization,
      login,
      register,
      logout,
      switchOrganization,
      refresh,
    };
  }, [status, login, register, logout, switchOrganization, refresh]);

  return (
    <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
  );
}

export function useSession(): SessionContextValue {
  const context = useContext(SessionContext);
  if (context === null) {
    throw new Error("useSession must be used within a SessionProvider");
  }
  return context;
}
