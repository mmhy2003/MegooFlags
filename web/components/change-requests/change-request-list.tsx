"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { GitPullRequest } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { type Column, DataList } from "@/components/patterns/data-list";
import { EmptyState } from "@/components/patterns/empty-state";
import { LoadFailed, LoadingRows } from "@/components/patterns/list-state";
import { PageHeader } from "@/components/patterns/page-header";
import { StatusPill } from "@/components/patterns/status-pill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { useApiData } from "@/hooks/use-api-data";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import {
  type CrFilters,
  crFiltersFromParams,
  crFiltersToParams,
  crListQuery,
} from "@/lib/change-requests/filters";
import {
  approvalsText,
  crPill,
  KIND_LABELS,
} from "@/lib/change-requests/status";
import { ApiError, type ApiFailure, must } from "@/lib/flags/errors";
import { changeRequestPath, projectTrail } from "@/lib/routes";

type Summary = components["schemas"]["ChangeRequestSummary"];
type Page = components["schemas"]["Page_ChangeRequestSummary_"];

const DATE_TIME = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

const FLAG_DELAY_MS = 300;

const NETWORK: ApiFailure = {
  status: 0,
  code: "network",
  message: "Couldn't reach the server",
  path: null,
};

const STATUS_OPTIONS: { value: CrFilters["status"]; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "applied", label: "Applied" },
  { value: "rejected", label: "Rejected" },
  { value: "cancelled", label: "Cancelled" },
  { value: "failed", label: "Failed" },
  { value: "all", label: "All" },
];

function columns(project: string): Column<Summary>[] {
  return [
    {
      id: "title",
      header: "Change request",
      priority: "key",
      cell: (cr) => (
        <Link
          href={changeRequestPath(project, cr.id)}
          className="inline-flex min-h-11 items-center font-medium break-words hover:underline"
        >
          {cr.title}
        </Link>
      ),
    },
    {
      id: "status",
      header: "Status",
      priority: "status",
      cell: (cr) => <StatusPill {...crPill(cr.status)} />,
    },
    {
      id: "change",
      header: "Change",
      priority: "main",
      cell: (cr) => (
        <span className="break-all">
          {cr.flag} · {cr.environment} · {KIND_LABELS[cr.kind]}
        </span>
      ),
    },
    {
      id: "approvals",
      header: "Approvals",
      priority: "main",
      cell: (cr) => approvalsText(cr),
    },
    {
      id: "author",
      header: "Author",
      priority: "extra",
      cell: (cr) => cr.author,
    },
    {
      id: "created",
      header: "Created",
      priority: "extra",
      cell: (cr) => (
        <time dateTime={cr.created_at}>
          {DATE_TIME.format(new Date(cr.created_at))}
        </time>
      ),
    },
  ];
}

/**
 * The flag key filter. It sends its text 300 ms after typing stops, and follows the URL when that
 * changes from outside (back and forward), but not when the URL only catches up with it.
 */
function FlagField({
  value,
  onChange,
}: {
  value: string;
  onChange: (flag: string) => void;
}) {
  const id = useId();
  const [text, setText] = useState(value);
  const [seen, setSeen] = useState(value);
  const [sent, setSent] = useState(value);
  if (value !== seen) {
    setSeen(value);
    if (value !== sent) {
      setText(value);
      setSent(value);
    }
  }
  // Kept in a ref so a parent render doesn't restart the debounce.
  const onChangeRef = useRef(onChange);
  useEffect(() => {
    onChangeRef.current = onChange;
  });
  useEffect(() => {
    if (text === sent) return;
    const timer = setTimeout(() => {
      setSent(text);
      onChangeRef.current(text.trim());
    }, FLAG_DELAY_MS);
    return () => clearTimeout(timer);
  }, [text, sent]);

  return (
    <div className="flex min-w-0 flex-col gap-2">
      <Label htmlFor={id}>Flag key</Label>
      <Input
        id={id}
        type="search"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
    </div>
  );
}

