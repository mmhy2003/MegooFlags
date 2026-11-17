"use client";

import { useId, useState } from "react";
import { toast } from "sonner";

import { NodeToken, TOKEN_WARNING } from "@/components/nodes/node-token";
import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { Button } from "@/components/ui/button";
import { DialogClose } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api/client";
import { ApiError, must } from "@/lib/flags/errors";
import { nameError, submitError } from "@/lib/nodes/form";

function Form({ onCreated }: { onCreated: (token: string) => void }) {
  const id = useId();
  const [name, setName] = useState("");
  // The field error shows only after the first submit.
  const [checked, setChecked] = useState(false);
  const [serverError, setServerError] = useState<string | undefined>();
  const [busy, setBusy] = useState(false);

  const local = nameError(name);
  const error = serverError ?? (checked ? local : undefined);
  const errorId = `${id}-name-error`;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setChecked(true);
    if (local) return;
    setBusy(true);
    try {
      const created = await must(api.POST("/api/nodes", { body: { name } }));
      onCreated(created.token);
    } catch (err) {
      setBusy(false);
      const where = submitError(err);
      if ("field" in where) {
        setServerError(where.field);
      } else {
        if (!(err instanceof ApiError)) {
          console.error("adding the worker failed", err);
        }
        toast.error(where.toast);
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
          autoCapitalize="none"
          spellCheck={false}
          placeholder="worker-1"
          onChange={(e) => {
            setName(e.target.value);
            setServerError(undefined);
          }}
          {...(error && { "aria-invalid": true, "aria-describedby": errorId })}
        />
        {error && (
          <p id={errorId} className="text-sm text-destructive">
            {error}
          </p>
        )}
      </div>
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <DialogClose render={<Button type="button" variant="outline" />}>
          Cancel
        </DialogClose>
        <Button type="submit" disabled={busy}>
          Add worker
        </Button>
      </div>
    </form>
  );
}

/** Adds a worker, then shows its token and `.env` lines once. */
export function AddNodeDialog({
  open,
  onOpenChange,
  onAdded,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** After the API created the node, while its token is still on screen. */
  onAdded: () => void;
}) {
  const [token, setToken] = useState<string | null>(null);
  const change = (next: boolean) => {
    // The token is gone for good once the dialog closes.
    if (!next) setToken(null);
    onOpenChange(next);
  };
  return (
    <ResponsiveDialog
      // Once the token shows, a stray click outside must not throw it away.
      alert={token !== null}
      open={open}
      onOpenChange={change}
      title="Add worker"
      description={token === null ? undefined : TOKEN_WARNING}
      footer={
        token !== null && <Button onClick={() => change(false)}>Done</Button>
      }
    >
      {open &&
        (token === null ? (
          <Form
            onCreated={(t) => {
              setToken(t);
              onAdded();
            }}
          />
        ) : (
          <NodeToken token={token} />
        ))}
    </ResponsiveDialog>
  );
}
