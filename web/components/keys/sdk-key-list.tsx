"use client";

import { KeyRound, Plus, RefreshCw, Trash2 } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { NewSdkKeyDialog } from "@/components/keys/new-sdk-key-dialog";
import { RotateDialog } from "@/components/keys/rotate-dialog";
import { ConfirmDialog } from "@/components/patterns/confirm-dialog";
import { type Column, DataList } from "@/components/patterns/data-list";
import { EmptyState } from "@/components/patterns/empty-state";
import { LoadFailed, LoadingRows } from "@/components/patterns/list-state";
import { SecretReveal } from "@/components/patterns/secret-reveal";
import { StatusPill } from "@/components/patterns/status-pill";
import { useProjectPermissions } from "@/components/shell/use-project-permissions";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useApiData } from "@/hooks/use-api-data";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, failureText, must } from "@/lib/flags/errors";
import { can } from "@/lib/flags/permissions";
import {
  credentialPill,
  credentialState,
  SDK_KIND_TEXT,
  visibleCredentials,
} from "@/lib/keys/status";

type SdkKey = components["schemas"]["SdkKeyOut"];
type Loaded = { keys: SdkKey[]; now: number };

const DATE_TIME = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

type RowActions = {
  onRotate: (k: SdkKey) => void;
  onRevoke: (k: SdkKey) => void;
  onRevokeNow: (k: SdkKey) => void;
};

function columns(now: Date, actions: RowActions | null): Column<SdkKey>[] {
  const list: Column<SdkKey>[] = [
    {
      id: "name",
      header: "Name",
      priority: "key",
      cell: (k) => <span className="font-medium break-words">{k.name}</span>,
    },
    {
      id: "status",
      header: "Status",
      priority: "status",
      cell: (k) => <StatusPill {...credentialPill(credentialState(k, now))} />,
    },
    {
      id: "kind",
      header: "Kind",
      priority: "main",
      cell: (k) => SDK_KIND_TEXT[k.kind].label,
    },
    {
      id: "prefix",
      header: "Key",
      priority: "main",
      cell: (k) => (
        <span className="font-mono text-sm break-all">{k.prefix}…</span>
      ),
    },
    {
      id: "created",
      header: "Created",
      priority: "extra",
      cell: (k) => (
        <time dateTime={k.created_at}>
          {DATE_TIME.format(new Date(k.created_at))}
        </time>
      ),
    },
  ];
  if (!actions) return list;
  list.push({
    id: "actions",
    header: "Actions",
    priority: "main",
    cell: (k) => {
      const { kind } = credentialState(k, now);
      if (kind === "expiring")
        return (
          <Button
            variant="outline"
            className="min-h-11"
            onClick={() => actions.onRevokeNow(k)}
          >
            <Trash2 aria-hidden />
            Revoke now
          </Button>
        );
      if (kind !== "active") return null;
      return (
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            className="min-h-11"
            onClick={() => actions.onRotate(k)}
          >
            <RefreshCw aria-hidden />
            Rotate
          </Button>
          <Button
            variant="outline"
            className="min-h-11"
            onClick={() => actions.onRevoke(k)}
          >
            <Trash2 aria-hidden />
            Revoke
          </Button>
        </div>
      );
    },
  });
  return list;
}

/**
 * The project's SDK keys, one environment at a time (`?env=`), for the environments where the
 * caller holds sdk_key.read. Creating, rotating and revoking need sdk_key.manage there.
 */