function Filters({
  project,
  filters,
  onChange,
}: {
  project: string;
  filters: CrFilters;
  onChange: (patch: Partial<CrFilters>) => void;
}) {
  const statusId = useId();
  const envId = useId();
  const environments = useApiData(`environments:${project}`, () =>
    must(
      api.GET("/api/projects/{project}/environments", {
        params: { path: { project } },
      }),
    ),
  );

  return (
    <div className="mb-4 grid gap-4 sm:grid-cols-3">
      <div className="flex min-w-0 flex-col gap-2">
        <Label htmlFor={statusId}>Status</Label>
        <NativeSelect
          id={statusId}
          value={filters.status}
          onChange={(e) =>
            onChange({ status: e.target.value as CrFilters["status"] })
          }
        >
          {STATUS_OPTIONS.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </NativeSelect>
      </div>
      <div className="flex min-w-0 flex-col gap-2">
        <Label htmlFor={envId}>Environment</Label>
        <NativeSelect
          id={envId}
          value={filters.env}
          onChange={(e) => onChange({ env: e.target.value })}
        >
          <option value="">Any</option>
          {environments.data?.map((e) => (
            <option key={e.key} value={e.key}>
              {e.name}
            </option>
          ))}
          {/* Keeps a URL's environment selectable while the list loads. */}
          {filters.env &&
            !environments.data?.some((e) => e.key === filters.env) && (
              <option value={filters.env}>{filters.env}</option>
            )}
        </NativeSelect>
      </div>
      <FlagField value={filters.flag} onChange={(flag) => onChange({ flag })} />
    </div>
  );
}

/** The project's change requests, filtered by status, environment and flag in the URL. */
export function ChangeRequestList({ project }: { project: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const filters = crFiltersFromParams(new URLSearchParams(useSearchParams()));
  const qs = crFiltersToParams(filters).toString();
  const list = useApiData(`${project}?${qs}`, () =>
    must(
      api.GET("/api/projects/{project}/change-requests", {
        params: { path: { project }, query: crListQuery(filters) },
      }),
    ),
  );
  // Pages after the first, for one set of filters; any change of filters drops them.
  const [more, setMore] = useState<{
    qs: string;
    pages: Page[];
    loading: boolean;
    failure: ApiFailure | null;
  }>({ qs, pages: [], loading: false, failure: null });
  const extra =
    more.qs === qs ? more : { qs, pages: [], loading: false, failure: null };

  const update = (patch: Partial<CrFilters>) => {
    const query = crFiltersToParams({ ...filters, ...patch }).toString();
    router.replace(pathname + (query ? `?${query}` : ""), { scroll: false });
  };

  const last = extra.pages.at(-1) ?? list.data;
  const next = last?.next_cursor ?? null;

  const loadMore = async () => {
    if (!next) return;
    setMore({ ...extra, loading: true, failure: null });
    try {
      const page = await must(
        api.GET("/api/projects/{project}/change-requests", {
          params: { path: { project }, query: crListQuery(filters, next) },
        }),
      );
      setMore((m) =>
        m.qs === qs ? { ...m, pages: [...m.pages, page], loading: false } : m,
      );
    } catch (error) {
      if (!(error instanceof ApiError))
        console.error("loading more change requests failed", error);
      const failure = error instanceof ApiError ? error.failure : NETWORK;
      setMore((m) => (m.qs === qs ? { ...m, loading: false, failure } : m));
    }
  };

  let body: React.ReactNode;
  if (list.failure) {
    body = (
      <LoadFailed
        title="Couldn't load change requests"
        failure={list.failure}
        onRetry={list.reload}
      />
    );
  } else if (!list.data) {
    body = <LoadingRows label="Loading change requests" />;
  } else if (list.data.items.length === 0) {
    body =
      qs === "" ? (
        <EmptyState
          icon={GitPullRequest}
          title="No open change requests"
          description="Changes to environments that need approval wait here for review."
        />
      ) : (
        <EmptyState
          title="No change requests match"
          description="Try another status, environment or flag."
        />
      );
  } else {
    const rows = [list.data, ...extra.pages].flatMap((p) => p.items);
    body = (
      <>
        <DataList
          caption="Change requests"
          columns={columns(project)}
          rows={rows}
          rowKey={(cr) => cr.id}
        />
        {extra.failure && (
          <div className="mt-4">
            <LoadFailed
              title="Couldn't load more change requests"
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
      <PageHeader title="Change requests" trail={projectTrail(project)} />
      <Filters project={project} filters={filters} onChange={update} />
      {body}
    </>
  );
}
