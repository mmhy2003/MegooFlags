"use client";

import { KeyRound, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/patterns/confirm-dialog";
import { type Column, DataList } from "@/components/patterns/data-list";
import { EmptyState } from "@/components/patterns/empty-state";
import { LoadFailed, LoadingRows } from "@/components/patterns/list-state";
import { SecretReveal } from "@/components/patterns/secret-reveal";
import { StatusPill } from "@/components/patterns/status-pill";
import { NewPersonalTokenDialog } from "@/components/tokens/new-personal-token-dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { useApiData } from "@/hooks/use-api-data";
import { scopeText } from "@/lib/access/bindings";
import { PERMISSION_LABELS } from "@/lib/access/permissions";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, failureText, must } from "@/lib/flags/errors";
import {
  credentialPill,
  credentialState,
  visibleCredentials,
} from "@/lib/keys/status";
import { expiryText } from "@/lib/tokens/expiry";

type Token = components["schemas"]["PersonalTokenOut"];
type Loaded = { tokens: Token[]; now: number };

const DATE_TIME = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

function columns(now: Date, onRevoke: (t: Token) => void): Column<Token>[] {
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
      id: "where",
      header: "Where it works",
      priority: "main",
      cell: (t) => (
        <span className="break-words">
          {t.project === null
            ? "Everything"
            : scopeText({
                scope: t.env === null ? "project" : "environment",
                project: t.project,
                env: t.env,
              })}
        </span>
      ),
    },
    {
      id: "permissions",
      header: "Permissions",
      priority: "extra",
      cell: (t) => (
        <ul className="flex flex-col gap-1">
          {t.permissions.map((p) => (
            <li key={p} className="break-words">
              {PERMISSION_LABELS[p]}
            </li>
          ))}
        </ul>
      ),
    },
    {
      id: "last-used",
      header: "Last used",
      priority: "extra",
      cell: (t) =>
        t.last_used_at === null ? (
          <>Never</>
        ) : (
          <time dateTime={t.last_used_at}>
            {DATE_TIME.format(new Date(t.last_used_at))}
          </time>
        ),
    },
    {
      id: "expires",
      header: "Expires",
      priority: "extra",
      cell: (t) =>
        t.expires_at === null ? (
          <>Never</>
        ) : (
          <time
            dateTime={t.expires_at}
            title={DATE_TIME.format(new Date(t.expires_at))}
          >
            {expiryText(t.expires_at, now)}
          </time>
        ),
    },
    {
      id: "actions",
      header: "Actions",
      priority: "main",
      cell: (t) => {
        const { kind } = credentialState(t, now);
        if (kind !== "active" && kind !== "expiring") return null;
        return (
          <Button
            variant="outline"
            className="min-h-11"
            onClick={() => onRevoke(t)}
          >
            <Trash2 aria-hidden />
            Revoke
          </Button>
        );
      },
    },
  ];
}

/** Your personal API tokens: create one for a scope and permissions, or revoke one. */
export function PersonalTokenList() {
  const list = useApiData<Loaded>("account-tokens", async () => ({
    tokens: await must(api.GET("/api/account/tokens")),
    now: Date.now(),
  }));
  const [showInactive, setShowInactive] = useState(false);
  const [creating, setCreating] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);
  // Keeps its token while closing, so the title doesn't change mid-animation.
  const [revoking, setRevoking] = useState<{
    open: boolean;
    token: Token | null;
  }>({ open: false, token: null });

  const revoke = async (t: Token) => {
    try {
      await must(
        api.DELETE("/api/account/tokens/{id}", {
          params: { path: { id: t.id } },
        }),
      );
      toast("API token revoked");
      list.reload();
    } catch (error) {
      if (!(error instanceof ApiError)) {
        console.error("revoking the API token failed", error);
        toast.error("Couldn't reach the server");
        return;
      }
      toast.error(failureText(error.failure));
    }
  };

  const add = (
    <Button onClick={() => setCreating(true)}>
      <Plus aria-hidden />
      New token
    </Button>
  );

  let body: React.ReactNode;
  if (list.failure) {
    body = (
      <LoadFailed
        title="Couldn't load your API tokens"
        failure={list.failure}
        onRetry={list.reload}
      />
    );
  } else if (!list.data) {
    body = <LoadingRows label="Loading API tokens" />;
  } else {
    const now = new Date(list.data.now);
    const rows = visibleCredentials(list.data.tokens, showInactive, now);
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
            title="No API tokens"
            description="Scripts and the CLI use an API token to act as you. Give it only the permissions it needs."
          >
            {add}
          </EmptyState>
        ) : (
          <DataList
            caption="API tokens"
            columns={columns(now, (token) =>
              setRevoking({ open: true, token }),
            )}
            rows={rows}
            rowKey={(t) => t.id}
          />
        )}
      </>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {body}
      <NewPersonalTokenDialog
        open={creating}
        onOpenChange={setCreating}
        onCreated={(created) => {
          setCreating(false);
          setSecret(created);
          list.reload();
        }}
      />
      <SecretReveal
        secret={secret}
        title="API token"
        copyLabel="Copy token"
        onClose={() => setSecret(null)}
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
