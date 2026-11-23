"use client";

import { useId, useState } from "react";
import { toast } from "sonner";

import { ScopePicker } from "@/components/access/scope-picker";
import { usePermissionCatalogue } from "@/components/access/use-catalogue";
import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { ExpiryField } from "@/components/tokens/expiry-field";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { DialogClose } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useApiData } from "@/hooks/use-api-data";
import { formFailureText, type ScopeValue } from "@/lib/access/bindings";
import {
  grantableAt,
  heldAt,
  type Level,
  type Permission,
  PERMISSION_LABELS,
} from "@/lib/access/permissions";
import { api } from "@/lib/api/client";
import { ApiError, must } from "@/lib/flags/errors";
import {
  expiresAt,
  expiryError,
  type ExpiryValue,
  PERSONAL_EXPIRY_DEFAULT,
} from "@/lib/tokens/expiry";

const NAME_MAX = 200;

/** "Where it works", in the words of a token rather than a role. */
const WHERE_TEXT: Record<Level, { label: string; where: string }> = {
  org: {
    label: "Everything",
    where: "Works everywhere you have access.",
  },
  project: {
    label: "One project",
    where: "Works only in one project and its environments.",
  },
  environment: {
    label: "One environment",
    where: "Works only in one environment of a project.",
  },
};

/** Whether the scope names the project and environment its level needs. */
function scopeComplete(s: ScopeValue): boolean {
  if (s.scope === "org") return true;
  if (s.project === null) return false;
  return s.scope === "project" || s.env !== null;
}

/**
 * Creates a personal API token that acts as you, within a scope and limited to permissions you
 * hold there; `onCreated` gets the secret, which the API shows only once.
 */
export function NewPersonalTokenDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (secret: string) => void;
}) {
  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={onOpenChange}
      title="New API token"
      description="A token that acts as you, for scripts and the CLI. It never does more than you can."
    >
      {open && <Form onCreated={onCreated} />}
    </ResponsiveDialog>
  );
}

function Form({ onCreated }: { onCreated: (secret: string) => void }) {
  const id = useId();
  const [name, setName] = useState("");
  const [scope, setScope] = useState<ScopeValue>({
    scope: "org",
    project: null,
    env: null,
  });
  const [ticked, setTicked] = useState<Permission[]>([]);
  const [expiry, setExpiry] = useState<ExpiryValue>(PERSONAL_EXPIRY_DEFAULT);
  // The expiry error shows only after the first submit.
  const [checked, setChecked] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const catalogue = usePermissionCatalogue();
  const project = scope.scope === "org" ? null : scope.project;
  const perms = useApiData(
    scope.scope === "org" || project !== null ? `me:${project ?? ""}` : null,
    () =>
      must(
        api.GET("/api/auth/me/permissions", {
          params: { query: project ? { project } : {} },
        }),
      ),
  );
  const grantable =
    scopeComplete(scope) && catalogue && perms.data
      ? grantableAt(
          catalogue,
          heldAt(perms.data, scope.scope, scope.env ?? undefined),
          scope.scope,
        )
      : null;

  // Ticked permissions that the new scope doesn't allow are dropped once it is known.
  const scopeKey = `${scope.scope}|${scope.project}|${scope.env}`;
  const [prunedFor, setPrunedFor] = useState(scopeKey);
  if (grantable && prunedFor !== scopeKey) {
    setPrunedFor(scopeKey);
    setTicked((t) => t.filter((p) => grantable.includes(p)));
  }
  const chosen = grantable ? ticked.filter((p) => grantable.includes(p)) : [];

  const expiryProblem = expiryError(expiry, new Date());
  const canSubmit =
    name.trim() !== "" && name.length <= NAME_MAX && chosen.length > 0;

  const toggle = (p: Permission, on: boolean) =>
    setTicked((t) => (on ? [...t, p] : t.filter((x) => x !== p)));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setChecked(true);
    if (!canSubmit || expiryProblem) return;
    setBusy(true);
    setFailure(null);
    try {
      const created = await must(
        api.POST("/api/account/tokens", {
          body: {
            name,
            permissions: chosen,
            ...(scope.project !== null && { project: scope.project }),
            ...(scope.env !== null && { env: scope.env }),
            expires_at: expiresAt(expiry, new Date()),
          },
        }),
      );
      toast("API token created");
      onCreated(created.secret);
    } catch (err) {
      setBusy(false);
      if (err instanceof ApiError) {
        setFailure(formFailureText(err.failure));
      } else {
        console.error("creating the API token failed", err);
        setFailure("Couldn't reach the server");
      }
    }
  };

  let permissions: React.ReactNode;
  if (!scopeComplete(scope)) {
    permissions = (
      <p className="text-sm text-muted-foreground">
        Pick where it works first.
      </p>
    );
  } else if (perms.failure) {
    permissions = (
      <p role="alert" className="text-sm text-destructive">
        Couldn&apos;t load your permissions. {perms.failure.message}
      </p>
    );
  } else if (!grantable) {
    permissions = (
      <p role="status" className="text-sm text-muted-foreground">
        Loading your permissions…
      </p>
    );
  } else if (grantable.length === 0) {
    permissions = (
      <p className="text-sm text-muted-foreground">
        You have no permissions there that a token can use.
      </p>
    );
  } else {
    permissions = (
      <ul className="flex flex-col gap-1">
        {grantable.map((p) => (
          <li key={p}>
            <label
              data-hit-area
              className="inline-flex min-h-11 min-w-11 items-center gap-2 text-sm"
            >
              <Checkbox
                checked={chosen.includes(p)}
                onChange={(e) => toggle(p, e.target.checked)}
              />
              {PERMISSION_LABELS[p]}
            </label>
          </li>
        ))}
      </ul>
    );
  }

  return (
    <form noValidate onSubmit={submit} className="flex flex-col gap-5">
      <div className="flex min-w-0 flex-col gap-2">
        <Label htmlFor={`${id}-name`}>Name</Label>
        <Input
          id={`${id}-name`}
          value={name}
          maxLength={NAME_MAX}
          placeholder="laptop"
          onChange={(e) => setName(e.target.value)}
        />
      </div>
      <ScopePicker
        value={scope}
        onChange={setScope}
        idPrefix="token"
        label="Where it works"
        levelText={WHERE_TEXT}
      />
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-sm font-medium">Permissions</legend>
        {permissions}
      </fieldset>
      <ExpiryField
        value={expiry}
        onChange={setExpiry}
        idPrefix={`${id}-expiry`}
        error={checked ? expiryProblem : undefined}
      />
      {failure && (
        <p role="alert" className="text-sm text-destructive">
          {failure}
        </p>
      )}
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <DialogClose render={<Button type="button" variant="outline" />}>
          Cancel
        </DialogClose>
        <Button type="submit" disabled={busy || !canSubmit}>
          Create token
        </Button>
      </div>
    </form>
  );
}
