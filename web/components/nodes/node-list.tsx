"use client";

import {
  Ban,
  Ellipsis,
  Pause,
  Play,
  Plus,
  RotateCw,
  Server,
  Trash2,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { AddNodeDialog } from "@/components/nodes/add-node-dialog";
import { NodeSummary } from "@/components/nodes/node-summary";
import { RotateNodeDialog } from "@/components/nodes/rotate-node-dialog";
import { ConfirmDialog } from "@/components/patterns/confirm-dialog";
import { type Column, DataList } from "@/components/patterns/data-list";
import { EmptyState } from "@/components/patterns/empty-state";
import { PageHeader } from "@/components/patterns/page-header";
import { StatusPill } from "@/components/patterns/status-pill";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiData } from "@/hooks/use-api-data";
import { useVisibleInterval } from "@/hooks/use-visible-interval";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, failureText, must } from "@/lib/flags/errors";
import { relativeTime, snapshotText } from "@/lib/nodes/format";
import { nodeActions, nodePill } from "@/lib/nodes/status";

type Node = components["schemas"]["NodeOut"];
type NodeList = components["schemas"]["NodeList"];

/** A loaded list, with the time it was loaded so relative times stay pure. */
type Loaded = { list: NodeList; now: number };

const REFRESH_MS = 10_000;

/** The actions that ask first, with their dialog texts and the toast after them. */
type Confirmed = "drain" | "resume" | "revoke" | "remove";

const CONFIRMS: Record<
  Confirmed,
  {
    title: (name: string) => string;
    description: string;
    label: string;
    destructive: boolean;
    done: (name: string) => string;
  }
> = {
  drain: {
    title: (name) => `Drain ${name}?`,
    description:
      "It stops taking new traffic: /readyz turns unready and after the grace time its SDK streams close so SDKs reconnect elsewhere.",
    label: "Drain",
    destructive: false,
    done: (name) => `${name} is draining`,
  },
  resume: {
    title: (name) => `Resume ${name}?`,
    description: "It becomes ready and takes traffic again.",
    label: "Resume",
    destructive: false,
    done: (name) => `${name} is active again`,
  },
  revoke: {
    title: (name) => `Revoke ${name}?`,
    description:
      "It stops serving at once and answers SDK requests with 503. This can't be undone; add a new worker to replace it.",
    label: "Revoke",
    destructive: true,
    done: (name) => `${name} revoked`,
  },
  remove: {
    title: (name) => `Remove ${name}?`,
    description: "It disappears from this list.",
    label: "Remove",
    destructive: true,
    done: (name) => `${name} removed`,
  },
};

function showError(error: unknown, what: string) {
  if (!(error instanceof ApiError)) console.error(what, error);
  toast.error(
    error instanceof ApiError
      ? failureText(error.failure)
      : "Couldn't reach the server",
  );
}

/** Runs a confirmed action against the API. */
async function run(action: Confirmed, n: Node): Promise<void> {
  const params = { params: { path: { id: n.id } } };
  if (action === "drain") await must(api.POST("/api/nodes/{id}/drain", params));
  if (action === "resume") {
    await must(api.POST("/api/nodes/{id}/resume", params));
  }
  if (action === "revoke") {
    await must(api.POST("/api/nodes/{id}/revoke", params));
  }
  if (action === "remove") await must(api.DELETE("/api/nodes/{id}", params));
}

type RowActions = {
  onConfirm: (action: Confirmed, n: Node) => void;
  onRotate: (n: Node) => void;
};

function columns(
  current: number,
  now: number,
  actions: RowActions,
): Column<Node>[] {
  return [
    {
      id: "name",
      header: "Name",
      priority: "key",
      cell: (n) => (
        <div className="flex min-w-0 flex-col">
          <span className="font-medium break-words">{n.name}</span>
          {n.token_prefix && (
            <code className="text-sm break-all text-muted-foreground">
              {n.token_prefix}
            </code>
          )}
        </div>
      ),
    },
    {
      id: "status",
      header: "Status",
      priority: "status",
      cell: (n) => <StatusPill {...nodePill(n.status)} />,
    },
    {
      id: "snapshot",
      header: "Snapshot",
      priority: "main",
      cell: (n) => snapshotText(n, current, now),
    },
    {
      id: "heartbeat",
      header: "Heartbeat",
      priority: "main",
      cell: (n) =>
        n.last_heartbeat_at ? (
          <time dateTime={n.last_heartbeat_at}>
            {relativeTime(n.last_heartbeat_at, now)}
          </time>
        ) : (
          relativeTime(null, now)
        ),
    },
    {
      id: "version",
      header: "Version",
      priority: "main",
      cell: (n) => n.version ?? "—",
    },
    {
      id: "streams",
      header: "SDK streams",
      priority: "extra",
      cell: (n) => n.sdk_streams,
    },
    {
      id: "buffered",
      header: "Buffered",
      priority: "extra",
      cell: (n) => n.buffered_events,
    },
    {
      id: "address",
      header: "Address",
      priority: "extra",
      cell: (n) => n.address ?? "—",
    },
    {
      id: "actions",
      header: "Actions",
      priority: "main",
      cell: (n) => {
        const allowed = nodeActions(n);
        const draining = n.status === "draining";
        return (
          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`Actions for ${n.name}`}
                  className="min-h-11 min-w-11"
                />
              }
            >
              <Ellipsis aria-hidden />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-auto min-w-40">
              <DropdownMenuItem
                disabled={draining ? !allowed.resume : !allowed.drain}
                onClick={() =>
                  actions.onConfirm(draining ? "resume" : "drain", n)
                }
              >
                {draining ? <Play aria-hidden /> : <Pause aria-hidden />}
                {draining ? "Resume" : "Drain"}
              </DropdownMenuItem>
              <DropdownMenuItem
                disabled={!allowed.rotate}
                onClick={() => actions.onRotate(n)}
              >
                <RotateCw aria-hidden />
                Rotate token
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                variant="destructive"
                disabled={!allowed.revoke}
                onClick={() => actions.onConfirm("revoke", n)}
              >
                <Ban aria-hidden />
                Revoke
              </DropdownMenuItem>
              <DropdownMenuItem
                variant="destructive"
                disabled={!allowed.remove}
                onClick={() => actions.onConfirm("remove", n)}
              >
                <Trash2 aria-hidden />
                Remove
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        );
      },
    },
  ];
}

