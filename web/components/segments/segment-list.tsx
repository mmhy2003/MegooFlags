"use client";

import { Plus, Users } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { type Column, DataList } from "@/components/patterns/data-list";
import { EmptyState } from "@/components/patterns/empty-state";
import { LoadFailed, LoadingRows } from "@/components/patterns/list-state";
import { PageHeader } from "@/components/patterns/page-header";
import { SearchField } from "@/components/patterns/search-field";
import { NewSegmentDialog } from "@/components/segments/new-segment-dialog";
import { useProjectPermissions } from "@/components/shell/use-project-permissions";
import { Button } from "@/components/ui/button";
import { useApiData } from "@/hooks/use-api-data";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, failureText, must } from "@/lib/flags/errors";
import { can } from "@/lib/flags/permissions";
import { projectTrail, segmentPath } from "@/lib/routes";
import { usedByText } from "@/lib/segments/segment-draft";

type Segment = components["schemas"]["SegmentListItem"];
type SegmentPage = components["schemas"]["Page_SegmentListItem_"];

const PAGE_SIZE = 50;

const DATE = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });

function columns(project: string): Column<Segment>[] {
  return [
    {
      id: "name",
      header: "Segment",
      priority: "key",
      cell: (s) => (
        <div className="flex min-w-0 flex-col">
          <Link
            href={segmentPath(project, s.key)}
            className="inline-flex min-h-11 items-center font-medium break-words hover:underline"
          >
            {s.name}
          </Link>
          <span className="font-mono text-sm break-all text-muted-foreground">
            {s.key}
          </span>
        </div>
      ),
    },
    {
      id: "description",
      header: "Description",
      priority: "main",
      cell: (s) =>
        s.description === "" ? (
          <span className="text-muted-foreground">—</span>
        ) : (
          <span className="break-words">{s.description}</span>
        ),
    },
    {
      id: "used-by",
      header: "Used by",
      priority: "main",
      cell: (s) => usedByText(s.flag_count),
    },
    {
      id: "updated",
      header: "Updated",
      priority: "extra",
      cell: (s) => (
        <time dateTime={s.updated_at}>
          {DATE.format(new Date(s.updated_at))}
        </time>
      ),
    },
  ];
}

/** The project's segments, with a search kept in `?q=`, how many flags use each, and New segment. */
export function SegmentList({ project }: { project: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const q = params.get("q") ?? "";
  const perms = useProjectPermissions(project, null, null);
  const canWrite = can(perms, "segment.write");

  const load = (cursor?: string) =>
    must(
      api.GET("/api/projects/{project}/segments", {
        params: {
          path: { project },
          query: { q: q || undefined, limit: PAGE_SIZE, cursor },
        },
      }),
    );
  const first = useApiData<SegmentPage>(`segments:${project}?${q}`, () =>
    load(),
  );

  // Pages loaded with "Load more" after the first; a reload of the first page drops them.
  const [more, setMore] = useState<{
    first: SegmentPage | null;
    items: Segment[];
    cursor: string | null;
  }>({ first: null, items: [], cursor: null });
  const [loadingMore, setLoadingMore] = useState(false);
  const extra = first.data && more.first === first.data ? more : null;
  const cursor = extra ? extra.cursor : (first.data?.next_cursor ?? null);
  const [creating, setCreating] = useState(false);

  const showError = (error: unknown, what: string) => {
    if (!(error instanceof ApiError)) {
      console.error(what, error);
      toast.error("Couldn't reach the server");
      return;
    }
    toast.error(failureText(error.failure));
  };

  const search = (next: string) => {
    const query = new URLSearchParams(params);
    if (next.trim() === "") query.delete("q");
    else query.set("q", next);
    const qs = query.toString();
    router.replace(pathname + (qs ? `?${qs}` : ""), { scroll: false });
  };

  const loadMore = async () => {
    if (!cursor) return;
    setLoadingMore(true);
    try {
      const page = await load(cursor);
      setMore({
        first: first.data,
        items: [...(extra?.items ?? []), ...page.items],
        cursor: page.next_cursor,
      });
    } catch (error) {
      showError(error, "loading more segments failed");
    } finally {
      setLoadingMore(false);
    }
  };

  const add = canWrite && (
    <Button onClick={() => setCreating(true)}>
      <Plus aria-hidden />
      New segment
    </Button>
  );

  let body: React.ReactNode;
  if (first.failure) {
    body = (
      <LoadFailed
        title="Couldn't load segments"
        failure={first.failure}
        onRetry={first.reload}
      />
    );
  } else if (!first.data) {
    body = <LoadingRows label="Loading segments" />;
  } else {
    const rows = [...first.data.items, ...(extra?.items ?? [])];
    if (rows.length === 0)
      body =
        q === "" ? (
          <EmptyState
            icon={Users}
            title="No segments yet"
            description="A segment is a named group of users that flag rules can target by key or by attributes."
          >
            {add}
          </EmptyState>
        ) : (
          <EmptyState
            title="No segments match"
            description="Try another search, or clear it to see every segment."
          >
            <Button variant="outline" onClick={() => search("")}>
              Clear search
            </Button>
          </EmptyState>
        );
    else
      body = (
        <>
          <DataList
            caption="Segments"
            columns={columns(project)}
            rows={rows}
            rowKey={(s) => s.key}
          />
          {cursor && (
            <div>
              <Button
                variant="outline"
                disabled={loadingMore}
                onClick={() => void loadMore()}
              >
                {loadingMore ? "Loading…" : "Load more"}
              </Button>
            </div>
          )}
        </>
      );
  }

  return (
    <>
      <PageHeader
        title="Segments"
        trail={projectTrail(project)}
        actions={add || undefined}
      />
      <div className="flex flex-col gap-4">
        <SearchField
          label="Search segments"
          placeholder="Search by name or key"
          value={q}
          onChange={search}
        />
        {body}
      </div>
      {canWrite && (
        <NewSegmentDialog
          project={project}
          open={creating}
          onOpenChange={setCreating}
        />
      )}
    </>
  );
}
