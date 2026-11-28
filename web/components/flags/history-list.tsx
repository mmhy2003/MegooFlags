"use client";

import { useId, useState } from "react";
import { toast } from "sonner";

import { DiffViewer } from "@/components/patterns/diff-viewer";
import { EmptyState } from "@/components/patterns/empty-state";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiData } from "@/hooks/use-api-data";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, failureText, must } from "@/lib/flags/errors";
import { actionLabel, entryText } from "@/lib/flags/history";

type AuditEntry = components["schemas"]["AuditEntryOut"];

const PAGE_SIZE = 20;

const TIME = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

function Entry({ entry }: { entry: AuditEntry }) {
  return (
    <li className="flex min-w-0 flex-col gap-2 rounded-lg border p-3">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-semibold">{actionLabel(entry)}</span>
        {entry.environment && (
          <Badge variant="outline">{entry.environment}</Badge>
        )}
      </div>
      <div className="flex flex-wrap gap-x-2 text-muted-foreground">
        <span>{entry.actor.label}</span>
        <time dateTime={entry.occurred_at}>
          {TIME.format(new Date(entry.occurred_at))}
        </time>
      </div>
      {(entry.before !== null || entry.after !== null) && (
        <details className="min-w-0">
          <summary className="flex min-h-11 cursor-pointer items-center font-medium">
            Show diff
          </summary>
          <div className="pt-2">
            <DiffViewer
              before={entryText(entry.before)}
              after={entryText(entry.after)}
            />
          </div>
        </details>
      )}
    </li>
  );
}

/**
 * A flag's change history from the audit log, newest first, optionally only for the selected
 * environment. Each change with a before or after value can show its diff. It loads when it
 * mounts, so it is fresh each time the History tab opens.
 */
export function HistoryList({
  project,
  flag,
  env,
  canRead,
}: {
  project: string;
  flag: string;
  env: string;
  /** Whether the caller holds audit.read; null while permissions load. */
  canRead: boolean | null;
}) {
  const id = useId();
  const [envOnly, setEnvOnly] = useState(false);
  const scope = envOnly ? env : null;
  const query = {
    project,
    resource_type: "flag",
    resource_key: flag,
    limit: PAGE_SIZE,
    ...(scope ? { env: scope } : {}),
  };
  const key = canRead ? `history:${project}/${flag}/${scope ?? "*"}` : null;
  const first = useApiData(key, () =>
    must(api.GET("/api/audit", { params: { query } })),
  );

  // Pages loaded with "Load more" after the first, for the current key.
  const [more, setMore] = useState<{
    key: string | null;
    items: AuditEntry[];
    cursor: string | null;
  }>({ key: null, items: [], cursor: null });
  const [loadingMore, setLoadingMore] = useState(false);
  const extra = more.key === key ? more : null;

  if (canRead === null) return <Skeleton className="h-40 w-full" aria-hidden />;
  if (!canRead)
    return (
      <EmptyState
        title="You need the audit.read permission to see history"
        description="Ask an admin for it."
      />
    );

  const items = [...(first.data?.items ?? []), ...(extra?.items ?? [])];
  const cursor = extra ? extra.cursor : (first.data?.next_cursor ?? null);

  const loadMore = async () => {
    if (!cursor) return;
    setLoadingMore(true);
    try {
      const page = await must(
        api.GET("/api/audit", { params: { query: { ...query, cursor } } }),
      );
      setMore({
        key,
        items: [...(extra?.items ?? []), ...page.items],
        cursor: page.next_cursor,
      });
    } catch (error) {
      if (!(error instanceof ApiError)) console.error("load failed", error);
      toast.error(
        error instanceof ApiError
          ? failureText(error.failure)
          : "Couldn't reach the server",
      );
    } finally {
      setLoadingMore(false);
    }
  };

  let body: React.ReactNode;
  if (first.failure)
    body = (
      <Alert variant="destructive">
        <AlertTitle>Couldn&apos;t load history</AlertTitle>
        <AlertDescription className="flex flex-col items-start gap-2">
          <p>{failureText(first.failure)}</p>
          <Button variant="outline" onClick={first.reload}>
            Try again
          </Button>
        </AlertDescription>
      </Alert>
    );
  else if (first.loading)
    body = (
      <div role="status">
        <span className="sr-only">Loading history</span>
        <Skeleton className="h-40 w-full" />
      </div>
    );
  else if (items.length === 0) body = <EmptyState title="No history yet" />;
  else
    body = (
      <>
        <ol
          aria-labelledby={`${id}-heading`}
          className="flex min-w-0 flex-col gap-3"
        >
          {items.map((entry) => (
            <Entry key={entry.id} entry={entry} />
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
    <section
      aria-labelledby={`${id}-heading`}
      className="flex min-w-0 flex-col gap-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id={`${id}-heading`} className="text-lg font-semibold">
          Change history
        </h2>
        <label
          data-hit-area
          className="inline-flex min-h-11 min-w-11 items-center gap-2"
        >
          <Checkbox
            checked={envOnly}
            onChange={(e) => setEnvOnly(e.target.checked)}
          />
          This environment only
        </label>
      </div>
      {body}
    </section>
  );
}
