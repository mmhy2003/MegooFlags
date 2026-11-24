"use client";

import { ArrowDown, ArrowUp, Layers, Lock, Plus } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { EnvironmentDialog } from "@/components/environments/environment-dialog";
import { type Column, DataList } from "@/components/patterns/data-list";
import { EmptyState } from "@/components/patterns/empty-state";
import { LoadFailed, LoadingRows } from "@/components/patterns/list-state";
import { PageHeader } from "@/components/patterns/page-header";
import { StatusPill } from "@/components/patterns/status-pill";
import { useProjectPermissions } from "@/components/shell/use-project-permissions";
import { Button } from "@/components/ui/button";
import { useApiData } from "@/hooks/use-api-data";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { approvalsLabel, moveKey } from "@/lib/environments/order";
import { ApiError, failureText, must } from "@/lib/flags/errors";
import { can } from "@/lib/flags/permissions";
import { projectTrail } from "@/lib/routes";

type Environment = components["schemas"]["EnvironmentOut"];

type Direction = "up" | "down";

function columns(
  canManage: boolean,
  rows: Environment[],
  onMove: (env: Environment, dir: Direction) => void,
  onEdit: (env: Environment) => void,
): Column<Environment>[] {
  const move = (env: Environment, index: number, dir: Direction) => (
    <Button
      variant="outline"
      size="icon"
      className="min-h-11 min-w-11"
      data-move={`${env.key}:${dir}`}
      aria-label={`Move ${env.name} ${dir}`}
      disabled={dir === "up" ? index === 0 : index === rows.length - 1}
      onClick={() => onMove(env, dir)}
    >
      {dir === "up" ? <ArrowUp aria-hidden /> : <ArrowDown aria-hidden />}
    </Button>
  );
  const list: Column<Environment>[] = [
    {
      id: "name",
      header: "Environment",
      priority: "key",
      cell: (e) => (
        <div className="flex min-w-0 flex-col">
          <span className="font-medium break-words">{e.name}</span>
          <span className="font-mono text-sm break-all text-muted-foreground">
            {e.key}
          </span>
        </div>
      ),
    },
    {
      id: "changes",
      header: "Changes",
      priority: "status",
      cell: (e) =>
        e.approvals_required > 0 ? (
          <StatusPill
            status="pending"
            icon={Lock}
            label={approvalsLabel(e.approvals_required)}
          />
        ) : (
          <StatusPill status="off" label={approvalsLabel(0)} />
        ),
    },
    {
      id: "description",
      header: "Description",
      priority: "main",
      cell: (e) =>
        e.description === "" ? (
          <span className="text-muted-foreground">—</span>
        ) : (
          <span className="break-words">{e.description}</span>
        ),
    },
  ];
  if (canManage)
    list.push({
      id: "actions",
      header: "Actions",
      priority: "main",
      cell: (e) => {
        // The position in the list: sort_order can have gaps after a delete.
        const index = rows.findIndex((r) => r.key === e.key);
        return (
          <div className="flex flex-wrap gap-2">
            {move(e, index, "up")}
            {move(e, index, "down")}
            <Button
              variant="outline"
              aria-label={`Edit ${e.name}`}
              onClick={() => onEdit(e)}
            >
              Edit
            </Button>
          </div>
        );
      },
    });
  return list;
}

/** The project's environments in their order, with up and down moves for managers. */
export function EnvironmentList({ project }: { project: string }) {
  const perms = useProjectPermissions(project, null, null);
  const canManage = can(perms, "environment.manage");

  const list = useApiData<Environment[]>(`environments:${project}`, () =>
    must(
      api.GET("/api/projects/{project}/environments", {
        params: { path: { project } },
      }),
    ),
  );
  // The order a move saved, until the list loads again, and the moved environment to keep focused.
  const [moved, setMoved] = useState<{
    base: Environment[] | null;
    rows: Environment[];
    focus: { key: string; dir: Direction };
  } | null>(null);
  const rows = useMemo(
    () => (moved && moved.base === list.data ? moved.rows : (list.data ?? [])),
    [moved, list.data],
  );
  // The environment the dialog edits; null creates one.
  const [dialog, setDialog] = useState<{
    open: boolean;
    environment: Environment | null;
  }>({ open: false, environment: null });

  // A move waits for the one before it, so each PUT starts from the saved order.
  const [moving, setMoving] = useState(false);

  // DataList renders a table and cards, so the move buttons exist twice; focus the visible one.
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const root = container.current;
    if (!moved || !root) return;
    const { key, dir } = moved.focus;
    const visible = (d: Direction) =>
      [
        ...root.querySelectorAll<HTMLButtonElement>(
          `[data-move="${CSS.escape(`${key}:${d}`)}"]`,
        ),
      ].find((b) => !b.disabled && b.getClientRects().length > 0);
    // At an end the pressed direction is disabled, so focus the other one.
    (visible(dir) ?? visible(dir === "up" ? "down" : "up"))?.focus();
  }, [moved]);

  const onMove = async (env: Environment, dir: Direction) => {
    if (moving) return;
    setMoving(true);
    const keys = moveKey(
      rows.map((e) => e.key),
      env.key,
      dir === "up" ? -1 : 1,
    );
    try {
      const saved = await must(
        api.PUT("/api/projects/{project}/environment-order", {
          params: { path: { project } },
          body: { environments: keys },
        }),
      );
      setMoved({
        base: list.data,
        rows: saved,
        focus: { key: env.key, dir },
      });
    } catch (error) {
      if (!(error instanceof ApiError)) {
        console.error("reordering environments failed", error);
        toast.error("Couldn't reach the server");
      } else if (error.failure.status === 422) {
        setMoved(null);
        list.reload();
        toast.error(
          "The environments changed meanwhile. The list was reloaded.",
        );
      } else {
        toast.error(failureText(error.failure));
      }
    } finally {
      setMoving(false);
    }
  };

  let body: React.ReactNode;
  if (list.failure) {
    body = (
      <LoadFailed
        title="Couldn't load environments"
        failure={list.failure}
        onRetry={list.reload}
      />
    );
  } else if (!list.data) {
    body = <LoadingRows label="Loading environments" />;
  } else if (rows.length === 0) {
    body = (
      <EmptyState
        icon={Layers}
        title="No environments"
        description="Flags are turned on and targeted per environment."
      />
    );
  } else {
    body = (
      <DataList
        caption="Environments"
        columns={columns(
          canManage,
          rows,
          (env, dir) => void onMove(env, dir),
          (environment) => setDialog({ open: true, environment }),
        )}
        rows={rows}
        rowKey={(e) => e.key}
      />
    );
  }

  return (
    <>
      <PageHeader
        title="Environments"
        trail={projectTrail(project)}
        actions={
          canManage ? (
            <Button
              onClick={() => setDialog({ open: true, environment: null })}
            >
              <Plus aria-hidden />
              New environment
            </Button>
          ) : undefined
        }
      />
      <div ref={container} className="flex flex-col gap-4">
        {body}
      </div>
      {canManage && (
        <EnvironmentDialog
          project={project}
          environment={dialog.environment}
          open={dialog.open}
          onOpenChange={(open) => setDialog((d) => ({ ...d, open }))}
          onSaved={() => {
            setMoved(null);
            list.reload();
          }}
        />
      )}
    </>
  );
}