export function SdkKeyList({ project }: { project: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const perms = useProjectPermissions(project, null, null);
  const envs = perms
    ? Object.entries(perms.environments)
        .filter(([, held]) => held.includes("sdk_key.read"))
        .map(([key]) => key)
    : [];
  const wanted = params.get("env");
  const env = wanted && envs.includes(wanted) ? wanted : (envs[0] ?? null);
  const canManage = env !== null && can(perms, "sdk_key.manage", env);

  const list = useApiData<Loaded>(
    env && `sdk-keys:${project}/${env}`,
    async () => ({
      keys: await must(
        api.GET("/api/projects/{project}/environments/{env}/sdk-keys", {
          params: { path: { project, env: env as string } },
        }),
      ),
      now: Date.now(),
    }),
  );
  const [showInactive, setShowInactive] = useState(false);
  const [creating, setCreating] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);
  // These keep their key while closing, so the title doesn't change mid-animation.
  const [rotating, setRotating] = useState<{
    open: boolean;
    key: SdkKey | null;
  }>({ open: false, key: null });
  const [revoking, setRevoking] = useState<{
    open: boolean;
    key: SdkKey | null;
  }>({ open: false, key: null });

  const selectEnv = (value: string) => {
    const next = new URLSearchParams(params);
    next.set("env", value);
    router.replace(`${pathname}?${next.toString()}`, { scroll: false });
  };

  /** Toasts the failure; a key that stopped working meanwhile reloads the list. */
  const showError = (error: unknown, what: string) => {
    if (!(error instanceof ApiError)) {
      console.error(what, error);
      toast.error("Couldn't reach the server");
      return;
    }
    toast.error(failureText(error.failure));
    if (error.failure.status === 409 && error.failure.code === "key_inactive")
      list.reload();
  };

  const path = (k: SdkKey) => ({ project, env: env as string, id: k.id });

  const rotate = async (k: SdkKey, overlap: number) => {
    try {
      const rotated = await must(
        api.POST(
          "/api/projects/{project}/environments/{env}/sdk-keys/{id}/rotate",
          { params: { path: path(k) }, body: { overlap_seconds: overlap } },
        ),
      );
      toast("SDK key rotated");
      setSecret(rotated.key);
      list.reload();
    } catch (error) {
      showError(error, "rotating the SDK key failed");
      if (error instanceof ApiError && error.failure.code === "key_inactive")
        setRotating((r) => ({ ...r, open: false }));
      throw error;
    }
  };

  const revoke = async (k: SdkKey) => {
    try {
      await must(
        api.DELETE("/api/projects/{project}/environments/{env}/sdk-keys/{id}", {
          params: { path: path(k) },
        }),
      );
      toast("SDK key revoked");
      list.reload();
    } catch (error) {
      showError(error, "revoking the SDK key failed");
    }
  };

  const add = canManage && (
    <Button onClick={() => setCreating(true)}>
      <Plus aria-hidden />
      New SDK key
    </Button>
  );

  let body: React.ReactNode;
  if (!perms) {
    body = <LoadingRows label="Loading SDK keys" />;
  } else if (env === null) {
    body = (
      <EmptyState
        icon={KeyRound}
        title="No SDK keys to show"
        description="You can't see the SDK keys of any environment in this project."
      />
    );
  } else if (list.failure) {
    body = (
      <LoadFailed
        title="Couldn't load SDK keys"
        failure={list.failure}
        onRetry={list.reload}
      />
    );
  } else if (!list.data) {
    body = <LoadingRows label="Loading SDK keys" />;
  } else {
    const now = new Date(list.data.now);
    const rows = visibleCredentials(list.data.keys, showInactive, now);
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
            title={`No SDK keys in ${env}`}
            description="SDKs use a key to download flags. Server keys are for backends, client keys for browser and mobile apps."
          >
            {add}
          </EmptyState>
        ) : (
          <DataList
            caption={`SDK keys in ${env}`}
            columns={columns(
              now,
              canManage
                ? {
                    onRotate: (key) => setRotating({ open: true, key }),
                    onRevoke: (key) => setRevoking({ open: true, key }),
                    onRevokeNow: (key) => void revoke(key),
                  }
                : null,
            )}
            rows={rows}
            rowKey={(k) => k.id}
          />
        )}
      </>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {envs.length > 0 && env && (
        <Tabs value={env} onValueChange={(value) => selectEnv(String(value))}>
          <div className="max-w-full overflow-x-auto">
            <TabsList
              aria-label="Environment"
              className="group-data-horizontal/tabs:h-auto"
            >
              {envs.map((key) => (
                <TabsTrigger key={key} value={key} className="min-h-11 px-3">
                  {key}
                </TabsTrigger>
              ))}
            </TabsList>
          </div>
        </Tabs>
      )}
      {body}
      {env && (
        <NewSdkKeyDialog
          project={project}
          env={env}
          open={creating}
          onOpenChange={setCreating}
          onCreated={(key) => {
            setCreating(false);
            setSecret(key);
            list.reload();
          }}
        />
      )}
      <SecretReveal
        secret={secret}
        title="SDK key"
        copyLabel="Copy key"
        onClose={() => setSecret(null)}
      />
      <RotateDialog
        open={rotating.open}
        onOpenChange={(open) => setRotating((r) => ({ ...r, open }))}
        name={rotating.key?.name ?? "the key"}
        onRotate={(overlap) =>
          rotating.key ? rotate(rotating.key, overlap) : Promise.resolve()
        }
      />
      <ConfirmDialog
        open={revoking.open}
        onOpenChange={(open) => setRevoking((r) => ({ ...r, open }))}
        title={`Revoke ${revoking.key?.name ?? "the key"}?`}
        description="SDKs that use it stop getting flags at once. This can't be undone."
        confirmLabel="Revoke key"
        destructive
        onConfirm={() => {
          if (revoking.key) void revoke(revoking.key);
        }}
      />
    </div>
  );
}