function LoadingList() {
  return (
    <div role="status" className="flex flex-col gap-3">
      <span className="sr-only">Loading nodes</span>
      {[0, 1, 2].map((i) => (
        <Skeleton key={i} className="h-16 w-full" />
      ))}
    </div>
  );
}

/** The control plane's workers: their health, snapshot and heartbeat. Needs org.manage. */
export function NodeList() {
  const list = useApiData<Loaded>("nodes", async () => ({
    list: await must(api.GET("/api/nodes")),
    now: Date.now(),
  }));
  useVisibleInterval(list.reload, REFRESH_MS);
  // A refresh keeps showing the last list instead of the skeleton until it lands.
  const [shown, setShown] = useState<Loaded | null>(null);
  if (list.data && list.data !== shown) setShown(list.data);
  if (list.failure && shown) setShown(null);
  const loaded = list.data ?? (list.failure ? null : shown);
  const [adding, setAdding] = useState(false);
  // Like the confirm dialog, the rotate dialog keeps its node while closing.
  const [rotating, setRotating] = useState<{
    open: boolean;
    node: Node | null;
  }>({ open: false, node: null });
  const [confirming, setConfirming] = useState<{
    open: boolean;
    action: Confirmed;
    node: Node | null;
  }>({ open: false, action: "drain", node: null });

  const confirm = async (action: Confirmed, n: Node) => {
    try {
      await run(action, n);
      toast(CONFIRMS[action].done(n.name));
      list.reload();
    } catch (error) {
      showError(error, `${action} of the node failed`);
    }
  };

  const add = (
    <Button onClick={() => setAdding(true)}>
      <Plus aria-hidden />
      Add worker
    </Button>
  );

  let body: React.ReactNode;
  if (
    list.failure?.status === 409 &&
    list.failure.code === "not_control_plane"
  ) {
    body = (
      <Alert>
        <Server aria-hidden />
        <AlertTitle>Not a control plane</AlertTitle>
        <AlertDescription>
          Nodes are available on a control plane. Set
          MEGOOFLAGS_ROLE=control-plane on this instance to add workers.
        </AlertDescription>
      </Alert>
    );
  } else if (list.failure?.status === 403) {
    body = (
      <EmptyState
        title="You need the org.manage permission to see nodes"
        description="Ask an admin for it."
      />
    );
  } else if (list.failure) {
    body = (
      <Alert variant="destructive">
        <AlertTitle>Couldn&apos;t load nodes</AlertTitle>
        <AlertDescription className="flex flex-col items-start gap-2">
          <p>{failureText(list.failure)}</p>
          <Button variant="outline" onClick={list.reload}>
            Try again
          </Button>
        </AlertDescription>
      </Alert>
    );
  } else if (!loaded) {
    body = <LoadingList />;
  } else if (loaded.list.nodes.length === 0) {
    body = (
      <>
        <NodeSummary list={loaded.list} />
        <EmptyState
          icon={Server}
          title="No workers yet"
          description="Add a worker to serve SDK and OFREP traffic closer to your apps."
        >
          {add}
        </EmptyState>
      </>
    );
  } else {
    body = (
      <>
        <NodeSummary list={loaded.list} />
        <DataList
          caption="Nodes"
          columns={columns(loaded.list.snapshot_version, loaded.now, {
            onConfirm: (action, node) =>
              setConfirming({ open: true, action, node }),
            onRotate: (node) => setRotating({ open: true, node }),
          })}
          rows={loaded.list.nodes}
          rowKey={(n) => n.id}
        />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Nodes"
        description="Workers serve SDK and OFREP traffic from a signed copy of your flags."
        actions={loaded ? add : undefined}
      />
      {body}
      <AddNodeDialog
        open={adding}
        onOpenChange={setAdding}
        onAdded={list.reload}
      />
      <RotateNodeDialog
        node={rotating.node}
        open={rotating.open}
        onOpenChange={(open) => setRotating((r) => ({ ...r, open }))}
        onRotated={list.reload}
        onError={(error) => showError(error, "rotating the token failed")}
      />
      <ConfirmDialog
        open={confirming.open}
        onOpenChange={(open) => setConfirming((c) => ({ ...c, open }))}
        title={CONFIRMS[confirming.action].title(
          confirming.node?.name ?? "the worker",
        )}
        description={CONFIRMS[confirming.action].description}
        confirmLabel={CONFIRMS[confirming.action].label}
        destructive={CONFIRMS[confirming.action].destructive}
        onConfirm={() => {
          if (confirming.node) void confirm(confirming.action, confirming.node);
        }}
      />
    </>
  );
}
