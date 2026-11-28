"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Flag, Plus } from "lucide-react";
import { useState } from "react";

import { BulkBar } from "@/components/flags/bulk-bar";
import { BulkResults } from "@/components/flags/bulk-results";
import { FlagFilters } from "@/components/flags/flag-filters";
import { type Column, DataList } from "@/components/patterns/data-list";
import { EmptyState } from "@/components/patterns/empty-state";
import { PageHeader } from "@/components/patterns/page-header";
import { StatusPill } from "@/components/patterns/status-pill";
import { useProjectPermissions } from "@/components/shell/use-project-permissions";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiData } from "@/hooks/use-api-data";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import type { BulkResult } from "@/lib/flags/bulk";
import { failureText, must } from "@/lib/flags/errors";
import {
  EMPTY_FILTERS,
  type FlagFilters as Filters,
  filtersFromParams,
  filtersToParams,
  filtersToQuery,
  withFilter,
} from "@/lib/flags/filters";
import { can } from "@/lib/flags/permissions";
import type { MePermissions } from "@/lib/nav";
import { flagPath, newFlagPath, projectTrail } from "@/lib/routes";

type FlagSummary = components["schemas"]["FlagSummary"];

const DATE = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });

function columns(project: string): Column<FlagSummary>[] {
  return [
    {
      id: "key",
      header: "Flag",
      priority: "key",
      cell: (f) => (
        <div className="flex min-w-0 flex-col">
          <Link
            href={flagPath(project, f.key)}
            className="inline-flex min-h-11 items-center font-medium break-all hover:underline"
          >
            {f.key}
          </Link>
          <span className="text-sm break-words text-muted-foreground">
            {f.name}
          </span>
        </div>
      ),
    },
    {
      id: "status",
      header: "Status",
      priority: "status",
      cell: (f) => (
        <div className="flex flex-wrap gap-1">
          {f.environments.map((e) => (
            <StatusPill
              key={e.environment}
              status={e.enabled ? "on" : "off"}
              label={`${e.environment}: ${e.enabled ? "On" : "Off"}`}
            />
          ))}
        </div>
      ),
    },
    { id: "type", header: "Type", priority: "main", cell: (f) => f.type },
    {
      id: "tags",
      header: "Tags",
      priority: "main",
      cell: (f) => (
        <div className="flex flex-wrap gap-1">
          {f.tags.map((tag) => (
            <Badge key={tag} variant="outline">
              {tag}
            </Badge>
          ))}
        </div>
      ),
    },
    {
      id: "owner",
      header: "Owner",
      priority: "extra",
      cell: (f) => f.owner ?? "—",
    },
    {
      id: "lifecycle",
      header: "Lifecycle",
      priority: "extra",
      cell: (f) => f.lifecycle,
    },
    {
      id: "updated",
      header: "Updated",
      priority: "extra",
      cell: (f) => (
        <time dateTime={f.updated_at}>
          {DATE.format(new Date(f.updated_at))}
        </time>
      ),
    },
  ];
}

/** Whether the caller may run any bulk action, which is when rows can be selected. */
function canBulk(perms: MePermissions | null): boolean {
  if (can(perms, "flag.update") || can(perms, "flag.archive")) return true;
  return Object.keys(perms?.environments ?? {}).some((env) =>
    can(perms, "flag.toggle", env),
  );
}

function selectColumn(
  selected: Set<string>,
  onSelect: (key: string, on: boolean) => void,
): Column<FlagSummary> {
  return {
    id: "select",
    header: "Select",
    priority: "key",
    cell: (f) => (
      <label
        data-hit-area
        className="inline-flex min-h-11 min-w-11 items-center gap-2"
      >
        <Checkbox
          checked={selected.has(f.key)}
          onChange={(e) => onSelect(f.key, e.target.checked)}
        />
        <span className="sr-only">Select {f.key}</span>
      </label>
    ),
  };
}

function LoadingList() {
  return (
    <div role="status" className="flex flex-col gap-3">
      <span className="sr-only">Loading flags</span>
      {[0, 1, 2, 3].map((i) => (
        <Skeleton key={i} className="h-16 w-full" />
      ))}
    </div>
  );
}

