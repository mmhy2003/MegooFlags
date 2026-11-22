"use client";

import { useId, useState } from "react";
import { toast } from "sonner";

import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { Button } from "@/components/ui/button";
import { DialogClose } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, failureText, must } from "@/lib/flags/errors";
import { SDK_KIND_TEXT } from "@/lib/keys/status";

type Kind = components["schemas"]["SdkKeyKind"];

const KINDS: Kind[] = ["server", "client"];
const NAME_MAX = 200;

function nameError(name: string): string | undefined {
  if (name.trim() === "") return "Enter a name";
  if (name.length > NAME_MAX) return `Use at most ${NAME_MAX} characters`;
  return undefined;
}

/** Creates an SDK key in `env`; `onCreated` gets the secret, which the API shows only once. */
export function NewSdkKeyDialog({
  project,
  env,
  open,
  onOpenChange,
  onCreated,
}: {
  project: string;
  env: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (secret: string) => void;
}) {
  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={onOpenChange}
      title="New SDK key"
      description={`The key works only in ${env}.`}
    >
      {open && <Form project={project} env={env} onCreated={onCreated} />}
    </ResponsiveDialog>
  );
}

function Form({
  project,
  env,
  onCreated,
}: {
  project: string;
  env: string;
  onCreated: (secret: string) => void;
}) {
  const id = useId();
  const [name, setName] = useState("");
  const [kind, setKind] = useState<Kind>("server");
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
      const created = await must(
        api.POST("/api/projects/{project}/environments/{env}/sdk-keys", {
          params: { path: { project, env } },
          body: { name, kind },
        }),
      );
      toast("SDK key created");
      onCreated(created.key);
    } catch (err) {
      setBusy(false);
      if (!(err instanceof ApiError)) {
        console.error("creating the SDK key failed", err);
        toast.error("Couldn't reach the server");
      } else if (err.failure.status === 422) {
        setServerError(err.failure.message);
      } else {
        toast.error(failureText(err.failure));
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
          placeholder="checkout backend"
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
      <fieldset className="flex flex-col gap-1">
        <legend className="mb-1 text-sm font-medium">Kind</legend>
        <div role="radiogroup" className="flex flex-wrap gap-x-6">
          {KINDS.map((k) => (
            <label
              key={k}
              data-hit-area
              className="inline-flex min-h-11 min-w-11 items-center gap-2"
            >
              <input
                type="radio"
                name={`${id}-kind`}
                className="size-5 shrink-0 accent-primary"
                checked={kind === k}
                aria-describedby={`${id}-kind-help`}
                onChange={() => setKind(k)}
              />
              {SDK_KIND_TEXT[k].label}
            </label>
          ))}
        </div>
        <p id={`${id}-kind-help`} className="text-sm text-muted-foreground">
          {SDK_KIND_TEXT[kind].help}
        </p>
      </fieldset>
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <DialogClose render={<Button type="button" variant="outline" />}>
          Cancel
        </DialogClose>
        <Button type="submit" disabled={busy}>
          Create key
        </Button>
      </div>
    </form>
  );
}
