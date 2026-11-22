"use client";

import { Plus, Users } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/patterns/empty-state";
import { type Column, DataList } from "@/components/patterns/data-list";
import { LoadFailed, LoadingRows } from "@/components/patterns/list-state";
import { StatusPill } from "@/components/patterns/status-pill";
import { InviteDialog } from "@/components/settings/invite-dialog";
import { MemberDialog } from "@/components/settings/member-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useApiData } from "@/hooks/use-api-data";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, failureText, must } from "@/lib/flags/errors";
import { memberPill, memberState } from "@/lib/settings/members";

type User = components["schemas"]["UserOut"];
type UserPage = components["schemas"]["Page_UserOut_"];
type Loaded = { page: UserPage; roles: Map<string, string[]>; now: number };

const PAGE_SIZE = 50;
const SEARCH_DELAY_MS = 200;

const DATE_TIME = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

function When({ at }: { at: string }) {
  return <time dateTime={at}>{DATE_TIME.format(new Date(at))}</time>;
}

/** Each user's organization roles, by user id. */
async function orgRoles(): Promise<Map<string, string[]>> {
  const page = await must(
    api.GET("/api/bindings", {
      params: { query: { subject_kind: "user", scope: "org", limit: 500 } },
    }),
  );
  const roles = new Map<string, string[]>();
  for (const b of page.items) {
    roles.set(b.subject_id, [...(roles.get(b.subject_id) ?? []), b.role]);
  }
  return roles;
}

function columns(
  now: Date,
  roles: Map<string, string[]>,
  onManage: (u: User) => void,
): Column<User>[] {
  return [
    {
      id: "name",
      header: "Member",
      priority: "key",
      cell: (u) => (
        <div className="flex min-w-0 flex-col">
          <span className="font-medium break-words">{u.name}</span>
          <span className="text-sm break-all text-muted-foreground">
            {u.email}
          </span>
        </div>
      ),
    },
    {
      id: "status",
      header: "Status",
      priority: "status",
      cell: (u) => {
        const state = memberState(u, now);
        return (
          <div className="flex flex-col items-start gap-1">
            <StatusPill {...memberPill(state)} />
            {state.kind === "invited" && u.invitation_expires_at && (
              <span className="text-sm text-muted-foreground">
                link expires <When at={u.invitation_expires_at} />
              </span>
            )}
          </div>
        );
      },
    },
    {
      id: "roles",
      header: "Organization role",
      priority: "main",
      cell: (u) => {
        const held = roles.get(u.id) ?? [];
        if (held.length === 0)
          return <span className="text-muted-foreground">None</span>;
        return <span className="break-words">{held.join(", ")}</span>;
      },
    },
    {
      id: "last-sign-in",
      header: "Last sign-in",
      priority: "extra",
      cell: (u) => (u.last_login_at ? <When at={u.last_login_at} /> : "Never"),
    },
    {
      id: "actions",
      header: "Actions",
      priority: "main",
      cell: (u) => (
        <Button
          variant="outline"
          className="min-h-11"
          aria-label={`Manage ${u.name}`}
          onClick={() => onManage(u)}
        >
          Manage
        </Button>
      ),
    },
  ];
}

/** The search box; it sends its text after a short pause, and follows `value` (back, forward). */
function SearchField({
  value,
  onChange,
}: {
  value: string;
  onChange: (q: string) => void;
}) {
  const id = useId();
  const [text, setText] = useState(value);
  const [seen, setSeen] = useState(value);
  const [sent, setSent] = useState(value);
  // A new `value` replaces the text, unless it only catches up with what this field sent.
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
      onChangeRef.current(text);
    }, SEARCH_DELAY_MS);
    return () => clearTimeout(timer);
  }, [text, sent]);

  return (
    <div className="min-w-48 flex-1 sm:max-w-sm">
      <Label htmlFor={id} className="sr-only">
        Search members
      </Label>
      <Input
        id={id}
        type="search"
        placeholder="Search by name or email"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
    </div>
  );
}

/** The organization's members and invitations, with a search kept in `?q=`. */
export function MembersTab() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const q = params.get("q") ?? "";

  const load = (cursor?: string) =>
    must(
      api.GET("/api/users", {
        params: { query: { q: q || undefined, limit: PAGE_SIZE, cursor } },
      }),
    );
  const first = useApiData<Loaded>(`members?${q}`, async () => {
    const [page, roles] = await Promise.all([load(), orgRoles()]);
    return { page, roles, now: Date.now() };
  });

  // Pages loaded with "Load more" after the first; a reload of the first page drops them.
  const [more, setMore] = useState<{
    first: Loaded | null;
    items: User[];
    cursor: string | null;
  }>({ first: null, items: [], cursor: null });
  const [loadingMore, setLoadingMore] = useState(false);
  const extra = first.data && more.first === first.data ? more : null;
  const cursor = extra ? extra.cursor : (first.data?.page.next_cursor ?? null);
  const [inviting, setInviting] = useState(false);
  // The user stays set while the dialog closes, so its title doesn't change mid-animation.
  const [managing, setManaging] = useState<{
    open: boolean;
    user: User | null;
  }>({ open: false, user: null });

  const search = (next: string) => {
    const query = new URLSearchParams(params);
    if (next.trim() === "") query.delete("q");
    else query.set("q", next);
    router.replace(`${pathname}?${query.toString()}`, { scroll: false });
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
      if (error instanceof ApiError) toast.error(failureText(error.failure));
      else {
        console.error("loading more members failed", error);
        toast.error("Couldn't reach the server");
      }
    } finally {
      setLoadingMore(false);
    }
  };

  const add = (
    <Button onClick={() => setInviting(true)}>
      <Plus aria-hidden />
      Invite member
    </Button>
  );

  let body: React.ReactNode;
  if (first.failure) {
    body = (
      <LoadFailed
        title="Couldn't load members"
        failure={first.failure}
        onRetry={first.reload}
      />
    );
  } else if (!first.data) {
    body = <LoadingRows label="Loading members" />;
  } else {
    const now = new Date(first.data.now);
    const rows = [...first.data.page.items, ...(extra?.items ?? [])];
    body =
      rows.length === 0 ? (
        <EmptyState
          icon={Users}
          title={q ? "No members match" : "No members"}
          description={
            q
              ? "No name or email contains this text."
              : "Invite people to give them access to the organization."
          }
        />
      ) : (
        <>
          <DataList
            caption="Members"
            columns={columns(now, first.data.roles, (user) =>
              setManaging({ open: true, user }),
            )}
            rows={rows}
            rowKey={(u) => u.id}
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
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <SearchField value={q} onChange={search} />
        {add}
      </div>
      {body}
      <InviteDialog
        open={inviting}
        onOpenChange={setInviting}
        onInvited={first.reload}
      />
      <MemberDialog
        user={managing.user}
        open={managing.open}
        onOpenChange={(open) => setManaging((m) => ({ ...m, open }))}
        onChanged={first.reload}
      />
    </div>
  );
}
