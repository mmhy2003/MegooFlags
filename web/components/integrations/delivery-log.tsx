"use client";

import { useState } from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/patterns/empty-state";
import { JsonViewer } from "@/components/patterns/json-viewer";
import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiData } from "@/hooks/use-api-data";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, failureText, must } from "@/lib/flags/errors";
import {
  canSendAgain,
  deliveryLabel,
  deliveryOutcome,
} from "@/lib/integrations/status";

type Integration = components["schemas"]["IntegrationOut"];
type Delivery = components["schemas"]["DeliveryOut"];
type DeliveryPage = components["schemas"]["Page_DeliveryOut_"];

const TIME = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "medium",
});

function showError(error: unknown, what: string) {
  if (!(error instanceof ApiError)) console.error(what, error);
  toast.error(
    error instanceof ApiError
      ? failureText(error.failure)
      : "Couldn't reach the server",
  );
}

function DeliveryRow({
  delivery,
  onSendAgain,
}: {
  delivery: Delivery;
  onSendAgain: (d: Delivery) => Promise<void>;
}) {
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState(false);
  const outcome = deliveryOutcome(delivery);
  const detailsId = `delivery-${delivery.id}`;
  return (
    <li className="flex min-w-0 flex-col gap-2 rounded-lg border p-3">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-mono text-sm font-semibold break-all">
          {delivery.event_type}
        </span>
        <span>{deliveryLabel(delivery)}</span>
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-sm text-muted-foreground">
        <time dateTime={delivery.created_at}>
          {TIME.format(new Date(delivery.created_at))}
        </time>
        {outcome && <span className="break-words">{outcome}</span>}
        {delivery.duration_ms !== null && (
          <span>{delivery.duration_ms} ms</span>
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          className="min-h-11"
          aria-expanded={expanded}
          aria-controls={detailsId}
          onClick={() => setExpanded((e) => !e)}
        >
          Details
        </Button>
        {canSendAgain(delivery) && (
          <Button
            variant="outline"
            className="min-h-11"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await onSendAgain(delivery);
              } finally {
                setBusy(false);
              }
            }}
          >
            Send again
          </Button>
        )}
      </div>
      {expanded && (
        <div id={detailsId} className="flex min-w-0 flex-col gap-2">
          <JsonViewer value={delivery.request_body} label="Request body" />
          <span className="text-sm font-medium">Response body</span>
          {delivery.response_body ? (
            <pre
              tabIndex={0}
              className="max-w-full overflow-x-auto rounded-lg border bg-muted p-3 font-mono text-sm whitespace-pre-wrap break-all"
            >
              {delivery.response_body}
            </pre>
          ) : (
            <p className="text-sm text-muted-foreground">No response body</p>
          )}
        </div>
      )}
    </li>
  );
}

/** An integration's recent deliveries, newest first, with their bodies and a way to resend. */
export function DeliveryLog({
  project,
  integration,
  open,
  onOpenChange,
}: {
  project: string;
  integration: Integration | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const key = open && integration ? `deliveries:${integration.id}` : null;
  const path = { project, id: integration?.id ?? "" };
  const first = useApiData(key, () =>
    must(
      api.GET("/api/projects/{project}/integrations/{id}/deliveries", {
        params: { path },
      }),
    ),
  );

  // Pages loaded with "Load more" after the first; a reload of the first page drops them.
  const [more, setMore] = useState<{
    first: DeliveryPage | null;
    items: Delivery[];
    cursor: string | null;
  }>({ first: null, items: [], cursor: null });
  const [loadingMore, setLoadingMore] = useState(false);
  const extra = first.data && more.first === first.data ? more : null;

  const items = [...(first.data?.items ?? []), ...(extra?.items ?? [])];
  const cursor = extra ? extra.cursor : (first.data?.next_cursor ?? null);

  const loadMore = async () => {
    if (!cursor) return;
    setLoadingMore(true);
    try {
      const page = await must(
        api.GET("/api/projects/{project}/integrations/{id}/deliveries", {
          params: { path, query: { cursor } },
        }),
      );
      setMore({
        first: first.data,
        items: [...(extra?.items ?? []), ...page.items],
        cursor: page.next_cursor,
      });
    } catch (error) {
      showError(error, "loading deliveries failed");
    } finally {
      setLoadingMore(false);
    }
  };

  const sendAgain = async (d: Delivery) => {
    try {
      await must(
        api.POST(
          "/api/projects/{project}/integrations/{id}/deliveries/{delivery}/redeliver",
          { params: { path: { ...path, delivery: d.id } } },
        ),
      );
      toast("Queued again");
      first.reload();
    } catch (error) {
      showError(error, "sending the delivery again failed");
    }
  };

  let body: React.ReactNode;
  if (first.failure)
    body = (
      <Alert variant="destructive">
        <AlertTitle>Couldn&apos;t load deliveries</AlertTitle>
        <AlertDescription className="flex flex-col items-start gap-2">
          <p>{failureText(first.failure)}</p>
          <Button variant="outline" onClick={first.reload}>
            Try again
          </Button>
        </AlertDescription>
      </Alert>
    );
  else if (!first.data)
    body = (
      <div role="status">
        <span className="sr-only">Loading deliveries</span>
        <Skeleton className="h-40 w-full" />
      </div>
    );
  else if (items.length === 0)
    body = <EmptyState title="No deliveries in the last 7 days." />;
  else
    body = (
      <>
        <ol aria-label="Deliveries" className="flex min-w-0 flex-col gap-3">
          {items.map((d) => (
            <DeliveryRow key={d.id} delivery={d} onSendAgain={sendAgain} />
          ))}
        </ol>
        {cursor && (
          <div>
            <Button variant="outline" onClick={loadMore} disabled={loadingMore}>
              Load more
            </Button>
          </div>
        )}
      </>
    );

  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Deliveries: ${integration?.name ?? ""}`}
    >
      {body}
    </ResponsiveDialog>
  );
}
