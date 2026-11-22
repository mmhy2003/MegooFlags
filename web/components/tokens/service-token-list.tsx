"use client";

import { KeyRound, Plus, RefreshCw, Shield, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { RotateDialog } from "@/components/keys/rotate-dialog";
import { ConfirmDialog } from "@/components/patterns/confirm-dialog";
import { type Column, DataList } from "@/components/patterns/data-list";
import { EmptyState } from "@/components/patterns/empty-state";
import { LoadFailed, LoadingRows } from "@/components/patterns/list-state";
import { SecretReveal } from "@/components/patterns/secret-reveal";
import { StatusPill } from "@/components/patterns/status-pill";
import { NewServiceTokenDialog } from "@/components/tokens/new-service-token-dialog";
import { TokenAccessDialog } from "@/components/tokens/token-access-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { useApiData } from "@/hooks/use-api-data";
import { bindingText } from "@/lib/access/bindings";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, failureText, must } from "@/lib/flags/errors";
import {
  credentialPill,
  credentialState,
  visibleCredentials,
} from "@/lib/keys/status";

type Token = components["schemas"]["TokenOut"];
type TokenPage = components["schemas"]["Page_TokenOut_"];
type Loaded = { page: TokenPage; now: number };

const PAGE_SIZE = 50;

const DATE_TIME = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

function When({ at }: { at: string | null }) {
  if (at === null) return <>Never</>;
  return <time dateTime={at}>{DATE_TIME.format(new Date(at))}</time>;
}

type RowActions = {
  onAccess: (t: Token) => void;
  onRotate: (t: Token) => void;
  onRevoke: (t: Token) => void;
};

function columns(
  now: Date,
  project: string | undefined,
  actions: RowActions,
): Column<Token>[] {
  return [
    {
      id: "name",
      header: "Name",
      priority: "key",
      cell: (t) => <span className="font-medium break-words">{t.name}</span>,
    },
    {
      id: "status",
      header: "Status",
      priority: "status",
      cell: (t) => <StatusPill {...credentialPill(credentialState(t, now))} />,
    },
    {
      id: "prefix",
      header: "Token",
      priority: "main",
      cell: (t) => (
        <span className="font-mono text-sm break-all">{t.prefix}…</span>
      ),
    },
    {
      id: "access",
      header: "Access",
      priority: "main",
      cell: (t) =>
        t.bindings.length === 0 ? (
          <span className="text-muted-foreground">No access</span>
        ) : (
          <ul className="flex flex-col gap-1">
            {t.bindings.map((b) => (
              <li
                key={b.id}
                className="flex flex-wrap items-center gap-2 break-words"
              >
                {bindingText(b)}
                {project && b.scope === "org" && (
                  <Badge variant="secondary">Org-wide</Badge>
                )}
              </li>
            ))}
          </ul>
        ),
    },
    {
      id: "last-used",
      header: "Last used",
      priority: "extra",
      cell: (t) => <When at={t.last_used_at} />,
    },
    {
      id: "expires",
      header: "Expires",
      priority: "extra",
      cell: (t) => <When at={t.expires_at} />,
    },
    {
      id: "actions",
      header: "Actions",
      priority: "main",
      cell: (t) => {
        const { kind } = credentialState(t, now);
        if (kind !== "active" && kind !== "expiring") return null;
        return (
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              className="min-h-11"
              onClick={() => actions.onAccess(t)}
            >
              <Shield aria-hidden />
              Access
            </Button>
            {kind === "active" && (
              <Button
                variant="outline"
                className="min-h-11"
                onClick={() => actions.onRotate(t)}
              >
                <RefreshCw aria-hidden />
                Rotate
              </Button>
            )}
            <Button
              variant="outline"
              className="min-h-11"
              onClick={() => actions.onRevoke(t)}
            >
              <Trash2 aria-hidden />
              Revoke
            </Button>
          </div>
        );
      },
    },
  ];
}

/**
 * Service tokens: with `project`, the ones that can act in it (org-wide ones are marked) and new
 * ones start in it; without, every token. The caller shows it only with token.manage.
 */
