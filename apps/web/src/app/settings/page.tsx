"use client";

import { useCallback, useEffect, useState } from "react";

import { useSession } from "@/components/auth/session-provider";
import { Panel, PanelBody, PanelHeader } from "@/components/ui/panel";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ui/states";
import { ApiError, apiFetch } from "@/lib/api";

interface Member {
  user_id: string;
  email: string;
  full_name: string;
  role: string;
  joined_at: string;
}

/**
 * Workspace settings.
 *
 * Every value here is a real record from the active organization. The member
 * list is fetched from an organization-scoped endpoint, so it can only ever
 * contain people who are actually in this workspace — switching organizations
 * refetches rather than filtering, because the browser never holds another
 * tenant's data to filter.
 */
export default function SettingsPage() {
  const { status, activeOrganization } = useSession();
  const [members, setMembers] = useState<Member[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const organizationId = activeOrganization?.id ?? null;

  const load = useCallback(async () => {
    setMembers(null);
    setError(null);
    try {
      setMembers(await apiFetch<Member[]>("/api/organizations/current/members"));
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "Could not load members.",
      );
    }
  }, []);

  useEffect(() => {
    if (organizationId) void load();
    // Refetch when the active organization changes — the endpoint's scope
    // comes from the session, so the same URL returns different rows.
  }, [organizationId, load]);

  const currentUserId =
    status.kind === "authenticated" ? status.session.user.id : null;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">Settings</h1>
        <p className="mt-1.5 text-sm text-muted-foreground">
          Workspace, members and security.
        </p>
      </header>

      <Panel>
        <PanelHeader
          title="Workspace"
          description="The organization this session is acting in."
        />
        <PanelBody>
          {activeOrganization ? (
            <dl className="grid gap-x-8 gap-y-3 sm:grid-cols-3">
              <div>
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                  Name
                </dt>
                <dd className="mt-1 text-sm text-foreground">
                  {activeOrganization.name}
                </dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                  Identifier
                </dt>
                <dd className="tabular mt-1 text-sm text-foreground">
                  {activeOrganization.slug}
                </dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                  Your role
                </dt>
                <dd className="mt-1 text-sm capitalize text-foreground">
                  {activeOrganization.role}
                </dd>
              </div>
            </dl>
          ) : (
            <p className="text-sm text-muted-foreground">
              This session has no workspace selected.
            </p>
          )}
        </PanelBody>
      </Panel>

      <Panel>
        <PanelHeader
          title="Members"
          description="People with access to this workspace."
        />
        <PanelBody className={members && members.length > 0 ? "p-0" : undefined}>
          {error ? (
            <ErrorState
              title="Could not load members"
              description={error}
              className="py-8"
            />
          ) : members === null ? (
            <div className="space-y-2.5" data-testid="members-loading">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : (
            <ul className="divide-y divide-border">
              {members.map((member) => (
                <li
                  key={member.user_id}
                  className="flex items-center justify-between gap-4 px-5 py-3"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm text-foreground">
                      {member.full_name}
                      {member.user_id === currentUserId ? (
                        <span className="ml-2 text-xs text-muted-foreground">
                          you
                        </span>
                      ) : null}
                    </p>
                    <p className="truncate text-xs text-muted-foreground">
                      {member.email}
                    </p>
                  </div>
                  <span className="shrink-0 rounded-full border border-border px-2.5 py-0.5 text-xs capitalize text-muted-foreground">
                    {member.role}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </PanelBody>
      </Panel>

      <Panel>
        <PanelHeader
          title="Security"
          description="Session and access controls."
        />
        <PanelBody>
          <ul className="space-y-2 text-sm text-muted-foreground">
            <li>
              Sessions are stored server-side and can be revoked immediately.
              Signing out ends the session on the server, not just in this
              browser.
            </li>
            <li>
              Passwords are hashed with Argon2id and are never returned by the
              API.
            </li>
            <li>
              Inviting members, changing roles and reviewing the audit trail
              arrive with the workspace administration screens.
            </li>
          </ul>
        </PanelBody>
      </Panel>
    </div>
  );
}
