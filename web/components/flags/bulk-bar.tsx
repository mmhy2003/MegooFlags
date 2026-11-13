"use client";

import { useId, useState } from "react";

import { ConfirmDialog } from "@/components/patterns/confirm-dialog";
import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { Button } from "@/components/ui/button";
import { DialogClose } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { useApiData } from "@/hooks/use-api-data";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { type BulkResult, runBulk, tagsAfter } from "@/lib/flags/bulk";
import { must } from "@/lib/flags/errors";
import { can } from "@/lib/flags/permissions";
import type { MePermissions } from "@/lib/nav";

type FlagSummary = components["schemas"]["FlagSummary"];
type Environment = components["schemas"]["EnvironmentOut"];

/** A dialog with one field and Apply, opened from a bulk action button. */
function ApplyDialog({
  label,
  disabled,
  canApply,
  onApply,
  children,
}: {
  label: string;
  disabled: boolean;
  canApply: boolean;
  onApply: () => void;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={setOpen}
      trigger={
        <Button variant="outline" disabled={disabled}>
          {label}
        </Button>
      }
      title={label}
      footer={
        <>
          <DialogClose render={<Button variant="outline" />}>
            Cancel
          </DialogClose>
          <Button
            disabled={!canApply}
            onClick={() => {
              onApply();
              setOpen(false);
            }}
          >
            Apply
          </Button>
        </>
      }
    >
      {children}
    </ResponsiveDialog>
  );
}

function ToggleAction({
  enabled,
  environments,
  disabled,
  onApply,
}: {
  enabled: boolean;
  environments: Environment[];
  disabled: boolean;
  onApply: (env: string) => void;
}) {
  const id = useId();
  const [picked, setPicked] = useState("");
  const env = environments.some((e) => e.key === picked)
    ? picked
    : (environments[0]?.key ?? "");
  return (
    <ApplyDialog
      label={enabled ? "Turn on" : "Turn off"}
      disabled={disabled}
      canApply={env !== ""}
      onApply={() => onApply(env)}
    >
      <Label htmlFor={id}>Environment</Label>
      <NativeSelect
        id={id}
        value={env}
        onChange={(e) => setPicked(e.target.value)}
      >
        {environments.map((e) => (
          <option key={e.key} value={e.key}>
            {e.approvals_required > 0 ? `${e.name} (needs approval)` : e.name}
          </option>
        ))}
      </NativeSelect>
    </ApplyDialog>
  );
}

function TagAction({
  op,
  disabled,
  onApply,
}: {
  op: "add" | "remove";
  disabled: boolean;
  onApply: (tag: string) => void;
}) {
  const id = useId();
  const [text, setText] = useState("");
  const tag = text.trim();
  return (
    <ApplyDialog
      label={op === "add" ? "Add tag" : "Remove tag"}
      disabled={disabled}
      canApply={tag !== ""}
      onApply={() => {
        onApply(tag);
        setText("");
      }}
    >
      <Label htmlFor={id}>Tag</Label>
      <Input id={id} value={text} onChange={(e) => setText(e.target.value)} />
    </ApplyDialog>
  );
}

/**
 * Actions on the selected flags. Each sends one API call per flag, at most 4 at a time, and
 * `onFinished` gets a result for every flag.
 */
export function BulkBar({
  project,
  rows,
  perms,
  archived,
  onClear,
  onFinished,
}: {
  project: string;
  rows: FlagSummary[];
  perms: MePermissions | null;
  /** The list shows archived flags, so Restore replaces Archive. */
  archived: boolean;
  onClear: () => void;
  onFinished: (results: BulkResult[]) => void;
}) {
  const [progress, setProgress] = useState<{
    done: number;
    total: number;
  } | null>(null);
  const environments = useApiData(`environments:${project}`, () =>
    must(
      api.GET("/api/projects/{project}/environments", {
        params: { path: { project } },
      }),
    ),
  );
  const toggleEnvs = (environments.data ?? []).filter((e) =>
    can(perms, "flag.toggle", e.key),
  );
  const canToggle = perms
    ? Object.values(perms.environments).some((p) => p.includes("flag.toggle"))
    : false;
  const busy = progress !== null;

  const run = async (action: (row: FlagSummary) => Promise<unknown>) => {
    const byKey = new Map(rows.map((r) => [r.key, r]));
    setProgress({ done: 0, total: rows.length });
    const results = await runBulk(
      rows.map((r) => r.key),
      (key) => action(byKey.get(key)!),
      4,
      () => setProgress((p) => p && { ...p, done: p.done + 1 }),
    );
    setProgress(null);
    onFinished(results);
  };

  const flagPath = (key: string) => ({ path: { project, flag: key } });
  const toggle = (enabled: boolean) => (env: string) =>
    run((row) =>
      must(
        api.POST(
          "/api/projects/{project}/flags/{flag}/environments/{env}/toggle",
          {
            params: { path: { project, flag: row.key, env } },
            body: { enabled },
          },
        ),
      ),
    );
  const tag = (op: "add" | "remove") => (value: string) =>
    run(async (row) => {
      const tags = tagsAfter(row.tags, value, op);
      if (tags === null) return;
      await must(
        api.PATCH("/api/projects/{project}/flags/{flag}", {
          params: flagPath(row.key),
          body: { tags },
        }),
      );
    });
  const lifecycle = (to: "archive" | "restore") =>
    run((row) =>
      must(
        to === "archive"
          ? api.POST("/api/projects/{project}/flags/{flag}/archive", {
              params: flagPath(row.key),
            })
          : api.POST("/api/projects/{project}/flags/{flag}/restore", {
              params: flagPath(row.key),
            }),
      ),
    );

  const n = rows.length;
  return (
    <section
      aria-label="Bulk actions"
      className="sticky bottom-0 z-10 mt-4 -mx-4 border-t bg-background px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]"
    >
      <div className="flex flex-wrap items-center gap-2">
        <p className="mr-auto text-sm font-medium">{n} selected</p>
        {canToggle && (
          <>
            <ToggleAction
              enabled
              environments={toggleEnvs}
              disabled={busy}
              onApply={toggle(true)}
            />
            <ToggleAction
              enabled={false}
              environments={toggleEnvs}
              disabled={busy}
              onApply={toggle(false)}
            />
          </>
        )}
        {can(perms, "flag.update") && (
          <>
            <TagAction op="add" disabled={busy} onApply={tag("add")} />
            <TagAction op="remove" disabled={busy} onApply={tag("remove")} />
          </>
        )}
        {can(perms, "flag.archive") &&
          (archived ? (
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => lifecycle("restore")}
            >
              Restore
            </Button>
          ) : (
            <ConfirmDialog
              trigger={
                <Button variant="outline" disabled={busy}>
                  Archive
                </Button>
              }
              title={`Archive ${n} flags?`}
              description="Archived flags leave the list. You can restore them later."
              confirmLabel="Archive"
              destructive
              onConfirm={() => lifecycle("archive")}
            />
          ))}
        <Button variant="ghost" disabled={busy} onClick={onClear}>
          Clear selection
        </Button>
      </div>
      {progress && (
        <p role="status" className="mt-2 text-sm text-muted-foreground">
          {progress.done} of {progress.total}
        </p>
      )}
    </section>
  );
}
