"use client";

import { useId, useState } from "react";
import { toast } from "sonner";

import { AccessRows } from "@/components/access/access-rows";
import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { ExpiryField } from "@/components/tokens/expiry-field";
import { Button } from "@/components/ui/button";
import { DialogClose } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  type AccessRow,
  formFailureText,
  scopeBody,
} from "@/lib/access/bindings";
import { api } from "@/lib/api/client";
import { ApiError, must } from "@/lib/flags/errors";
import {
  expiresAt,
  expiryError,
  type ExpiryValue,
  SERVICE_EXPIRY_DEFAULT,
} from "@/lib/tokens/expiry";

const NAME_MAX = 200;

function nameError(name: string): string | undefined {
  if (name.trim() === "") return "Enter a name";
  if (name.length > NAME_MAX) return `Use at most ${NAME_MAX} characters`;
  return undefined;
}

/** Whether every row has a role and the scope its level needs. */
function rowsComplete(rows: AccessRow[]): boolean {
  return rows.every(
    (r) =>
      r.role !== "" &&
      (r.scope === "org" ||
        (r.project !== null && (r.scope === "project" || r.env !== null))),
  );
}

/**
 * Creates a service token; `onCreated` gets the secret, which the API shows only once. With
 * `project` its access starts, and stays, in that project.
 */
export function NewServiceTokenDialog({
  project,
  open,
  onOpenChange,
  onCreated,
}: {
  project?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (secret: string) => void;
}) {
  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={onOpenChange}
      title="New service token"
      description="A token for scripts and CI. It can do what its roles allow, and no more than you can."
    >
      {open && <Form project={project} onCreated={onCreated} />}
    </ResponsiveDialog>
  );
}

function Form({
  project,
  onCreated,
}: {
  project?: string;
  onCreated: (secret: string) => void;
}) {
  const id = useId();
  const [name, setName] = useState("");
  const [expiry, setExpiry] = useState<ExpiryValue>(SERVICE_EXPIRY_DEFAULT);
  const [rows, setRows] = useState<AccessRow[]>([
    {
      role: "viewer",
      scope: project ? "project" : "org",
      project: project ?? null,
      env: null,
    },
  ]);
  // Field errors show only after the first submit.
  const [checked, setChecked] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const nameProblem = nameError(name);
  const expiryProblem = expiryError(expiry, new Date());
  const errorId = `${id}-name-error`;
  const showName = checked ? nameProblem : undefined;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setChecked(true);
    if (nameProblem || expiryProblem || !rowsComplete(rows)) return;
    setBusy(true);
    setFailure(null);
    try {
      const created = await must(
        api.POST("/api/tokens", {
          body: {
            name,
            expires_at: expiresAt(expiry, new Date()),
            bindings: rows.map((r) => ({ role: r.role, ...scopeBody(r) })),
          },
        }),
      );
      toast("Service token created");
      onCreated(created.secret);
    } catch (err) {
      setBusy(false);
      if (err instanceof ApiError) {
        setFailure(formFailureText(err.failure));
      } else {
        console.error("creating the service token failed", err);
        setFailure("Couldn't reach the server");
      }
    }
  };

  return (
    <form noValidate onSubmit={submit} className="flex flex-col gap-5">
      <div className="flex min-w-0 flex-col gap-2">
        <Label htmlFor={`${id}-name`}>Name</Label>
        <Input
          id={`${id}-name`}
          value={name}
          placeholder="deploy pipeline"
          onChange={(e) => setName(e.target.value)}
          {...(showName && {
            "aria-invalid": true,
            "aria-describedby": errorId,
          })}
        />
        {showName && (
          <p id={errorId} className="text-sm text-destructive">
            {showName}
          </p>
        )}
      </div>
      <ExpiryField
        value={expiry}
        onChange={setExpiry}
        idPrefix={`${id}-expiry`}
        error={checked ? expiryProblem : undefined}
      />
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-sm font-medium">Access</legend>
        <AccessRows
          value={rows}
          onChange={setRows}
          lockedProject={project}
          idPrefix={`${id}-access`}
        />
        {checked && !rowsComplete(rows) && (
          <p className="text-sm text-destructive">
            Pick a role and where it applies for every access.
          </p>
        )}
      </fieldset>
      {failure && (
        <p role="alert" className="text-sm text-destructive">
          {failure}
        </p>
      )}
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <DialogClose render={<Button type="button" variant="outline" />}>
          Cancel
        </DialogClose>
        <Button type="submit" disabled={busy}>
          Create token
        </Button>
      </div>
    </form>
  );
}
