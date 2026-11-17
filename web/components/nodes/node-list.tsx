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

import { NodeSummary } from "@/components/nodes/node-summary";
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
import { failureText, must } from "@/lib/flags/errors";
import { relativeTime, snapshotText } from "@/lib/nodes/format";
import { nodePill } from "@/lib/nodes/status";

type Node = components["schemas"]["NodeOut"];
type NodeList = components["schemas"]["NodeList"];

/** A loaded list, with the time it was loaded so relative times stay pure. */
type Loaded = { list: NodeList; now: number };

const REFRESH_MS = 10_000;

function columns(current: number, now: number): Column<Node>[] {
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
      // The items stay disabled until the dialogs that run them are wired in.
      cell: (n) => (
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
            <DropdownMenuItem disabled>
              {n.status === "draining" ? (
                <Play aria-hidden />
              ) : (
                <Pause aria-hidden />
              )}
              {n.status === "draining" ? "Resume" : "Drain"}
            </DropdownMenuItem>
            <DropdownMenuItem disabled>
              <RotateCw aria-hidden />
              Rotate token
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" disabled>
              <Ban aria-hidden />
              Revoke
            </DropdownMenuItem>
            <DropdownMenuItem variant="destructive" disabled>
              <Trash2 aria-hidden />
              Remove
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      ),
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

  // Wired to its dialog in the next step.
  const add = (
    <Button disabled>
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
          columns={columns(loaded.list.snapshot_version, loaded.now)}
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
    </>
  );
}
