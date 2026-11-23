"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { ScrollText } from "lucide-react";
import { useRef, useState } from "react";

import { AuditEntryDetails } from "@/components/audit/audit-entry-details";
import { AuditFilters } from "@/components/audit/audit-filters";
import { ExportDialog } from "@/components/audit/export-dialog";
import { type Column, DataList } from "@/components/patterns/data-list";
import { EmptyState } from "@/components/patterns/empty-state";
import { LoadFailed, LoadingRows } from "@/components/patterns/list-state";
import { Button } from "@/components/ui/button";
import { useApiData } from "@/hooks/use-api-data";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { actionLabel, resourceHref } from "@/lib/audit/actions";
import {
  activeFilterCount,
  type AuditFilterValues,
  auditFiltersFromParams,
  auditFiltersToParams,
  auditQuery,
  type AuditScope,
} from "@/lib/audit/filters";
import { ApiError, type ApiFailure, must } from "@/lib/flags/errors";

type Entry = components["schemas"]["AuditEntryOut"];
type Page = components["schemas"]["Page_AuditEntryOut_"];

const PAGE_SIZE = 50;

const DATE_TIME = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "medium",
});

const NETWORK: ApiFailure = {
  status: 0,
  code: "network",
  message: "Couldn't reach the server",
  path: null,
};

function columns(scope: AuditScope): Column<Entry>[] {
  return [
    {
      id: "time",
      header: "Time",
      priority: "status",
      cell: (e) => (
        <time dateTime={e.occurred_at} className="text-sm">
          {DATE_TIME.format(new Date(e.occurred_at))}
        </time>
      ),
    },
    {
      id: "action",
      header: "Action",
      priority: "key",
      cell: (e) => (
        <div className="flex flex-col">
          <span className="font-medium">{actionLabel(e.action)}</span>
          <span className="font-mono text-sm break-all text-muted-foreground">
            {e.action}
          </span>
        </div>
      ),
    },
    {
      id: "actor",
      header: "Actor",
      priority: "main",
      cell: (e) => <span className="break-all">{e.actor.label}</span>,
    },
    {
      id: "resource",
      header: "Resource",
      priority: "main",
      cell: (e) => {
        const href = resourceHref(e);
        return (
          <span className="break-all">
            <span className="text-muted-foreground">{e.resource_type}</span>{" "}
            {href ? (
              <Link
                href={href}
                className="inline-flex min-h-11 items-center font-medium hover:underline"
              >
                {e.resource_key}
              </Link>
            ) : (
              e.resource_key
            )}
          </span>
        );
      },
    },
    scope.kind === "project"
      ? {
          id: "environment",
          header: "Environment",
          priority: "main",
          cell: (e) => e.environment ?? "—",
        }
      : {
          id: "project",
          header: "Project",
          priority: "main",
          cell: (e) => e.project ?? "—",
        },
    {
      id: "details",
      header: "Details",
      priority: "main",
      cell: (e) => <AuditEntryDetails entry={e} />,
    },
  ];
}

/** The audit log of a project or the organization, filtered by the URL's search params. */
export function AuditLog({ scope }: { scope: AuditScope }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = new URLSearchParams(useSearchParams());
  const filters = auditFiltersFromParams(params);
  const query = auditQuery(filters, scope);
  const qs = new URLSearchParams(query).toString();
  const load = (cursor?: string) =>
    must(
      api.GET("/api/audit", {
        params: { query: { ...query, limit: PAGE_SIZE, cursor } },
      }),
    );
  const list = useApiData(`audit?${qs}`, () => load());
  // Pages after the first, for one set of filters; any change of filters drops them.
  const [more, setMore] = useState<{
    qs: string;
    pages: Page[];
    loading: boolean;
    failure: ApiFailure | null;
  }>({ qs, pages: [], loading: false, failure: null });
  const extra =
    more.qs === qs ? more : { qs, pages: [], loading: false, failure: null };

  // The URLs this page sent and the newest of them. Two text filters can send within one
  // navigation, so a change builds on the newest URL sent while the address bar still shows
  // an older one of them; otherwise it would drop the other filter's change.
  const sent = useRef<{ urls: Set<string>; last: string } | null>(null);
  const update = (patch: Partial<AuditFilterValues>) => {
    const current = params.toString();
    const chain = sent.current?.urls.has(current) ? sent.current : null;
    const base = chain ? new URLSearchParams(chain.last) : params;
    const search = auditFiltersToParams(
      { ...auditFiltersFromParams(base), ...patch },
      base,
    ).toString();
    sent.current = {
      urls: new Set([...(chain?.urls ?? [current]), search]),
      last: search,
    };
    router.replace(pathname + (search ? `?${search}` : ""), { scroll: false });
  };

  const last = extra.pages.at(-1) ?? list.data;
  const next = last?.next_cursor ?? null;

  const loadMore = async () => {
    if (!next) return;
    setMore({ ...extra, loading: true, failure: null });
    try {
      const page = await load(next);
      setMore((m) =>
        m.qs === qs ? { ...m, pages: [...m.pages, page], loading: false } : m,
      );
    } catch (error) {
      if (!(error instanceof ApiError))
        console.error("loading more audit entries failed", error);
      const failure = error instanceof ApiError ? error.failure : NETWORK;
      setMore((m) => (m.qs === qs ? { ...m, loading: false, failure } : m));
    }
  };

  let body: React.ReactNode;
  if (list.failure) {
    body = (
      <LoadFailed
        title="Couldn't load the audit log"
        failure={list.failure}
        onRetry={list.reload}
      />
    );
  } else if (!list.data) {
    body = <LoadingRows label="Loading the audit log" />;
  } else if (list.data.items.length === 0) {
    body =
      activeFilterCount(filters, scope) === 0 ? (
        <EmptyState
          icon={ScrollText}
          title="No audit entries yet"
          description="Every change made here is recorded, with who made it and when."
        />
      ) : (
        <EmptyState
          title="No entries match"
          description="Try other filters or a wider date range."
        />
      );
  } else {
    const rows = [list.data, ...extra.pages].flatMap((p) => p.items);
    body = (
      <>
        <DataList
          caption="Audit log"
          columns={columns(scope)}
          rows={rows}
          rowKey={(e) => String(e.id)}
        />
        {extra.failure && (
          <div className="mt-4">
            <LoadFailed
              title="Couldn't load more entries"
              failure={extra.failure}
              onRetry={() => void loadMore()}
            />
          </div>
        )}
        {next && !extra.failure && (
          <div className="mt-4">
            <Button
              variant="outline"
              disabled={extra.loading}
              onClick={() => void loadMore()}
            >
              {extra.loading ? "Loading…" : "Load more"}
            </Button>
          </div>
        )}
      </>
    );
  }

  return (
    <>
      <div className="flex flex-wrap items-end gap-x-2">
        <div className="min-w-0 flex-1">
          <AuditFilters scope={scope} value={filters} onChange={update} />
        </div>
        <div className="mb-4">
          <ExportDialog
            filters={filters}
            scope={scope}
            unavailable={list.failure !== null}
          />
        </div>
      </div>
      {body}
    </>
  );
}
