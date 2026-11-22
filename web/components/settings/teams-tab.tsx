"use client";

import { Plus, UsersRound } from "lucide-react";
import { useState } from "react";

import { EmptyState } from "@/components/patterns/empty-state";
import { type Column, DataList } from "@/components/patterns/data-list";
import { LoadFailed, LoadingRows } from "@/components/patterns/list-state";
import { TeamDialog } from "@/components/settings/team-dialog";
import { Button } from "@/components/ui/button";
import { useApiData } from "@/hooks/use-api-data";
import { bindingText } from "@/lib/access/bindings";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { must } from "@/lib/flags/errors";

type Team = components["schemas"]["TeamOut"];
type Loaded = { teams: Team[]; roles: Map<string, string[]> };

/** Each team's roles as text ("viewer · Project shop"), by team id. */
async function teamRoles(): Promise<Map<string, string[]>> {
  const page = await must(
    api.GET("/api/bindings", {
      params: { query: { subject_kind: "team", limit: 500 } },
    }),
  );
  const roles = new Map<string, string[]>();
  for (const b of page.items) {
    roles.set(b.subject_id, [
      ...(roles.get(b.subject_id) ?? []),
      bindingText(b),
    ]);
  }
  return roles;
}

function columns(
  roles: Map<string, string[]>,
  onOpen: (t: Team) => void,
): Column<Team>[] {
  return [
    {
      id: "name",
      header: "Team",
      priority: "key",
      cell: (t) => (
        <div className="flex min-w-0 flex-col">
          <span className="font-medium break-words">{t.name}</span>
          <span className="font-mono text-sm break-all text-muted-foreground">
            {t.key}
          </span>
        </div>
      ),
    },
    {
      id: "members",
      header: "Members",
      priority: "main",
      cell: (t) =>
        `${t.member_count} ${t.member_count === 1 ? "member" : "members"}`,
    },
    {
      id: "roles",
      header: "Roles",
      priority: "main",
      cell: (t) => {
        const held = roles.get(t.id) ?? [];
        if (held.length === 0)
          return <span className="text-muted-foreground">No roles</span>;
        return <span className="break-words">{held.join(", ")}</span>;
      },
    },
    {
      id: "actions",
      header: "Actions",
      priority: "main",
      cell: (t) => (
        <Button
          variant="outline"
          className="min-h-11"
          aria-label={`Open ${t.name}`}
          onClick={() => onOpen(t)}
        >
          Open
        </Button>
      ),
    },
  ];
}

/**
 * The organization's teams with their member counts and roles. Anyone signed in can look; with
 * `member.manage` they can also create, rename and delete teams and change members and roles.
 */
export function TeamsTab({ canManage }: { canManage: boolean }) {
  const list = useApiData<Loaded>("teams", async () => {
    const [page, roles] = await Promise.all([
      must(api.GET("/api/teams", { params: { query: { limit: 500 } } })),
      teamRoles(),
    ]);
    return { teams: page.items, roles };
  });
  // The team stays set while the dialog closes, so its title doesn't change mid-animation.
  const [dialog, setDialog] = useState<{
    open: boolean;
    team: Team | null;
  }>({ open: false, team: null });

  let body: React.ReactNode;
  if (list.failure) {
    body = (
      <LoadFailed
        title="Couldn't load teams"
        failure={list.failure}
        onRetry={list.reload}
      />
    );
  } else if (!list.data) {
    body = <LoadingRows label="Loading teams" />;
  } else if (list.data.teams.length === 0) {
    body = (
      <EmptyState
        icon={UsersRound}
        title="No teams yet"
        description="A team gives its members the same roles. SSO groups can fill teams too."
      />
    );
  } else {
    body = (
      <DataList
        caption="Teams"
        columns={columns(list.data.roles, (team) =>
          setDialog({ open: true, team }),
        )}
        rows={list.data.teams}
        rowKey={(t) => t.id}
      />
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {canManage && (
        <div className="flex justify-end">
          <Button onClick={() => setDialog({ open: true, team: null })}>
            <Plus aria-hidden />
            New team
          </Button>
        </div>
      )}
      {body}
      <TeamDialog
        team={dialog.team}
        open={dialog.open}
        onOpenChange={(open) => setDialog((d) => ({ ...d, open }))}
        canManage={canManage}
        onChanged={list.reload}
      />
    </div>
  );
}
