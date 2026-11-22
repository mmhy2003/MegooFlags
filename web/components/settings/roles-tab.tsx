"use client";

import { Plus, ShieldCheck } from "lucide-react";
import { useState } from "react";

import { EmptyState } from "@/components/patterns/empty-state";
import { type Column, DataList } from "@/components/patterns/data-list";
import { LoadFailed, LoadingRows } from "@/components/patterns/list-state";
import { RoleDialog } from "@/components/settings/role-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useApiData } from "@/hooks/use-api-data";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { must } from "@/lib/flags/errors";

type Role = components["schemas"]["RoleOut"];

function columns(onOpen: (r: Role) => void): Column<Role>[] {
  return [
    {
      id: "name",
      header: "Role",
      priority: "key",
      cell: (r) => (
        <div className="flex min-w-0 flex-col">
          <span className="font-medium break-words">{r.name}</span>
          <span className="font-mono text-sm break-all text-muted-foreground">
            {r.key}
          </span>
        </div>
      ),
    },
    {
      id: "kind",
      header: "Kind",
      priority: "status",
      cell: (r) => (
        <Badge variant={r.builtin ? "secondary" : "outline"}>
          {r.builtin ? "Built-in" : "Custom"}
        </Badge>
      ),
    },
    {
      id: "permissions",
      header: "Permissions",
      priority: "main",
      cell: (r) =>
        `${r.permissions.length} ${r.permissions.length === 1 ? "permission" : "permissions"}`,
    },
    {
      id: "description",
      header: "Description",
      priority: "extra",
      cell: (r) =>
        r.description ? (
          <span className="break-words">{r.description}</span>
        ) : (
          <span className="text-muted-foreground">No description</span>
        ),
    },
    {
      id: "actions",
      header: "Actions",
      priority: "main",
      cell: (r) => (
        <Button
          variant="outline"
          className="min-h-11"
          aria-label={`Open ${r.name}`}
          onClick={() => onOpen(r)}
        >
          Open
        </Button>
      ),
    },
  ];
}

/**
 * The organization's built-in and custom roles. Anyone signed in can look; with `role.manage`
 * they can also create, edit and delete custom roles.
 */
export function RolesTab({ canManage }: { canManage: boolean }) {
  const list = useApiData("roles", () =>
    must(api.GET("/api/roles", { params: { query: { limit: 500 } } })).then(
      (page) => page.items,
    ),
  );
  // The role stays set while the dialog closes, so its title doesn't change mid-animation.
  const [dialog, setDialog] = useState<{
    open: boolean;
    role: Role | null;
  }>({ open: false, role: null });

  let body: React.ReactNode;
  if (list.failure) {
    body = (
      <LoadFailed
        title="Couldn't load roles"
        failure={list.failure}
        onRetry={list.reload}
      />
    );
  } else if (!list.data) {
    body = <LoadingRows label="Loading roles" />;
  } else if (list.data.length === 0) {
    body = (
      <EmptyState
        icon={ShieldCheck}
        title="No roles"
        description="A role is a set of permissions you grant to people, teams and tokens."
      />
    );
  } else {
    body = (
      <DataList
        caption="Roles"
        columns={columns((role) => setDialog({ open: true, role }))}
        rows={list.data}
        rowKey={(r) => r.id}
      />
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {canManage && (
        <div className="flex justify-end">
          <Button onClick={() => setDialog({ open: true, role: null })}>
            <Plus aria-hidden />
            New role
          </Button>
        </div>
      )}
      {body}
      <RoleDialog
        role={dialog.role}
        open={dialog.open}
        onOpenChange={(open) => setDialog((d) => ({ ...d, open }))}
        canManage={canManage}
        onChanged={list.reload}
      />
    </div>
  );
}
