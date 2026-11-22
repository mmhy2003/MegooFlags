"use client";

import {
  Ellipsis,
  KeyRound,
  Pause,
  Pencil,
  Play,
  Plus,
  Trash2,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/patterns/confirm-dialog";
import { type Column, DataList } from "@/components/patterns/data-list";
import { EmptyState } from "@/components/patterns/empty-state";
import { LoadFailed, LoadingRows } from "@/components/patterns/list-state";
import { StatusPill } from "@/components/patterns/status-pill";
import {
  PROTOCOL_LABELS,
  ProviderForm,
} from "@/components/settings/provider-form";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useApiData } from "@/hooks/use-api-data";
import { formFailureText } from "@/lib/access/bindings";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, must } from "@/lib/flags/errors";

type Provider = components["schemas"]["SsoProviderOut"];

type RowActions = {
  onEdit: (p: Provider) => void;
  onToggle: (p: Provider) => void;
  onDelete: (p: Provider) => void;
};

function showError(error: unknown, what: string) {
  if (!(error instanceof ApiError)) console.error(what, error);
  toast.error(
    error instanceof ApiError
      ? formFailureText(error.failure)
      : "Couldn't reach the server",
  );
}

function newUsersText(p: Provider): string {
  if (!p.jit_enabled) return "Existing users only";
  return `New users join as ${p.default_role ?? "no role"}`;
}

function columns(actions: RowActions): Column<Provider>[] {
  return [
    {
      id: "name",
      header: "Provider",
      priority: "key",
      cell: (p) => (
        <div className="flex min-w-0 flex-col">
          <span className="font-medium break-words">{p.name}</span>
          <span className="font-mono text-sm break-all text-muted-foreground">
            {p.key}
          </span>
        </div>
      ),
    },
    {
      id: "status",
      header: "Status",
      priority: "status",
      cell: (p) => (
        <StatusPill
          status={p.enabled ? "on" : "off"}
          label={p.enabled ? "Enabled" : "Disabled"}
        />
      ),
    },
    {
      id: "protocol",
      header: "Protocol",
      priority: "main",
      cell: (p) => PROTOCOL_LABELS[p.protocol],
    },
    {
      id: "new-users",
      header: "New users",
      priority: "main",
      cell: (p) => <span className="break-words">{newUsersText(p)}</span>,
    },
    {
      id: "actions",
      header: "Actions",
      priority: "main",
      cell: (p) => (
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Actions for ${p.name}`}
                className="min-h-11 min-w-11"
              />
            }
          >
            <Ellipsis aria-hidden />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-auto min-w-40">
            <DropdownMenuItem onClick={() => actions.onEdit(p)}>
              <Pencil aria-hidden />
              Edit
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => actions.onToggle(p)}>
              {p.enabled ? <Pause aria-hidden /> : <Play aria-hidden />}
              {p.enabled ? "Disable" : "Enable"}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              variant="destructive"
              onClick={() => actions.onDelete(p)}
            >
              <Trash2 aria-hidden />
              Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      ),
    },
  ];
}

/** The organization's OIDC and SAML identity providers. Only `org.manage` holders see this tab. */
export function SsoTab() {
  const list = useApiData("sso-providers", () =>
    must(
      api.GET("/api/sso/providers", { params: { query: { limit: 500 } } }),
    ).then((page) => page.items),
  );
  // The provider stays set while a dialog closes, so its title doesn't change mid-animation.
  const [form, setForm] = useState<{
    open: boolean;
    provider: Provider | null;
  }>({ open: false, provider: null });
  const [removing, setRemoving] = useState<{
    open: boolean;
    provider: Provider | null;
  }>({ open: false, provider: null });

  const toggle = async (p: Provider) => {
    try {
      await must(
        api.PATCH("/api/sso/providers/{key}", {
          params: { path: { key: p.key } },
          body: { enabled: !p.enabled },
        }),
      );
      toast(p.enabled ? "Provider disabled" : "Provider enabled");
      list.reload();
    } catch (error) {
      showError(error, "enabling or disabling the SSO provider failed");
    }
  };

  const remove = async (p: Provider) => {
    try {
      await must(
        api.DELETE("/api/sso/providers/{key}", {
          params: { path: { key: p.key } },
        }),
      );
      toast("Provider deleted");
      list.reload();
    } catch (error) {
      showError(error, "deleting the SSO provider failed");
    }
  };

  const add = (
    <Button onClick={() => setForm({ open: true, provider: null })}>
      <Plus aria-hidden />
      Add provider
    </Button>
  );

  let body: React.ReactNode;
  if (list.failure) {
    body = (
      <LoadFailed
        title="Couldn't load SSO providers"
        failure={list.failure}
        onRetry={list.reload}
      />
    );
  } else if (!list.data) {
    body = <LoadingRows label="Loading SSO providers" />;
  } else if (list.data.length === 0) {
    body = (
      <EmptyState
        icon={KeyRound}
        title="No SSO providers"
        description="Let people sign in through your identity provider with OIDC or SAML."
      />
    );
  } else {
    body = (
      <DataList
        caption="SSO providers"
        columns={columns({
          onEdit: (provider) => setForm({ open: true, provider }),
          onToggle: (provider) => void toggle(provider),
          onDelete: (provider) => setRemoving({ open: true, provider }),
        })}
        rows={list.data}
        rowKey={(p) => p.id}
      />
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex justify-end">{add}</div>
      {body}
      <ProviderForm
        provider={form.provider}
        open={form.open}
        onOpenChange={(open) => setForm((f) => ({ ...f, open }))}
        onSaved={() => {
          setForm((f) => ({ ...f, open: false }));
          list.reload();
        }}
      />
      <ConfirmDialog
        open={removing.open}
        onOpenChange={(open) => setRemoving((r) => ({ ...r, open }))}
        title={`Delete ${removing.provider?.name ?? "provider"}?`}
        description="People can no longer sign in through it, and the team memberships and roles its group mappings granted are removed."
        confirmLabel="Delete provider"
        destructive
        onConfirm={() => {
          if (removing.provider) void remove(removing.provider);
        }}
      />
    </div>
  );
}