export function ServiceTokenList({ project }: { project?: string }) {
  const load = (cursor?: string) =>
    must(
      api.GET("/api/tokens", {
        params: { query: { project, limit: PAGE_SIZE, cursor } },
      }),
    );
  const first = useApiData<Loaded>(`tokens:${project ?? ""}`, async () => ({
    page: await load(),
    now: Date.now(),
  }));

  // Pages loaded with "Load more" after the first; a reload of the first page drops them.
  const [more, setMore] = useState<{
    first: Loaded | null;
    items: Token[];
    cursor: string | null;
  }>({ first: null, items: [], cursor: null });
  const [loadingMore, setLoadingMore] = useState(false);
  const extra = first.data && more.first === first.data ? more : null;
  const cursor = extra ? extra.cursor : (first.data?.page.next_cursor ?? null);

  const [showInactive, setShowInactive] = useState(false);
  const [creating, setCreating] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);
  // These keep their token while closing, so the title doesn't change mid-animation.
  const [access, setAccess] = useState<{ open: boolean; token: Token | null }>({
    open: false,
    token: null,
  });
  const [rotating, setRotating] = useState<{
    open: boolean;
    token: Token | null;
  }>({ open: false, token: null });
  const [revoking, setRevoking] = useState<{
    open: boolean;
    token: Token | null;
  }>({ open: false, token: null });

  const showError = (error: unknown, what: string) => {
    if (!(error instanceof ApiError)) {
      console.error(what, error);
      toast.error("Couldn't reach the server");
      return;
    }
    toast.error(failureText(error.failure));
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
      showError(error, "loading more service tokens failed");
    } finally {
      setLoadingMore(false);
    }
  };

  const rotate = async (t: Token, overlap: number) => {
    try {
      const rotated = await must(
        api.POST("/api/tokens/{id}/rotate", {
          params: { path: { id: t.id } },
          body: { overlap_seconds: overlap },
        }),
      );
      toast("Service token rotated");
      setSecret(rotated.secret);
      first.reload();
    } catch (error) {
      showError(error, "rotating the service token failed");
      throw error;
    }
  };

  const revoke = async (t: Token) => {
    try {
      await must(
        api.DELETE("/api/tokens/{id}", { params: { path: { id: t.id } } }),
      );
      toast("Service token revoked");
      first.reload();
    } catch (error) {
      showError(error, "revoking the service token failed");
    }
  };

  const add = (
    <Button onClick={() => setCreating(true)}>
      <Plus aria-hidden />
      New service token
    </Button>
  );

  let body: React.ReactNode;
  if (first.failure) {
    body = (
      <LoadFailed
        title="Couldn't load service tokens"
        failure={first.failure}
        onRetry={first.reload}
      />
    );
  } else if (!first.data) {
    body = <LoadingRows label="Loading service tokens" />;
  } else {
    const now = new Date(first.data.now);
    const all = [...first.data.page.items, ...(extra?.items ?? [])];
    const rows = visibleCredentials(all, showInactive, now);
    body = (
      <>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <label
            data-hit-area
            className="inline-flex min-h-11 min-w-11 items-center gap-2 text-sm"
          >
            <Checkbox
              checked={showInactive}
              onChange={(e) => setShowInactive(e.target.checked)}
            />
            Show revoked and expired
          </label>
          {rows.length > 0 && add}
        </div>
        {rows.length === 0 ? (
          <EmptyState
            icon={KeyRound}
            title="No service tokens"
            description={
              project
                ? "Scripts and CI use a service token to call the API. Its roles decide what it can do in this project."
                : "Scripts and CI use a service token to call the API. Its roles decide what it can do."
            }
          >
            {add}
          </EmptyState>
        ) : (
          <DataList
            caption="Service tokens"
            columns={columns(now, project, {
              onAccess: (token) => setAccess({ open: true, token }),
              onRotate: (token) => setRotating({ open: true, token }),
              onRevoke: (token) => setRevoking({ open: true, token }),
            })}
            rows={rows}
            rowKey={(t) => t.id}
          />
        )}
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
      {body}
      <NewServiceTokenDialog
        project={project}
        open={creating}
        onOpenChange={setCreating}
        onCreated={(created) => {
          setCreating(false);
          setSecret(created);
          first.reload();
        }}
      />
      <SecretReveal
        secret={secret}
        title="Service token"
        copyLabel="Copy token"
        onClose={() => setSecret(null)}
      />
      <TokenAccessDialog
        token={access.token}
        open={access.open}
        onOpenChange={(open) => setAccess((a) => ({ ...a, open }))}
        lockedProject={project}
        onChanged={first.reload}
      />
      <RotateDialog
        open={rotating.open}
        onOpenChange={(open) => setRotating((r) => ({ ...r, open }))}
        name={rotating.token?.name ?? "the token"}
        submitLabel="Rotate token"
        onRotate={(overlap) =>
          rotating.token ? rotate(rotating.token, overlap) : Promise.resolve()
        }
      />
      <ConfirmDialog
        open={revoking.open}
        onOpenChange={(open) => setRevoking((r) => ({ ...r, open }))}
        title={`Revoke ${revoking.token?.name ?? "the token"}?`}
        description="Scripts that use it are refused at once. This can't be undone."
        confirmLabel="Revoke token"
        destructive
        onConfirm={() => {
          if (revoking.token) void revoke(revoking.token);
        }}
      />
    </div>
  );
}