/** The project's flags: search, filters and pages, all kept in the URL. */
export function FlagList({ project }: { project: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const filters = filtersFromParams(new URLSearchParams(useSearchParams()));
  const qs = filtersToParams(filters).toString();
  const perms = useProjectPermissions(project, null, null);
  const list = useApiData(`${project}?${qs}`, () =>
    must(
      api.GET("/api/projects/{project}/flags", {
        params: { path: { project }, query: filtersToQuery(filters) },
      }),
    ),
  );

  const go = (next: Filters) => {
    const query = filtersToParams(next).toString();
    router.replace(pathname + (query ? `?${query}` : ""), { scroll: false });
  };
  const update = (patch: Partial<Filters>) => go(withFilter(filters, patch));
  const clear = () => go(EMPTY_FILTERS);

  // The selection belongs to one set of filters and one page; any change empties it.
  const [selection, setSelection] = useState<{
    qs: string;
    keys: Set<string>;
  }>({ qs, keys: new Set() });
  const selected = selection.qs === qs ? selection.keys : new Set<string>();
  const setSelected = (keys: Set<string>) => setSelection({ qs, keys });
  const select = (key: string, on: boolean) => {
    const keys = new Set(selected);
    if (on) keys.add(key);
    else keys.delete(key);
    setSelected(keys);
  };
  const [results, setResults] = useState<BulkResult[] | null>(null);
  const bulk = canBulk(perms);

  const canCreate = can(perms, "flag.create");
  const newFlag = canCreate && (
    <Link href={newFlagPath(project)} className={buttonVariants()}>
      <Plus aria-hidden />
      New flag
    </Link>
  );
  const unfiltered = qs === "";

  let body: React.ReactNode;
  if (list.failure) {
    body = (
      <Alert variant="destructive">
        <AlertTitle>Couldn&apos;t load flags</AlertTitle>
        <AlertDescription className="flex flex-col items-start gap-2">
          <p>{failureText(list.failure)}</p>
          <Button variant="outline" onClick={list.reload}>
            Try again
          </Button>
        </AlertDescription>
      </Alert>
    );
  } else if (!list.data) {
    body = <LoadingList />;
  } else if (list.data.items.length === 0) {
    body = unfiltered ? (
      <EmptyState
        icon={Flag}
        title="No flags yet"
        description="Create a flag to start releasing features safely."
      >
        {newFlag}
      </EmptyState>
    ) : (
      <EmptyState
        title="No flags match these filters"
        description="Try other filters, or clear them to see every flag."
      >
        <Button variant="outline" onClick={clear}>
          Clear filters
        </Button>
      </EmptyState>
    );
  } else {
    const next = list.data.next_cursor;
    const rows = list.data.items;
    const all = rows.every((f) => selected.has(f.key));
    body = (
      <>
        {bulk && (
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <label
              data-hit-area
              className="inline-flex min-h-11 min-w-11 items-center gap-2 text-sm"
            >
              <Checkbox
                checked={all}
                ref={(el) => {
                  if (el) el.indeterminate = !all && selected.size > 0;
                }}
                onChange={(e) =>
                  setSelected(
                    e.target.checked
                      ? new Set(rows.map((f) => f.key))
                      : new Set(),
                  )
                }
              />
              Select all on this page
            </label>
          </div>
        )}
        <DataList
          caption="Flags"
          columns={
            bulk
              ? [selectColumn(selected, select), ...columns(project)]
              : columns(project)
          }
          rows={rows}
          rowKey={(f) => f.key}
        />
        {(next || filters.cursor) && (
          <nav aria-label="Pages" className="mt-4 flex flex-wrap gap-2">
            {filters.cursor && (
              <Button variant="outline" onClick={() => update({})}>
                First page
              </Button>
            )}
            {next && (
              <Button
                variant="outline"
                onClick={() => update({ cursor: next })}
              >
                Next page
              </Button>
            )}
          </nav>
        )}
        {bulk && selected.size > 0 && (
          <BulkBar
            project={project}
            rows={rows.filter((f) => selected.has(f.key))}
            perms={perms}
            archived={filters.lifecycle === "archived"}
            onClear={() => setSelected(new Set())}
            onFinished={(done) => {
              setResults(done);
              setSelected(new Set());
              list.reload();
            }}
          />
        )}
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Flags"
        trail={projectTrail(project)}
        actions={newFlag || undefined}
      />
      <FlagFilters
        project={project}
        filters={filters}
        onChange={update}
        onClear={clear}
      />
      {body}
      <BulkResults results={results} onClose={() => setResults(null)} />
    </>
  );
}
